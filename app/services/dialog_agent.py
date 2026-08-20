"""
Claude dialog agent — stateful multi-turn extraction of load status fields
from a carrier's spoken check-call.

ARCHITECTURE NOTE: the live call's speech-to-text comes from Twilio's
built-in <Gather input="speech"> (its SpeechResult field), not from a live
Whisper audio stream. Twilio trial accounts block both <Record> and
<Stream> outright (confirmed via Twilio's own docs — see README), so a
live Whisper pipeline isn't reachable without upgrading. Local Whisper
(app/services/stt.py) is still used and demoed separately for offline
batch transcription of recorded test audio. This is a deliberate,
documented tradeoff.

Claude decides, turn by turn, whether it has enough information:
- If not, it replies with a short natural-language follow-up question.
- If it does, it calls the `submit_load_status` tool, ending the call.

HARDENING (Step 5): API calls retry on transient failures (rate limits,
timeouts, connection errors) with exponential backoff, so a single flaky
request doesn't drop the call.
"""
import os
import time
import logging
import anthropic
from anthropic import Anthropic

logger = logging.getLogger("ccbot.dialog_agent")

_client = None

MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 1.5

# Errors worth retrying — transient/network/rate-limit issues, not e.g. bad requests
RETRYABLE_ERRORS = (
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
    anthropic.RateLimitError,
    anthropic.InternalServerError,
)


def get_client():
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

SYSTEM_PROMPT = """You are Vooma's automated carrier check-call assistant. You are on a live phone call with a truck driver, getting a status update on their load.

You need to collect exactly these four fields:
- current_location (city/state, highway marker, or "at destination")
- eta (their estimated arrival time or date, in their own words)
- status: one of "in_transit", "delayed", "delivered", "arrived_early"
- exception_reason: a short reason if there's a delay or problem, or "none" if there isn't one

Ask ONE short, natural, conversational question at a time — you're talking to a busy driver on the phone, not filling out a form. Don't re-ask for information you already have from earlier in the conversation. As soon as you have all four fields, call the submit_load_status tool immediately instead of asking anything else."""

SUBMIT_TOOL = {
    "name": "submit_load_status",
    "description": "Submit the final structured load status extracted from the carrier's check-call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "current_location": {"type": "string"},
            "eta": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["in_transit", "delayed", "delivered", "arrived_early"],
            },
            "exception_reason": {"type": "string"},
        },
        "required": ["current_location", "eta", "status", "exception_reason"],
    },
}


def _call_claude_with_retry(client, **kwargs):
    """
    Call the Messages API with retry + exponential backoff on transient
    errors. Raises immediately on non-retryable errors (e.g. bad request,
    auth failure) since retrying those would never succeed.
    """
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except RETRYABLE_ERRORS as e:
            last_error = e
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                f"Claude API call failed (attempt {attempt}/{MAX_RETRIES}): "
                f"{type(e).__name__}: {e}. Retrying in {delay:.1f}s..."
            )
            if attempt < MAX_RETRIES:
                time.sleep(delay)
        except anthropic.APIStatusError as e:
            # Non-retryable (bad request, auth, etc.) — fail fast, don't waste retries
            logger.error(f"Claude API non-retryable error: {e}")
            raise

    logger.error(f"Claude API call failed after {MAX_RETRIES} attempts: {last_error}")
    raise last_error


def run_turn(conversation_history: list) -> dict:
    """
    conversation_history: list of {"role": "user"|"assistant", "content": str}
    — carrier turns are "user", agent follow-up questions are "assistant".
    Must contain at least one message.

    Returns either:
      {"type": "question", "text": "..."}                    -> keep gathering
      {"type": "complete", "data": {...extracted fields...}}  -> done
      {"type": "error"}                                       -> all retries exhausted;
                                                                   caller should end
                                                                   the call gracefully
    """
    client = get_client()

    try:
        response = _call_claude_with_retry(
            client,
            model=MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            tools=[SUBMIT_TOOL],
            messages=conversation_history,
        )
    except Exception as e:
        logger.error(f"run_turn: giving up after retries: {e}")
        return {"type": "error"}

    for block in response.content:
        if block.type == "tool_use" and block.name == "submit_load_status":
            logger.info(f"Extraction complete: {block.input}")
            return {"type": "complete", "data": block.input}

    text_parts = [b.text for b in response.content if b.type == "text"]
    question = " ".join(text_parts).strip() or "Sorry, could you say that again?"
    return {"type": "question", "text": question}