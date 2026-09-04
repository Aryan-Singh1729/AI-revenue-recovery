"""
End-to-End Acceptance Test — the final gate before a demo.

Regenerates the database from scratch, runs the complete pipeline, and
verifies the properties a judge would actually check by hand:

  1. Every case reaches a terminal state (recovered / escalated / stopped).
  2. Every state transition on record is a legal edge in the state machine's
     transition graph — checked against the audit trail itself, not just by
     trusting that transition() didn't raise.
  3. Every case has a complete audit trail: a creation event and a terminal
     event that matches its final status.
  4. recovered + escalated + stopped == total (no case lost or double counted).
  5. Payment links are real Razorpay API objects when Razorpay is configured,
     or clearly-labelled simulated links when it isn't.
  6. No policy violations: zero automated actions on fraud-flagged or
     disputed cases, zero communications to opted-out customers, money is
     only ever attributed to cases that actually reached RECOVERED.
  7. The specific edge cases the batch generator plants are provably handled:
     below-minimum amount, opt-out, fraud, high-value, disputed, network
     error's high retry success rate, and the retry-count cap.

Usage:
    python -m tests.test_e2e
"""

import sys

import config
import razorpay_client
from database.db import get_connection, init_db, get_cases_by_status, get_all_cases
from engine.attribution import get_attribution_metrics
from engine.recovery_orchestrator import process_batch
from engine.state_machine import VALID_TRANSITIONS
from models.enums import RecoveryStatus

TERMINAL = {RecoveryStatus.RECOVERED.value, RecoveryStatus.ESCALATED.value, RecoveryStatus.STOPPED.value}
NON_TERMINAL = {
    RecoveryStatus.DETECTED.value, RecoveryStatus.DIAGNOSING.value,
    RecoveryStatus.INTERVENTION_SELECTED.value, RecoveryStatus.POLICY_CHECK.value,
    RecoveryStatus.EXECUTING.value, RecoveryStatus.AWAITING_OUTCOME.value,
}

TERMINAL_EVENT_FOR_STATUS = {
    "recovered": "case_recovered",
    "escalated": "case_escalated",
    "stopped": "case_stopped",
}


def regenerate_db():
    """Wipe and regenerate the database for a clean run."""
    from pathlib import Path
    db_path = Path(config.DATABASE_PATH)
    if db_path.exists():
        db_path.unlink()
        print("Old database deleted")
    init_db()

    print("\nGenerating batch data...")
    from data.generate_batch import generate_batch
    generate_batch()

    print("\nSeeding historical data...")
    from data.seed_historical import seed_historical
    seed_historical()


def check_terminal_states(conn, errors: list) -> dict:
    """(1) Every case reaches a terminal state."""
    print("\n--- 1. Terminal states ---")
    counts = {}
    for status in NON_TERMINAL:
        n = len(get_cases_by_status(conn, status))
        if n > 0:
            errors.append(f"FAIL: {n} cases stuck in non-terminal status '{status}'")
        print(f"  {status:24s} {n}")
    for status in TERMINAL:
        counts[status] = len(get_cases_by_status(conn, status))
        print(f"  {status:24s} {counts[status]}")
    return counts


def check_legal_transitions(conn, errors: list):
    """(2) Every logged state transition is a legal edge in the graph."""
    print("\n--- 2. State transition legality (audited, not just trusted) ---")
    rows = conn.execute(
        """SELECT case_id, timestamp, details FROM audit_log
           WHERE event_type = 'state_changed' ORDER BY case_id, timestamp"""
    ).fetchall()
    import json
    illegal = 0
    checked = 0
    for r in rows:
        d = json.loads(r["details"])
        frm, to = d.get("from_status"), d.get("to_status")
        checked += 1
        try:
            valid_targets = {t.value for t in VALID_TRANSITIONS[RecoveryStatus(frm)]}
        except (KeyError, ValueError):
            illegal += 1
            errors.append(f"FAIL: {r['case_id']} logged an unknown status '{frm}'")
            continue
        if to not in valid_targets:
            illegal += 1
            errors.append(f"FAIL: {r['case_id']} logged illegal transition {frm} -> {to}")
    print(f"  {checked} transitions checked, {illegal} illegal")
    if illegal == 0:
        print("  All logged transitions are legal edges in the state machine.")


