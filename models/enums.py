"""
Enumerations for the recovery system.

All state, classification, and action types are defined here as Python enums
so they are consistent across the entire codebase — database, engine, API, dashboard.
"""

from enum import Enum


class RootCause(str, Enum):
    """Why a payment failed — classified from Razorpay error payload."""
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_CARD = "expired_card"
    BANK_DECLINE = "bank_decline"
    AUTH_REQUIRED = "auth_required"
    NETWORK_ERROR = "network_error"
    ACCOUNT_CLOSED = "account_closed"
    INTERNATIONAL_RESTRICTION = "international_restriction"
    FRAUD_FLAG = "fraud_flag"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


class RecoveryStatus(str, Enum):
    """State machine states for a recovery case."""
    DETECTED = "detected"
    DIAGNOSING = "diagnosing"
    INTERVENTION_SELECTED = "intervention_selected"
    POLICY_CHECK = "policy_check"
    EXECUTING = "executing"
    AWAITING_OUTCOME = "awaiting_outcome"
    RECOVERED = "recovered"           # Terminal ✅
    ESCALATED = "escalated"           # Terminal ⚠️
    STOPPED = "stopped"               # Terminal 🛑


class ActionType(str, Enum):
    """Types of recovery actions the system can execute."""
    SMART_RETRY = "smart_retry"
    PAYMENT_LINK = "payment_link"
    DUNNING_MESSAGE = "dunning_message"
    ESCALATION = "escalation"


class PolicyResult(str, Enum):
    """Outcome of a policy check on a proposed action."""
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    ESCALATE = "escalate"
    STOP = "stop"
    WAIT = "wait"                     # Cooldown — try again later


class AuditEventType(str, Enum):
    """Types of events logged in the audit trail."""
    CASE_CREATED = "case_created"
    ROOT_CAUSE_DIAGNOSED = "root_cause_diagnosed"
    INTERVENTION_SELECTED = "intervention_selected"
    POLICY_EVALUATED = "policy_evaluated"
    ACTION_EXECUTED = "action_executed"
    PAYMENT_LINK_CREATED = "payment_link_created"
    DUNNING_GENERATED = "dunning_generated"
    OUTCOME_OBSERVED = "outcome_observed"
    STATE_CHANGED = "state_changed"
    CASE_RECOVERED = "case_recovered"
    CASE_ESCALATED = "case_escalated"
    CASE_STOPPED = "case_stopped"


class Actor(str, Enum):
    """Who performed an action — for audit trail attribution."""
    SYSTEM = "system"
    AI = "ai"
    RAZORPAY = "razorpay"
    POLICY_ENGINE = "policy_engine"
    MERCHANT = "merchant"


class BillingCycle(str, Enum):
    """Subscription billing frequency."""
    MONTHLY = "monthly"
    YEARLY = "yearly"
    QUARTERLY = "quarterly"
    WEEKLY = "weekly"
