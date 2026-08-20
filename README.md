# Carrier Check-Call Voice Bot

A production-shaped voice agent that extracts load status from carrier
check-calls and writes structured output to a mock TMS (McLeod/Turvo-style).

Built as a technical deep-dive for a Forward Deployed Engineer application at Vooma.

---

## Status

- [x] **Step 1 — Infra:** FastAPI app, Postgres schema (`calls`, `load_status_records`), webhook logging
- [x] **Step 2 — Speech input:** carrier speech arrives as text (see "Important note" below for why/how)
- [x] **Step 3 — Claude dialog agent:** multi-turn extraction via tool use (location, ETA, status, exceptions)
- [x] **Step 4 — TMS write integration:** structured JSON written to mock McLeod/Turvo endpoint + Postgres
- [ ] **Step 5 — Hardening:** retries, error handling, structured logging
- [ ] **Step 6 — Demo polish:** one-page dashboard, GitHub push, write-up

**The core hard part of this project — real-time AI extraction from a live
conversation, written to a TMS — is built and working.** What's not fully
live yet is explained honestly below.

---

## Important note: why this runs via typed input, not a live phone call

This project was built with a **$0 budget**, using a Twilio trial account.
Here's exactly what happened, in order, because it's a real engineering
tradeoff worth documenting rather than hiding:

1. **First attempt:** `<Record>` (Twilio records the call, then POSTs you
   the audio file). Twilio's trial accounts **block the recording-completed
   webhook for incoming calls** — this only works on upgraded (paid) accounts.
2. **Second attempt:** `<Stream>` (Twilio Media Streams — live audio over a
   WebSocket, meant to feed local Whisper in real time). Confirmed via
   Twilio's own docs: `<Stream>` is **also blocked on trial accounts**,
   silently stripped from the TwiML and replaced with an error message.
3. **Third attempt:** `<Gather input="speech">` (Twilio's own built-in
   speech recognition) — this one **is allowed on trial**. But testing it
   surfaced a separate wall: without an owned phone number (buying one
   requires the $20 minimum Twilio upgrade), the only way to receive test
   calls is Twilio Console's "Try it out / Custom" tool using their shared
   demo number. That tool turned out to only preview the *first* TwiML
   response — it never follows up on `<Gather>`/`<Record>`/`<Stream>`
   action URLs to continue a real multi-turn call. Same symptom, three
   different approaches: first webhook always fires, second one never does.

