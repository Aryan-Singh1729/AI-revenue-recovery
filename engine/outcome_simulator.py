"""
Outcome Simulator — deterministic, seeded outcome generator for recovery actions.

Each case gets a reproducible outcome based on:
1. Root cause + action type → base success probability
2. Attempt number → diminishing returns
3. Case ID hash → deterministic per-case seed

This ensures the demo is reproducible: same cases → same outcomes every time.
"""

import hashlib

from models.enums import RootCause, ActionType


# ─── Base Success Rates ───────────────────────────────────────────────────────
# Maps (root_cause, action_type) → base success probability (0.0 - 1.0)

SUCCESS_RATES: dict[tuple[str, str], float] = {
    # Network errors → retries almost always work
    (RootCause.NETWORK_ERROR.value, ActionType.SMART_RETRY.value): 0.90,

    # Insufficient funds → moderate retry success (payday effect)
    (RootCause.INSUFFICIENT_FUNDS.value, ActionType.SMART_RETRY.value): 0.45,
    (RootCause.INSUFFICIENT_FUNDS.value, ActionType.PAYMENT_LINK.value): 0.40,
    (RootCause.INSUFFICIENT_FUNDS.value, ActionType.DUNNING_MESSAGE.value): 0.25,

    # Expired card → payment link works well (customer enters new card)
    (RootCause.EXPIRED_CARD.value, ActionType.PAYMENT_LINK.value): 0.55,
    (RootCause.EXPIRED_CARD.value, ActionType.DUNNING_MESSAGE.value): 0.30,

    # Auth required → payment link is the solution (customer completes 3DS)
    (RootCause.AUTH_REQUIRED.value, ActionType.PAYMENT_LINK.value): 0.65,
    (RootCause.AUTH_REQUIRED.value, ActionType.DUNNING_MESSAGE.value): 0.25,

    # Bank decline → moderate retry success
    (RootCause.BANK_DECLINE.value, ActionType.SMART_RETRY.value): 0.40,
    (RootCause.BANK_DECLINE.value, ActionType.PAYMENT_LINK.value): 0.35,

    # International restriction → dunning can help (suggest domestic card)
    (RootCause.INTERNATIONAL_RESTRICTION.value, ActionType.DUNNING_MESSAGE.value): 0.35,
}

# Default rate for unmapped combinations
DEFAULT_SUCCESS_RATE = 0.15


def _case_seed(case_id: str, step_index: int) -> float:
    """
    Deterministic pseudo-random value in [0, 1) from case ID + sequence step.

    `step_index` is the position in the case's intervention sequence (0, 1, 2...),
    NOT the per-action-type repeat count. Seeding on the repeat count made every
    action type in a sequence draw step 0, so a case's retry, payment link and
    dunning message all shared one random value — a case that failed its retry
    was mathematically guaranteed to fail the cheaper follow-ups too. Seeding on
    the sequence position makes the attempts independent, as intended.
    """
    seed_str = f"{case_id}:step:{step_index}"
    hash_bytes = hashlib.sha256(seed_str.encode()).hexdigest()
    # Take first 8 hex chars → convert to int → normalize to [0, 1)
    return int(hash_bytes[:8], 16) / 0xFFFFFFFF


def simulate_outcome(
    case_id: str,
    root_cause: str,
    action_type: str,
    attempt_index: int = 0,
    step_index: int | None = None,
) -> dict:
    """
    Simulate the outcome of a recovery action.

    Args:
        case_id: Unique case identifier (used as seed).
        root_cause: RootCause enum value.
        action_type: ActionType enum value.
        attempt_index: How many times THIS action type has already been tried on
            this case (0-based). Drives diminishing returns only.
        step_index: Position in the case's overall intervention sequence (0-based).
            Drives the random seed so each step is an independent draw. Defaults
            to attempt_index for backwards compatibility.

    Returns:
        {
            "success": bool,
            "probability": float,
            "seed_value": float,
            "reasoning": str
        }
    """
    if step_index is None:
        step_index = attempt_index

    # Get base probability
    key = (root_cause, action_type)
    base_rate = SUCCESS_RATES.get(key, DEFAULT_SUCCESS_RATE)

    # Diminishing returns: 2nd attempt of same type → 50% of base rate
    if attempt_index > 0:
        base_rate *= 0.5 ** attempt_index

    # Generate deterministic random value
    rand_value = _case_seed(case_id, step_index)

    # Determine outcome
    success = rand_value < base_rate

    reasoning = (
        f"Outcome simulation: {action_type} for {root_cause} "
        f"(sequence step {step_index + 1}, attempt #{attempt_index + 1} of this type). "
        f"Base rate: {base_rate * 100:.0f}%, "
        f"seed value: {rand_value:.4f} "
        f"→ {'SUCCESS' if success else 'FAILURE'}"
    )

    return {
        "success": success,
        "probability": base_rate,
        "seed_value": rand_value,
        "reasoning": reasoning,
    }