def check_audit_completeness(conn, errors: list):
    """(3) Every case has a creation event and a terminal event matching its status."""
    print("\n--- 3. Audit trail completeness ---")
    cases = get_all_cases(conn)
    missing_creation = 0
    missing_terminal = 0
    for case in cases:
        events = {r["event_type"] for r in conn.execute(
            "SELECT event_type FROM audit_log WHERE case_id = ?", (case["id"],)
        ).fetchall()}
        if "case_created" not in events:
            missing_creation += 1
            errors.append(f"FAIL: {case['id']} has no case_created audit event")
        if case["status"] in TERMINAL_EVENT_FOR_STATUS:
            expected = TERMINAL_EVENT_FOR_STATUS[case["status"]]
            if expected not in events:
                missing_terminal += 1
                errors.append(f"FAIL: {case['id']} is '{case['status']}' but has no {expected} event")
    print(f"  {len(cases)} cases checked")
    print(f"  Missing case_created: {missing_creation}")
    print(f"  Missing matching terminal event: {missing_terminal}")


def check_metrics_math(counts: dict, total: int, errors: list):
    """(4) recovered + escalated + stopped == total."""
    print("\n--- 4. Metrics math ---")
    summed = sum(counts.values())
    print(f"  recovered({counts.get('recovered', 0)}) + escalated({counts.get('escalated', 0)}) "
          f"+ stopped({counts.get('stopped', 0)}) = {summed}  vs total = {total}")
    if summed != total:
        errors.append(f"FAIL: recovered+escalated+stopped ({summed}) != total cases ({total})")


def check_payment_links(conn, errors: list):
    """(5) Payment links are real when Razorpay is configured, honestly labelled otherwise."""
    print("\n--- 5. Payment links ---")
    rows = conn.execute(
        """SELECT id, razorpay_payment_link_id, razorpay_payment_link_url
           FROM recovery_cases WHERE razorpay_payment_link_id IS NOT NULL"""
    ).fetchall()
    print(f"  {len(rows)} cases have a payment link")
    if not rows:
        print("  (no payment links were created in this run)")
        return

    if razorpay_client.is_configured():
        print("  Razorpay is configured — verifying at least one link via the real API")
        sample = rows[0]
        result = razorpay_client.fetch_payment_link(sample["razorpay_payment_link_id"])
        if not result.get("success"):
            errors.append(f"FAIL: could not fetch real payment link {sample['razorpay_payment_link_id']}: "
                          f"{result.get('error')}")
        else:
            print(f"  Verified {sample['id']}'s link is a real Razorpay object: "
                  f"{sample['razorpay_payment_link_url']}")
    else:
        print("  Razorpay is NOT configured in this environment (no RAZORPAY_KEY_ID/SECRET) — "
              "links are simulated by design (see razorpay_client.is_configured()).")
        bad = [r for r in rows if not (r["razorpay_payment_link_url"] or "").startswith("https://rzp.io/i/")]
        if bad:
            errors.append(f"FAIL: {len(bad)} simulated links don't match the expected rzp.io/i/ URL shape")
        else:
            print(f"  All {len(rows)} simulated links follow the correct rzp.io/i/<id> shape.")


def check_no_policy_violations(conn, errors: list):
    """(6) Fraud/dispute cases got zero automated actions; opt-outs got zero comms; attribution is clean."""
    print("\n--- 6. Policy violations ---")
    fraud_actions = conn.execute(
        """SELECT COUNT(*) FROM recovery_actions a JOIN recovery_cases c ON a.case_id = c.id
           WHERE c.root_cause = 'fraud_flag' AND a.action_type != 'escalation'"""
    ).fetchone()[0]
    dispute_actions = conn.execute(
        """SELECT COUNT(*) FROM recovery_actions a JOIN recovery_cases c ON a.case_id = c.id
           WHERE c.root_cause = 'disputed' AND a.action_type != 'escalation'"""
    ).fetchone()[0]
    optout_comms = conn.execute(
        """SELECT COUNT(*) FROM recovery_actions a
           JOIN recovery_cases c ON a.case_id = c.id
           JOIN subscriptions s ON c.subscription_id = s.id
           JOIN customers cu ON s.customer_id = cu.id
           WHERE cu.opt_out = 1 AND a.action_type IN ('dunning_message', 'payment_link', 'smart_retry')"""
    ).fetchone()[0]
    leaked_attribution = conn.execute(
        "SELECT COUNT(*) FROM recovery_cases WHERE status != 'recovered' AND amount_recovered > 0"
    ).fetchone()[0]

    print(f"  Automated actions on fraud-flagged cases: {fraud_actions} (must be 0)")
    print(f"  Automated actions on disputed cases:       {dispute_actions} (must be 0)")
    print(f"  Communications to opted-out customers:     {optout_comms} (must be 0)")
    print(f"  Non-recovered cases with attributed money: {leaked_attribution} (must be 0)")

    if fraud_actions:
        errors.append(f"FAIL: {fraud_actions} automated actions taken on fraud-flagged cases")
    if dispute_actions:
        errors.append(f"FAIL: {dispute_actions} automated actions taken on disputed cases")
    if optout_comms:
        errors.append(f"FAIL: {optout_comms} communications sent to opted-out customers")
    if leaked_attribution:
        errors.append(f"FAIL: {leaked_attribution} non-recovered cases carry attributed money")


