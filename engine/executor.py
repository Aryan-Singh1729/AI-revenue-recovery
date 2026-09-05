"""
Executor — dispatches recovery actions via Razorpay APIs, AI dunning, or escalation.

Handles four action types:
1. SMART_RETRY: Attempt payment via Razorpay test mode
2. PAYMENT_LINK: Create a real Razorpay Payment Link (clickable URL)
3. DUNNING_MESSAGE: Generate an AI-powered dunning message
4. ESCALATION: Record escalation with full context for merchant

Every execution is logged to the audit trail and recorded in recovery_actions.
"""

import json
import sqlite3
from datetime import datetime, timedelta

import config
from database.db import generate_id, insert_action, update_case
from models.enums import ActionType, AuditEventType, Actor
from engine.audit import log_event
from ai import llm

# Try importing razorpay client — may not be configured
try:
    import razorpay_client
    _razorpay_available = razorpay_client.is_configured()
except Exception:
    _razorpay_available = False


def execute_action(
    conn: sqlite3.Connection,
    case: dict,
    action_type: ActionType,
    ai_explanation: str | None = None,
) -> dict:
    """
    Execute a recovery action for a case.

    Args:
        conn: Active SQLite connection.
        case: Enriched case dict with customer/subscription details.
        action_type: The action to execute.
        ai_explanation: Optional AI-generated explanation of why this
            intervention was chosen (from ai.llm.explain_intervention()),
            attached to the audit entry for SMART_RETRY/PAYMENT_LINK. Ignored
            by DUNNING_MESSAGE (which generates its own AI content — the
            message itself) and ESCALATION (deterministic reasoning only).

    Returns:
        {
            "action_id": str,
            "action_type": str,
            "success": bool,
            "details": dict,
        }
    """
    if action_type == ActionType.SMART_RETRY:
        return _execute_smart_retry(conn, case, ai_explanation)
    elif action_type == ActionType.PAYMENT_LINK:
        return _execute_payment_link(conn, case, ai_explanation)
    elif action_type == ActionType.DUNNING_MESSAGE:
        return _execute_dunning(conn, case)
    elif action_type == ActionType.ESCALATION:
        return _execute_escalation(conn, case, reason="All interventions exhausted")
    else:
        raise ValueError(f"Unknown action type: {action_type}")


def execute_escalation(
    conn: sqlite3.Connection,
    case: dict,
    reason: str,
) -> dict:
    """Public escalation entry point with custom reason."""
    return _execute_escalation(conn, case, reason)


# ─── Smart Retry ──────────────────────────────────────────────────────────────

