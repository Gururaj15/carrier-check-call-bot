"""
Voice routes — multi-turn check-call conversation driven by Twilio's
built-in speech recognition (<Gather input="speech">) and the Claude
dialog agent.

ARCHITECTURE NOTE: the original design used Twilio Media Streams
(<Start><Stream>) + local Whisper for live transcription. Live testing
showed Twilio trial accounts silently strip both <Record> and <Stream>
from TwiML (confirmed in Twilio's own docs). <Gather input="speech"> IS
supported on trial, so the live-call leg uses Twilio's built-in ASR for
turn-by-turn text. Local Whisper (app/services/stt.py) is still built and
demoed separately for offline/batch transcription of recorded audio. This
is a deliberate, documented tradeoff — see README.

HARDENING (Step 5):
- MAX_HISTORY_ENTRIES guards against a runaway conversation (confused
  caller, looping agent) — after a cap, we end the call gracefully instead
  of looping forever.
- The dialog agent's "error" result (all Claude API retries exhausted) ends
  the call with an apology instead of leaving the caller stuck.
- The TMS write is wrapped so a DB hiccup doesn't produce a raw 500 back to
  Twilio mid-call — the caller still hears a normal goodbye, and the
  failure is logged for follow-up.
"""
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.models.db import get_db, Call
from app.services.dialog_agent import run_turn
from app.routes.tms import write_load_status

logger = logging.getLogger("ccbot.voice")
router = APIRouter()

# ~6 back-and-forth exchanges (each exchange = 1 user entry + 1 assistant entry)
MAX_HISTORY_ENTRIES = 12


def gather_twiml(prompt_text: str) -> str:
    """Build a <Gather> TwiML block: speak prompt_text, then listen for speech."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="/gather-response" method="POST"
            speechTimeout="auto" speechModel="phone_call" language="en-US">
        <Say voice="Polly.Joanna">{prompt_text}</Say>
    </Gather>
    <Say>We didn't catch that. Goodbye.</Say>
    <Hangup/>
</Response>"""


def end_call_twiml(message: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{message}</Say>
    <Hangup/>
</Response>"""


@router.post("/incoming-call")
async def incoming_call(request: Request, db: Session = Depends(get_db)):
    """Twilio webhook: fired when a call comes in. Logs the call, greets the
    carrier, and opens the first <Gather> to start the check-call conversation."""
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From")
    to_number = form.get("To")

    logger.info(f"Incoming call: sid={call_sid} from={from_number} to={to_number}")

    existing = db.query(Call).filter(Call.twilio_call_sid == call_sid).first()
    if not existing:
        call_row = Call(
            twilio_call_sid=call_sid,
            from_number=from_number,
            to_number=to_number,
            status="in_progress",
            intent_trace=[],
        )
        db.add(call_row)
        db.commit()

    greeting = (
        "Hi, this is Vooma's automated check call assistant. "
        "Can you give me an update on your load? "
        "Where are you right now, and what's your E T A?"
    )
    return Response(content=gather_twiml(greeting), media_type="application/xml")


@router.post("/gather-response")
async def gather_response(request: Request, db: Session = Depends(get_db)):
    """
    Fires after each <Gather> completes with SpeechResult text.
    Feeds the running conversation into the Claude dialog agent, which
    either asks a follow-up question or signals extraction is complete.
    """
    form = await request.form()
    call_sid = form.get("CallSid")
    speech_result = (form.get("SpeechResult") or "").strip()

    logger.info(f"[{call_sid}] carrier said: {speech_result!r}")

    call_row = db.query(Call).filter(Call.twilio_call_sid == call_sid).first()
    if not call_row:
        logger.error(f"[{call_sid}] no Call row found — cannot continue conversation")
        return Response(
            content=end_call_twiml("Sorry, something went wrong on our end. Goodbye."),
            media_type="application/xml",
        )

    history = list(call_row.intent_trace or [])

    if not speech_result:
        if not history:
            # Nothing heard yet at all — re-prompt without calling Claude
            return Response(
                content=gather_twiml("Sorry, I didn't catch that. Could you repeat your update?"),
                media_type="application/xml",
            )
    else:
        history.append({"role": "user", "content": speech_result})
        call_row.full_transcript = (call_row.full_transcript or "") + f"\nCarrier: {speech_result}"

    # --- Hardening: runaway conversation guard ---
    if len(history) >= MAX_HISTORY_ENTRIES:
        logger.warning(f"[{call_sid}] hit MAX_HISTORY_ENTRIES ({MAX_HISTORY_ENTRIES}) — ending call")
        call_row.intent_trace = history
        call_row.status = "incomplete_max_turns"
        db.commit()
        return Response(
            content=end_call_twiml(
                "Thanks for the details. We'll follow up if we need anything else. Goodbye."
            ),
            media_type="application/xml",
        )

    result = run_turn(history)

    # --- Hardening: dialog agent exhausted retries ---
    if result["type"] == "error":
        logger.error(f"[{call_sid}] dialog agent failed after retries — ending call gracefully")
        call_row.intent_trace = history
        call_row.status = "failed_agent_error"
        db.commit()
        return Response(
            content=end_call_twiml(
                "Sorry, we're having trouble on our end right now. "
                "Someone will follow up with you shortly. Goodbye."
            ),
            media_type="application/xml",
        )

    if result["type"] == "complete":
        history.append({"role": "assistant", "content": f"[extracted: {result['data']}]"})
        call_row.intent_trace = history
        db.commit()

        # --- Hardening: TMS write failure shouldn't break the call ---
        try:
            write_load_status(db, call_row.id, result["data"])
        except Exception as e:
            logger.error(f"[{call_sid}] TMS write failed: {e}")
            db.rollback()
            call_row.status = "completed_tms_write_failed"
            db.commit()

        return Response(
            content=end_call_twiml("Got it, thanks for the update. Goodbye."),
            media_type="application/xml",
        )

    # Still gathering info — ask the follow-up question and keep listening
    question = result["text"]
    history.append({"role": "assistant", "content": question})
    call_row.full_transcript = (call_row.full_transcript or "") + f"\nAgent: {question}"
    call_row.intent_trace = history
    db.commit()

    return Response(content=gather_twiml(question), media_type="application/xml")