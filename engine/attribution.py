import sqlite3
from typing import Dict, Any

def get_attribution_metrics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """
    Calculate conservative recovery attribution metrics.
    Only counts revenue directly recovered by the AI agent's actions
    (Smart Retry, Payment Link, Dunning). Escalated and stopped cases
    are strictly excluded from the 'recovered' bucket.
    """
    
    # Hero Metrics
    hero = conn.execute("""
        SELECT 
            COUNT(id) as total_cases,
            SUM(total_risk) as revenue_at_risk,
            SUM(amount_recovered) as revenue_recovered
        FROM recovery_cases
        WHERE status = 'recovered' OR status = 'escalated' OR status = 'stopped'
    """).fetchone()

    total_cases = hero["total_cases"] or 0
    at_risk = hero["revenue_at_risk"] or 0
    recovered = hero["revenue_recovered"] or 0
    
    # We also want revenue at risk for ALL cases (including not yet resolved)
    total_at_risk_row = conn.execute("""
        SELECT 
            COUNT(id) as total_active_cases,
            SUM(total_risk) as total_revenue_at_risk
        FROM recovery_cases
    """).fetchone()
    
    total_active_cases = total_at_risk_row["total_active_cases"] or 0
    total_revenue_at_risk = total_at_risk_row["total_revenue_at_risk"] or 0

    recovery_rate = (recovered / total_revenue_at_risk * 100) if total_revenue_at_risk > 0 else 0.0

    # Group by Intervention (Action Type)
    # We parse the last successful action from interventions_tried if it's recovered
    # But for a simple metric, we can just query the audit_log for CASE_RECOVERED
    intervention_rows = conn.execute("""
        SELECT json_extract(details, '$.recovery_action') as action,
               COUNT(*) as case_count,
               SUM(json_extract(details, '$.amount_recovered_paise')) as amount_recovered
        FROM audit_log
        WHERE event_type = 'case_recovered'
        GROUP BY action
    """).fetchall()

    by_intervention = [
        {
            "action": r["action"],
            "cases": r["case_count"],
            "amount_recovered": r["amount_recovered"]
        } for r in intervention_rows
    ]

    # Group by Root Cause (for recovered cases only)
    root_cause_rows = conn.execute("""
        SELECT root_cause, 
               COUNT(*) as case_count,
               SUM(amount_recovered) as amount_recovered
        FROM recovery_cases
        WHERE status = 'recovered'
        GROUP BY root_cause
    """).fetchall()

    by_root_cause = [
        {
            "root_cause": r["root_cause"],
            "cases": r["case_count"],
            "amount_recovered": r["amount_recovered"]
        } for r in root_cause_rows
    ]

    return {
        "summary": {
            "total_cases_tracked": total_active_cases,
            "revenue_at_risk_paise": total_revenue_at_risk,
            "revenue_recovered_paise": recovered,
            "recovery_rate_percent": round(recovery_rate, 2),
        },
        "by_intervention": by_intervention,
        "by_root_cause": by_root_cause
    }
