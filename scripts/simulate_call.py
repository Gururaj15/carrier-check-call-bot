"""
Simulated carrier check-call — drives your running FastAPI server with the
exact same POST requests Twilio sends (CallSid, From, To, SpeechResult),
so you can test/demo the full pipeline (dialog agent -> extraction ->
TMS write) without needing a real phone call.

This exists because Twilio's Console "Try it out / Custom" test tool turned
out to only preview the FIRST TwiML response — it never follows <Gather>/
<Record>/<Stream> action URLs to continue a multi-turn call, so it can't be
used to test this app's real conversation flow. This script IS the real
integration test, using the exact request shape Twilio's docs specify.

Usage:
    python scripts/simulate_call.py
    (make sure uvicorn is running on localhost:8000 first)
"""
import re
import uuid
import requests

BASE_URL = "http://localhost:8000"


def extract_gather_prompt(twiml: str) -> str:
    """
    Extract the actual spoken prompt: the <Say> INSIDE <Gather>, if present
    (that's the only one Twilio actually speaks when Gather succeeds — the
    <Say> after </Gather> is a fallback only spoken if Gather times out
    with no input at all, so it should NOT be shown as part of the normal
    flow).
    """
    gather_match = re.search(r"<Gather.*?>(.*?)</Gather>", twiml, re.DOTALL)
    if gather_match:
        say_match = re.search(r"<Say[^>]*>(.*?)</Say>", gather_match.group(1), re.DOTALL)
        return say_match.group(1).strip() if say_match else ""
    # No <Gather> in this response — call is ending, show whatever <Say> is there
    matches = re.findall(r"<Say[^>]*>(.*?)</Say>", twiml, re.DOTALL)
    return " ".join(m.strip() for m in matches)


def call_ended(twiml: str) -> bool:
    return "<Gather" not in twiml


def main():
    call_sid = f"SIMULATED_CALL_{uuid.uuid4().hex[:12]}"
    from_number = "+15555550123"
    to_number = "+17372583478"

    print(f"=== Simulated call: {call_sid} ===\n")

    resp = requests.post(
        f"{BASE_URL}/incoming-call",
        data={"CallSid": call_sid, "From": from_number, "To": to_number},
    )
    resp.raise_for_status()
    twiml = resp.text
    print(f"BOT: {extract_gather_prompt(twiml)}\n")

    while not call_ended(twiml):
        speech = input("YOU (type what the carrier says): ").strip()
        if not speech:
            print("(empty input — simulating no speech detected)")

        resp = requests.post(
            f"{BASE_URL}/gather-response",
            data={"CallSid": call_sid, "SpeechResult": speech},
        )
        resp.raise_for_status()
        twiml = resp.text
        print(f"\nBOT: {extract_gather_prompt(twiml)}\n")

    print("=== Call ended ===")
    print(f"Check the DB: docker exec -it ccbot_postgres psql -U ccbot -d checkcallbot "
          f"-c \"SELECT * FROM load_status_records WHERE call_id = "
          f"(SELECT id FROM calls WHERE twilio_call_sid = '{call_sid}');\"")


if __name__ == "__main__":
    main()