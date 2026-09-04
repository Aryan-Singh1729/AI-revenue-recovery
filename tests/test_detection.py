"""
Detection & Root Cause Diagnosis — Integration Test.

Runs the full detection and diagnosis pipeline:
1. Detector scans for 'detected' cases
2. Diagnoser classifies each case's root cause
3. Verifies all 50 active cases are diagnosed
4. Checks audit trail completeness
5. Reports summary statistics

Usage:
    python -m tests.test_detection
"""

from database.db import get_connection, get_cases_by_status, get_audit_trail
from models.enums import RecoveryStatus, RootCause
from engine.detector import detect_new_cases
from engine.diagnoser import diagnose_batch


def run_detection_test():
    """Execute and verify the full detection & diagnosis pipeline."""
    conn = get_connection()

    # ── Step 1: Pre-flight checks ─────────────────────────────────────────
    print("=" * 60)
    print("DETECTION & ROOT CAUSE DIAGNOSIS TEST")
    print("=" * 60)

    # Count cases before
    detected_before = get_cases_by_status(conn, RecoveryStatus.DETECTED.value)
    print(f"\n📋 Pre-flight: {len(detected_before)} cases in 'detected' status")

    if len(detected_before) == 0:
        print("⚠️  No 'detected' cases found. Has detection already run?")
        print("    To re-run, regenerate the database first:")
        print("    $env:PYTHONIOENCODING='utf-8'; Remove-Item data/recovery.db; python -m data.generate_batch; python -m data.seed_historical")
        conn.close()
        return

    # ── Step 2: Run Detector ──────────────────────────────────────────────
    print("\n" + "─" * 40)
    print("Step 1: DETECTION")
    print("─" * 40)
    enriched_cases = detect_new_cases(conn)

    # ── Step 3: Run Diagnoser ─────────────────────────────────────────────
    print("\n" + "─" * 40)
    print("Step 2: DIAGNOSIS")
    print("─" * 40)
    summary = diagnose_batch(conn, enriched_cases)

    # ── Step 4: Verification ──────────────────────────────────────────────
    print("\n" + "─" * 40)
    print("VERIFICATION")
    print("─" * 40)

    errors = []

    # Check 1: No cases should remain in 'detected' or 'diagnosing' status
    still_detected = get_cases_by_status(conn, RecoveryStatus.DETECTED.value)
    still_diagnosing = get_cases_by_status(conn, RecoveryStatus.DIAGNOSING.value)
    intervention_selected = get_cases_by_status(conn, RecoveryStatus.INTERVENTION_SELECTED.value)

    print(f"\n  Cases still in 'detected':              {len(still_detected)}")
    print(f"  Cases still in 'diagnosing':             {len(still_diagnosing)}")
    print(f"  Cases in 'intervention_selected':        {len(intervention_selected)}")

    if len(still_detected) > 0:
        errors.append(f"FAIL: {len(still_detected)} cases still in 'detected' status")
    if len(still_diagnosing) > 0:
        errors.append(f"FAIL: {len(still_diagnosing)} cases still stuck in 'diagnosing'")
    if len(intervention_selected) != len(detected_before):
        errors.append(
            f"FAIL: Expected {len(detected_before)} in 'intervention_selected', "
            f"got {len(intervention_selected)}"
        )

    # Check 2: Every processed case has a root_cause set
    null_root_cause = conn.execute(
        "SELECT COUNT(*) FROM recovery_cases WHERE id LIKE 'RC-%' AND root_cause IS NULL"
    ).fetchone()[0]
    print(f"  Active cases with NULL root_cause:       {null_root_cause}")
    if null_root_cause > 0:
        errors.append(f"FAIL: {null_root_cause} active cases still have NULL root_cause")

    # Check 3: Verify root cause distribution matches expected
    root_cause_rows = conn.execute(
        """SELECT root_cause, COUNT(*) as c
           FROM recovery_cases WHERE id LIKE 'RC-%'
           GROUP BY root_cause ORDER BY c DESC"""
    ).fetchall()
    print("\n  Root cause distribution (active cases):")
    for row in root_cause_rows:
        print(f"    {row[0]:30s} → {row[1]} cases")

    # Verify all root causes are valid enum values
    valid_causes = {rc.value for rc in RootCause}
    for row in root_cause_rows:
        if row[0] not in valid_causes:
            errors.append(f"FAIL: Invalid root_cause '{row[0]}' found in database")

    # Check 4: Verify diagnosis method distribution
    method_rows = conn.execute(
        """SELECT diagnosis_method, COUNT(*) as c
           FROM recovery_cases WHERE id LIKE 'RC-%'
           GROUP BY diagnosis_method"""
    ).fetchall()
    print("\n  Diagnosis method distribution:")
    for row in method_rows:
        print(f"    {row[0]:30s} → {row[1]} cases")

    # Check 5: Audit trail completeness — each case should have at least 3 new entries
    # (CASE_CREATED + STATE_CHANGED detected→diagnosing + ROOT_CAUSE_DIAGNOSED + STATE_CHANGED diagnosing→intervention_selected)
    sample_case_id = enriched_cases[0]["id"] if enriched_cases else None
    if sample_case_id:
        audit_entries = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE case_id = ?",
            (sample_case_id,),
        ).fetchone()[0]
        print(f"\n  Audit entries for sample case {sample_case_id}: {audit_entries}")
        if audit_entries < 3:
            errors.append(
                f"FAIL: Case {sample_case_id} has only {audit_entries} audit entries "
                f"(expected at least 3: CASE_CREATED, STATE_CHANGED×2, ROOT_CAUSE_DIAGNOSED)"
            )

    # Check 6: Total audit entries increased
    total_audit = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    print(f"  Total audit log entries (all cases):     {total_audit}")

    # New entries should be: 50 × 4 (CASE_CREATED + STATE_CHANGED + ROOT_CAUSE_DIAGNOSED + STATE_CHANGED) = 200
    new_audit_for_active = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE case_id LIKE 'RC-%'"
    ).fetchone()[0]
    print(f"  New audit entries (active cases only):    {new_audit_for_active}")
    expected_min_entries = len(detected_before) * 3  # At least 3 per case
    if new_audit_for_active < expected_min_entries:
        errors.append(
            f"FAIL: Expected at least {expected_min_entries} audit entries for active cases, "
            f"got {new_audit_for_active}"
        )

    # Check 7: Verify revenue-at-risk math
    risk_check = conn.execute(
        """SELECT rc.id, rc.amount_at_risk, rc.total_risk,
                  s.amount as sub_amount, s.remaining_cycles
           FROM recovery_cases rc
           JOIN subscriptions s ON rc.subscription_id = s.id
           WHERE rc.id LIKE 'RC-%'
           LIMIT 5"""
    ).fetchall()
    print("\n  Revenue-at-risk verification (5 samples):")
    risk_errors = 0
    for row in risk_check:
        expected_total = row[3] * row[4]  # sub_amount × remaining_cycles
        matches = "✅" if row[2] == expected_total else "❌"
        if row[2] != expected_total:
            risk_errors += 1
        print(
            f"    {row[0]}: immediate={row[1]}, total={row[2]}, "
            f"expected={expected_total} {matches}"
        )
    if risk_errors > 0:
        errors.append(f"FAIL: {risk_errors} cases have incorrect total_risk calculation")

    # ── Final Result ──────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print("❌ DETECTION VERIFICATION FAILED:")
        for err in errors:
            print(f"  • {err}")
    else:
        print("✅ DETECTION VERIFICATION PASSED — All checks successful!")
        print(f"  • {len(enriched_cases)} cases detected, diagnosed, and transitioned")
        print(f"  • {summary['by_method']['deterministic']} deterministic, {summary['by_method']['ai']} AI")
        print(f"  • {new_audit_for_active} new audit trail entries created")
        print(f"  • 0 cases with NULL root cause")
        print(f"  • Revenue-at-risk math verified")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    run_detection_test()
