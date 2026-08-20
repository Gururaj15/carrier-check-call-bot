# Carrier Check-Call Voice Bot

![Demo](./demo.gif)

A production-shaped voice agent that extracts load status from carrier
check-calls and writes structured output to a mock TMS (McLeod/Turvo-style).

A technical deep-dive into building a production-shaped voice AI pipeline for logistics — from live call handling through structured TMS writes.

---

## Status: complete (Steps 1–6)

- [x] **Infra:** FastAPI app, Postgres schema (`calls`, `load_status_records`), webhook logging
- [x] **Speech input:** carrier speech arrives as text (see "Important note" below for why/how)
- [x] **Claude dialog agent:** multi-turn extraction via tool use (location, ETA, status, exceptions)
- [x] **TMS write integration:** structured JSON written to a mock McLeod/Turvo endpoint + Postgres
- [x] **Hardening:** retry + backoff on transient Claude API errors, runaway-conversation guard,
      TMS-write-failure isolation, global exception safety net
- [x] **Demo polish:** live dispatch dashboard, GitHub repo, demo GIF, this README

**The core hard part of this project — real-time AI extraction from a live
conversation, written to a TMS — is fully built, hardened, and demoable end
to end.** What isn't a literal phone call is explained honestly below.

---

## Demo

The GIF above shows: running `scripts/simulate_call.py`, typing a check-call
update, and watching it land live on the dashboard — transcript, extracted
fields, and mock TMS confirmation, all in real time.

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

