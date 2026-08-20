"""
Mock TMS (McLeod/Turvo-style) write endpoint.

Accepts the structured JSON extracted by the Claude dialog agent, persists
it to load_status_records, and returns a fake confirmation ID — simulating
the shape of a real McLeod/Turvo API write response.
"""
import uuid
import logging
from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.db import get_db, LoadStatusRecord, Call

logger = logging.getLogger("ccbot.tms")
router = APIRouter()


class LoadStatusPayload(BaseModel):
    call_id: str
    current_location: str
    eta: str
    status: str
    exception_reason: str
    load_id: Optional[str] = None
    driver_name: Optional[str] = None


@router.get("/ping")
def ping():
    return {"status": "tms mock alive"}


def write_load_status(db: Session, call_id: str, data: dict) -> dict:
    """
    Reusable write function — called both by the /tms/mcleod/status HTTP
    route and directly by the voice pipeline when a call's extraction
    completes, simulating the "write to McLeod/Turvo" integration step.
    """
    record = LoadStatusRecord(
        call_id=call_id,
        load_id=data.get("load_id"),
        driver_name=data.get("driver_name"),
        current_location=data.get("current_location"),
        eta=data.get("eta"),
        load_status_value=data.get("status"),
        exception_reason=data.get("exception_reason"),
        raw_extraction=data,
        tms_write_status="success",
        tms_response={
            "confirmation_id": f"MCLEOD-{uuid.uuid4().hex[:10].upper()}",
            "status": "accepted",
        },
    )
    db.add(record)

    call_row = db.query(Call).filter(Call.id == call_id).first()
    if call_row:
        call_row.status = "completed"

    db.commit()
    db.refresh(record)
    logger.info(f"Wrote load status record {record.id} for call {call_id}: {data}")
    return {
        "record_id": record.id,
        "confirmation_id": record.tms_response["confirmation_id"],
    }


@router.post("/mcleod/status")
def mcleod_status_write(payload: LoadStatusPayload, db: Session = Depends(get_db)):
    result = write_load_status(db, payload.call_id, payload.dict())
    return {"status": "accepted", **result}