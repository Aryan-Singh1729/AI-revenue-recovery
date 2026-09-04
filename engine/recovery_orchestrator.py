"""
Recovery Orchestrator — master loop connecting all engine components.

Processes cases through the complete recovery pipeline:
  detect → diagnose → select intervention → policy check → execute → outcome

Split into two public halves so the live demo can show them as two distinct,
visible moments (the agent deciding/acting, then the world responding):

  dispatch_next_action(conn, case) — select the next intervention, run it
      through all 10 policy rules, execute it if allowed. Leaves the case in
      AWAITING_OUTCOME (or a terminal state if policy blocked it / no
      automated path exists). Never simulates an outcome.

  resolve_outcome(conn, case) — for a case in AWAITING_OUTCOME, simulate the
      customer's response. On failure it calls dispatch_next_action() again
      for the next intervention in the sequence and simulates that too,
      looping until the case reaches a terminal state or the safety bound
      is hit.

process_case() composes both halves for batch/CLI use, where there is no UI
moment to preserve — it is unchanged in behavior and return shape.
"""

import sqlite3
from datetime import datetime

from database.db import (
    get_connection,
    get_cases_by_status,
    get_case_with_details,
    update_case,
)
from models.enums import (
    RootCause, RecoveryStatus, ActionType, PolicyResult,
    AuditEventType, Actor,
)
from engine.audit import log_event
from engine.detector import detect_new_cases
from engine.diagnoser import diagnose_batch
from engine.intervention_selector import (
    select_intervention,
    get_intervention_sequence,
)
from engine.policy_engine import check_policy
from engine.executor import execute_action, execute_escalation
from engine.outcome_simulator import simulate_outcome
from engine.state_machine import transition

MAX_ROUNDS = 5  # Safety bound on interventions-per-case, shared by both halves.


def _root_cause(case: dict) -> RootCause:
    try:
        return RootCause(case.get("root_cause", "unknown"))
    except ValueError:
        return RootCause.UNKNOWN


