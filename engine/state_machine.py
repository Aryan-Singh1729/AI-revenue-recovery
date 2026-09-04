"""
State Machine — enforces valid state transitions for recovery cases.

Every transition is validated against the allowed transition graph.
Invalid transitions raise errors to prevent corrupt state.
All transitions are logged to the audit trail.
"""

import sqlite3

from database.db import update_case
from models.enums import RecoveryStatus
from engine.audit import log_state_change


# ─── Valid Transition Graph ───────────────────────────────────────────────────
# Maps current_status → set of valid next statuses.

VALID_TRANSITIONS: dict[RecoveryStatus, set[RecoveryStatus]] = {
    RecoveryStatus.DETECTED: {
        RecoveryStatus.DIAGNOSING,
    },
    RecoveryStatus.DIAGNOSING: {
        RecoveryStatus.INTERVENTION_SELECTED,
        RecoveryStatus.ESCALATED,   # Unknown root cause → escalate
    },
    RecoveryStatus.INTERVENTION_SELECTED: {
        RecoveryStatus.POLICY_CHECK,
        RecoveryStatus.ESCALATED,   # Immediate escalation (fraud, account_closed)
        RecoveryStatus.STOPPED,     # Immediate stop (disputed)
    },
    RecoveryStatus.POLICY_CHECK: {
        RecoveryStatus.EXECUTING,   # Policy allowed
        RecoveryStatus.ESCALATED,   # Policy escalated
        RecoveryStatus.STOPPED,     # Policy stopped
    },
    RecoveryStatus.EXECUTING: {
        RecoveryStatus.AWAITING_OUTCOME,
    },
    RecoveryStatus.AWAITING_OUTCOME: {
        RecoveryStatus.RECOVERED,          # Success!
        RecoveryStatus.INTERVENTION_SELECTED,  # Failed → try next
        RecoveryStatus.ESCALATED,          # All interventions exhausted
        RecoveryStatus.STOPPED,            # Stopping rule triggered on retry
    },
    # Terminal states — no further transitions
    RecoveryStatus.RECOVERED: set(),
    RecoveryStatus.ESCALATED: set(),
    RecoveryStatus.STOPPED: set(),
}


def transition(
    conn: sqlite3.Connection,
    case_id: str,
    current_status: str,
    new_status: str,
    reason: str = "",
) -> str:
    """
    Transition a case to a new status, with validation and audit logging.

    Args:
        conn: Active SQLite connection.
        case_id: Recovery case ID.
        current_status: Current status string (RecoveryStatus value).
        new_status: Target status string (RecoveryStatus value).
        reason: Human-readable reason for the transition.

    Returns:
        The new status string.

    Raises:
        ValueError: If the transition is invalid.
    """
    # Parse enum values
    try:
        current = RecoveryStatus(current_status)
    except ValueError:
        raise ValueError(f"Invalid current status: '{current_status}'")

    try:
        target = RecoveryStatus(new_status)
    except ValueError:
        raise ValueError(f"Invalid target status: '{new_status}'")

    # Validate transition
    valid_targets = VALID_TRANSITIONS.get(current, set())
    if target not in valid_targets:
        raise ValueError(
            f"Invalid transition: {current.value} → {target.value}. "
            f"Valid targets: {[t.value for t in valid_targets]}"
        )

    # Update case in DB
    updates = {"status": target.value}
    # If transitioning to a terminal state, record resolved_at
    if target in (RecoveryStatus.RECOVERED, RecoveryStatus.ESCALATED, RecoveryStatus.STOPPED):
        from datetime import datetime
        updates["resolved_at"] = datetime.utcnow().isoformat()

    update_case(conn, case_id, updates)

    # Audit log
    log_state_change(conn, case_id, current.value, target.value, reason)

    return target.value


def get_valid_transitions(current_status: str) -> list[str]:
    """Get list of valid next statuses from the current status."""
    try:
        current = RecoveryStatus(current_status)
    except ValueError:
        return []
    return [t.value for t in VALID_TRANSITIONS.get(current, set())]


def is_terminal(status: str) -> bool:
    """Check if a status is terminal (no further transitions possible)."""
    try:
        s = RecoveryStatus(status)
    except ValueError:
        return False
    return len(VALID_TRANSITIONS.get(s, set())) == 0
