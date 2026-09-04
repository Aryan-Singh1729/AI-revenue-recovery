"""
Historical Seed Data — pre-loads 30 already-resolved recovery cases.

These give the dashboard historical context so it looks like a running product,
not a freshly-initialized demo. Each case has a complete audit trail.

Distribution:
- 12 recovered (with varied root causes and interventions)
- 8 escalated (fraud, account closed, high-value)
- 5 stopped (opt-out, min amount, cost ratio)
- 5 unrecovered (all interventions exhausted)

Usage:
    python -m data.seed_historical
"""

import json
import random
from datetime import datetime, timedelta
from database.db import (
    get_connection, init_db,
    insert_customer, insert_subscription, insert_recovery_case,
    insert_action, insert_audit_entry,
)
from models.enums import (
    RootCause, RecoveryStatus, ActionType,
    AuditEventType, Actor,
)

SEED = 99
rng = random.Random(SEED)


def generate_id(prefix: str = "") -> str:
    """
    Seeded ID generator, shadowing database.db.generate_id on purpose.

    Historical IDs must be stable across regenerations so that case links in a
    rehearsed demo keep working and the dashboard shows the same cases every run.
    """
    return f"{prefix}{rng.getrandbits(32):08x}"

# ─── Historical Case Templates ────────────────────────────────────────────────

HISTORICAL_FIRST_NAMES = [
    "Aman", "Riya", "Dev", "Sita", "Kartik", "Lata", "Aryan", "Geeta",
    "Yash", "Maya", "Rohit", "Zara", "Kabir", "Preeti", "Sahil", "Dimple",
    "Jay", "Kiran", "Akash", "Usha", "Raj", "Payal", "Om", "Tanya",
    "Veer", "Jaya", "Neil", "Rani", "Sahil", "Sunita",
]
HISTORICAL_LAST_NAMES = [
    "Roy", "Dutta", "Shah", "Bose", "Khanna", "Gill", "Sethi", "Bhatt",
    "Mitra", "Ahuja", "Sen", "Kohli", "Grover", "Luthra", "Bajaj",
]

