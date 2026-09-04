"""
Audit Logger — centralized audit trail writer.

Every decision, action, and state transition in the recovery pipeline
is logged here with:
- event_type: What happened (from AuditEventType enum)
- actor: Who did it (SYSTEM, AI, RAZORPAY, POLICY_ENGINE)
- details: Machine-readable JSON payload
- reasoning: Human-readable one-liner for the dashboard timeline

This module is the backbone of Judging Criterion ④ (Audit Trail).
"""

import json
import sqlite3
from datetime import datetime

from database.db import generate_id
from models.enums import AuditEventType, Actor


def log_event(
    conn: sqlite3.Connection,
    case_id: str,
    event_type: AuditEventType,
    actor: Actor,
    details: dict,
    reasoning: str,
) -> dict:
    """
    Write a single audit log entry.

    Args:
        conn: Active SQLite connection (caller manages commit/rollback).
        case_id: Recovery case ID (e.g. 'RC-abc12345').
        event_type: What kind of event this is.
        actor: Who performed the action.
        details: Structured JSON-serializable payload.
        reasoning: Human-readable explanation for the dashboard.

    Returns:
        The audit entry dict that was inserted.
    """
    entry = {
        "id": generate_id("AUD-"),
        "case_id": case_id,
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": event_type.value,
        "actor": actor.value,
        "details": json.dumps(details) if isinstance(details, dict) else details,
        "reasoning": reasoning,
    }

    conn.execute(
        """INSERT INTO audit_log
           (id, case_id, timestamp, event_type, actor, details, reasoning)
           VALUES (:id, :case_id, :timestamp, :event_type, :actor,
                   :details, :reasoning)""",
        entry,
    )

    return entry


def log_state_change(
    conn: sqlite3.Connection,
    case_id: str,
    from_status: str,
    to_status: str,
    reason: str = "",
):
    """Convenience wrapper for state transition audit events."""
    log_event(
        conn=conn,
        case_id=case_id,
        event_type=AuditEventType.STATE_CHANGED,
        actor=Actor.SYSTEM,
        details={
            "from_status": from_status,
            "to_status": to_status,
            "reason": reason,
        },
        reasoning=f"State changed: {from_status} → {to_status}" + (f" ({reason})" if reason else ""),
    )