**The fix:** `scripts/simulate_call.py` sends your FastAPI server the exact
same POST request shape Twilio sends (`CallSid`, `From`, `To`,
`SpeechResult`) — so instead of speaking into a phone, you type what the
carrier would say. Everything downstream is 100% real and live: the real
Claude API call, real multi-turn reasoning, a real Postgres write, a real
mock TMS confirmation. The *only* simulated piece is the transport layer
(typed text standing in for "phone call → Twilio speech recognition →
text"). Your backend code has no idea which one it was — it just reads a
`SpeechResult` field either way.

**If you have an upgraded Twilio account + an owned phone number, live
calling works with zero code changes** — the `/incoming-call` and
`/gather-response` webhook routes are the same ones a real Twilio phone
number would hit. See "Optional: live phone call setup" near the bottom.

---

## Stack

Python · FastAPI · Twilio Voice (webhook contract) · Claude (Anthropic API, tool use) · Postgres · local Whisper (built, used for offline transcription — see file guide)

---

## File guide — what everything does

```
carrier-check-call-bot/
├── app/
│   ├── main.py                  # FastAPI entrypoint — wires up all the routes below
│   ├── models/db.py              # Postgres schema: Call + LoadStatusRecord tables
│   ├── routes/
│   │   ├── voice.py               # THE CORE FILE. Handles /incoming-call (greets
│   │   │                          # the carrier) and /gather-response (runs each
│   │   │                          # conversation turn through the dialog agent).
│   │   │                          # This is what Twilio calls OR what the simulator calls.
│   │   ├── tms.py                  # Mock TMS write — takes extracted fields, saves
│   │   │                          # them to Postgres, returns a fake McLeod
│   │   │                          # confirmation ID. Also a real POST /tms/mcleod/status
│   │   │                          # route if you want to write to it directly.
│   │   ├── dashboard.py            # GET /calls — JSON list of logged calls (Step 6
│   │   │                          # will turn this into a real HTML dashboard)
│   │   └── media_stream.py         # WebSocket handler for Twilio Media Streams.
│   │                              # Built and working (tested directly, bypassing
│   │                              # Twilio) but NOT reachable live on a trial
│   │                              # account — see note above. Kept in the repo to
│   │                              # show the production-path implementation.
│   └── services/
│       ├── dialog_agent.py         # THE AI CORE. Calls Claude with a tool
│       │                          # definition; Claude either asks a follow-up
│       │                          # question or calls submit_load_status when
│       │                          # it has everything it needs.
│       ├── stt.py                  # Local Whisper wrapper — loads the model once,
│       │                          # transcribes audio. Used by media_stream.py
│       │                          # (production path) — not exercised in the
│       │                          # current typed-input demo, since there's no
│       │                          # audio in that flow.
│       └── audio_utils.py          # Decodes Twilio's mu-law audio + silence-based
│                                  # turn segmentation. Supports media_stream.py.
├── scripts/
│   └── simulate_call.py            # RUN THIS TO DEMO THE PROJECT. Drives the real
│                                  # backend with Twilio-shaped requests via typed
│                                  # input instead of a phone call.
├── docker-compose.yml               # Local Postgres
├── requirements.txt
├── .env.example
└── README.md
```

**tl;dr — the two files that matter most:** `app/routes/voice.py` (the
conversation logic) and `app/services/dialog_agent.py` (the Claude
extraction). Everything else is infrastructure supporting those two.

---

## Setup

### 1. Prerequisites
- Python 3.11+ (3.13 is fine too — `audioop-lts` in requirements.txt covers the
  standard-library `audioop` module removed in 3.13)
- Docker Desktop (for Postgres)
- ffmpeg — only needed if you use the Whisper/media-stream path.
  - Windows: https://www.gyan.dev/ffmpeg/builds/ ("essentials" build), unzip,
    add the `bin` folder to PATH, verify with `ffmpeg -version`
  - Mac: `brew install ffmpeg` · Linux: `sudo apt install ffmpeg`
- An Anthropic API key (required — this is what powers the dialog agent)
- ngrok + Twilio account (only required for the optional live-call path)

### 2. Install
```bash
cd carrier-check-call-bot
python -m venv venv
venv\Scripts\activate        # Windows cmd
# source venv/bin/activate   # Mac/Linux
pip install -r requirements.txt
```

### 3. Configure environment
```bash
copy .env.example .env       # Windows cmd
# cp .env.example .env       # Mac/Linux
```
Open `.env` and set at minimum:
```
ANTHROPIC_API_KEY=sk-ant-...your real key...
DATABASE_URL=postgresql://ccbot:ccbot_pw@localhost:5432/checkcallbot
```
(`PUBLIC_BASE_URL`, Twilio SID/token are only needed for the optional live-call path below.)

### 4. Start Postgres
```bash
docker compose up -d
```

### 5. Run the app
```bash
uvicorn app.main:app --reload --port 8000
```
Visit http://localhost:8000/health — should return `{"status": "ok"}`

---

## Run the demo (recommended — this is the main way to test/show this project)

```bash
python scripts/simulate_call.py
```

Type a check-call update when prompted, e.g.:
```
I'm about 50 miles from Dallas, ETA is 3pm, no delays
```

Watch it greet you, extract the fields, and confirm. Then verify the write:
```bash
docker exec -it ccbot_postgres psql -U ccbot -d checkcallbot -c "SELECT * FROM load_status_records ORDER BY created_at DESC LIMIT 1;"
```

Try a version with a delay/exception, or leave out the ETA and see the agent
ask a natural follow-up question — that's the multi-turn reasoning in action.

---

## Optional: live phone call setup (requires an upgraded Twilio account)

If you upgrade Twilio (removes the trial restrictions, ~$20 minimum funding)
and buy a real number, live calling works with **zero code changes** —
`voice.py`'s routes are the same webhook contract Twilio expects:

1. In Twilio Console → Phone Numbers → Manage → Active Numbers → your number
   → Voice Configuration → "A call comes in" → Webhook (POST) →
   `https://your-ngrok-url.ngrok-free.app/incoming-call`
2. Start ngrok: `ngrok http 8000`, copy the forwarding URL into `.env` as
   `PUBLIC_BASE_URL` (only used by the media-stream path, not required for
   the Gather-based flow to function, but good practice to keep accurate).
3. Call your number. The `/incoming-call` → `/gather-response` loop runs
   exactly like the simulator does, except Twilio's real speech recognition
   fills `SpeechResult` instead of your typed input.
4. (Optional, also requires upgrade) Swap `<Gather>` for `<Start><Stream>`
   in `voice.py` to use the already-built local Whisper pipeline
   (`media_stream.py` + `stt.py` + `audio_utils.py`) instead of Twilio's ASR.

---

## Next steps (in progress)

- **Step 5 — Hardening:** retry logic around the Claude call, structured
  error logging, graceful handling of malformed/partial speech input.
- **Step 6 — Demo polish:** turn `GET /calls` into a real one-page HTML
  dashboard showing call log, transcript, extracted fields, and TMS
  confirmation per call. Record a short demo video of `simulate_call.py`
  in action. Push to GitHub with commit history. Write up the build as a
  Medium post.