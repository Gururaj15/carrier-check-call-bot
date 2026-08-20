"""
Database models for the Carrier Check-Call Voice Bot.

Two core tables:
- calls: one row per phone call, tracks lifecycle + full transcript
- load_status_records: one row per structured extraction written to the mock TMS
"""
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, Column, String, DateTime, Text, JSON, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://ccbot:ccbot_pw@localhost:5432/checkcallbot")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def gen_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Call(Base):
    """One row per inbound Twilio call."""
    __tablename__ = "calls"

    id = Column(String, primary_key=True, default=gen_uuid)
    twilio_call_sid = Column(String, unique=True, index=True, nullable=False)
    from_number = Column(String, nullable=True)
    to_number = Column(String, nullable=True)
    status = Column(String, default="in_progress")  # in_progress, completed, failed
    full_transcript = Column(Text, default="")       # concatenated turn-by-turn transcript
    intent_trace = Column(JSON, default=list)        # list of {role, text, timestamp} entries
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    load_status = relationship("LoadStatusRecord", back_populates="call", uselist=False)


class LoadStatusRecord(Base):
    """Structured extraction result, written to mock TMS + persisted here for audit."""
    __tablename__ = "load_status_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    call_id = Column(String, ForeignKey("calls.id"), nullable=False)

    load_id = Column(String, nullable=True)
    driver_name = Column(String, nullable=True)
    current_location = Column(String, nullable=True)
    eta = Column(String, nullable=True)
    load_status_value = Column(String, nullable=True)  # e.g. "in_transit", "delivered", "delayed"
    exception_reason = Column(String, nullable=True)   # e.g. "traffic", "mechanical", "none"
    raw_extraction = Column(JSON, default=dict)         # full JSON payload as extracted by Claude
    tms_write_status = Column(String, default="pending")  # pending, success, failed
    tms_response = Column(JSON, default=dict)

    created_at = Column(DateTime, default=utcnow)

    call = relationship("Call", back_populates="load_status")


def init_db():
    """Create all tables. Safe to call multiple times."""
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
