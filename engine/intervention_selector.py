"""
Intervention Selector — maps root cause to an ordered intervention sequence.

For each root cause, defines a prioritized list of recovery actions.
Tracks which interventions have been tried per case and advances to the next.
Some root causes (fraud, dispute, account_closed) skip straight to escalation/stop.
"""

from models.enums import RootCause, ActionType


# ─── Root Cause → Ordered Intervention Sequence ───────────────────────────────
# Each entry is an ordered list of ActionType values to try in sequence.
# An empty list means "escalate/stop immediately — no automated intervention."

INTERVENTION_MATRIX: dict[RootCause, list[ActionType]] = {
    RootCause.INSUFFICIENT_FUNDS: [
        ActionType.SMART_RETRY,       # Try after payday (3-day delay)
        ActionType.PAYMENT_LINK,      # Send payment link (7-day expiry)
        ActionType.DUNNING_MESSAGE,   # Gentle nudge with link
    ],
    RootCause.EXPIRED_CARD: [
        ActionType.PAYMENT_LINK,      # Customer needs to enter new card
        ActionType.DUNNING_MESSAGE,   # Remind to update card
    ],
    RootCause.BANK_DECLINE: [
        ActionType.SMART_RETRY,       # Retry next day
        ActionType.SMART_RETRY,       # Retry at different time
        ActionType.PAYMENT_LINK,      # Fallback: payment link
    ],
    RootCause.AUTH_REQUIRED: [
        ActionType.PAYMENT_LINK,      # Customer must complete 3DS/OTP
        ActionType.DUNNING_MESSAGE,   # Remind to authenticate
    ],
    RootCause.NETWORK_ERROR: [
        ActionType.SMART_RETRY,       # Immediate retry (transient error)
        ActionType.SMART_RETRY,       # Retry after short delay
    ],
    RootCause.ACCOUNT_CLOSED: [],     # → Escalate immediately
    RootCause.INTERNATIONAL_RESTRICTION: [
        ActionType.DUNNING_MESSAGE,   # Suggest using a domestic card
    ],
    RootCause.FRAUD_FLAG: [],         # → Escalate immediately
    RootCause.DISPUTED: [],           # → Stop immediately
    RootCause.UNKNOWN: [],            # → Escalate (conservative)
}


def select_intervention(
    root_cause: RootCause,
    interventions_tried: list[str],
) -> ActionType | None:
    """
    Select the next intervention for a case based on root cause and history.

    Args:
        root_cause: Diagnosed root cause of the failure.
        interventions_tried: List of ActionType values already attempted.

    Returns:
        The next ActionType to try, or None if all interventions exhausted.
    """
    sequence = INTERVENTION_MATRIX.get(root_cause, [])

    if not sequence:
        return None  # No automated interventions — escalate or stop

    # Find how many interventions from the sequence have been tried
    tried_count = len(interventions_tried)

    if tried_count >= len(sequence):
        return None  # All interventions exhausted

    return sequence[tried_count]


def get_intervention_sequence(root_cause: RootCause) -> list[ActionType]:
    """Get the full intervention sequence for a root cause."""
    return INTERVENTION_MATRIX.get(root_cause, [])


def should_escalate_immediately(root_cause: RootCause) -> bool:
    """Check if this root cause should be escalated without trying interventions."""
    return root_cause in (
        RootCause.ACCOUNT_CLOSED,
        RootCause.FRAUD_FLAG,
        RootCause.UNKNOWN,
    )


def should_stop_immediately(root_cause: RootCause) -> bool:
    """Check if this root cause should be stopped without trying interventions."""
    return root_cause == RootCause.DISPUTED
