"""
Dashboard — one-page live view of the call log, transcripts, extracted
fields, and TMS write status.

GET /dashboard      -> the HTML page (Step 6)
GET /calls          -> enriched JSON (calls joined with their load_status
                        record) — used both as a plain API and as the data
                        source the dashboard's JS polls for live updates.
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.db import get_db, Call

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _serialize_call(c: Call) -> dict:
    load_status = None
    if c.load_status:
        ls = c.load_status
        load_status = {
            "current_location": ls.current_location,
            "eta": ls.eta,
            "status": ls.load_status_value,
            "exception_reason": ls.exception_reason,
            "tms_write_status": ls.tms_write_status,
            "confirmation_id": (ls.tms_response or {}).get("confirmation_id"),
        }

    return {
        "id": c.id,
        "twilio_call_sid": c.twilio_call_sid,
        "from_number": c.from_number,
        "to_number": c.to_number,
        "status": c.status,
        "full_transcript": c.full_transcript,
        "intent_trace": c.intent_trace or [],
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "load_status": load_status,
    }


@router.get("/calls")
def list_calls(db: Session = Depends(get_db)):
    """Enriched JSON list of calls, newest first. Also powers the dashboard's live polling."""
    calls = db.query(Call).order_by(Call.created_at.desc()).limit(100).all()
    return [_serialize_call(c) for c in calls]


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    """The live dispatch console — fetches /calls via JS and renders it client-side."""
    return templates.TemplateResponse(request=request, name="dashboard.html", context={})