"""
Carrier Check-Call Voice Bot — FastAPI Orchestrator

Central controller coordinating Twilio, Whisper STT, and Claude dialog agent.
Run with: uvicorn app.main:app --reload --port 8000
"""
from fastapi import FastAPI, Request
from fastapi.responses import Response
import logging

from app.models.db import init_db
from app.routes import voice, tms, dashboard, media_stream

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ccbot")

app = FastAPI(title="Carrier Check-Call Voice Bot")


@app.on_event("startup")
def on_startup():
    logger.info("Initializing database tables...")
    init_db()
    logger.info("Startup complete.")


# Route groups
app.include_router(voice.router, prefix="", tags=["voice"])
app.include_router(tms.router, prefix="/tms", tags=["tms"])
app.include_router(dashboard.router, prefix="", tags=["dashboard"])
app.include_router(media_stream.router, prefix="", tags=["media-stream"])


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Hardening (Step 5): last-resort safety net ---
# If anything unexpected slips through a voice route's own error handling,
# this ensures Twilio still gets valid TwiML back (so the caller hears a
# graceful goodbye) instead of a raw 500 that would just drop the call.
@app.exception_handler(Exception)
async def voice_safe_fallback(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.url.path}: {exc}", exc_info=True)

    voice_paths = {"/incoming-call", "/gather-response"}
    if request.url.path in voice_paths:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Sorry, we're having a technical issue. Someone will follow up with you. Goodbye.</Say>
    <Hangup/>
</Response>"""
        return Response(content=twiml, media_type="application/xml", status_code=200)

    return Response(content='{"error": "internal server error"}', media_type="application/json", status_code=500)