def dispatch_next_action(conn: sqlite3.Connection, case: dict) -> dict:
    """
    Advance a case by exactly one step: pick the next intervention (if any),
    run it through all 10 policy rules, and execute it if allowed.

    Returns:
        {
            "case_id": str,
            "outcome": "awaiting_outcome" | "escalated" | "stopped",
            "root_cause": str,
            "selected_action": str | None,        # ActionType value, if one was proposed
            "intervention_step": str | None,       # e.g. "1/3"
            "policy_result": str | None,           # PolicyResult value
            "policy_summary": str | None,          # e.g. "10/10 rules passed"
            "action_result": dict | None,          # executor's return value
            "reason": str,
        }
    """
    case_id = case["id"]
    root_cause = _root_cause(case)
    current_status = case["status"]
    interventions_tried = list(case.get("interventions_tried", []))

    next_action = select_intervention(root_cause, interventions_tried)

    if next_action is None:
        # No (further) automated intervention available. Two distinct cases:
        #   a) nothing was ever tried  -> root cause has no automated path
        #      (fraud, dispute, account_closed, unknown). Run the full
        #      10-rule policy evaluation so the terminal state is backed by
        #      a named rule rather than a hard-coded branch.
        #   b) interventions were tried and all failed -> exhausted.
        never_attempted = not interventions_tried

        if never_attempted:
            if current_status == RecoveryStatus.INTERVENTION_SELECTED.value:
                transition(conn, case_id, current_status, RecoveryStatus.POLICY_CHECK.value,
                           reason=f"No automated intervention exists for {root_cause.value} "
                                  f"— evaluating policy for disposition")
            case = get_case_with_details(conn, case_id)
            policy_result = check_policy(conn, case, ActionType.ESCALATION)
            passed = sum(1 for r in policy_result["all_rules"] if r["passed"])
            policy_summary = f"{passed}/{len(policy_result['all_rules'])} rules passed"

            if policy_result["result"] == PolicyResult.STOP:
                transition(conn, case_id, RecoveryStatus.POLICY_CHECK.value,
                           RecoveryStatus.STOPPED.value,
                           reason=f"Policy stop: {policy_result['reason']}")
                update_case(conn, case_id, {"stop_reason": policy_result["reason"]})
                log_event(conn, case_id, AuditEventType.CASE_STOPPED, Actor.POLICY_ENGINE,
                          {"rule": policy_result["rule_name"],
                           "reason": policy_result["reason"],
                           "root_cause": root_cause.value},
                          f"Case stopped by policy: {policy_result['reason']}")
                return {
                    "case_id": case_id, "outcome": "stopped", "root_cause": root_cause.value,
                    "selected_action": None, "intervention_step": None,
                    "policy_result": policy_result["result"].value, "policy_summary": policy_summary,
                    "action_result": None, "reason": policy_result["reason"],
                }

            # ESCALATE (fraud, high value) or ALLOWED-but-no-path
            # (account_closed, unknown) both hand the case to a human.
            if policy_result["result"] == PolicyResult.ESCALATE:
                reason = policy_result["reason"]
            else:
                reason = (f"No automated recovery path exists for root cause "
                          f"'{root_cause.value}' — merchant action required")
            transition(conn, case_id, RecoveryStatus.POLICY_CHECK.value,
                       RecoveryStatus.ESCALATED.value,
                       reason=f"Policy escalation: {reason}")
            esc_result = execute_escalation(conn, case, reason=reason)
            return {
                "case_id": case_id, "outcome": "escalated", "root_cause": root_cause.value,
                "selected_action": None, "intervention_step": None,
                "policy_result": policy_result["result"].value, "policy_summary": policy_summary,
                "action_result": esc_result, "reason": reason,
            }

        # (b) Interventions were attempted and all of them failed.
        if current_status == RecoveryStatus.INTERVENTION_SELECTED.value:
            transition(conn, case_id, current_status, RecoveryStatus.ESCALATED.value,
                       reason="All interventions exhausted")
        elif current_status == RecoveryStatus.AWAITING_OUTCOME.value:
            transition(conn, case_id, current_status, RecoveryStatus.ESCALATED.value,
                       reason="All interventions exhausted after final failure")
        esc_result = execute_escalation(conn, case, reason="All automated interventions exhausted")
        return {
            "case_id": case_id, "outcome": "escalated", "root_cause": root_cause.value,
            "selected_action": None, "intervention_step": None,
            "policy_result": None, "policy_summary": None,
            "action_result": esc_result, "reason": "All automated interventions exhausted",
        }

    # ── A concrete intervention is proposed ─────────────────────────────────
    sequence = get_intervention_sequence(root_cause)
    step_label = f"{len(interventions_tried) + 1}/{len(sequence)}"
    alternatives = [a.value for a in sequence if a != next_action]
    log_event(conn, case_id, AuditEventType.INTERVENTION_SELECTED, Actor.SYSTEM,
              {
                  "selected_action": next_action.value,
                  "intervention_index": len(interventions_tried),
                  "total_interventions": len(sequence),
                  "alternatives": alternatives,
                  "root_cause": root_cause.value,
              },
              f"Selected intervention: {next_action.value} "
              f"(step {step_label} for {root_cause.value})")

    if current_status == RecoveryStatus.INTERVENTION_SELECTED.value:
        transition(conn, case_id, current_status, RecoveryStatus.POLICY_CHECK.value,
                   reason=f"Checking policy for {next_action.value}")
    elif current_status == RecoveryStatus.AWAITING_OUTCOME.value:
        # Defensive fallback: resolve_outcome() normally pre-transitions this
        # case to INTERVENTION_SELECTED before calling dispatch_next_action(),
        # but handle a direct call on an AWAITING_OUTCOME case too.
        transition(conn, case_id, current_status, RecoveryStatus.INTERVENTION_SELECTED.value,
                   reason="Advancing to next intervention after failed attempt")
        transition(conn, case_id, RecoveryStatus.INTERVENTION_SELECTED.value,
                   RecoveryStatus.POLICY_CHECK.value,
                   reason=f"Checking policy for {next_action.value}")

    case = get_case_with_details(conn, case_id)
    policy_result = check_policy(conn, case, next_action)
    passed = sum(1 for r in policy_result["all_rules"] if r["passed"])
    policy_summary = f"{passed}/{len(policy_result['all_rules'])} rules passed"

    if policy_result["result"] == PolicyResult.ESCALATE:
        transition(conn, case_id, RecoveryStatus.POLICY_CHECK.value,
                   RecoveryStatus.ESCALATED.value,
                   reason=f"Policy escalation: {policy_result['reason']}")
        esc_result = execute_escalation(conn, case, reason=policy_result["reason"])
        return {
            "case_id": case_id, "outcome": "escalated", "root_cause": root_cause.value,
            "selected_action": next_action.value, "intervention_step": step_label,
            "policy_result": policy_result["result"].value, "policy_summary": policy_summary,
            "action_result": esc_result, "reason": policy_result["reason"],
        }

    if policy_result["result"] == PolicyResult.STOP:
        transition(conn, case_id, RecoveryStatus.POLICY_CHECK.value,
                   RecoveryStatus.STOPPED.value,
                   reason=f"Policy stop: {policy_result['reason']}")
        update_case(conn, case_id, {"stop_reason": policy_result["reason"]})
        log_event(conn, case_id, AuditEventType.CASE_STOPPED, Actor.POLICY_ENGINE,
                  {"rule": policy_result["rule_name"], "reason": policy_result["reason"]},
                  f"Case stopped by policy: {policy_result['reason']}")
        return {
            "case_id": case_id, "outcome": "stopped", "root_cause": root_cause.value,
            "selected_action": next_action.value, "intervention_step": step_label,
            "policy_result": policy_result["result"].value, "policy_summary": policy_summary,
            "action_result": None, "reason": policy_result["reason"],
        }

    # ALLOWED (or WAIT, waived in batch mode — see policy_engine rule 7) → execute
    transition(conn, case_id, RecoveryStatus.POLICY_CHECK.value,
               RecoveryStatus.EXECUTING.value,
               reason=f"Policy allowed: executing {next_action.value}")

    action_result = execute_action(conn, case, next_action)

    interventions_tried.append(next_action.value)
    update_case(conn, case_id, {"interventions_tried": interventions_tried})

    transition(conn, case_id, RecoveryStatus.EXECUTING.value,
               RecoveryStatus.AWAITING_OUTCOME.value,
               reason="Action dispatched, awaiting outcome")

    return {
        "case_id": case_id, "outcome": "awaiting_outcome", "root_cause": root_cause.value,
        "selected_action": next_action.value, "intervention_step": step_label,
        "policy_result": policy_result["result"].value, "policy_summary": policy_summary,
        "action_result": action_result,
        "reason": f"{next_action.value} dispatched, awaiting outcome",
    }


