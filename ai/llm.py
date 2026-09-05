"""
LLM client — Groq-hosted open-weight models (or any OpenAI-compatible
endpoint — swap LLM_BASE_URL/LLM_MODEL in .env to point elsewhere).

Three bounded functions only:
1. diagnose_ambiguous()     — Root cause reasoning for ambiguous error codes
2. explain_intervention()   — Human-readable explanation for audit trail
3. generate_dunning_message() — Contextual, empathetic recovery message

All detection, policy, and state logic is deterministic Python — not AI.
Every function below falls back to deterministic text if the call fails
(unreachable endpoint, bad key, rate limit) — the pipeline never stalls or
breaks on an AI failure.
"""

import json
from openai import OpenAI

import config


# ─── Client Initialization ─────────────────────────────────────────────────────

_clients: dict[str, OpenAI] = {}


def _client_for(api_key: str) -> OpenAI:
    if api_key not in _clients:
        _clients[api_key] = OpenAI(
            base_url=config.LLM_BASE_URL,
            api_key=api_key,
            timeout=15.0,  # fail fast to the deterministic fallback, don't hang the UI
        )
    return _clients[api_key]


def get_client() -> OpenAI:
    """Back-compat accessor — the first configured key's client."""
    return _client_for(config.LLM_API_KEYS[0] if config.LLM_API_KEYS else config.LLM_API_KEY)


def is_configured() -> bool:
    """Check if LLM endpoint is configured."""
    return bool(config.LLM_BASE_URL and config.LLM_API_KEYS)


def _complete(messages: list[dict], temperature: float, max_tokens: int,
              response_format: dict | None = None) -> str | None:
    """
    Call chat.completions, trying each configured Groq key in turn.

    Multiple comma-separated keys in LLM_API_KEY exist to multiply the
    effective free-tier rate limit: if one key fails (exhausted, revoked,
    transient error), the next is tried before giving up. Returns None
    (never raises) once every key has failed, so callers apply their own
    deterministic fallback text — the pipeline never stalls on an AI
    failure.

    Reasoning models (gpt-oss on Groq, this project's default) spend part
    of max_tokens on an internal reasoning trace before the visible answer
    — confirmed via the API's own usage.completion_tokens_details
    (reasoning_tokens) during setup, where the default reasoning effort
    ate enough of a 200-token budget to truncate a 3-sentence message.
    reasoning_effort="low" is plenty for these three short, bounded tasks
    and leaves far more of the budget for the actual answer. Not every
    OpenAI-compatible model accepts this param, so each key is tried once
    with it and once without before moving on — keeps the "point
    LLM_BASE_URL at any compatible endpoint" promise honest.
    """
    keys = config.LLM_API_KEYS or ([config.LLM_API_KEY] if config.LLM_API_KEY else [])
    base_kwargs = {"model": config.LLM_MODEL, "messages": messages,
                   "temperature": temperature, "max_tokens": max_tokens}
    if response_format:
        base_kwargs["response_format"] = response_format
    for key in keys:
        client = _client_for(key)
        for kwargs in ({**base_kwargs, "reasoning_effort": "low"}, base_kwargs):
            try:
                response = client.chat.completions.create(**kwargs)
                return response.choices[0].message.content.strip()
            except Exception:
                continue
    return None


# ─── 1. Ambiguous Root Cause Diagnosis ─────────────────────────────────────────

def diagnose_ambiguous(
    error_code: str,
    error_description: str,
    error_reason: str,
    error_source: str,
    payment_history: str = "",
) -> dict:
    """
    Diagnose root cause for ambiguous/compound Razorpay errors.

    Returns:
        {
            "root_cause": "insufficient_funds",  # RootCause enum value
            "confidence": 0.85,
            "reasoning": "Human-readable explanation..."
        }
    """
    prompt = f"""You are a payment failure diagnosis engine for Razorpay subscriptions.

Analyze this failed payment and determine the root cause.

## Razorpay Error Payload
- error_code: {error_code}
- error_description: {error_description}
- error_reason: {error_reason}
- error_source: {error_source}

## Payment History
{payment_history or "No prior history available."}

## Valid Root Causes (pick exactly one)
- insufficient_funds: Customer's account lacks funds
- expired_card: Card has expired or is invalid
- bank_decline: Bank declined for unspecified reason
- auth_required: Payment needs 3DS/OTP authentication
- network_error: Technical/network/gateway timeout
- account_closed: Bank account is closed
- international_restriction: Cross-border payment blocked
- fraud_flag: Transaction flagged as potentially fraudulent
- disputed: Payment was disputed or charged back
- unknown: Cannot determine with confidence

## Response Format
Respond ONLY with valid JSON (no markdown, no code blocks):
{{"root_cause": "<value>", "confidence": <0.0-1.0>, "reasoning": "<one sentence>"}}
"""

    content = _complete(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=350,
        response_format={"type": "json_object"},
    )
    if content is None:
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "reasoning": "AI diagnosis unavailable (endpoint unreachable or all keys failed). Defaulting to unknown.",
        }
    try:
        result = json.loads(content)
        return {
            "root_cause": result.get("root_cause", "unknown"),
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": result.get("reasoning", "AI diagnosis — see raw output"),
        }
    except Exception as e:
        # Fallback: model returned non-JSON despite json_object mode
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "reasoning": f"AI diagnosis failed: {str(e)}. Defaulting to unknown.",
        }


