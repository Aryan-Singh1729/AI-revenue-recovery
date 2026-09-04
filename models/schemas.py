"""
Pydantic models for all entities in the recovery system.

Used for:
- API request/response validation
- Internal data passing between engine components
- Structured serialization
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field

from models.enums import (
    RootCause, RecoveryStatus, ActionType, PolicyResult,
    AuditEventType, Actor, BillingCycle,
)


# ─── Customer ──────────────────────────────────────────────────────────────────

class Customer(BaseModel):
    """A merchant's customer who has a subscription."""
    id: str
    name: str
    email: str
    phone: str
    created_at: datetime
    opt_out: bool = False


# ─── Subscription ──────────────────────────────────────────────────────────────

class Subscription(BaseModel):
    """A recurring subscription linked to a customer."""
    id: str
    customer_id: str
    plan_name: str
    amount: int                       # In paise (₹299 = 29900)
    currency: str = "INR"
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    remaining_cycles: int = 12
    status: str = "active"
    created_at: datetime


# ─── Recovery Case ─────────────────────────────────────────────────────────────

class RecoveryCase(BaseModel):
    """
    A single recovery case — one failed payment that the system is trying to recover.
    This is the central entity. Each case flows through the state machine.
    """
    id: str
    subscription_id: str
    razorpay_payment_id: Optional[str] = None

    # Failure details (from Razorpay error payload)
    failure_error_code: str = ""
    failure_error_description: str = ""
    failure_error_reason: str = ""
    failure_error_source: str = ""
    failure_error_step: str = ""

    # Revenue at risk
    amount_at_risk: int = 0           # In paise — immediate cycle amount
    total_risk: int = 0               # In paise — amount × remaining cycles

    # Diagnosis
    root_cause: Optional[RootCause] = None
    diagnosis_confidence: float = 0.0
    diagnosis_method: str = ""        # "deterministic" or "ai"

    # Intervention
    current_intervention: Optional[ActionType] = None
    interventions_tried: list[str] = Field(default_factory=list)

    # State machine
    status: RecoveryStatus = RecoveryStatus.DETECTED

    # Counters
    attempt_count: int = 0
    communication_count: int = 0

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None

    # Recovery outcome
    amount_recovered: int = 0         # In paise
    razorpay_payment_link_id: Optional[str] = None
    razorpay_payment_link_url: Optional[str] = None

    # Terminal state details
    escalation_reason: Optional[str] = None
    stop_reason: Optional[str] = None

    class Config:
        use_enum_values = True


# ─── Recovery Action ───────────────────────────────────────────────────────────

class RecoveryAction(BaseModel):
    """A single action taken on a recovery case (retry, payment link, dunning, etc.)."""
    id: str
    case_id: str
    action_type: ActionType
    action_details: dict = Field(default_factory=dict)
    razorpay_response: Optional[dict] = None
    outcome: Optional[str] = None     # "success", "failed", "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


# ─── Audit Entry ───────────────────────────────────────────────────────────────

class AuditEntry(BaseModel):
    """A single event in a case's audit trail — captures what, why, and by whom."""
    id: str
    case_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_type: AuditEventType
    actor: Actor
    details: dict = Field(default_factory=dict)
    reasoning: str = ""               # Human-readable explanation

    class Config:
        use_enum_values = True


# ─── API Response Models ───────────────────────────────────────────────────────

class MetricsSummary(BaseModel):
    """Aggregate recovery metrics for the dashboard hero row."""
    total_revenue_at_risk: int = 0    # Paise
    total_revenue_recovered: int = 0  # Paise
    recovery_rate: float = 0.0        # Percentage
    total_cases: int = 0
    cases_recovered: int = 0
    cases_escalated: int = 0
    cases_stopped: int = 0
    cases_unrecovered: int = 0
    cases_in_progress: int = 0
    total_actions: int = 0
    payment_links_created: int = 0
    dunning_messages_sent: int = 0


class PolicyCheckResult(BaseModel):
    """Result of running all 10 policy rules on a proposed action."""
    result: PolicyResult
    reason: Optional[str] = None
    rules_checked: list[dict] = Field(default_factory=list)
    # Each rule: {"rule": "max_retries", "threshold": 3, "current": 1, "passed": True}

    class Config:
        use_enum_values = True