# Pre-defined resolved cases with complete details
RESOLVED_CASES = [
    # ─── 12 RECOVERED ──────────────────────────────────────────────────
    {"root_cause": RootCause.INSUFFICIENT_FUNDS, "status": RecoveryStatus.RECOVERED,
     "plan": "Basic", "amount": 49900, "actions": ["smart_retry", "payment_link"],
     "days_ago": 28, "recovered_amount": 49900},
    {"root_cause": RootCause.INSUFFICIENT_FUNDS, "status": RecoveryStatus.RECOVERED,
     "plan": "Pro", "amount": 99900, "actions": ["smart_retry"],
     "days_ago": 25, "recovered_amount": 99900},
    {"root_cause": RootCause.INSUFFICIENT_FUNDS, "status": RecoveryStatus.RECOVERED,
     "plan": "Starter", "amount": 19900, "actions": ["smart_retry", "payment_link", "dunning_message"],
     "days_ago": 20, "recovered_amount": 19900},
    {"root_cause": RootCause.EXPIRED_CARD, "status": RecoveryStatus.RECOVERED,
     "plan": "Business", "amount": 299900, "actions": ["payment_link"],
     "days_ago": 22, "recovered_amount": 299900},
    {"root_cause": RootCause.EXPIRED_CARD, "status": RecoveryStatus.RECOVERED,
     "plan": "Pro", "amount": 99900, "actions": ["payment_link", "dunning_message"],
     "days_ago": 18, "recovered_amount": 99900},
    {"root_cause": RootCause.AUTH_REQUIRED, "status": RecoveryStatus.RECOVERED,
     "plan": "Basic", "amount": 49900, "actions": ["payment_link"],
     "days_ago": 15, "recovered_amount": 49900},
    {"root_cause": RootCause.AUTH_REQUIRED, "status": RecoveryStatus.RECOVERED,
     "plan": "Enterprise", "amount": 999900, "actions": ["payment_link", "dunning_message"],
     "days_ago": 12, "recovered_amount": 999900},
    {"root_cause": RootCause.NETWORK_ERROR, "status": RecoveryStatus.RECOVERED,
     "plan": "Starter", "amount": 19900, "actions": ["smart_retry"],
     "days_ago": 10, "recovered_amount": 19900},
    {"root_cause": RootCause.NETWORK_ERROR, "status": RecoveryStatus.RECOVERED,
     "plan": "Business", "amount": 299900, "actions": ["smart_retry"],
     "days_ago": 8, "recovered_amount": 299900},
    {"root_cause": RootCause.BANK_DECLINE, "status": RecoveryStatus.RECOVERED,
     "plan": "Pro", "amount": 99900, "actions": ["smart_retry", "smart_retry"],
     "days_ago": 14, "recovered_amount": 99900},
    {"root_cause": RootCause.BANK_DECLINE, "status": RecoveryStatus.RECOVERED,
     "plan": "Basic", "amount": 49900, "actions": ["smart_retry", "payment_link"],
     "days_ago": 7, "recovered_amount": 49900},
    {"root_cause": RootCause.INTERNATIONAL_RESTRICTION, "status": RecoveryStatus.RECOVERED,
     "plan": "Enterprise", "amount": 999900, "actions": ["dunning_message", "payment_link"],
     "days_ago": 5, "recovered_amount": 999900},

    # ─── 8 ESCALATED ──────────────────────────────────────────────────
    {"root_cause": RootCause.FRAUD_FLAG, "status": RecoveryStatus.ESCALATED,
     "plan": "Business", "amount": 299900, "actions": ["escalation"],
     "days_ago": 26, "escalation_reason": "Fraud flagged — policy blocks autonomous recovery",
     "triggered_rule": {"number": 8, "name": "Fraud Block", "threshold": "No fraud flag",
                        "value": "Root cause: fraud_flag", "action": "ESCALATE"}},
    {"root_cause": RootCause.FRAUD_FLAG, "status": RecoveryStatus.ESCALATED,
     "plan": "Enterprise", "amount": 999900, "actions": ["escalation"],
     "days_ago": 19, "escalation_reason": "Fraud flagged — immediate escalation required",
     "triggered_rule": {"number": 8, "name": "Fraud Block", "threshold": "No fraud flag",
                        "value": "Root cause: fraud_flag", "action": "ESCALATE"}},
    {"root_cause": RootCause.ACCOUNT_CLOSED, "status": RecoveryStatus.ESCALATED,
     "plan": "Pro", "amount": 99900, "actions": ["escalation"],
     "days_ago": 23, "escalation_reason": "Bank account closed — cannot retry"},
    {"root_cause": RootCause.ACCOUNT_CLOSED, "status": RecoveryStatus.ESCALATED,
     "plan": "Basic", "amount": 49900, "actions": ["escalation"],
     "days_ago": 16, "escalation_reason": "Bank account closed — merchant must contact customer"},
    {"root_cause": RootCause.ACCOUNT_CLOSED, "status": RecoveryStatus.ESCALATED,
     "plan": "Starter", "amount": 19900, "actions": ["escalation"],
     "days_ago": 11, "escalation_reason": "Bank account closed — no viable recovery path"},
    {"root_cause": RootCause.INSUFFICIENT_FUNDS, "status": RecoveryStatus.ESCALATED,
     "plan": "Premium", "amount": 2999900, "actions": ["escalation"],
     "days_ago": 9, "escalation_reason": "Policy rule #10 (High-Value Review): Rs 29,999 exceeds Below Rs 25,000",
     "triggered_rule": {"number": 10, "name": "High-Value Review",
                        "threshold": "Below Rs 25,000", "value": "Rs 29,999",
                        "action": "ESCALATE"}},
    {"root_cause": RootCause.BANK_DECLINE, "status": RecoveryStatus.ESCALATED,
     "plan": "Business", "amount": 299900, "actions": ["smart_retry", "smart_retry", "smart_retry", "escalation"],
     "days_ago": 6, "escalation_reason": "Max retry attempts (3/3) exhausted — persistent bank decline",
     "triggered_rule": {"number": 1, "name": "Max Retry Attempts", "threshold": "3 retries",
                        "value": "3 retries done", "action": "STOP"}},
    {"root_cause": RootCause.EXPIRED_CARD, "status": RecoveryStatus.ESCALATED,
     "plan": "Enterprise", "amount": 999900, "actions": ["payment_link", "dunning_message", "escalation"],
     "days_ago": 3, "escalation_reason": "Customer did not respond to payment link or dunning — needs direct merchant outreach"},

    # ─── 5 STOPPED ──────────────────────────────────────────────────
    {"root_cause": RootCause.DISPUTED, "status": RecoveryStatus.STOPPED,
     "plan": "Pro", "amount": 99900, "actions": [],
     "days_ago": 24, "stop_reason": "Payment disputed — policy prohibits recovery",
     "triggered_rule": {"number": 9, "name": "Dispute Block", "threshold": "No active dispute",
                        "value": "Root cause: disputed", "action": "STOP"}},
    {"root_cause": RootCause.DISPUTED, "status": RecoveryStatus.STOPPED,
     "plan": "Basic", "amount": 49900, "actions": [],
     "days_ago": 17, "stop_reason": "Payment disputed — must not attempt recovery",
     "triggered_rule": {"number": 9, "name": "Dispute Block", "threshold": "No active dispute",
                        "value": "Root cause: disputed", "action": "STOP"}},
    {"root_cause": RootCause.INSUFFICIENT_FUNDS, "status": RecoveryStatus.STOPPED,
     "plan": "Micro", "amount": 3500, "actions": [],
     "days_ago": 13, "stop_reason": "Amount ₹35 below minimum recovery threshold (₹50)",
     "triggered_rule": {"number": 4, "name": "Minimum Viable Amount", "threshold": "Rs 50",
                        "value": "Rs 35", "action": "STOP"}},
    {"root_cause": RootCause.BANK_DECLINE, "status": RecoveryStatus.STOPPED,
     "plan": "Starter", "amount": 19900, "actions": ["smart_retry"],
     "days_ago": 4, "stop_reason": "Customer opted out of recovery communications",
     "triggered_rule": {"number": 6, "name": "Customer Opt-Out", "threshold": "Not opted out",
                        "value": "Opted out", "action": "STOP"}},
    {"root_cause": RootCause.INSUFFICIENT_FUNDS, "status": RecoveryStatus.STOPPED,
     "plan": "Pro", "amount": 99900, "actions": ["smart_retry", "payment_link"],
     "days_ago": 2, "stop_reason": "Recovery window exceeded (14 days) — stopping all recovery",
     "triggered_rule": {"number": 3, "name": "Max Recovery Window", "threshold": "14 days",
                        "value": "16 days elapsed", "action": "STOP"}},

    # ─── 5 UNRECOVERED (status = stopped, all interventions failed) ──
    # Note: We model unrecovered as a subtype of stopped with different stop_reason
]