def resolve_outcome(conn: sqlite3.Connection, case: dict) -> dict:
    """
    For a case in AWAITING_OUTCOME, simulate the customer's response. Success
    ends the case as RECOVERED. Failure calls dispatch_next_action() again for
    the next intervention and simulates that too, repeating until the case
    reaches a terminal state or MAX_ROUNDS is hit.

    Returns:
        {
            "case_id": str,
            "final_status": str,        # RecoveryStatus value
            "amount_recovered": int,    # paise
            "rounds": [{"action": str, "success": bool, "probability": float}, ...],
            "actions_taken": [dict, ...],  # action_results from any dispatch_next_action calls
            "reason": str,
        }
    """
    case_id = case["id"]
    root_cause = _root_cause(case)
    amount_paise = case.get("amount_at_risk", 0)
    amount_rupees = amount_paise / 100

    rounds: list[dict] = []
    actions_taken: list[dict] = []

    if case["status"] != RecoveryStatus.AWAITING_OUTCOME.value:
        return {
            "case_id": case_id, "final_status": case["status"],
            "amount_recovered": case.get("amount_recovered", 0),
            "rounds": rounds, "actions_taken": actions_taken,
            "reason": "Case is not awaiting an outcome",
        }

    for _ in range(MAX_ROUNDS):
        case = get_case_with_details(conn, case_id)
        if not case or case["status"] != RecoveryStatus.AWAITING_OUTCOME.value:
            break

        interventions_tried = list(case.get("interventions_tried", []))
        last_action = interventions_tried[-1] if interventions_tried else None
        if last_action is None:
            break  # defensive: nothing to simulate an outcome for

        attempt_index = interventions_tried.count(last_action) - 1
        step_index = len(interventions_tried) - 1
        outcome = simulate_outcome(case_id, root_cause.value, last_action,
                                   attempt_index, step_index)

        log_event(conn, case_id, AuditEventType.OUTCOME_OBSERVED, Actor.SYSTEM,
                  {
                      "action_type": last_action,
                      "success": outcome["success"],
                      "probability": outcome["probability"],
                      "attempt_index": attempt_index,
                      "step_index": step_index,
                  },
                  outcome["reasoning"])
        rounds.append({"action": last_action, "success": outcome["success"],
                       "probability": outcome["probability"]})

        if outcome["success"]:
            transition(conn, case_id, RecoveryStatus.AWAITING_OUTCOME.value,
                       RecoveryStatus.RECOVERED.value,
                       reason=f"Payment recovered via {last_action}")
            update_case(conn, case_id, {"amount_recovered": amount_paise})
            log_event(conn, case_id, AuditEventType.CASE_RECOVERED, Actor.SYSTEM,
                      {
                          "amount_recovered_paise": amount_paise,
                          "amount_recovered_rupees": amount_rupees,
                          "recovery_action": last_action,
                          "attempt_count": len(interventions_tried),
                      },
                      f"Rs {amount_rupees:,.0f} recovered via {last_action}! "
                      f"({len(interventions_tried)} intervention(s) attempted)")
            return {
                "case_id": case_id, "final_status": RecoveryStatus.RECOVERED.value,
                "amount_recovered": amount_paise, "rounds": rounds,
                "actions_taken": actions_taken,
                "reason": f"Recovered via {last_action}",
            }

        # Failed — advance to the next intervention (if any) and loop back to
        # simulate its outcome on the next iteration.
        transition(conn, case_id, RecoveryStatus.AWAITING_OUTCOME.value,
                   RecoveryStatus.INTERVENTION_SELECTED.value,
                   reason="Advancing to next intervention after failed attempt")
        case = get_case_with_details(conn, case_id)
        dispatch_result = dispatch_next_action(conn, case)
        if dispatch_result["action_result"]:
            actions_taken.append(dispatch_result["action_result"])

        if dispatch_result["outcome"] != "awaiting_outcome":
            return {
                "case_id": case_id, "final_status": dispatch_result["outcome"],
                "amount_recovered": 0, "rounds": rounds, "actions_taken": actions_taken,
                "reason": dispatch_result["reason"],
            }
        # else: loop again, next iteration simulates this new action.

    # Safety bound hit without a terminal state.
    case = get_case_with_details(conn, case_id)
    if case and case["status"] == RecoveryStatus.AWAITING_OUTCOME.value:
        transition(conn, case_id, RecoveryStatus.AWAITING_OUTCOME.value,
                   RecoveryStatus.ESCALATED.value,
                   reason="All interventions exhausted (safety bound)")
        execute_escalation(conn, case, reason="All interventions exhausted")
        return {
            "case_id": case_id, "final_status": RecoveryStatus.ESCALATED.value,
            "amount_recovered": 0, "rounds": rounds, "actions_taken": actions_taken,
            "reason": "Safety bound reached",
        }
    final_case = get_case_with_details(conn, case_id)
    return {
        "case_id": case_id,
        "final_status": final_case["status"] if final_case else "escalated",
        "amount_recovered": final_case.get("amount_recovered", 0) if final_case else 0,
        "rounds": rounds, "actions_taken": actions_taken, "reason": "Terminal state reached",
    }


