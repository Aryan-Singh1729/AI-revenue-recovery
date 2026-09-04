"""
Detector — scans the database for unprocessed failed payments.

Responsibilities:
1. Query all recovery_cases with status = 'detected'
2. Enrich each case with subscription + customer context
3. Verify revenue-at-risk calculations (amount × remaining_cycles)
4. Log CASE_CREATED audit event for each case
5. Return the list of cases ready for diagnosis
"""

import sqlite3

from database.db import (
    get_connection,
    get_cases_by_status,
    get_case_with_details,
    update_case,
)
from models.enums import RecoveryStatus, AuditEventType, Actor
from engine.audit import log_event, log_state_change


def detect_new_cases(conn: sqlite3.Connection | None = None) -> list[dict]:
    """
    Scan DB for all cases in 'detected' status, verify revenue-at-risk,
    log CASE_CREATED audit events, and transition them to 'diagnosing'.

    Args:
        conn: Optional SQLite connection. Creates one if not provided.

    Returns:
        List of enriched case dicts (with customer + subscription info)
        that are now in 'diagnosing' status and ready for root cause analysis.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    # 1. Fetch all cases still in 'detected' status
    detected_cases = get_cases_by_status(conn, RecoveryStatus.DETECTED.value)

    if not detected_cases:
        print("ℹ️  No new cases to detect.")
        return []

    print(f"🔍 Detected {len(detected_cases)} new cases to process")

    enriched_cases = []

    for case in detected_cases:
        case_id = case["id"]

        # 2. Enrich with subscription + customer details
        enriched = get_case_with_details(conn, case_id)
        if not enriched:
            print(f"  ⚠️  Could not enrich case {case_id} — skipping")
            continue

        # 3. Verify revenue-at-risk calculation
        subscription_amount = enriched["subscription_amount"]  # paise
        remaining_cycles = enriched["remaining_cycles"]
        expected_total_risk = subscription_amount * remaining_cycles

        # Update if the stored values don't match (defensive)
        updates = {}
        if enriched["amount_at_risk"] != subscription_amount:
            updates["amount_at_risk"] = subscription_amount
        if enriched["total_risk"] != expected_total_risk:
            updates["total_risk"] = expected_total_risk

        # 4. Log CASE_CREATED audit event
        log_event(
            conn=conn,
            case_id=case_id,
            event_type=AuditEventType.CASE_CREATED,
            actor=Actor.SYSTEM,
            details={
                "subscription_id": enriched["subscription_id"],
                "customer_name": enriched["customer_name"],
                "plan_name": enriched["plan_name"],
                "amount_at_risk_paise": subscription_amount,
                "amount_at_risk_rupees": subscription_amount / 100,
                "total_risk_paise": expected_total_risk,
                "total_risk_rupees": expected_total_risk / 100,
                "remaining_cycles": remaining_cycles,
                "billing_cycle": enriched["billing_cycle"],
                "failure_error_code": enriched["failure_error_code"],
                "failure_error_reason": enriched["failure_error_reason"],
                "failure_error_source": enriched["failure_error_source"],
                "failure_error_step": enriched.get("failure_error_step", ""),
                "razorpay_payment_id": enriched["razorpay_payment_id"],
                "customer_opt_out": bool(enriched.get("customer_opt_out", 0)),
            },
            reasoning=(
                f"Failed payment of Rs {subscription_amount / 100:,.0f} detected for "
                f"{enriched['customer_name']} ({enriched['plan_name']} plan). "
                f"Total revenue at risk: Rs {expected_total_risk / 100:,.0f} "
                f"across {remaining_cycles} remaining billing cycles."
            ),
        )

        # 5. Transition status: detected → diagnosing
        updates["status"] = RecoveryStatus.DIAGNOSING.value
        update_case(conn, case_id, updates)

        log_state_change(
            conn=conn,
            case_id=case_id,
            from_status=RecoveryStatus.DETECTED.value,
            to_status=RecoveryStatus.DIAGNOSING.value,
            reason="Case picked up by detector for root cause analysis",
        )

        # Update the enriched dict with new values
        enriched["status"] = RecoveryStatus.DIAGNOSING.value
        enriched["amount_at_risk"] = subscription_amount
        enriched["total_risk"] = expected_total_risk
        enriched_cases.append(enriched)

    conn.commit()

    # Summary
    total_immediate_risk = sum(c["amount_at_risk"] for c in enriched_cases)
    total_lifetime_risk = sum(c["total_risk"] for c in enriched_cases)
    print(f"  💰 Immediate revenue at risk: Rs {total_immediate_risk / 100:,.0f}")
    print(f"  💰 Lifetime revenue at risk:  Rs {total_lifetime_risk / 100:,.0f}")
    print(f"  ✅ {len(enriched_cases)} cases transitioned to 'diagnosing'")

    if own_conn:
        conn.close()

    return enriched_cases