# Add 5 "unrecovered" cases (stopped because all interventions exhausted, not policy-blocked)
for i, rc in enumerate([
    RootCause.INSUFFICIENT_FUNDS, RootCause.BANK_DECLINE,
    RootCause.EXPIRED_CARD, RootCause.AUTH_REQUIRED, RootCause.BANK_DECLINE,
]):
    RESOLVED_CASES.append({
        "root_cause": rc,
        "status": RecoveryStatus.STOPPED,
        "plan": ["Basic", "Pro", "Starter", "Business", "Basic"][i],
        "amount": [49900, 99900, 19900, 299900, 49900][i],
        "actions": ["smart_retry", "payment_link", "dunning_message"],
        "days_ago": 27 - i * 3,
        "stop_reason": "All interventions exhausted — no further recovery actions available",
    })


# ─── Error payloads for each root cause (same as generate_batch.py) ────────────

ERROR_TEMPLATES = {
    RootCause.INSUFFICIENT_FUNDS: ("BAD_REQUEST_ERROR", "insufficient_funds", "bank", "payment_authorization"),
    RootCause.EXPIRED_CARD: ("BAD_REQUEST_ERROR", "card_expired", "customer", "payment_authorization"),
    RootCause.BANK_DECLINE: ("GATEWAY_ERROR", "payment_declined", "bank", "payment_authorization"),
    RootCause.AUTH_REQUIRED: ("BAD_REQUEST_ERROR", "payment_requires_action", "customer", "payment_authentication"),
    RootCause.NETWORK_ERROR: ("GATEWAY_ERROR", "request_timeout", "internal", "payment_processing"),
    RootCause.ACCOUNT_CLOSED: ("BAD_REQUEST_ERROR", "account_closed", "bank", "payment_authorization"),
    RootCause.INTERNATIONAL_RESTRICTION: ("BAD_REQUEST_ERROR", "international_transaction_not_allowed", "bank", "payment_authorization"),
    RootCause.FRAUD_FLAG: ("BAD_REQUEST_ERROR", "suspected_fraud", "bank", "payment_authorization"),
    RootCause.DISPUTED: ("BAD_REQUEST_ERROR", "payment_disputed", "customer", "payment_capture"),
}


