"""
Razorpay SDK wrapper — provides helper methods for recovery actions.

Uses Razorpay Python SDK in test mode. Only two real API calls matter:
1. create_payment_link() — creates a real Payment Link for card-update / retry
2. create_payment()      — attempts a test-mode payment (smart retry)
"""

import time
import razorpay

import config


# ─── Client Initialization ─────────────────────────────────────────────────────

_client: razorpay.Client | None = None


def get_client() -> razorpay.Client:
    """Get or create the Razorpay client singleton."""
    global _client
    if _client is None:
        if not config.RAZORPAY_KEY_ID or not config.RAZORPAY_KEY_SECRET:
            raise ValueError(
                "Razorpay API keys not set. "
                "Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to your .env file."
            )
        _client = razorpay.Client(
            auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET)
        )
    return _client


def _looks_like_placeholder(value: str) -> bool:
    """
    Detect the "rzp_test_XXXXXXXXXX" / "XXXXXXXXXXXXXXXX" style placeholder
    values shipped in .env.example — a long run of the literal letter X is
    not a pattern any real Razorpay key or secret can contain.
    """
    return "XXXXX" in value.upper()


def is_configured() -> bool:
    """
    Check whether real Razorpay keys are present.

    A bare non-empty check previously accepted the .env.example placeholder
    strings as "configured", which made executor.py attempt a real API call
    with garbage credentials on every action — a network round trip that was
    always going to fail auth, before silently falling back to a simulated
    response. That also produced a dishonest audit trail: the reasoning text
    said "Real Razorpay order created" for a call that never succeeded (see
    engine/executor.py's actor/reasoning logic, which was fixed alongside
    this to check the actual response instead of this flag alone).
    """
    key, secret = config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET
    if not key or not secret:
        return False
    if _looks_like_placeholder(key) or _looks_like_placeholder(secret):
        return False
    return True


# ─── Payment Links ─────────────────────────────────────────────────────────────

def create_payment_link(
    amount_paise: int,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    description: str,
    reference_id: str = "",
    expiry_days: int = None,
) -> dict:
    """
    Create a real Razorpay Payment Link (test mode).

    Returns dict with:
      - id: plink_XXXX
      - short_url: https://rzp.io/i/XXXX  (the clickable link)
      - amount, status, etc.
    """
    if expiry_days is None:
        expiry_days = config.PAYMENT_LINK_EXPIRY_DAYS

    client = get_client()

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "customer": {
            "name": customer_name,
            "email": customer_email,
            "contact": customer_phone,
        },
        "notify": {
            "sms": False,    # We control messaging ourselves
            "email": False,
        },
        "expire_by": int(time.time()) + (expiry_days * 24 * 3600),
        "notes": {
            "source": "ai_revenue_recovery",
            "reference_id": reference_id,
        },
    }

    try:
        response = client.payment_link.create(payload)
        return {
            "success": True,
            "id": response.get("id"),
            "short_url": response.get("short_url"),
            "amount": response.get("amount"),
            "status": response.get("status"),
            "raw_response": response,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "raw_response": None,
        }


# ─── Payments (Smart Retry) ───────────────────────────────────────────────────

def create_test_payment(amount_paise: int, description: str = "Recovery retry") -> dict:
    """
    Attempt a test-mode payment (simulates a smart retry).

    In real production, this would retry the subscription charge.
    In test mode, we create a payment order to show real API interaction.
    """
    client = get_client()

    try:
        # Create an order first (required by Razorpay)
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "notes": {
                "source": "ai_revenue_recovery",
                "type": "smart_retry",
            },
        })
        return {
            "success": True,
            "order_id": order.get("id"),
            "amount": order.get("amount"),
            "status": order.get("status"),
            "raw_response": order,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "raw_response": None,
        }


# ─── Fetch Payment Details ────────────────────────────────────────────────────

def fetch_payment(payment_id: str) -> dict:
    """Fetch details of a specific payment by ID."""
    client = get_client()
    try:
        payment = client.payment.fetch(payment_id)
        return {"success": True, "payment": payment}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_payment_link(link_id: str) -> dict:
    """Fetch details of a payment link by ID."""
    client = get_client()
    try:
        link = client.payment_link.fetch(link_id)
        return {"success": True, "link": link}
    except Exception as e:
        return {"success": False, "error": str(e)}
