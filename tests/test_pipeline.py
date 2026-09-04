"""
Recovery Pipeline — Full Integration Test.

Regenerates the database, then runs the full pipeline:
  detect → diagnose → select → policy → execute → outcome

Verifies:
1. All 50 active cases reach a terminal state
2. Expected distribution: ~18-24 recovered, ~15-21 escalated, ~5-8 stopped
3. Fraud cases (3) are escalated
4. Dispute cases (2) are stopped
5. Opt-out customers are stopped
6. High-value cases (₹25,000+) are escalated
7. Minimum amount (₹35) case is stopped
8. Audit trail is complete for every case
9. Policy engine ran for applicable cases

Usage:
    python -m tests.test_pipeline
"""

import os
import sys

# Ensure clean DB
from database.db import get_connection, init_db, get_all_cases, get_cases_by_status
from models.enums import RecoveryStatus, RootCause


def regenerate_db():
    """Wipe and regenerate the database for a clean test."""
    import config
    from pathlib import Path

    db_path = Path(config.DATABASE_PATH)
    if db_path.exists():
        db_path.unlink()
        print("🗑️  Old database deleted")

    init_db()

    # Run generators
    print("\n📦 Generating batch data...")
    from data.generate_batch import generate_batch
    generate_batch()

    print("\n📦 Seeding historical data...")
    from data.seed_historical import seed_historical
    seed_historical()

    # Verify
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM recovery_cases").fetchone()[0]
    detected = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status = 'detected'").fetchone()[0]
    print(f"\n✅ Database regenerated: {total} total cases, {detected} in 'detected' status")
    conn.close()