# The 10 rules in the same shape policy_engine.check_policy() writes them, so
# database.db.get_policy_trigger_stats() can count live and historical
# evaluations with one query.
RULE_CATALOG = [
    (1,  "Max Retry Attempts",    "3 retries",          "0 retries done",       "STOP"),
    (2,  "Max Communications",    "2 messages",         "0 messages sent",      "STOP"),
    (3,  "Max Recovery Window",   "14 days",            "within window",        "STOP"),
    (4,  "Minimum Viable Amount", "Rs 50",              "above minimum",        "STOP"),
    (5,  "Cost Ratio Limit",      "30% of amount",      "within budget",        "STOP"),
    (6,  "Customer Opt-Out",      "Not opted out",      "Active",               "STOP"),
    (7,  "Action Cooldown",       "24 hours",           "cooldown satisfied",   "WAIT"),
    (8,  "Fraud Block",           "No fraud flag",      "no fraud flag",        "ESCALATE"),
    (9,  "Dispute Block",         "No active dispute",  "no active dispute",    "STOP"),
    (10, "High-Value Review",     "Below Rs 25,000",    "below threshold",      "ESCALATE"),
]


def _build_all_rules(triggered: dict | None) -> list[dict]:
    """Render the 10-rule checklist, marking `triggered` (if any) as failed."""
    rules = []
    for number, name, threshold, ok_value, action in RULE_CATALOG:
        failed = bool(triggered) and triggered["number"] == number
        rules.append({
            "rule_number": number,
            "rule_name": name,
            "threshold": triggered["threshold"] if failed else threshold,
            "current_value": triggered["value"] if failed else ok_value,
            "passed": not failed,
            "action_if_failed": action,
        })
    return rules


def _policy_details(template: dict, action: str, is_final: bool) -> dict:
    """Details payload for a historical POLICY_EVALUATED audit entry."""
    triggered = template.get("triggered_rule") if is_final else None
    rules = _build_all_rules(triggered)
    if triggered:
        result = "escalate" if triggered["action"] == "ESCALATE" else "stop"
    elif action == "escalation":
        result = "escalate"
    else:
        result = "allowed"
    return {
        "proposed_action": action,
        "result": result,
        "triggered_rule": triggered["name"] if triggered else None,
        "rules_passed": sum(1 for r in rules if r["passed"]),
        "rules_total": len(rules),
        "all_rules": rules,
    }


def _policy_reasoning(template: dict, action: str, is_final: bool) -> str:
    triggered = template.get("triggered_rule") if is_final else None
    if triggered:
        return (f"Policy check for '{action}': {triggered['action'].lower()}. "
                f"9/10 rules passed. Blocked by: Policy rule #{triggered['number']} "
                f"({triggered['name']}): {triggered['value']} exceeds {triggered['threshold']}")
    if action == "escalation":
        return f"Policy check for 'escalation': escalate. 10/10 rules passed. All rules passed."
    return f"Policy check for '{action}': allowed. 10/10 rules passed. All rules passed."


