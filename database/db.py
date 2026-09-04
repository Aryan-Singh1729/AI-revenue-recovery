"""
Database connection helper, migration runner, and query functions.

Uses plain sqlite3 — no ORM. Keeps things simple and debuggable.
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional
import uuid

import config


# ─── Connection Management ─────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """Get a SQLite connection with row_factory for dict-like access."""
    db_path = Path(config.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # Better concurrent read perf
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Run the schema.sql to create all tables (idempotent via IF NOT EXISTS)."""
    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text()

    conn = get_connection()
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {config.DATABASE_PATH}")


def generate_id(prefix: str = "") -> str:
    """Generate a short unique ID with optional prefix (e.g., 'RC-', 'ACT-')."""
    short_id = uuid.uuid4().hex[:8]
    return f"{prefix}{short_id}" if prefix else short_id


# ─── Customer Queries ──────────────────────────────────────────────────────────

def insert_customer(conn: sqlite3.Connection, customer: dict):
    conn.execute(
        """INSERT INTO customers (id, name, email, phone, created_at, opt_out)
           VALUES (:id, :name, :email, :phone, :created_at, :opt_out)""",
        customer,
    )


def get_customer(conn: sqlite3.Connection, customer_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    return dict(row) if row else None


def get_all_customers(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM customers").fetchall()
    return [dict(r) for r in rows]


# ─── Subscription Queries ──────────────────────────────────────────────────────

def insert_subscription(conn: sqlite3.Connection, sub: dict):
    conn.execute(
        """INSERT INTO subscriptions
           (id, customer_id, plan_name, amount, currency, billing_cycle,
            remaining_cycles, status, created_at)
           VALUES (:id, :customer_id, :plan_name, :amount, :currency,
                   :billing_cycle, :remaining_cycles, :status, :created_at)""",
        sub,
    )


def get_subscription(conn: sqlite3.Connection, sub_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (sub_id,)).fetchone()
    return dict(row) if row else None


def get_subscription_with_customer(conn: sqlite3.Connection, sub_id: str) -> Optional[dict]:
    row = conn.execute(
        """SELECT s.*, c.name as customer_name, c.email as customer_email,
                  c.phone as customer_phone, c.opt_out as customer_opt_out
           FROM subscriptions s
           JOIN customers c ON s.customer_id = c.id
           WHERE s.id = ?""",
        (sub_id,),
    ).fetchone()
    return dict(row) if row else None


# ─── Recovery Case Queries ─────────────────────────────────────────────────────

def insert_recovery_case(conn: sqlite3.Connection, case: dict):
    # Ensure interventions_tried is JSON-serialized
    if isinstance(case.get("interventions_tried"), list):
        case["interventions_tried"] = json.dumps(case["interventions_tried"])

    conn.execute(
        """INSERT INTO recovery_cases
           (id, subscription_id, razorpay_payment_id,
            failure_error_code, failure_error_description,
            failure_error_reason, failure_error_source,
            failure_error_step,
            amount_at_risk, total_risk,
            root_cause, diagnosis_confidence, diagnosis_method,
            current_intervention, interventions_tried,
            status, attempt_count, communication_count,
            created_at, updated_at, resolved_at,
            amount_recovered, razorpay_payment_link_id,
            razorpay_payment_link_url,
            escalation_reason, stop_reason)
           VALUES
           (:id, :subscription_id, :razorpay_payment_id,
            :failure_error_code, :failure_error_description,
            :failure_error_reason, :failure_error_source,
            :failure_error_step,
            :amount_at_risk, :total_risk,
            :root_cause, :diagnosis_confidence, :diagnosis_method,
            :current_intervention, :interventions_tried,
            :status, :attempt_count, :communication_count,
            :created_at, :updated_at, :resolved_at,
            :amount_recovered, :razorpay_payment_link_id,
            :razorpay_payment_link_url,
            :escalation_reason, :stop_reason)""",
        case,
    )


def get_recovery_case(conn: sqlite3.Connection, case_id: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM recovery_cases WHERE id = ?", (case_id,)).fetchone()
    if row:
        d = dict(row)
        d["interventions_tried"] = json.loads(d.get("interventions_tried", "[]"))
        return d
    return None


def get_cases_by_status(conn: sqlite3.Connection, status: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM recovery_cases WHERE status = ? ORDER BY created_at",
        (status,),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["interventions_tried"] = json.loads(d.get("interventions_tried", "[]"))
        results.append(d)
    return results


def get_all_cases(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM recovery_cases ORDER BY created_at DESC").fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["interventions_tried"] = json.loads(d.get("interventions_tried", "[]"))
        results.append(d)
    return results


def get_case_with_details(conn: sqlite3.Connection, case_id: str) -> Optional[dict]:
    """Get a case with customer and subscription details joined."""
    row = conn.execute(
        """SELECT rc.*, s.plan_name, s.amount as subscription_amount,
                  s.billing_cycle, s.remaining_cycles,
                  c.name as customer_name, c.email as customer_email,
                  c.phone as customer_phone, c.opt_out as customer_opt_out
           FROM recovery_cases rc
           JOIN subscriptions s ON rc.subscription_id = s.id
           JOIN customers c ON s.customer_id = c.id
           WHERE rc.id = ?""",
        (case_id,),
    ).fetchone()
    if row:
        d = dict(row)
        d["interventions_tried"] = json.loads(d.get("interventions_tried", "[]"))
        return d
    return None


def update_case(conn: sqlite3.Connection, case_id: str, updates: dict):
    """Update specific fields on a recovery case."""
    if "interventions_tried" in updates and isinstance(updates["interventions_tried"], list):
        updates["interventions_tried"] = json.dumps(updates["interventions_tried"])

    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["_id"] = case_id
    conn.execute(
        f"UPDATE recovery_cases SET {set_clause} WHERE id = :_id",
        updates,
    )


# ─── Recovery Action Queries ───────────────────────────────────────────────────

def insert_action(conn: sqlite3.Connection, action: dict):
    if isinstance(action.get("action_details"), dict):
        action["action_details"] = json.dumps(action["action_details"])
    if isinstance(action.get("razorpay_response"), dict):
        action["razorpay_response"] = json.dumps(action["razorpay_response"])

    conn.execute(
        """INSERT INTO recovery_actions
           (id, case_id, action_type, action_details, razorpay_response, outcome, created_at)
           VALUES (:id, :case_id, :action_type, :action_details,
                   :razorpay_response, :outcome, :created_at)""",
        action,
    )


def get_actions_for_case(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM recovery_actions WHERE case_id = ? ORDER BY created_at",
        (case_id,),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["action_details"] = json.loads(d.get("action_details", "{}"))
        if d.get("razorpay_response"):
            d["razorpay_response"] = json.loads(d["razorpay_response"])
        results.append(d)
    return results


# ─── Audit Log Queries ─────────────────────────────────────────────────────────

def insert_audit_entry(conn: sqlite3.Connection, entry: dict):
    if isinstance(entry.get("details"), dict):
        entry["details"] = json.dumps(entry["details"])

    conn.execute(
        """INSERT INTO audit_log
           (id, case_id, timestamp, event_type, actor, details, reasoning)
           VALUES (:id, :case_id, :timestamp, :event_type, :actor,
                   :details, :reasoning)""",
        entry,
    )


def get_audit_trail(conn: sqlite3.Connection, case_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit_log WHERE case_id = ? ORDER BY timestamp",
        (case_id,),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["details"] = json.loads(d.get("details", "{}"))
        results.append(d)
    return results


def get_recent_activity(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["details"] = json.loads(d.get("details", "{}"))
        results.append(d)
    return results


# ─── Metrics Queries ───────────────────────────────────────────────────────────

def get_metrics_summary(conn: sqlite3.Connection) -> dict:
    """Aggregate metrics for the dashboard hero row."""
    row = conn.execute(
        """SELECT
             COUNT(*) as total_cases,
             COALESCE(SUM(amount_at_risk), 0) as total_revenue_at_risk,
             COALESCE(SUM(amount_recovered), 0) as total_revenue_recovered,
             SUM(CASE WHEN status = 'recovered' THEN 1 ELSE 0 END) as cases_recovered,
             SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) as cases_escalated,
             SUM(CASE WHEN status = 'stopped' THEN 1 ELSE 0 END) as cases_stopped,
             SUM(CASE WHEN status NOT IN ('recovered', 'escalated', 'stopped', 'detected')
                 THEN 1 ELSE 0 END) as cases_in_progress
           FROM recovery_cases"""
    ).fetchone()

    d = dict(row)
    d["cases_unrecovered"] = (
        d["total_cases"] - d["cases_recovered"] - d["cases_escalated"]
        - d["cases_stopped"] - d["cases_in_progress"]
    )
    if d["total_revenue_at_risk"] > 0:
        d["recovery_rate"] = round(
            d["total_revenue_recovered"] / d["total_revenue_at_risk"] * 100, 1
        )
    else:
        d["recovery_rate"] = 0.0

    # Action counts
    action_row = conn.execute(
        """SELECT
             COUNT(*) as total_actions,
             SUM(CASE WHEN action_type = 'payment_link' THEN 1 ELSE 0 END) as payment_links_created,
             SUM(CASE WHEN action_type = 'dunning_message' THEN 1 ELSE 0 END) as dunning_messages_sent
           FROM recovery_actions"""
    ).fetchone()
    d.update(dict(action_row))

    return d


def get_metrics_by_root_cause(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT
             root_cause,
             COUNT(*) as total,
             SUM(CASE WHEN status = 'recovered' THEN 1 ELSE 0 END) as recovered,
             SUM(CASE WHEN status = 'escalated' THEN 1 ELSE 0 END) as escalated,
             SUM(CASE WHEN status = 'stopped' THEN 1 ELSE 0 END) as stopped,
             COALESCE(SUM(amount_at_risk), 0) as amount_at_risk,
             COALESCE(SUM(amount_recovered), 0) as amount_recovered
           FROM recovery_cases
           WHERE root_cause IS NOT NULL
           GROUP BY root_cause
           ORDER BY total DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_metrics_by_intervention(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT
             action_type,
             COUNT(*) as total,
             SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) as successful,
             SUM(CASE WHEN outcome = 'failed' THEN 1 ELSE 0 END) as failed
           FROM recovery_actions
           GROUP BY action_type
           ORDER BY total DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_policy_trigger_stats(conn: sqlite3.Connection) -> list[dict]:
    """
    How many times each of the 10 policy rules was evaluated and how many
    times it blocked an action. Drives the Stopping Rules table (criterion 3).

    Walks the `all_rules` array that policy_engine.check_policy() writes into
    every POLICY_EVALUATED audit entry, so the counts come from the same
    evidence a judge can expand on any individual case.
    """
    rows = conn.execute(
        """SELECT
             json_extract(r.value, '$.rule_number')     AS rule_number,
             json_extract(r.value, '$.rule_name')       AS rule_name,
             json_extract(r.value, '$.threshold')       AS threshold,
             json_extract(r.value, '$.action_if_failed') AS action_if_failed,
             COUNT(*)                                   AS times_evaluated,
             SUM(CASE WHEN json_extract(r.value, '$.passed') = 0
                      THEN 1 ELSE 0 END)                AS times_triggered,
             MIN(CASE WHEN json_extract(r.value, '$.passed') = 0
                      THEN a.case_id END)               AS example_case_id
           FROM audit_log a,
                json_each(json_extract(a.details, '$.all_rules')) r
           WHERE a.event_type = 'policy_evaluated'
             AND json_extract(a.details, '$.all_rules') IS NOT NULL
           GROUP BY rule_number, rule_name
           ORDER BY rule_number"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_escalated_cases(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT rc.*, c.name as customer_name, s.plan_name, s.amount as subscription_amount
           FROM recovery_cases rc
           JOIN subscriptions s ON rc.subscription_id = s.id
           JOIN customers c ON s.customer_id = c.id
           WHERE rc.status = 'escalated'
           ORDER BY rc.amount_at_risk DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_stopped_cases(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT rc.*, c.name as customer_name, s.plan_name, s.amount as subscription_amount
           FROM recovery_cases rc
           JOIN subscriptions s ON rc.subscription_id = s.id
           JOIN customers c ON s.customer_id = c.id
           WHERE rc.status = 'stopped'
           ORDER BY rc.created_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]