# ─── 2. Intervention Explanation ───────────────────────────────────────────────

def explain_intervention(
    root_cause: str,
    selected_action: str,
    alternatives: list[str],
    amount_rupees: float,
    attempt_number: int,
) -> str:
    """
    Generate a human-readable explanation of why a specific intervention
    was selected. Used in the audit trail.

    Returns: A 1-2 sentence explanation string.
    """
    prompt = f"""You are explaining a recovery decision for a failed subscription payment.

Root cause: {root_cause}
Selected action: {selected_action}
Alternatives considered: {', '.join(alternatives) if alternatives else 'None'}
Payment amount: ₹{amount_rupees:,.0f}
Attempt number: {attempt_number}

Write a clear, concise 1-2 sentence explanation of WHY this action was chosen
for this specific root cause. Be specific about the expected outcome.
Do not use markdown formatting. Write plain text only."""

    content = _complete(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=300,
    )
    if content is not None:
        return content
    else:
        # Deterministic fallback — never block the pipeline on AI failure
        fallback_map = {
            "insufficient_funds": f"Smart retry selected — insufficient funds often resolves within 2-3 days as salary/income credits arrive.",
            "expired_card": f"Payment link selected — customer needs to enter updated card details since their current card has expired.",
            "bank_decline": f"Smart retry selected — bank declines can be transient and may succeed on a subsequent attempt.",
            "auth_required": f"Payment link selected — customer must complete authentication (3DS/OTP) which requires their direct action.",
            "network_error": f"Immediate retry selected — network/gateway errors are typically transient and resolve quickly.",
            "account_closed": f"Immediate escalation — closed bank account cannot be retried and requires merchant intervention.",
            "fraud_flag": f"Immediate escalation — fraud-flagged transactions must never be auto-recovered per policy.",
            "disputed": f"Immediate stop — disputed payments must not be recovered to maintain compliance.",
        }
        return fallback_map.get(
            root_cause,
            f"Action '{selected_action}' selected for root cause '{root_cause}' (attempt {attempt_number})."
        )


# ─── 3. Dunning Message Generation ────────────────────────────────────────────

def generate_dunning_message(
    customer_name: str,
    amount_rupees: float,
    plan_name: str,
    failure_reason: str,
    attempt_number: int,
    payment_link_url: str = "",
) -> str:
    """
    Generate a contextual, empathetic dunning message for the customer.

    Tone varies by:
    - attempt_number: 1st = friendly, 2nd = gentle urgency
    - failure_reason: each cause gets appropriate messaging
    - amount: higher amounts get more formal tone

    Returns: The message text (ready to send via email/SMS).
    """
    tone = "friendly and reassuring" if attempt_number == 1 else "polite with gentle urgency"
    link_instruction = f"\nInclude this payment link naturally in the message: {payment_link_url}" if payment_link_url else ""

    prompt = f"""Write a short recovery message (3-4 sentences max) for a customer whose
subscription payment failed.

Customer name: {customer_name}
Amount: ₹{amount_rupees:,.0f}
Plan: {plan_name}
Failure reason: {failure_reason}
Attempt number: {attempt_number} (tone should be {tone})
{link_instruction}

Rules:
- Be empathetic, not threatening
- Focus on helping them keep their access, not on debt collection
- Keep it under 4 sentences
- If a payment link is provided, include it naturally
- Use casual Indian English, not overly formal
- Do not use subject lines or email formatting — just the message body
- Do not use markdown formatting"""

    content = _complete(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=350,
    )
    if content is not None:
        return content
    else:
        # Deterministic fallback
        if payment_link_url:
            return (
                f"Hi {customer_name}, your ₹{amount_rupees:,.0f} payment for {plan_name} "
                f"didn't go through. No worries — here's a quick link to complete it: "
                f"{payment_link_url}. Takes just a moment!"
            )
        return (
            f"Hi {customer_name}, your ₹{amount_rupees:,.0f} payment for {plan_name} "
            f"didn't go through. We'd love to help you sort this out — "
            f"please check your payment method and try again."
        )