def _build_audit_trail(case_id: str, template: dict, base_time: datetime) -> list[dict]:
    """Build a complete audit trail for a resolved historical case."""
    entries = []
    t = base_time
    root_cause = template["root_cause"]

    # 1. CASE_CREATED
    entries.append({
        "id": generate_id("AUD-"),
        "case_id": case_id,
        "timestamp": t.isoformat(),
        "event_type": AuditEventType.CASE_CREATED.value,
        "actor": Actor.SYSTEM.value,
        "details": json.dumps({
            "amount_at_risk": template["amount"],
            "error_reason": root_cause.value,
        }),
        "reasoning": f"Failed payment detected. ₹{template['amount'] / 100:,.0f} at risk.",
    })

    t += timedelta(seconds=1)

    # 2. ROOT_CAUSE_DIAGNOSED
    entries.append({
        "id": generate_id("AUD-"),
        "case_id": case_id,
        "timestamp": t.isoformat(),
        "event_type": AuditEventType.ROOT_CAUSE_DIAGNOSED.value,
        "actor": Actor.SYSTEM.value,
        "details": json.dumps({
            "root_cause": root_cause.value,
            "method": "deterministic",
            "confidence": 1.0,
        }),
        "reasoning": f"Root cause: {root_cause.value}. Identified via deterministic error code mapping.",
    })

    t += timedelta(seconds=1)

    # 3. For each action taken
    for i, action in enumerate(template.get("actions", [])):
        # INTERVENTION_SELECTED
        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.INTERVENTION_SELECTED.value,
            "actor": Actor.SYSTEM.value,
            "details": json.dumps({
                "action_type": action,
                "attempt_number": i + 1,
            }),
            "reasoning": f"Selected {action} as intervention #{i + 1} for {root_cause.value}.",
        })
        t += timedelta(seconds=1)

        # POLICY_EVALUATED
        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.POLICY_EVALUATED.value,
            "actor": Actor.POLICY_ENGINE.value,
            "details": json.dumps(_policy_details(template, action, is_final=(
                i == len(template.get("actions", [])) - 1))),
            "reasoning": _policy_reasoning(template, action, is_final=(
                i == len(template.get("actions", [])) - 1)),
        })
        t += timedelta(seconds=1)

        # ACTION_EXECUTED
        is_last = i == len(template["actions"]) - 1
        is_recovered = template["status"] == RecoveryStatus.RECOVERED and is_last
        outcome = "success" if is_recovered else "failed"

        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.ACTION_EXECUTED.value,
            "actor": Actor.RAZORPAY.value if action in ["smart_retry", "payment_link"] else Actor.AI.value,
            "details": json.dumps({
                "action_type": action,
                "outcome": outcome if action != "escalation" else "escalated",
            }),
            "reasoning": f"Executed {action}. Outcome: {outcome}." if action != "escalation" else f"Escalated to merchant.",
        })
        t += timedelta(hours=rng.randint(1, 48))

    # 3b. Cases blocked before any action ever ran (disputed, below-minimum,
    #     opted-out) still get a full policy evaluation — that evaluation IS the
    #     record of why nothing was attempted.
    if template.get("triggered_rule") and not template.get("actions"):
        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.POLICY_EVALUATED.value,
            "actor": Actor.POLICY_ENGINE.value,
            "details": json.dumps(_policy_details(template, "escalation", is_final=True)),
            "reasoning": _policy_reasoning(template, "escalation", is_final=True),
        })
        t += timedelta(seconds=1)

    # 4. Terminal state
    if template["status"] == RecoveryStatus.RECOVERED:
        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.CASE_RECOVERED.value,
            "actor": Actor.SYSTEM.value,
            "details": json.dumps({
                # Key names must match engine/recovery_orchestrator.py so that
                # attribution can group historical and live recoveries together.
                "amount_recovered_paise": template.get("recovered_amount", 0),
                "amount_recovered_rupees": template.get("recovered_amount", 0) / 100,
                "recovery_action": (template["actions"] or ["smart_retry"])[-1],
                "attempt_count": len(template["actions"]),
            }),
            "reasoning": f"₹{template.get('recovered_amount', 0) / 100:,.0f} recovered after {len(template['actions'])} action(s).",
        })
    elif template["status"] == RecoveryStatus.ESCALATED:
        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.CASE_ESCALATED.value,
            "actor": Actor.SYSTEM.value,
            "details": json.dumps({
                "reason": template.get("escalation_reason", ""),
            }),
            "reasoning": template.get("escalation_reason", "Escalated to merchant."),
        })
    elif template["status"] == RecoveryStatus.STOPPED:
        entries.append({
            "id": generate_id("AUD-"),
            "case_id": case_id,
            "timestamp": t.isoformat(),
            "event_type": AuditEventType.CASE_STOPPED.value,
            "actor": Actor.POLICY_ENGINE.value,
            "details": json.dumps({
                "reason": template.get("stop_reason", ""),
            }),
            "reasoning": template.get("stop_reason", "Recovery stopped."),
        })

    return entries


