import sqlite3
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel

from database.db import get_connection, get_all_cases, get_case_with_details, get_audit_trail
from engine.attribution import get_attribution_metrics
from engine.recovery_orchestrator import process_batch

router = APIRouter()

# ── Dependency ──────────────────────────────────────────────────────────

def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()

# ── Endpoints ───────────────────────────────────────────────────────────

@router.get("/cases")
def list_cases(status: Optional[str] = None):
    """Get all cases, optionally filtered by status."""
    conn = get_connection()
    try:
        # We'll just fetch all and filter in python for simplicity in this demo,
        # but in production you'd use a WHERE clause.
        cases = get_all_cases(conn)
        if status:
            cases = [c for c in cases if c["status"] == status]
        return {"cases": cases}
    finally:
        conn.close()

@router.get("/cases/{case_id}")
def get_case(case_id: str):
    """Get full case details including audit timeline."""
    conn = get_connection()
    try:
        case = get_case_with_details(conn, case_id)
        if not case:
            raise HTTPException(status_code=404, detail="Case not found")
        
        audit_trail = get_audit_trail(conn, case_id)
        return {
            "case": case,
            "audit_trail": audit_trail
        }
    finally:
        conn.close()

@router.get("/metrics/summary")
def get_metrics_summary():
    """Get hero metrics for the dashboard (Revenue at risk, recovered, etc)."""
    conn = get_connection()
    try:
        metrics = get_attribution_metrics(conn)
        return metrics["summary"]
    finally:
        conn.close()

@router.get("/metrics/by-root-cause")
def get_metrics_by_root_cause():
    """Get recovery metrics grouped by root cause."""
    conn = get_connection()
    try:
        metrics = get_attribution_metrics(conn)
        return {"data": metrics["by_root_cause"]}
    finally:
        conn.close()

@router.get("/metrics/by-intervention")
def get_metrics_by_intervention():
    """Get recovery metrics grouped by action type."""
    conn = get_connection()
    try:
        metrics = get_attribution_metrics(conn)
        return {"data": metrics["by_intervention"]}
    finally:
        conn.close()

@router.get("/metrics/funnel")
def get_metrics_funnel():
    """Get case counts by status for funnel visualization."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM recovery_cases GROUP BY status"
        ).fetchall()
        return {"data": [dict(r) for r in rows]}
    finally:
        conn.close()

@router.get("/activity/recent")
def get_recent_activity(limit: int = 50):
    """Get recent audit events for the live feed."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
        
        import json
        events = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d["details"])
            events.append(d)
            
        return {"events": events}
    finally:
        conn.close()

class ProcessRequest(BaseModel):
    batch_size: int = 50

@router.post("/recovery/process")
def trigger_batch_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    """Trigger the recovery orchestrator to process the batch in the background."""
    # In a real app we'd trigger a celery task. Here we use FastAPI background tasks.
    def run_pipeline():
        process_batch(limit=req.batch_size)
        
    background_tasks.add_task(run_pipeline)
    return {"message": f"Started processing batch of up to {req.batch_size} cases."}