def check_edge_cases(conn, errors: list):
    """(7) The specific scenarios the plan calls out are provably handled."""
    print("\n--- 7. Edge cases (active batch only, id LIKE 'RC-%') ---")

    # Below-minimum amount -> stopped by rule 4.
    micro = conn.execute(
        "SELECT id, status, stop_reason FROM recovery_cases "
        "WHERE amount_at_risk < ? AND id LIKE 'RC-%'", (config.MIN_RECOVERY_AMOUNT * 100,)
    ).fetchall()
    print(f"  Below-minimum-amount cases: {len(micro)}")
    if not micro:
        errors.append("FAIL: no below-minimum case in the batch")
    for m in micro:
        ok = m["status"] == "stopped" and "Minimum Viable Amount" in (m["stop_reason"] or "")
        print(f"    {m['id']}: {m['status']} ({m['stop_reason']}) {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"FAIL: below-minimum case {m['id']} not stopped by the minimum-amount rule")

    # Opt-out -> stopped, no actions beyond what policy allowed before the block.
    optout = conn.execute(
        """SELECT rc.id, rc.status, rc.stop_reason FROM recovery_cases rc
           JOIN subscriptions s ON rc.subscription_id = s.id
           JOIN customers c ON s.customer_id = c.id
           WHERE c.opt_out = 1 AND rc.id LIKE 'RC-%'"""
    ).fetchall()
    print(f"  Opted-out customer cases: {len(optout)}")
    if len(optout) != 3:
        errors.append(f"FAIL: expected 3 opted-out cases in the active batch, found {len(optout)}")
    for o in optout:
        ok = o["status"] == "stopped" and "Opt-Out" in (o["stop_reason"] or "")
        print(f"    {o['id']}: {o['status']} ({o['stop_reason']}) {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"FAIL: opted-out case {o['id']} not stopped by the opt-out rule")

    # Fraud -> escalated immediately, zero interventions tried.
    fraud = conn.execute(
        "SELECT id, status, interventions_tried FROM recovery_cases "
        "WHERE root_cause = 'fraud_flag' AND id LIKE 'RC-%'"
    ).fetchall()
    print(f"  Fraud-flagged cases: {len(fraud)}")
    if len(fraud) != 3:
        errors.append(f"FAIL: expected 3 fraud cases in the active batch, found {len(fraud)}")
    import json
    for f in fraud:
        tried = json.loads(f["interventions_tried"])
        ok = f["status"] == "escalated" and not tried
        print(f"    {f['id']}: {f['status']}, tried={tried} {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"FAIL: fraud case {f['id']} was not escalated immediately with zero attempts")

    # High value -> escalated for human review.
    high_value = conn.execute(
        "SELECT id, status FROM recovery_cases WHERE amount_at_risk >= ? AND id LIKE 'RC-%'",
        (config.HIGH_VALUE_THRESHOLD * 100,)
    ).fetchall()
    print(f"  High-value (>= Rs {config.HIGH_VALUE_THRESHOLD:,}) cases: {len(high_value)}")
    if not high_value:
        errors.append("FAIL: no high-value case in the batch")
    for h in high_value:
        ok = h["status"] == "escalated"
        print(f"    {h['id']}: {h['status']} {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"FAIL: high-value case {h['id']} was not escalated for human review")

    # Disputed -> stopped, never recovered, zero automated actions.
    disputed = conn.execute(
        "SELECT id, status, amount_recovered FROM recovery_cases "
        "WHERE root_cause = 'disputed' AND id LIKE 'RC-%'"
    ).fetchall()
    print(f"  Disputed cases: {len(disputed)}")
    if len(disputed) != 2:
        errors.append(f"FAIL: expected 2 disputed cases in the active batch, found {len(disputed)}")
    for d in disputed:
        ok = d["status"] == "stopped" and d["amount_recovered"] == 0
        print(f"    {d['id']}: {d['status']}, recovered={d['amount_recovered']} {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"FAIL: disputed case {d['id']} was not cleanly stopped")

    # Network error -> high retry success rate (directional, not exact — the
    # outcome simulator is probabilistic even though it's seeded).
    network = conn.execute(
        "SELECT status FROM recovery_cases WHERE root_cause = 'network_error' AND id LIKE 'RC-%'"
    ).fetchall()
    if network:
        recovered_n = sum(1 for n in network if n["status"] == "recovered")
        rate = recovered_n / len(network) * 100
        print(f"  Network-error cases: {recovered_n}/{len(network)} recovered ({rate:.0f}%)")
        if rate < 50:
            errors.append(f"FAIL: network-error recovery rate too low ({rate:.0f}%) — "
                          f"immediate retry should succeed most of the time")

    # Retry cap: no case ever exceeds MAX_RETRY_ATTEMPTS actual smart_retry actions.
    over_retried = conn.execute(
        """SELECT case_id, COUNT(*) n FROM recovery_actions
           WHERE action_type = 'smart_retry' AND case_id LIKE 'RC-%'
           GROUP BY case_id HAVING n > ?""", (config.MAX_RETRY_ATTEMPTS,)
    ).fetchall()
    print(f"  Cases exceeding the {config.MAX_RETRY_ATTEMPTS}-retry cap: {len(over_retried)}")
    if over_retried:
        errors.append(f"FAIL: {len(over_retried)} cases exceeded the max-retry cap "
                      f"— {[r['case_id'] for r in over_retried]}")

    # The rule-1 planted case: pre-loaded with 3 retries already spent, so the
    # very first proposed retry must be refused, not silently allowed.
    preloaded = conn.execute(
        "SELECT id, status, stop_reason FROM recovery_cases "
        "WHERE attempt_count >= ? AND id LIKE 'RC-%'", (config.MAX_RETRY_ATTEMPTS,)
    ).fetchall()
    for p in preloaded:
        actual_retries = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions WHERE case_id = ? AND action_type = 'smart_retry'",
            (p["id"],)
        ).fetchone()[0]
        ok = actual_retries == 0 and p["status"] == "stopped"
        print(f"    Rule-1 planted case {p['id']}: {p['status']}, "
              f"new retries dispatched={actual_retries} {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"FAIL: case {p['id']} started with {config.MAX_RETRY_ATTEMPTS} retries already "
                          f"spent but still dispatched a new one, or wasn't stopped")


def run():
    print("=" * 60)
    print("END-TO-END ACCEPTANCE TEST")
    print("=" * 60)

    regenerate_db()

    conn = get_connection()
    process_batch(conn)

    errors: list[str] = []

    counts = check_terminal_states(conn, errors)
    check_legal_transitions(conn, errors)
    check_audit_completeness(conn, errors)
    total = len(get_all_cases(conn))
    check_metrics_math(counts, total, errors)
    check_payment_links(conn, errors)
    check_no_policy_violations(conn, errors)
    check_edge_cases(conn, errors)

    m = get_attribution_metrics(conn)["summary"]

    conn.close()

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  {m['total_cases_tracked']} cases | "
          f"{m['cases_recovered']} recovered | {m['cases_escalated']} escalated | "
          f"{m['cases_stopped']} stopped")
    print(f"  Rs {m['revenue_at_risk_paise']/100:,.0f} at risk -> "
          f"Rs {m['revenue_recovered_paise']/100:,.0f} recovered "
          f"({m['recovery_rate_percent']}%)")

    if errors:
        print(f"\nFAILED — {len(errors)} issue(s):")
        for e in errors:
            print(f"  - {e}")
        return False

    print("\nE2E ACCEPTANCE TEST PASSED — ready for judges.")
    return True


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
