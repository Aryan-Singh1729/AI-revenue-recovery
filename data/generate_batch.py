"""
Synthetic Batch Generator — creates 50 realistic failed subscription payment cases.

Each case has:
- A customer profile (realistic Indian names, emails, phones)
- A subscription on a specific plan/tier
- A failed payment with Razorpay-format error payload
- Varied root causes, amounts, and edge cases for policy testing

The error payloads match Razorpay's actual error format so the diagnoser
can process them identically to real webhook data.

Usage:
    python -m data.generate_batch
"""

import random
import json
from datetime import datetime, timedelta
from database.db import (
    get_connection, init_db, generate_id,
    insert_customer, insert_subscription, insert_recovery_case,
)
from models.enums import RootCause, RecoveryStatus

# Seed for reproducible demo output
SEED = 42
rng = random.Random(SEED)

# ─── Realistic Indian Customer Data ────────────────────────────────────────────

FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Arjun", "Divya",
    "Karthik", "Meera", "Rahul", "Nisha", "Amit", "Pooja", "Suresh", "Kavita",
    "Rajesh", "Deepa", "Mohit", "Ritu", "Sanjay", "Swati", "Varun", "Neha",
    "Aditya", "Shreya", "Nikhil", "Anjali", "Manish", "Komal", "Gaurav", "Pallavi",
    "Harsh", "Tanvi", "Ajay", "Sonal", "Pankaj", "Megha", "Vivek", "Isha",
    "Rakesh", "Bhavna", "Ashish", "Sakshi", "Kunal", "Trisha", "Naveen", "Simran",
    "Yogesh", "Aditi",
]

LAST_NAMES = [
    "Sharma", "Patel", "Kumar", "Singh", "Gupta", "Agarwal", "Joshi", "Mehta",
    "Reddy", "Iyer", "Nair", "Verma", "Rao", "Desai", "Bhat", "Mishra",
    "Saxena", "Kapoor", "Malhotra", "Chopra", "Bansal", "Jain", "Chauhan",
    "Tiwari", "Pandey", "Thakur", "Srinivasan", "Pillai", "Menon", "Das",
]

DOMAINS = ["gmail.com", "yahoo.in", "outlook.com", "hotmail.com", "protonmail.com"]

# ─── Subscription Plans ────────────────────────────────────────────────────────

PLANS = [
    {"name": "Starter",    "amount_rupees": 199,    "count": 8},
    {"name": "Basic",      "amount_rupees": 499,    "count": 10},
    {"name": "Pro",        "amount_rupees": 999,    "count": 8},
    {"name": "Business",   "amount_rupees": 2999,   "count": 7},
    {"name": "Enterprise", "amount_rupees": 9999,   "count": 5},
    {"name": "Premium",    "amount_rupees": 24999,  "count": 2},
]
# Plus special edge cases added separately

# ─── Razorpay Error Payloads by Root Cause ─────────────────────────────────────

ERROR_TEMPLATES = {
    RootCause.INSUFFICIENT_FUNDS: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Your payment didn't go through as it was declined by the bank. Try again or use another payment method.",
        "error_reason": "insufficient_funds",
        "error_source": "bank",
        "error_step": "payment_authorization",
    },
    RootCause.EXPIRED_CARD: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "The card is expired. Please use a different card or contact your bank.",
        "error_reason": "card_expired",
        "error_source": "customer",
        "error_step": "payment_authorization",
    },
    RootCause.BANK_DECLINE: {
        "error_code": "GATEWAY_ERROR",
        "error_description": "The payment was declined by the issuing bank. No specific reason provided.",
        "error_reason": "payment_declined",
        "error_source": "bank",
        "error_step": "payment_authorization",
    },
    RootCause.AUTH_REQUIRED: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "Payment requires additional authentication from the cardholder.",
        "error_reason": "payment_requires_action",
        "error_source": "customer",
        "error_step": "payment_authentication",
    },
    RootCause.NETWORK_ERROR: {
        "error_code": "GATEWAY_ERROR",
        "error_description": "The request timed out while processing. Please retry.",
        "error_reason": "request_timeout",
        "error_source": "internal",
        "error_step": "payment_processing",
    },
    RootCause.ACCOUNT_CLOSED: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "The bank account associated with this payment method has been closed.",
        "error_reason": "account_closed",
        "error_source": "bank",
        "error_step": "payment_authorization",
    },
    RootCause.INTERNATIONAL_RESTRICTION: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "International transactions are not allowed on this card.",
        "error_reason": "international_transaction_not_allowed",
        "error_source": "bank",
        "error_step": "payment_authorization",
    },
    RootCause.FRAUD_FLAG: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "This transaction has been flagged for suspected fraud by the risk system.",
        "error_reason": "suspected_fraud",
        "error_source": "bank",
        "error_step": "payment_authorization",
    },
    RootCause.DISPUTED: {
        "error_code": "BAD_REQUEST_ERROR",
        "error_description": "This payment has been disputed by the cardholder.",
        "error_reason": "payment_disputed",
        "error_source": "customer",
        "error_step": "payment_capture",
    },
}