def _execute_smart_retry(conn: sqlite3.Connection, case: dict, ai_explanation: str | None = None) -> dict:
    """Attempt a payment retry via Razorpay test mode."""
    case_id = case["id"]
    amount_paise = case.get("amount_at_risk", 0)
    amount_rupees = amount_paise / 100

    action_success = True  # Action was dispatched (outcome determined separately)
    real_order_created = False

    if _razorpay_available:
        try:
            rzp_response = razorpay_client.create_test_payment(
                amount_paise=amount_paise,
                description=f"Smart retry for case {case_id}",
            )
            real_order_created = bool(rzp_response.get("success"))
        except Exception as e:
            rzp_response = {"success": False, "error": str(e), "raw_response": None}
    else:
        rzp_response = None

    # Fall back to a clearly-labelled simulated order whenever the real call
    # wasn't attempted (not configured) or was attempted and failed (bad
    # credentials, network error, etc.) — mirrors _execute_payment_link's
    # fallback so both actions behave consistently either way.
    if not real_order_created:
        rzp_response = {
            **(rzp_response or {}),
            "simulated": True,
            "order_id": f"order_sim_{generate_id()}",
            "amount": amount_paise,
            "currency": "INR",
            "status": "created",
            "note": "Razorpay not configured or call failed — simulated order",
        }

    # Record action
    action_id = generate_id("ACT-")
    insert_action(conn, {
        "id": action_id,
        "case_id": case_id,
        "action_type": ActionType.SMART_RETRY.value,
        "action_details": {
            "amount_paise": amount_paise,
            "retry_type": "smart_retry",
            "attempt_number": case.get("attempt_count", 0) + 1,
        },
        "razorpay_response": rzp_response,
        "outcome": "pending",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Update case counters
    update_case(conn, case_id, {
        "attempt_count": case.get("attempt_count", 0) + 1,
        "current_intervention": ActionType.SMART_RETRY.value,
    })

    # Audit log
    log_event(
        conn=conn,
        case_id=case_id,
        event_type=AuditEventType.ACTION_EXECUTED,
        actor=Actor.RAZORPAY if real_order_created else Actor.SYSTEM,
        details={
            "action_type": ActionType.SMART_RETRY.value,
            "action_id": action_id,
            "amount_rupees": amount_rupees,
            "attempt_number": case.get("attempt_count", 0) + 1,
            "razorpay_response": rzp_response,
            "is_real_order": real_order_created,
            "ai_explanation": ai_explanation,
        },
        reasoning=(
            f"Smart retry #{case.get('attempt_count', 0) + 1} dispatched for "
            f"Rs {amount_rupees:,.0f}. "
            f"{'Real Razorpay order created.' if real_order_created else 'Simulated order (Razorpay not configured or call failed).'}"
            + (f" {ai_explanation}" if ai_explanation else "")
        ),
    )

    return {
        "action_id": action_id,
        "action_type": ActionType.SMART_RETRY.value,
        "success": action_success,
        "details": rzp_response,
        "ai_explanation": ai_explanation,
    }


# ─── Payment Link ─────────────────────────────────────────────────────────────

def _execute_payment_link(conn: sqlite3.Connection, case: dict, ai_explanation: str | None = None) -> dict:
    """Create a real Razorpay Payment Link."""
    case_id = case["id"]
    amount_paise = case.get("amount_at_risk", 0)
    amount_rupees = amount_paise / 100
    customer_name = case.get("customer_name", "Customer")
    customer_email = case.get("customer_email", "")
    customer_phone = case.get("customer_phone", "")
    plan_name = case.get("plan_name", "Subscription")

    rzp_response = None
    link_id = None
    link_url = None

    if _razorpay_available:
        try:
            link_result = razorpay_client.create_payment_link(
                amount_paise=amount_paise,
                customer_name=customer_name,
                customer_email=customer_email,
                customer_phone=customer_phone,
                description=f"Recovery: {plan_name} subscription renewal",
                expiry_days=config.PAYMENT_LINK_EXPIRY_DAYS,
            )
            rzp_response = link_result
            link_id = link_result.get("id", "")
            link_url = link_result.get("short_url", "")
        except Exception as e:
            rzp_response = {"error": str(e), "simulated": True}
    
    # If Razorpay call failed or not configured, simulate
    if not link_id:
        sim_id = generate_id("plink_sim_")
        link_id = sim_id
        link_url = f"https://rzp.io/i/{sim_id[-8:]}"
        rzp_response = {
            "simulated": True,
            "id": link_id,
            "short_url": link_url,
            "amount": amount_paise,
            "currency": "INR",
            "status": "created",
            "customer": {"name": customer_name, "email": customer_email},
            "expire_by": (datetime.utcnow() + timedelta(days=config.PAYMENT_LINK_EXPIRY_DAYS)).isoformat(),
            "note": "Razorpay not configured — simulated payment link",
        }

    # Update case with link info
    update_case(conn, case_id, {
        "razorpay_payment_link_id": link_id,
        "razorpay_payment_link_url": link_url,
        "current_intervention": ActionType.PAYMENT_LINK.value,
    })

    # Record action
    action_id = generate_id("ACT-")
    insert_action(conn, {
        "id": action_id,
        "case_id": case_id,
        "action_type": ActionType.PAYMENT_LINK.value,
        "action_details": {
            "amount_paise": amount_paise,
            "link_id": link_id,
            "link_url": link_url,
            "expiry_days": config.PAYMENT_LINK_EXPIRY_DAYS,
        },
        "razorpay_response": rzp_response,
        "outcome": "pending",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Audit: PAYMENT_LINK_CREATED
    log_event(
        conn=conn,
        case_id=case_id,
        event_type=AuditEventType.PAYMENT_LINK_CREATED,
        actor=Actor.RAZORPAY if _razorpay_available and not rzp_response.get("simulated") else Actor.SYSTEM,
        details={
            "action_id": action_id,
            "link_id": link_id,
            "link_url": link_url,
            "amount_rupees": amount_rupees,
            "customer_name": customer_name,
            "expiry_days": config.PAYMENT_LINK_EXPIRY_DAYS,
            "is_real_link": _razorpay_available and not rzp_response.get("simulated", False),
            "ai_explanation": ai_explanation,
        },
        reasoning=(
            f"Payment link created: {link_url} for Rs {amount_rupees:,.0f} "
            f"(expires in {config.PAYMENT_LINK_EXPIRY_DAYS} days). "
            f"{'Real Razorpay link — clickable!' if _razorpay_available and not rzp_response.get('simulated') else 'Simulated link.'}"
            + (f" {ai_explanation}" if ai_explanation else "")
        ),
    )

    return {
        "action_id": action_id,
        "action_type": ActionType.PAYMENT_LINK.value,
        "success": True,
        "details": {
            "link_id": link_id,
            "link_url": link_url,
            "razorpay_response": rzp_response,
        },
        "ai_explanation": ai_explanation,
    }


# ─── Dunning Message ──────────────────────────────────────────────────────────

def _execute_dunning(conn: sqlite3.Connection, case: dict) -> dict:
    """Generate an AI-powered dunning message."""
    case_id = case["id"]
    amount_paise = case.get("amount_at_risk", 0)
    amount_rupees = amount_paise / 100
    customer_name = case.get("customer_name", "Customer")
    plan_name = case.get("plan_name", "Subscription")
    root_cause = case.get("root_cause", "unknown")
    comm_count = case.get("communication_count", 0)
    payment_link_url = case.get("razorpay_payment_link_url", "")

    # Map root cause to human-readable failure reason
    reason_map = {
        "insufficient_funds": "a temporary issue with your bank account",
        "expired_card": "your card on file has expired",
        "bank_decline": "your bank declined the transaction",
        "auth_required": "additional authentication was needed",
        "network_error": "a temporary network issue",
        "international_restriction": "your card doesn't support international transactions",
    }
    failure_reason = reason_map.get(root_cause, "a payment processing issue")

    # Generate dunning message via AI (with deterministic fallback)
    message = llm.generate_dunning_message(
        customer_name=customer_name,
        amount_rupees=amount_rupees,
        plan_name=plan_name,
        failure_reason=failure_reason,
        attempt_number=comm_count + 1,
        payment_link_url=payment_link_url,
    )

    # Update case counters
    update_case(conn, case_id, {
        "communication_count": comm_count + 1,
        "current_intervention": ActionType.DUNNING_MESSAGE.value,
    })

    # Record action
    action_id = generate_id("ACT-")
    insert_action(conn, {
        "id": action_id,
        "case_id": case_id,
        "action_type": ActionType.DUNNING_MESSAGE.value,
        "action_details": {
            "message_text": message,
            "attempt_number": comm_count + 1,
            "customer_name": customer_name,
            "payment_link_included": bool(payment_link_url),
        },
        "razorpay_response": None,
        "outcome": "sent",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Audit: DUNNING_GENERATED
    log_event(
        conn=conn,
        case_id=case_id,
        event_type=AuditEventType.DUNNING_GENERATED,
        actor=Actor.AI,
        details={
            "action_id": action_id,
            "message_text": message,
            "attempt_number": comm_count + 1,
            "failure_reason": failure_reason,
            "payment_link_included": bool(payment_link_url),
        },
        reasoning=(
            f"Dunning message #{comm_count + 1} generated for {customer_name}. "
            f"Tone: {'friendly' if comm_count == 0 else 'gentle urgency'}. "
            f"Payment link {'included' if payment_link_url else 'not included'}."
        ),
    )

    return {
        "action_id": action_id,
        "action_type": ActionType.DUNNING_MESSAGE.value,
        "success": True,
        "details": {
            "message_text": message,
            "attempt_number": comm_count + 1,
        },
    }


# ─── Escalation ───────────────────────────────────────────────────────────────

def _execute_escalation(conn: sqlite3.Connection, case: dict, reason: str) -> dict:
    """Record an escalation with full context for merchant review."""
    case_id = case["id"]
    amount_paise = case.get("amount_at_risk", 0)
    amount_rupees = amount_paise / 100
    root_cause = case.get("root_cause", "unknown")
    customer_name = case.get("customer_name", "Customer")

    # Build escalation context for the merchant
    escalation_context = {
        "case_id": case_id,
        "customer_name": customer_name,
        "plan_name": case.get("plan_name", ""),
        "amount_rupees": amount_rupees,
        "root_cause": root_cause,
        "reason": reason,
        "attempts_made": case.get("attempt_count", 0),
        "communications_sent": case.get("communication_count", 0),
        "recommended_action": _get_merchant_recommendation(root_cause),
    }

    # Update case
    update_case(conn, case_id, {
        "escalation_reason": reason,
        "current_intervention": ActionType.ESCALATION.value,
    })

    # Record action
    action_id = generate_id("ACT-")
    insert_action(conn, {
        "id": action_id,
        "case_id": case_id,
        "action_type": ActionType.ESCALATION.value,
        "action_details": escalation_context,
        "razorpay_response": None,
        "outcome": "escalated",
        "created_at": datetime.utcnow().isoformat(),
    })

    # Audit: CASE_ESCALATED
    log_event(
        conn=conn,
        case_id=case_id,
        event_type=AuditEventType.CASE_ESCALATED,
        actor=Actor.SYSTEM,
        details=escalation_context,
        reasoning=(
            f"Case escalated to merchant: {reason}. "
            f"Rs {amount_rupees:,.0f} for {customer_name} ({root_cause}). "
            f"Recommendation: {escalation_context['recommended_action']}"
        ),
    )

    return {
        "action_id": action_id,
        "action_type": ActionType.ESCALATION.value,
        "success": True,
        "details": escalation_context,
    }


def _get_merchant_recommendation(root_cause: str) -> str:
    """Get a recommended next action for the merchant."""
    recs = {
        "fraud_flag": "Review transaction for fraud indicators. Contact customer directly if legitimate.",
        "account_closed": "Contact customer to obtain alternative payment method.",
        "disputed": "Do not attempt recovery. Await dispute resolution through Razorpay.",
        "unknown": "Manual review needed — root cause could not be determined with confidence.",
        "insufficient_funds": "Customer may need a flexible payment plan or extended deadline.",
        "expired_card": "VIP customer — personal outreach recommended for card update.",
        "bank_decline": "Consider contacting customer's bank or trying alternative payment method.",
        "auth_required": "Customer may need assistance completing authentication.",
        "network_error": "Likely resolved — monitor for repeat failures.",
        "international_restriction": "Help customer set up domestic payment method.",
    }
    return recs.get(root_cause, "Manual review recommended.")