def process_case(conn: sqlite3.Connection, case: dict) -> dict:
    """
    Process a single case through the intervention → policy → execute → outcome
    loop, end to end. Composes dispatch_next_action() + resolve_outcome() —
    used by process_batch() / CLI / tests, where there's no UI moment to
    preserve between "action dispatched" and "outcome observed".

    Returns:
        {
            "case_id": str,
            "final_status": str,          # RecoveryStatus value
            "actions_taken": [dict, ...],
            "amount_recovered": int,      # paise
            "reason": str,
        }
    """
    case_id = case["id"]
    dispatch_result = dispatch_next_action(conn, case)
    actions_taken = [dispatch_result["action_result"]] if dispatch_result["action_result"] else []

    if dispatch_result["outcome"] != "awaiting_outcome":
        return {
            "case_id": case_id,
            "final_status": dispatch_result["outcome"],
            "actions_taken": actions_taken,
            "amount_recovered": 0,
            "reason": dispatch_result["reason"],
        }

    case = get_case_with_details(conn, case_id)
    resolve_result = resolve_outcome(conn, case)
    actions_taken += resolve_result.get("actions_taken", [])

    return {
        "case_id": case_id,
        "final_status": resolve_result["final_status"],
        "actions_taken": actions_taken,
        "amount_recovered": resolve_result["amount_recovered"],
        "reason": resolve_result["reason"],
    }