**The fix:** `scripts/simulate_call.py` sends the running FastAPI server the
exact same POST request shape Twilio sends (`CallSid`, `From`, `To`,
`SpeechResult`) — so instead of speaking into a phone, you type what the
carrier would say. Everything downstream is 100% real and live: the real
Claude API call, real multi-turn reasoning, a real Postgres write, a real
mock TMS confirmation, reflected live on the real dashboard. The *only*
simulated piece is the transport layer (typed text standing in for "phone
call → Twilio speech recognition → text"). The backend code has no idea
which one it was — it just reads a `SpeechResult` field either way.

**If you have an upgraded Twilio account + an owned phone number, live
calling works with zero code changes** — see "Optional: live phone call
setup" near the bottom.

---

## A quick note on "Completed" vs. load status

On the dashboard, a call marked **Completed** means *the agent finished the
conversation and successfully wrote the extraction to the TMS* — it says
nothing about whether the shipment itself is on time. A completed call can
absolutely show `status: delayed` with an `exception_reason` like "stuck in
traffic" — both are true at once, and both are shown as separate fields.
The only call statuses that mean something went wrong with the *conversation
itself* are `failed_agent_error` (Claude API exhausted its retries) and
`incomplete_max_turns` (the conversation looped too long without ever
pinning down all four fields).

---

## Stack

Python · FastAPI · Twilio Voice (webhook contract) · Claude (Anthropic API, tool use) · Postgres · local Whisper (built, used for offline transcription — see file guide)

---

## File guide — what everything does

```
carrier-check-call-bot/
├── app/
│   ├── main.py                  # FastAPI entrypoint — wires up all routes,
│   │                            # plus a global exception handler that keeps
│   │                            # voice webhooks returning valid TwiML even
│   │                            # on unexpected errors.
│   ├── models/db.py              # Postgres schema: Call + LoadStatusRecord tables
│   ├── templates/
│   │   └── dashboard.html         # The live dispatch console UI (HTML/CSS/JS,
│   │                            # polls /calls every 4s, no build step needed)
│   ├── routes/
│   │   ├── voice.py               # THE CORE FILE. Handles /incoming-call (greets
│   │   │                          # the carrier) and /gather-response (runs each
│   │   │                          # conversation turn through the dialog agent).
│   │   │                          # This is what Twilio calls OR what the
│   │   │                          # simulator calls. Includes Step 5 hardening:
│   │   │                          # max-turn guard, agent-error handling,
│   │   │                          # TMS-write-failure isolation.
│   │   ├── tms.py                  # Mock TMS write — takes extracted fields, saves
│   │   │                          # them to Postgres, returns a fake McLeod
│   │   │                          # confirmation ID. POST /tms/mcleod/status
│   │   │                          # also works standalone.
│   │   ├── dashboard.py            # GET /dashboard (HTML page) + GET /calls
│   │   │                          # (enriched JSON, call + load_status joined —
│   │   │                          # also what the dashboard polls)
│   │   └── media_stream.py         # WebSocket handler for Twilio Media Streams.
│   │                              # Built and verified working (tested directly,
│   │                              # bypassing Twilio) but not reachable live on
│   │                              # a trial account — see note above. Kept in
│   │                              # the repo as the production-path implementation.
│   └── services/
│       ├── dialog_agent.py         # THE AI CORE. Calls Claude with a tool
│       │                          # definition and retry/backoff hardening;
│       │                          # Claude either asks a follow-up question or
│       │                          # calls submit_load_status when it has
│       │                          # everything it needs.
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
├── demo.gif                         # Screen recording of the demo in action
├── docker-compose.yml               # Local Postgres (credentials via .env, not hardcoded)
├── requirements.txt
├── .env.example
└── README.md
```

**tl;dr — the three files that matter most:** `app/routes/voice.py` (the
conversation logic), `app/services/dialog_agent.py` (the Claude extraction),
and `app/templates/dashboard.html` (the live view of it all working).

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
POSTGRES_USER=ccbot
POSTGRES_PASSWORD=ccbot_pw
POSTGRES_DB=checkcallbot
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

**Terminal 1** — keep `uvicorn` running.

**Terminal 2** — run a simulated call:
```bash
python scripts/simulate_call.py
```
Type a check-call update when prompted, e.g.:
```
I'm about 50 miles from Dallas, ETA is 3pm, no delays
```

**Browser** — open http://localhost:8000/dashboard and watch the call land
live, with the transcript, extracted fields, and TMS confirmation.

Try a version with a delay/exception too, e.g.:
```
I'm stuck in traffic near Memphis, running 2 hours late, ETA is 6pm
```
— the dashboard flags exceptions in red. Or leave out the ETA entirely and
watch the agent ask a natural follow-up question — that's the multi-turn
reasoning in action.

Verify the raw DB write directly if you want:
```bash
docker exec -it ccbot_postgres psql -U ccbot -d checkcallbot -c "SELECT * FROM load_status_records ORDER BY created_at DESC LIMIT 1;"
```

---

## Optional: live phone call setup (requires an upgraded Twilio account)

If you upgrade Twilio (removes the trial restrictions, ~$20 minimum funding)
and buy a real number, live calling works with **zero code changes** —
`voice.py`'s routes are the same webhook contract Twilio expects:

1. In Twilio Console → Phone Numbers → Manage → Active Numbers → your number
   → Voice Configuration → "A call comes in" → Webhook (POST) →
   `https://your-ngrok-url.ngrok-free.app/incoming-call`
2. Start ngrok: `ngrok http 8000`, copy the forwarding URL into `.env` as
   `PUBLIC_BASE_URL`.
3. Call your number. The `/incoming-call` → `/gather-response` loop runs
   exactly like the simulator does, except Twilio's real speech recognition
   fills `SpeechResult` instead of your typed input.
4. (Optional, also requires upgrade) Swap `<Gather>` for `<Start><Stream>`
   in `voice.py` to use the already-built local Whisper pipeline
   (`media_stream.py` + `stt.py` + `audio_utils.py`) instead of Twilio's ASR.

---

## Possible future work

- Real live-call validation on an upgraded Twilio account
- Auth on the dashboard (currently open — fine for a local demo, not for prod)
- Real McLeod/Turvo API integration in place of the mock
- Outbound check-calls (agent calls the carrier proactively) instead of inbound-only
- Multi-load support per call (currently assumes one load per check-call)
- Structured eval set of synthetic check-call transcripts to regression-test the extraction prompt