# ─── Failure Distribution (50 cases total) ─────────────────────────────────────

FAILURE_DISTRIBUTION = [
    (RootCause.INSUFFICIENT_FUNDS,        12),
    (RootCause.EXPIRED_CARD,               8),
    (RootCause.BANK_DECLINE,               7),
    (RootCause.AUTH_REQUIRED,              5),
    (RootCause.NETWORK_ERROR,              5),
    (RootCause.ACCOUNT_CLOSED,             5),
    (RootCause.INTERNATIONAL_RESTRICTION,  3),
    (RootCause.FRAUD_FLAG,                 3),
    (RootCause.DISPUTED,                   2),
]

# ─── Generator Functions ──────────────────────────────────────────────────────

def _generate_customer(index: int) -> dict:
    """Generate a realistic customer profile."""
    first = FIRST_NAMES[index % len(FIRST_NAMES)]
    last = rng.choice(LAST_NAMES)
    domain = rng.choice(DOMAINS)
    email = f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@{domain}"
    phone = f"+91{rng.randint(7000000000, 9999999999)}"
    days_ago = rng.randint(30, 1000)

    return {
        "id": generate_id("CUST-"),
        "name": f"{first} {last}",
        "email": email,
        "phone": phone,
        "created_at": (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
        "opt_out": 0,
    }


def _assign_plan(index: int, plans_remaining: dict) -> dict:
    """Assign a subscription plan based on distribution."""
    for plan in PLANS:
        key = plan["name"]
        if plans_remaining.get(key, 0) > 0:
            plans_remaining[key] -= 1
            return plan
    # Fallback
    return rng.choice(PLANS)


def _generate_subscription(customer_id: str, plan: dict) -> dict:
    """Generate a subscription record."""
    return {
        "id": generate_id("SUB-"),
        "customer_id": customer_id,
        "plan_name": plan["name"],
        "amount": plan["amount_rupees"] * 100,  # Convert to paise
        "currency": "INR",
        "billing_cycle": "monthly",
        "remaining_cycles": rng.randint(1, 11),
        "status": "active",
        "created_at": (datetime.utcnow() - timedelta(days=rng.randint(30, 365))).isoformat(),
    }


def _generate_recovery_case(
    subscription: dict,
    root_cause: RootCause,
    case_index: int,
) -> dict:
    """Generate a recovery case with Razorpay-format error payload."""
    error = ERROR_TEMPLATES[root_cause]
    amount = subscription["amount"]  # Already in paise
    remaining = subscription["remaining_cycles"]

    # Simulated Razorpay payment ID
    pay_id = f"pay_test_{generate_id()}"

    # Days since failure (for recovery window testing)
    days_since = rng.randint(0, 5)

    return {
        "id": generate_id("RC-"),
        "subscription_id": subscription["id"],
        "razorpay_payment_id": pay_id,
        "failure_error_code": error["error_code"],
        "failure_error_description": error["error_description"],
        "failure_error_reason": error["error_reason"],
        "failure_error_source": error["error_source"],
        "failure_error_step": error["error_step"],
        "amount_at_risk": amount,
        "total_risk": amount * remaining,
        "root_cause": None,           # Will be set by diagnoser
        "diagnosis_confidence": 0.0,
        "diagnosis_method": "",
        "current_intervention": None,
        "interventions_tried": json.dumps([]),
        "status": RecoveryStatus.DETECTED.value,
        "attempt_count": 0,
        "communication_count": 0,
        "created_at": (datetime.utcnow() - timedelta(days=days_since)).isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "resolved_at": None,
        "amount_recovered": 0,
        "razorpay_payment_link_id": None,
        "razorpay_payment_link_url": None,
        "escalation_reason": None,
        "stop_reason": None,
    }


def generate_batch():
    """
    Generate the full batch: 50 customers + subscriptions + failed payment cases.

    Includes edge cases for policy testing:
    - 3 customers with opt_out = True
    - 1 subscription at ₹35 (below ₹50 minimum recovery threshold)
    - 2 subscriptions at ₹24,999+ (high-value escalation threshold)
    """
    init_db()
    conn = get_connection()

    # Build the failure assignment list
    failure_assignments: list[RootCause] = []
    for root_cause, count in FAILURE_DISTRIBUTION:
        failure_assignments.extend([root_cause] * count)
    rng.shuffle(failure_assignments)

    # Build plan assignment pool
    plans_remaining = {p["name"]: p["count"] for p in PLANS}

    print("🔧 Generating 50 recovery cases...")
    print()

    all_cases = []
    plan_index = 0

    for i, root_cause in enumerate(failure_assignments):
        # 1. Create customer
        customer = _generate_customer(i)

        # Edge case: mark 3 customers as opted-out (for policy testing)
        if i in [7, 22, 38]:
            customer["opt_out"] = 1

        insert_customer(conn, customer)

        # 2. Assign plan and create subscription
        plan = _assign_plan(plan_index, plans_remaining)
        plan_index += 1
        subscription = _generate_subscription(customer["id"], plan)

        # Edge case: one ultra-low subscription (₹35) for min-amount stopping rule
        if i == 15:
            subscription["plan_name"] = "Micro"
            subscription["amount"] = 3500  # ₹35 in paise

        insert_subscription(conn, subscription)

        # 3. Create recovery case
        case = _generate_recovery_case(subscription, root_cause, i)
        insert_recovery_case(conn, case)
        all_cases.append((case, customer, subscription, root_cause))

    conn.commit()

    # Print summary
    print(f"✅ Generated {len(all_cases)} recovery cases")
    print()

    # Count by root cause
    cause_counts: dict[str, int] = {}
    for _, _, _, rc in all_cases:
        cause_counts[rc.value] = cause_counts.get(rc.value, 0) + 1

    print("📊 Distribution by root cause:")
    for cause, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
        print(f"   {cause:35s} → {count} cases")

    # Count edge cases
    opt_out_count = sum(1 for _, c, _, _ in all_cases if c["opt_out"])
    low_amount = sum(1 for _, _, s, _ in all_cases if s["amount"] < 5000)
    high_value = sum(1 for _, _, s, _ in all_cases if s["amount"] >= 2499900)

    print()
    print(f"🔒 Edge cases:")
    print(f"   Opted-out customers:     {opt_out_count}")
    print(f"   Below ₹50 (min amount):  {low_amount}")
    print(f"   ₹24,999+ (high-value):   {high_value}")

    # Total revenue at risk
    total_risk = sum(c["amount_at_risk"] for c, _, _, _ in all_cases)
    total_risk_extended = sum(c["total_risk"] for c, _, _, _ in all_cases)
    print()
    print(f"💰 Revenue at risk (immediate): ₹{total_risk / 100:,.0f}")
    print(f"💰 Revenue at risk (lifetime):  ₹{total_risk_extended / 100:,.0f}")

    conn.close()
    print()
    print("✅ Batch generation complete.")


if __name__ == "__main__":
    generate_batch()