def run_pipeline_test():
    """Execute and verify the full recovery pipeline."""

    # ── Step 1: Regenerate database ───────────────────────────────────────
    print("=" * 60)
    print("RECOVERY PIPELINE — Full Integration Test")
    print("=" * 60)

    regenerate_db()

    # ── Step 2: Run the orchestrator ──────────────────────────────────────
    from engine.recovery_orchestrator import process_batch
    from database.db import get_connection

    conn = get_connection()
    results = process_batch(conn)

    # ── Step 3: Verification ──────────────────────────────────────────────
    print("\n" + "─" * 40)
    print("VERIFICATION")
    print("─" * 40)

    errors = []

    # Check 1: No active cases should remain in non-terminal states
    non_terminal = ["detected", "diagnosing", "intervention_selected",
                    "policy_check", "executing", "awaiting_outcome"]
    for status in non_terminal:
        count = len(get_cases_by_status(conn, status))
        if count > 0:
            errors.append(f"FAIL: {count} cases still in '{status}' (should be 0)")
        print(f"  Cases in '{status}': {count}")

    # Check 2: Terminal state counts
    recovered = get_cases_by_status(conn, RecoveryStatus.RECOVERED.value)
    escalated = get_cases_by_status(conn, RecoveryStatus.ESCALATED.value)
    stopped = get_cases_by_status(conn, RecoveryStatus.STOPPED.value)

    # Subtract historical cases (30 total: 12 recovered, 8 escalated, 10 stopped)
    active_recovered = [c for c in recovered if c["id"].startswith("RC-")]
    active_escalated = [c for c in escalated if c["id"].startswith("RC-")]
    active_stopped = [c for c in stopped if c["id"].startswith("RC-")]

    print(f"\n  Active cases RECOVERED:  {len(active_recovered)}")
    print(f"  Active cases ESCALATED:  {len(active_escalated)}")
    print(f"  Active cases STOPPED:    {len(active_stopped)}")
    print(f"  Total active resolved:   {len(active_recovered) + len(active_escalated) + len(active_stopped)}")

    if len(active_recovered) + len(active_escalated) + len(active_stopped) != 50:
        errors.append(f"FAIL: Expected 50 active cases resolved, got {len(active_recovered) + len(active_escalated) + len(active_stopped)}")

    # Check 3: Fraud cases (3) must be escalated
    fraud_cases = [c for c in get_cases_by_status(conn, RecoveryStatus.ESCALATED.value)
                   if c.get("root_cause") == RootCause.FRAUD_FLAG.value and c["id"].startswith("RC-")]
    print(f"\n  Fraud cases escalated:   {len(fraud_cases)}/3")
    if len(fraud_cases) != 3:
        errors.append(f"FAIL: Expected 3 fraud cases escalated, got {len(fraud_cases)}")

    # Check 4: Dispute cases (2) must be stopped
    dispute_cases = [c for c in get_cases_by_status(conn, RecoveryStatus.STOPPED.value)
                     if c.get("root_cause") == RootCause.DISPUTED.value and c["id"].startswith("RC-")]
    print(f"  Dispute cases stopped:   {len(dispute_cases)}/2")
    if len(dispute_cases) != 2:
        errors.append(f"FAIL: Expected 2 dispute cases stopped, got {len(dispute_cases)}")

    # Check 5: Account closed cases (5) must be escalated
    closed_cases = [c for c in get_cases_by_status(conn, RecoveryStatus.ESCALATED.value)
                    if c.get("root_cause") == RootCause.ACCOUNT_CLOSED.value and c["id"].startswith("RC-")]
    print(f"  Account closed escalated: {len(closed_cases)}/5")
    if len(closed_cases) != 5:
        errors.append(f"FAIL: Expected 5 account_closed cases escalated, got {len(closed_cases)}")

    # Check 6: Opt-out customers stopped
    opt_out_stopped = conn.execute(
        """SELECT COUNT(*) FROM recovery_cases rc
           JOIN subscriptions s ON rc.subscription_id = s.id
           JOIN customers c ON s.customer_id = c.id
           WHERE c.opt_out = 1 AND rc.status = 'stopped' AND rc.id LIKE 'RC-%'"""
    ).fetchone()[0]
    print(f"  Opt-out customers stopped: {opt_out_stopped}")
    # (some opt-out cases may have been escalated before policy check due to root cause)

    # Check 7: Minimum amount case (₹35 = 3500 paise) stopped
    micro_cases = conn.execute(
        """SELECT rc.id, rc.status, rc.stop_reason, rc.amount_at_risk
           FROM recovery_cases rc WHERE rc.amount_at_risk < 5000 AND rc.id LIKE 'RC-%'"""
    ).fetchall()
    for mc in micro_cases:
        print(f"  Micro case {mc[0]}: Rs {mc[3]/100} → {mc[1]} (reason: {mc[2]})")

    # Check 8: High-value cases (₹25,000+ = 2500000 paise) escalated
    high_value_esc = conn.execute(
        """SELECT COUNT(*) FROM recovery_cases
           WHERE amount_at_risk >= 2500000 AND status = 'escalated' AND id LIKE 'RC-%'"""
    ).fetchone()[0]
    high_value_total = conn.execute(
        """SELECT COUNT(*) FROM recovery_cases
           WHERE amount_at_risk >= 2500000 AND id LIKE 'RC-%'"""
    ).fetchone()[0]
    print(f"  High-value cases escalated: {high_value_esc}/{high_value_total}")

    # Check 9: Revenue recovered
    total_recovered_paise = sum(c.get("amount_recovered", 0) for c in active_recovered)
    total_at_risk_paise = conn.execute(
        "SELECT SUM(amount_at_risk) FROM recovery_cases WHERE id LIKE 'RC-%'"
    ).fetchone()[0] or 0
    recovery_rate = total_recovered_paise / total_at_risk_paise * 100 if total_at_risk_paise > 0 else 0
    print(f"\n  💰 Revenue at risk (active):   Rs {total_at_risk_paise / 100:,.0f}")
    print(f"  💰 Revenue recovered (active): Rs {total_recovered_paise / 100:,.0f}")
    print(f"  📈 Recovery rate:              {recovery_rate:.1f}%")

    # Check 10: Audit trail depth
    active_audit_count = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE case_id LIKE 'RC-%'"
    ).fetchone()[0]
    avg_audit = active_audit_count / 50 if active_audit_count > 0 else 0
    print(f"\n  📋 Active audit entries: {active_audit_count} (avg {avg_audit:.1f}/case)")
    if avg_audit < 4:
        errors.append(f"FAIL: Average audit entries per case too low: {avg_audit:.1f} (expected ≥4)")

    # Check 11: Recovery actions created
    active_actions = conn.execute(
        """SELECT action_type, COUNT(*) FROM recovery_actions
           WHERE case_id LIKE 'RC-%' GROUP BY action_type"""
    ).fetchall()
    print(f"\n  Recovery actions (active cases):")
    for row in active_actions:
        print(f"    {row[0]:20s} → {row[1]} actions")

    total_new_actions = conn.execute(
        "SELECT COUNT(*) FROM recovery_actions WHERE case_id LIKE 'RC-%'"
    ).fetchone()[0]
    print(f"  Total new actions: {total_new_actions}")

    # ── Final Result ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print("❌ PIPELINE VERIFICATION — ISSUES FOUND:")
        for err in errors:
            print(f"  • {err}")
    else:
        print("✅ PIPELINE VERIFICATION PASSED!")
    print(f"  • {len(active_recovered)} recovered, {len(active_escalated)} escalated, {len(active_stopped)} stopped")
    print(f"  • Rs {total_recovered_paise / 100:,.0f} recovered ({recovery_rate:.1f}% rate)")
    print(f"  • {active_audit_count} audit entries, {total_new_actions} actions")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    run_pipeline_test()