def seed_historical():
    """Load 30 pre-resolved historical cases into the database."""
    init_db()
    conn = get_connection()

    print("📜 Seeding 30 historical recovery cases...")
    print()

    total_recovered_amount = 0
    status_counts = {"recovered": 0, "escalated": 0, "stopped": 0}

    for i, template in enumerate(RESOLVED_CASES):
        # 1. Create customer
        first = HISTORICAL_FIRST_NAMES[i % len(HISTORICAL_FIRST_NAMES)]
        last = rng.choice(HISTORICAL_LAST_NAMES)
        customer_id = generate_id("HCUST-")
        customer = {
            "id": customer_id,
            "name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@gmail.com",
            "phone": f"+91{rng.randint(7000000000, 9999999999)}",
            "created_at": (datetime.utcnow() - timedelta(days=rng.randint(60, 500))).isoformat(),
            "opt_out": 1 if template.get("stop_reason", "").startswith("Customer opted out") else 0,
        }
        insert_customer(conn, customer)

        # 2. Create subscription
        sub_id = generate_id("HSUB-")
        remaining = rng.randint(1, 11)
        subscription = {
            "id": sub_id,
            "customer_id": customer_id,
            "plan_name": template["plan"],
            "amount": template["amount"],
            "currency": "INR",
            "billing_cycle": "monthly",
            "remaining_cycles": remaining,
            "status": "active" if template["status"] == RecoveryStatus.RECOVERED else "halted",
            "created_at": (datetime.utcnow() - timedelta(days=rng.randint(60, 365))).isoformat(),
        }
        insert_subscription(conn, subscription)

        # 3. Create recovery case
        root_cause = template["root_cause"]
        err_code, err_reason, err_source, err_step = ERROR_TEMPLATES[root_cause]
        days_ago = template["days_ago"]
        base_time = datetime.utcnow() - timedelta(days=days_ago)

        case_id = generate_id("HRC-")
        recovered_amount = template.get("recovered_amount", 0)
        total_recovered_amount += recovered_amount

        case = {
            "id": case_id,
            "subscription_id": sub_id,
            "razorpay_payment_id": f"pay_hist_{generate_id()}",
            "failure_error_code": err_code,
            "failure_error_description": f"Historical: {err_reason}",
            "failure_error_reason": err_reason,
            "failure_error_source": err_source,
            "failure_error_step": err_step,
            "amount_at_risk": template["amount"],
            "total_risk": template["amount"] * remaining,
            "root_cause": root_cause.value,
            "diagnosis_confidence": 1.0,
            "diagnosis_method": "deterministic",
            "current_intervention": template["actions"][-1] if template["actions"] else None,
            "interventions_tried": json.dumps(template["actions"]),
            "status": template["status"].value,
            "attempt_count": sum(1 for a in template["actions"] if a == "smart_retry"),
            "communication_count": sum(1 for a in template["actions"] if a == "dunning_message"),
            "created_at": base_time.isoformat(),
            "updated_at": (base_time + timedelta(days=rng.randint(0, 3))).isoformat(),
            "resolved_at": (base_time + timedelta(days=rng.randint(1, 5))).isoformat(),
            "amount_recovered": recovered_amount,
            "razorpay_payment_link_id": f"plink_hist_{generate_id()}" if "payment_link" in template["actions"] else None,
            "razorpay_payment_link_url": f"https://rzp.io/i/hist{generate_id()[:6]}" if "payment_link" in template["actions"] else None,
            "escalation_reason": template.get("escalation_reason"),
            "stop_reason": template.get("stop_reason"),
        }
        insert_recovery_case(conn, case)

        # 4. Create recovery actions
        for j, action_type in enumerate(template["actions"]):
            is_last = j == len(template["actions"]) - 1
            is_recovered = template["status"] == RecoveryStatus.RECOVERED and is_last

            action = {
                "id": generate_id("HACT-"),
                "case_id": case_id,
                "action_type": action_type,
                "action_details": json.dumps({"attempt": j + 1, "historical": True}),
                "razorpay_response": json.dumps({"status": "success" if is_recovered else "failed", "historical": True}),
                "outcome": "success" if is_recovered else ("escalated" if action_type == "escalation" else "failed"),
                "created_at": (base_time + timedelta(hours=j * rng.randint(6, 48))).isoformat(),
            }
            insert_action(conn, action)

        # 5. Create audit trail
        audit_entries = _build_audit_trail(case_id, template, base_time)
        for entry in audit_entries:
            insert_audit_entry(conn, entry)

        # Track stats
        status_counts[template["status"].value] = status_counts.get(template["status"].value, 0) + 1

    conn.commit()

    # Print summary
    print(f"✅ Seeded {len(RESOLVED_CASES)} historical cases")
    print()
    print(f"📊 Status distribution:")
    print(f"   Recovered:  {status_counts.get('recovered', 0)} cases")
    print(f"   Escalated:  {status_counts.get('escalated', 0)} cases")
    print(f"   Stopped:    {status_counts.get('stopped', 0)} cases (includes unrecovered)")
    print()
    print(f"💰 Historical revenue recovered: ₹{total_recovered_amount / 100:,.0f}")

    conn.close()
    print()
    print("✅ Historical seed complete.")


if __name__ == "__main__":
    seed_historical()