def process_batch(
    conn: sqlite3.Connection | None = None,
    limit: int | None = None,
) -> dict:
    """
    Process all pending cases through the full recovery pipeline.

    Steps:
    1. Detect new cases (if any in 'detected' status)
    2. Diagnose all cases (if any in 'diagnosing' status)
    3. Process all cases in 'intervention_selected' through the action loop

    Args:
        conn: Optional SQLite connection. Creates (and closes) one if omitted.
        limit: Optional cap on how many cases to run through step 3. Used by
               the /api/recovery/process endpoint so the dashboard can process
               the batch in chunks.

    Returns:
        Batch summary with counts and totals.
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    print("=" * 60)
    print("RECOVERY ENGINE — BATCH PROCESSING")
    print("=" * 60)
    start_time = datetime.utcnow()

    # ── Step 1: Detect ────────────────────────────────────────────────────
    detected_cases = get_cases_by_status(conn, RecoveryStatus.DETECTED.value)
    if detected_cases:
        print(f"\n📋 Step 1: Detecting {len(detected_cases)} new cases...")
        enriched = detect_new_cases(conn)
        print(f"\n📋 Step 2: Diagnosing {len(enriched)} cases...")
        diagnose_batch(conn, enriched)
    else:
        print("\nℹ️  No new 'detected' cases — skipping detection & diagnosis")

    # ── Step 2: Process all intervention_selected cases ────────────────────
    pending_cases = get_cases_by_status(conn, RecoveryStatus.INTERVENTION_SELECTED.value)
    if limit is not None:
        pending_cases = pending_cases[:limit]
    if not pending_cases:
        print("\nℹ️  No cases in 'intervention_selected' status to process")
        if own_conn:
            conn.close()
        return {"total": 0}

    print(f"\n⚡ Step 3: Processing {len(pending_cases)} cases through recovery pipeline...")
    print("─" * 40)

    results = {
        "total": len(pending_cases),
        "recovered": 0,
        "escalated": 0,
        "stopped": 0,
        "amount_recovered_paise": 0,
        "amount_at_risk_paise": 0,
        "case_results": [],
    }

    for i, case_stub in enumerate(pending_cases, 1):
        case = get_case_with_details(conn, case_stub["id"])
        if not case:
            continue

        amount_rupees = case.get("amount_at_risk", 0) / 100
        results["amount_at_risk_paise"] += case.get("amount_at_risk", 0)

        print(f"\n  [{i}/{len(pending_cases)}] Case {case['id']}: "
              f"{case.get('customer_name', '?')} | {case.get('plan_name', '?')} | "
              f"Rs {amount_rupees:,.0f} | {case.get('root_cause', '?')}")

        result = process_case(conn, case)
        conn.commit()

        results["case_results"].append(result)

        if result["final_status"] == RecoveryStatus.RECOVERED.value:
            results["recovered"] += 1
            results["amount_recovered_paise"] += result["amount_recovered"]
            print(f"    ✅ RECOVERED Rs {result['amount_recovered'] / 100:,.0f}")
        elif result["final_status"] == RecoveryStatus.ESCALATED.value:
            results["escalated"] += 1
            print(f"    ⚠️  ESCALATED: {result.get('reason', '')}")
        elif result["final_status"] == RecoveryStatus.STOPPED.value:
            results["stopped"] += 1
            print(f"    🛑 STOPPED: {result.get('reason', '')}")

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = (datetime.utcnow() - start_time).total_seconds()
    recovery_rate = (
        results["amount_recovered_paise"] / results["amount_at_risk_paise"] * 100
        if results["amount_at_risk_paise"] > 0 else 0
    )

    print("\n" + "=" * 60)
    print("BATCH PROCESSING COMPLETE")
    print("=" * 60)
    print(f"  Total cases:       {results['total']}")
    print(f"  ✅ Recovered:      {results['recovered']} (Rs {results['amount_recovered_paise'] / 100:,.0f})")
    print(f"  ⚠️  Escalated:     {results['escalated']}")
    print(f"  🛑 Stopped:        {results['stopped']}")
    print(f"  📈 Recovery rate:  {recovery_rate:.1f}%")
    print(f"  ⏱️  Time:           {elapsed:.1f}s")
    print("=" * 60)

    results["recovery_rate"] = recovery_rate
    results["elapsed_seconds"] = elapsed

    if own_conn:
        conn.close()

    return results
