-- ============================================================================
-- AI Revenue Recovery — SQLite Schema
-- ============================================================================
-- All monetary amounts are stored in PAISE (₹1 = 100 paise) to avoid
-- floating-point issues. Convert to ₹ only in the display/dashboard layer.
-- ============================================================================

CREATE TABLE IF NOT EXISTS customers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    phone           TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    opt_out         INTEGER NOT NULL DEFAULT 0  -- 0 = active, 1 = opted out
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                  TEXT PRIMARY KEY,
    customer_id         TEXT NOT NULL REFERENCES customers(id),
    plan_name           TEXT NOT NULL,
    amount              INTEGER NOT NULL,        -- in paise
    currency            TEXT NOT NULL DEFAULT 'INR',
    billing_cycle       TEXT NOT NULL DEFAULT 'monthly',
    remaining_cycles    INTEGER NOT NULL DEFAULT 12,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS recovery_cases (
    id                          TEXT PRIMARY KEY,
    subscription_id             TEXT NOT NULL REFERENCES subscriptions(id),
    razorpay_payment_id         TEXT,

    -- Failure details (from Razorpay error payload)
    failure_error_code          TEXT NOT NULL DEFAULT '',
    failure_error_description   TEXT NOT NULL DEFAULT '',
    failure_error_reason        TEXT NOT NULL DEFAULT '',
    failure_error_source        TEXT NOT NULL DEFAULT '',
    failure_error_step          TEXT NOT NULL DEFAULT '',

    -- Revenue at risk
    amount_at_risk              INTEGER NOT NULL DEFAULT 0,   -- immediate cycle (paise)
    total_risk                  INTEGER NOT NULL DEFAULT 0,   -- amount × remaining cycles (paise)

    -- Diagnosis
    root_cause                  TEXT,                         -- RootCause enum value
    diagnosis_confidence        REAL NOT NULL DEFAULT 0.0,
    diagnosis_method            TEXT NOT NULL DEFAULT '',     -- 'deterministic' or 'ai'

    -- Intervention
    current_intervention        TEXT,                         -- ActionType enum value
    interventions_tried         TEXT NOT NULL DEFAULT '[]',   -- JSON array of ActionType values

    -- State machine
    status                      TEXT NOT NULL DEFAULT 'detected',

    -- Counters
    attempt_count               INTEGER NOT NULL DEFAULT 0,
    communication_count         INTEGER NOT NULL DEFAULT 0,

    -- Timestamps
    created_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at                  TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at                 TEXT,

    -- Recovery outcome
    amount_recovered            INTEGER NOT NULL DEFAULT 0,   -- paise
    razorpay_payment_link_id    TEXT,
    razorpay_payment_link_url   TEXT,

    -- Terminal state details
    escalation_reason           TEXT,
    stop_reason                 TEXT
);

CREATE TABLE IF NOT EXISTS recovery_actions (
    id                  TEXT PRIMARY KEY,
    case_id             TEXT NOT NULL REFERENCES recovery_cases(id),
    action_type         TEXT NOT NULL,            -- ActionType enum value
    action_details      TEXT NOT NULL DEFAULT '{}',  -- JSON
    razorpay_response   TEXT,                     -- JSON (real API response)
    outcome             TEXT,                     -- 'success', 'failed', 'pending'
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL REFERENCES recovery_cases(id),
    timestamp       TEXT NOT NULL DEFAULT (datetime('now')),
    event_type      TEXT NOT NULL,                -- AuditEventType enum value
    actor           TEXT NOT NULL DEFAULT 'system', -- Actor enum value
    details         TEXT NOT NULL DEFAULT '{}',   -- JSON
    reasoning       TEXT NOT NULL DEFAULT ''      -- Human-readable explanation
);

-- ─── Indexes for dashboard query performance ──────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_cases_status ON recovery_cases(status);
CREATE INDEX IF NOT EXISTS idx_cases_root_cause ON recovery_cases(root_cause);
CREATE INDEX IF NOT EXISTS idx_cases_subscription ON recovery_cases(subscription_id);
CREATE INDEX IF NOT EXISTS idx_actions_case ON recovery_actions(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_subscriptions_customer ON subscriptions(customer_id);
