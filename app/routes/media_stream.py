"""
Media Stream WebSocket endpoint.

Twilio's <Stream> TwiML noun opens a WebSocket connection here and pushes
JSON messages containing base64 mu-law audio chunks in real time. We decode
each chunk, run it through silence-based turn segmentation, and transcribe
each completed segment with local Whisper — appending results to the call's
transcript in Postgres as they come in.

This replaces the old <Record>-based approach (blocked on Twilio trial
accounts) with a Media Streams approach that trial accounts DO support, and
is closer to a real production near-real-time pipeline anyway.
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.models.db import SessionLocal, Call
from app.services.audio_utils import decode_twilio_chunk, SilenceSegmenter
from app.services.stt import transcribe_pcm16

logger = logging.getLogger("ccbot.media_stream")
router = APIRouter()


def _append_transcript(db: Session, call_sid: str, text: str):
    """Persist a transcribed segment onto the Call row (transcript + intent_trace)."""
    if not text:
        return
    call_row = db.query(Call).filter(Call.twilio_call_sid == call_sid).first()
    if not call_row:
        logger.warning(f"No Call row found for sid={call_sid}, skipping transcript write")
        return

    call_row.full_transcript = (call_row.full_transcript or "") + f"\nCarrier: {text}"

    trace = call_row.intent_trace or []
    trace.append({
        "role": "carrier",
        "text": text,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    call_row.intent_trace = trace

    db.commit()
    logger.info(f"[{call_sid}] transcribed segment: {text}")


@router.websocket("/media-stream")
async def media_stream(websocket: WebSocket):
    await websocket.accept()
    logger.info("Media stream WebSocket connected")

    call_sid = None
    segmenter = SilenceSegmenter()

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            event = msg.get("event")

            if event == "start":
                call_sid = msg["start"]["callSid"]
                logger.info(f"Stream started for call sid={call_sid}")

            elif event == "media":
                payload_b64 = msg["media"]["payload"]
                pcm_chunk = decode_twilio_chunk(payload_b64)
                segment = segmenter.add_chunk(pcm_chunk)

                if segment is not None:
                    text = transcribe_pcm16(segment, sample_rate=8000)
                    if text and call_sid:
                        db = SessionLocal()
                        try:
                            _append_transcript(db, call_sid, text)
                        finally:
                            db.close()

            elif event == "stop":
                logger.info(f"Stream stopped for call sid={call_sid}")
                # Flush any trailing buffered speech
                segment = segmenter.flush()
                if segment is not None and call_sid:
                    text = transcribe_pcm16(segment, sample_rate=8000)
                    if text:
                        db = SessionLocal()
                        try:
                            _append_transcript(db, call_sid, text)
                        finally:
                            db.close()
                break

    except WebSocketDisconnect:
        logger.info(f"Media stream WebSocket disconnected (call sid={call_sid})")
    except Exception as e:
        logger.error(f"Media stream error: {e}")
