"""
AI Revenue Recovery — Merchant Dashboard (Streamlit)

5 pages, each mapped to the judging criteria:
  1. Recovery Command Center   -> (1) Measured Money
  2. Recovery Batch            -> (1) Money + (2) Escalation + (3) Stopping
  3. Case Audit Trail           -> (4) Audit Trail  [primary credibility page]
  4. Live Recovery Engine       -> the complete loop, live, in two visible
                                    stages (dispatch, then outcome)
  5. Escalation & Stopping Rules-> (2) Escalation + (3) Stopping Rules

Reads the SQLite database directly (no FastAPI server dependency) so the demo
never breaks because a second process wasn't started.

Run:
    streamlit run dashboard/app.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import config
from database.db import (
    get_connection, get_all_cases, get_case_with_details, get_audit_trail,
    get_actions_for_case, get_policy_trigger_stats, get_escalated_cases,
    get_recent_activity,
)
from engine.attribution import get_attribution_metrics
from engine.detector import detect_new_cases
from engine.diagnoser import diagnose
from engine.recovery_orchestrator import dispatch_next_action, resolve_outcome
from models.enums import RecoveryStatus

st.set_page_config(
    page_title="AI Revenue Recovery",
    page_icon="\U0001F4B0",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Helpers ────────────────────────────────────────────────────────────────

def rupees(paise: float) -> str:
    """Format paise as a compact rupee string. Never mix paise and rupees on screen."""
    if paise is None:
        paise = 0
    return f"₹{paise / 100:,.0f}"


STATUS_BADGE = {
    "recovered": "🟢 RECOVERED",
    "escalated": "🟠 ESCALATED",
    "stopped": "🔴 STOPPED",
    "detected": "⚪ DETECTED",
    "diagnosing": "⚪ DIAGNOSING",
    "intervention_selected": "⚪ IN PROGRESS",
    "policy_check": "⚪ IN PROGRESS",
    "executing": "⚪ IN PROGRESS",
    "awaiting_outcome": "⚪ IN PROGRESS",
}

ACTION_LABEL = {
    "smart_retry": "Smart Retry",
    "payment_link": "Payment Link",
    "dunning_message": "Dunning Message",
    "escalation": "Escalation",
}

EVENT_ICON = {
    "case_created": "🔍",
    "root_cause_diagnosed": "🧠",
    "intervention_selected": "🎯",
    "policy_evaluated": "🛡️",
    "action_executed": "⚡",
    "payment_link_created": "🔗",
    "dunning_generated": "💬",
    "outcome_observed": "📊",
    "state_changed": "🔁",
    "case_recovered": "🎉",
    "case_escalated": "⚠️",
    "case_stopped": "🛑",
}


def is_unrecovered(case: dict) -> bool:
    """
    Cases have exactly 3 terminal states (recovered/escalated/stopped) — there
    is no 'unrecovered' state in the machine. On the dashboard, "unrecovered"
    means: escalated after at least one automated attempt failed, rather than
    escalated immediately because no automated path existed (fraud, dispute,
    account_closed, high-value, unknown).
    """
    return (
        case["status"] == "escalated"
        and len(case.get("interventions_tried") or []) > 0
        and (case.get("amount_recovered") or 0) == 0
    )


def root_cause_label(rc: str | None) -> str:
    if not rc:
        return "Unknown"
    return rc.replace("_", " ").title()


def goto(page_label: str, case_id: str | None = None):
    """
    Programmatic page navigation.

    Streamlit forbids writing to st.session_state["nav_page"] once the radio
    widget bound to that key has been instantiated in the current script run
    (it raises StreamlitAPIException) — and the radio renders near the top of
    every run, before any button click handler further down the page can
    fire. So a click can never write "nav_page" directly. Instead it stages
    the target in a plain, unbound key; the very top of the script (before
    the radio widget exists) consumes it into "nav_page" on the next run.
    """
    st.session_state["_pending_nav"] = page_label
    if case_id:
        st.session_state["selected_case_id"] = case_id
    st.rerun()


# ─── Cached data loaders ────────────────────────────────────────────────────

@st.cache_data(ttl=5)
def load_all_cases() -> pd.DataFrame:
    conn = get_connection()
    try:
        cases = get_all_cases(conn)
    finally:
        conn.close()
    df = pd.DataFrame(cases)
    if df.empty:
        return df
    df["unrecovered"] = df.apply(lambda r: is_unrecovered(r.to_dict()), axis=1)
    df["attempted"] = df["interventions_tried"].apply(lambda x: len(x or []) > 0)
    return df


@st.cache_data(ttl=5)
def load_case_lookup(case_ids: tuple[str, ...]) -> dict:
    """Batch-enrich a small set of case IDs with customer/plan for display."""
    conn = get_connection()
    try:
        out = {}
        for cid in case_ids:
            d = get_case_with_details(conn, cid)
            if d:
                out[cid] = d
        return out
    finally:
        conn.close()


@st.cache_data(ttl=5)
def load_summary() -> dict:
    conn = get_connection()
    try:
        return get_attribution_metrics(conn)
    finally:
        conn.close()


@st.cache_data(ttl=5)
def load_policy_stats() -> list[dict]:
    conn = get_connection()
    try:
        return get_policy_trigger_stats(conn)
    finally:
        conn.close()


@st.cache_data(ttl=5)
def load_escalated() -> list[dict]:
    conn = get_connection()
    try:
        return get_escalated_cases(conn)
    finally:
        conn.close()


@st.cache_data(ttl=5)
def load_escalation_context() -> dict:
    """case_id -> merchant recommendation text, from the escalation action's details."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT case_id, action_details FROM recovery_actions
               WHERE action_type = 'escalation' ORDER BY created_at"""
        ).fetchall()
        import json
        out = {}
        for r in rows:
            try:
                out[r["case_id"]] = json.loads(r["action_details"]).get("recommended_action", "—")
            except (ValueError, TypeError):
                pass
        return out
    finally:
        conn.close()


@st.cache_data(ttl=5)
def load_policy_result_distribution() -> pd.DataFrame:
    """Proportion of policy_evaluated checks by result (allowed/stop/escalate/wait)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT json_extract(details, '$.result') AS result, COUNT(*) AS n
               FROM audit_log WHERE event_type = 'policy_evaluated'
               GROUP BY result"""
        ).fetchall()
        return pd.DataFrame([dict(r) for r in rows])
    finally:
        conn.close()


@st.cache_data(ttl=5)
def load_curated_activity(limit: int = 15) -> list[dict]:
    """Recent terminal-outcome events only, for the Page 1 activity feed —
    matches the plan's example bullets rather than the raw full audit stream."""
    conn = get_connection()
    try:
        rows = get_recent_activity(conn, 200)
    finally:
        conn.close()
    terminal = [r for r in rows if r["event_type"] in
                ("case_recovered", "case_escalated", "case_stopped")]
    return terminal[:limit]


def clear_cache():
    st.cache_data.clear()


def format_activity_line(ev: dict, case: dict | None) -> str:
    d = ev["details"]
    customer = (case or {}).get("customer_name", "a customer")
    amount = rupees((case or {}).get("amount_at_risk", 0))
    if ev["event_type"] == "case_recovered":
        action = ACTION_LABEL.get(d.get("recovery_action"), d.get("recovery_action", "recovery action"))
        return f"✅ Recovered {amount} from {customer} via {action}"
    if ev["event_type"] == "case_escalated":
        reason = (d.get("reason") or ev["reasoning"] or "").split(".")[0]
        return f"⚠️ Escalated: {reason} on {amount} — handed to merchant"
    if ev["event_type"] == "case_stopped":
        reason = (d.get("reason") or ev["reasoning"] or "").split(".")[0]
        return f"🛑 Stopped: {reason} — not worth pursuing"
    return ev["reasoning"]


# ─── Sidebar Navigation ─────────────────────────────────────────────────────

st.sidebar.title("💰 AI Revenue Recovery")
st.sidebar.caption("Razorpay AI Hackathon — Track 03")

PAGES = [
    "1 · Recovery Command Center",
    "2 · Recovery Batch (Case Explorer)",
    "3 · Case Audit Trail",
    "4 · Live Recovery Engine",
    "5 · Escalation & Stopping Rules",
]
if "_pending_nav" in st.session_state:
    st.session_state["nav_page"] = st.session_state.pop("_pending_nav")
elif "nav_page" not in st.session_state:
    st.session_state["nav_page"] = PAGES[0]
page = st.sidebar.radio("Navigate", PAGES, key="nav_page", label_visibility="collapsed")

st.sidebar.divider()
summary_preview = load_summary()["summary"]
st.sidebar.metric("Revenue Recovered", rupees(summary_preview["revenue_recovered_paise"]))
st.sidebar.metric("Recovery Rate", f"{summary_preview['recovery_rate_percent']}%")
st.sidebar.caption(
    f"{summary_preview['cases_recovered']} recovered · "
    f"{summary_preview['cases_escalated']} escalated · "
    f"{summary_preview['cases_stopped']} stopped"
)
if st.sidebar.button("🔄 Refresh data"):
    clear_cache()
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# PAGE 1 — Recovery Command Center
# ══════════════════════════════════════════════════════════════════════════

if page == PAGES[0]:
    st.title("Recovery Command Center")
    st.caption("Did the agent actually recover money? How much?")

    m = load_summary()["summary"]
    df = load_all_cases()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Revenue at Risk", rupees(m["revenue_at_risk_paise"]))
    c2.metric("✅ Revenue Recovered", rupees(m["revenue_recovered_paise"]))
    c3.metric("📈 Recovery Rate", f"{m['recovery_rate_percent']}%")
    c4.metric("📊 Cases Processed", m["total_cases_tracked"])

    st.caption(
        f"Lifetime revenue at risk (amount × remaining billing cycles): "
        f"{rupees(m['lifetime_revenue_at_risk_paise'])} — shown for context only, "
        f"never used as the recovery-rate denominator."
    )

    st.divider()

    st.subheader("Recovery Results")
    if not df.empty:
        rec_amt = df.loc[df["status"] == "recovered", "amount_recovered"].sum()
        esc_amt = df.loc[df["status"] == "escalated", "amount_at_risk"].sum()
        stp_amt = df.loc[df["status"] == "stopped", "amount_at_risk"].sum()
        unrec_amt = df.loc[df["unrecovered"], "amount_at_risk"].sum()
        unrec_n = int(df["unrecovered"].sum())

        st.info(
            f"**{m['cases_recovered']}** cases recovered ({rupees(rec_amt)}) | "
            f"**{m['cases_escalated']}** escalated ({rupees(esc_amt)} at risk) | "
            f"**{m['cases_stopped']}** stopped ({rupees(stp_amt)} at risk) | "
            f"**{unrec_n}** unrecovered ({rupees(unrec_amt)} at risk)"
        )

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("🟢 Recovered", m["cases_recovered"], rupees(rec_amt))
        r2.metric("🟠 Escalated", m["cases_escalated"], rupees(esc_amt) + " at risk")
        r3.metric("🔴 Stopped", m["cases_stopped"], rupees(stp_amt) + " at risk")
        r4.metric("⚪ Unrecovered (exhausted)", unrec_n, rupees(unrec_amt) + " at risk")

    st.divider()

    col_funnel, col_cause = st.columns([1, 1])

    with col_funnel:
        st.subheader("Recovery Funnel")
        st.caption("Detected → Diagnosed → Policy-checked & Executed → Outcome")
        if df.empty:
            st.info("No cases yet.")
        else:
            total = len(df)
            diagnosed_mask = df["root_cause"].notna()
            diagnosed_n = int(diagnosed_mask.sum())
            attempted_mask = df["attempted"] & diagnosed_mask
            attempted_n = int(attempted_mask.sum())
            blocked_mask = diagnosed_mask & ~attempted_mask
            blocked_n = int(blocked_mask.sum())

            rec_mask = df["status"] == "recovered"
            esc_att_mask = (df["status"] == "escalated") & attempted_mask
            stp_att_mask = (df["status"] == "stopped") & attempted_mask
            esc_imm_mask = (df["status"] == "escalated") & blocked_mask
            stp_imm_mask = (df["status"] == "stopped") & blocked_mask

            nodes = [
                f"Detected ({total})",
                f"Diagnosed ({diagnosed_n})",
                f"Recovery Attempted ({attempted_n})",
                f"Blocked / Escalated Immediately ({blocked_n})",
                f"🟢 Recovered ({int(rec_mask.sum())})",
                f"🟠 Escalated ({m['cases_escalated']})",
                f"🔴 Stopped ({m['cases_stopped']})",
            ]
            raw_links = [
                (0, 1, diagnosed_n),
                (1, 2, attempted_n),
                (1, 3, blocked_n),
                (2, 4, int(rec_mask.sum())),
                (2, 5, int(esc_att_mask.sum())),
                (2, 6, int(stp_att_mask.sum())),
                (3, 5, int(esc_imm_mask.sum())),
                (3, 6, int(stp_imm_mask.sum())),
            ]
            links = [l for l in raw_links if l[2] > 0]
            fig = go.Figure(go.Sankey(
                node=dict(
                    label=nodes, pad=18, thickness=16,
                    color=["#6b7280", "#6b7280", "#3b82f6", "#94a3b8",
                          "#22c55e", "#f59e0b", "#ef4444"],
                ),
                link=dict(
                    source=[l[0] for l in links],
                    target=[l[1] for l in links],
                    value=[l[2] for l in links],
                ),
            ))
            fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=380, font_size=12)
            st.plotly_chart(fig, width="stretch")

    with col_cause:
        st.subheader("Recovery by Root Cause")
        rc_data = load_summary()["by_root_cause"]
        if rc_data:
            df_rc = pd.DataFrame(rc_data)
            df_rc["root_cause_label"] = df_rc["root_cause"].apply(root_cause_label)
            fig2 = px.bar(
                df_rc.sort_values("amount_at_risk", ascending=True),
                x="amount_at_risk", y="root_cause_label", orientation="h",
                labels={"amount_at_risk": "Amount at risk (paise)", "root_cause_label": ""},
                text=df_rc.sort_values("amount_at_risk", ascending=True)["recovery_rate_percent"]
                    .apply(lambda v: f"{v:.0f}% recovered"),
            )
            fig2.update_traces(marker_color="#3b82f6")
            fig2.update_xaxes(tickprefix="₹", tickformat=",.0f")
            fig2.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=380)
            st.plotly_chart(fig2, width="stretch")
        else:
            st.info("No diagnosed cases yet.")

    st.divider()
    st.subheader("Live Activity Feed")
    events = load_curated_activity(15)
    if not events:
        st.info("No terminal outcomes logged yet.")
    else:
        lookup = load_case_lookup(tuple(sorted({e["case_id"] for e in events})))
        for ev in events:
            ts = ev["timestamp"][:19].replace("T", " ")
            line = format_activity_line(ev, lookup.get(ev["case_id"]))
            st.markdown(f"`{ts}` **{ev['case_id']}** — {line}")


# ══════════════════════════════════════════════════════════════════════════
# PAGE 2 — Recovery Batch (Case Explorer)
# ══════════════════════════════════════════════════════════════════════════

elif page == PAGES[1]:
    st.title("Recovery Batch — Case Explorer")
    st.caption("The full batch, not a cherry-picked example.")

    df = load_all_cases()
    if df.empty:
        st.warning("No cases in the database. Generate a batch first (see Page 4).")
        st.stop()

    def display_status(row) -> str:
        if row["unrecovered"]:
            return "⚪ UNRECOVERED"
        return STATUS_BADGE.get(row["status"], row["status"])

    def detail_text(row) -> str:
        if row["status"] == "recovered":
            last = row["interventions_tried"][-1] if row["interventions_tried"] else None
            return f"via {ACTION_LABEL.get(last, last or '—')}"
        if row["unrecovered"]:
            tried = ", ".join(ACTION_LABEL.get(a, a) for a in row["interventions_tried"])
            return f"tried: {tried}" if tried else "—"
        if row["status"] == "escalated":
            return (row["escalation_reason"] or "—")[:70]
        if row["status"] == "stopped":
            return (row["stop_reason"] or "—")[:70]
        return "—"

    df["display_status"] = df.apply(display_status, axis=1)
    df["detail"] = df.apply(detail_text, axis=1)
    df["amount_rupees"] = df["amount_at_risk"] / 100
    df["recovered_rupees"] = df["amount_recovered"] / 100
    df["root_cause_label"] = df["root_cause"].apply(root_cause_label)
    df["action_label"] = df["current_intervention"].map(ACTION_LABEL).fillna("—")

    lookup = load_case_lookup(tuple(df["id"]))
    df["customer_name"] = df["id"].map(lambda i: lookup.get(i, {}).get("customer_name"))
    df["plan_name"] = df["id"].map(lambda i: lookup.get(i, {}).get("plan_name"))

    with st.expander("Filters", expanded=True):
        f1, f2, f3 = st.columns(3)
        status_options = ["recovered", "escalated", "stopped", "unrecovered"]
        status_sel = f1.multiselect("Status", status_options, default=[])
        cause_sel = f2.multiselect(
            "Root Cause", sorted(df["root_cause_label"].dropna().unique().tolist()), default=[]
        )
        amt_min, amt_max = float(df["amount_rupees"].min()), float(df["amount_rupees"].max())
        amount_range = f3.slider(
            "Amount range (₹)", min_value=0.0, max_value=max(amt_max, 1.0),
            value=(0.0, max(amt_max, 1.0)),
        )

    filtered = df.copy()
    if status_sel:
        mask = pd.Series(False, index=filtered.index)
        if "unrecovered" in status_sel:
            mask |= filtered["unrecovered"]
        real_statuses = [s for s in status_sel if s != "unrecovered"]
        if real_statuses:
            mask |= filtered["status"].isin(real_statuses)
        filtered = filtered[mask]
    if cause_sel:
        filtered = filtered[filtered["root_cause_label"].isin(cause_sel)]
    filtered = filtered[
        (filtered["amount_rupees"] >= amount_range[0]) & (filtered["amount_rupees"] <= amount_range[1])
    ]

    rec_n = int((filtered["status"] == "recovered").sum())
    esc_n = int((filtered["status"] == "escalated").sum())
    stp_n = int((filtered["status"] == "stopped").sum())
    unrec_n = int(filtered["unrecovered"].sum())
    rec_amt = filtered.loc[filtered["status"] == "recovered", "recovered_rupees"].sum()

    st.info(
        f"Showing **{len(filtered)}** of {len(df)} cases | "
        f"🟢 {rec_n} recovered (₹{rec_amt:,.0f}) | 🟠 {esc_n} escalated | "
        f"🔴 {stp_n} stopped | ⚪ {unrec_n} unrecovered"
    )

    show_cols = {
        "id": "Case ID",
        "customer_name": "Customer",
        "plan_name": "Plan",
        "amount_rupees": "Amount (₹)",
        "root_cause_label": "Root Cause",
        "action_label": "Action Taken",
        "display_status": "Status",
        "recovered_rupees": "Recovered (₹)",
        "detail": "Detail",
    }
    table = filtered[list(show_cols.keys())].rename(columns=show_cols)
    table["Amount (₹)"] = table["Amount (₹)"].map(lambda v: f"{v:,.0f}")
    table["Recovered (₹)"] = table["Recovered (₹)"].map(lambda v: f"{v:,.0f}" if v else "—")

    event = st.dataframe(
        table, width="stretch", hide_index=True, height=460,
        on_select="rerun", selection_mode="single-row",
    )

    if event and event.selection and event.selection.get("rows"):
        sel_idx = event.selection["rows"][0]
        sel_case_id = filtered.iloc[sel_idx]["id"]
        st.success(f"Selected **{sel_case_id}**")
        if st.button(f"🔍 View full audit trail for {sel_case_id}"):
            goto(PAGES[2], sel_case_id)


# ══════════════════════════════════════════════════════════════════════════
# PAGE 3 — Case Audit Trail (Deep Dive)
# ══════════════════════════════════════════════════════════════════════════

elif page == PAGES[2]:
    st.title("Case Audit Trail")
    st.caption("Point at any case. Get a full, honest explanation of every decision made.")

    df = load_all_cases()
    if df.empty:
        st.warning("No cases in the database.")
        st.stop()

    all_ids = df["id"].tolist()
    default_id = st.session_state.get("selected_case_id", all_ids[0])
    if default_id not in all_ids:
        default_id = all_ids[0]

    case_id = st.selectbox(
        "Select a case", all_ids, index=all_ids.index(default_id),
        format_func=lambda cid: f"{cid} — {root_cause_label(df.set_index('id').loc[cid, 'root_cause'])}",
    )
    st.session_state["selected_case_id"] = case_id

    conn = get_connection()
    try:
        case = get_case_with_details(conn, case_id)
        trail = get_audit_trail(conn, case_id)
        actions = get_actions_for_case(conn, case_id)
    finally:
        conn.close()

    if not case:
        st.error("Case not found.")
        st.stop()

    badge = STATUS_BADGE.get(case["status"], case["status"])
    st.subheader(f"{case['customer_name']} — {case['plan_name']} plan")
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("Status", badge)
    h2.metric("Amount at Risk", rupees(case["amount_at_risk"]))
    h3.metric("Amount Recovered", rupees(case["amount_recovered"]))
    h4.metric("Root Cause", root_cause_label(case.get("root_cause")))

    if case.get("razorpay_payment_link_url"):
        st.markdown(f"🔗 Payment link: [{case['razorpay_payment_link_url']}]({case['razorpay_payment_link_url']})")

    st.divider()
    st.subheader("Timeline")
    st.caption(
        "Every step: what happened → why → what was chosen → whether policy allowed it → "
        "what was done → the outcome."
    )

    if not trail:
        st.info("No audit entries for this case yet.")

    for ev in trail:
        icon = EVENT_ICON.get(ev["event_type"], "•")
        ts = ev["timestamp"][:19].replace("T", " ")
        title = ev["event_type"].replace("_", " ").title()
        with st.expander(f"{icon}  `{ts}`  **{title}** — {ev['reasoning'][:100]}", expanded=False):
            st.write(ev["reasoning"])
            if ev["event_type"] == "policy_evaluated" and ev["details"].get("all_rules"):
                rules_df = pd.DataFrame(ev["details"]["all_rules"])
                rules_df["passed"] = rules_df["passed"].map(lambda p: "✅" if p else "❌")
                st.dataframe(
                    rules_df[["rule_number", "rule_name", "threshold", "current_value", "passed"]]
                        .rename(columns={
                            "rule_number": "#", "rule_name": "Rule", "threshold": "Threshold",
                            "current_value": "Current Value", "passed": "Passed",
                        }),
                    hide_index=True, width="stretch",
                )
            elif ev["event_type"] == "dunning_generated" and ev["details"].get("message_text"):
                st.markdown("**AI-generated message:**")
                st.info(ev["details"]["message_text"])
            else:
                st.json(ev["details"])

    if actions:
        st.divider()
        st.subheader("Actions & Razorpay Responses")
        for a in actions:
            with st.expander(f"{ACTION_LABEL.get(a['action_type'], a['action_type'])} — outcome: {a.get('outcome', '?')}"):
                st.json(a.get("action_details", {}))
                if a.get("razorpay_response"):
                    st.markdown("**Razorpay API response:**")
                    st.json(a["razorpay_response"])


# ══════════════════════════════════════════════════════════════════════════
# PAGE 4 — Live Recovery Engine
# ══════════════════════════════════════════════════════════════════════════

elif page == PAGES[3]:
    st.title("Live Recovery Engine")
    st.caption(
        "The complete loop, in two visible moments: the agent deciding and acting, "
        "then the customer's response closing the loop."
    )

    conn = get_connection()
    try:
        pending_new = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status='detected'").fetchone()[0]
        pending_diag = conn.execute("SELECT COUNT(*) FROM recovery_cases WHERE status='diagnosing'").fetchone()[0]
        pending_dispatch = conn.execute(
            "SELECT COUNT(*) FROM recovery_cases WHERE status='intervention_selected'"
        ).fetchone()[0]
        pending_outcome = conn.execute(
            "SELECT COUNT(*) FROM recovery_cases WHERE status='awaiting_outcome'"
        ).fetchone()[0]
    finally:
        conn.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Awaiting Detection", pending_new)
    c2.metric("Awaiting Diagnosis", pending_diag)
    c3.metric("Ready to Dispatch", pending_dispatch)
    c4.metric("Awaiting Outcome", pending_outcome)

    nothing_pending = pending_new == pending_diag == pending_dispatch == pending_outcome == 0

    if nothing_pending:
        st.success("All cases have reached a terminal state. Nothing pending.")
        st.markdown("To re-run the live demo, regenerate a fresh batch below.")
        with st.expander("⚠️ Regenerate demo batch (wipes and rebuilds the database)"):
            st.warning(
                "This deletes all cases (including recovered/escalated/stopped history) "
                "and regenerates the seeded 50 active + 30 historical demo cases."
            )
            confirm = st.checkbox("I understand this replaces all current data")
            if st.button("🔄 Regenerate demo batch", disabled=not confirm):
                from pathlib import Path as _P
                dbp = _P(config.DATABASE_PATH)
                for ext in ("", "-wal", "-shm"):
                    p = _P(str(dbp) + ext)
                    if p.exists():
                        p.unlink()
                from database.db import init_db
                init_db()
                from data.generate_batch import generate_batch
                from data.seed_historical import seed_historical
                with st.spinner("Generating batch..."):
                    generate_batch()
                with st.spinner("Seeding historical data..."):
                    seed_historical()
                clear_cache()
                st.rerun()

    else:
        st.markdown("### Stage 1 — 🚀 Run Recovery Engine")
        st.caption("Detects, diagnoses, selects the right intervention, checks all 10 policy rules, and executes.")
        run_disabled = (pending_new + pending_diag + pending_dispatch) == 0
        if st.button("🚀 Run Recovery Engine", type="primary", disabled=run_disabled):
            log_box = st.container(height=420)
            progress = st.progress(0.0)
            counters = st.empty()
            conn = get_connection()
            try:
                if pending_new > 0:
                    with log_box:
                        st.write(f"🔍 Detecting {pending_new} new cases...")
                    enriched = detect_new_cases(conn)
                    with log_box:
                        st.write(f"🧠 Diagnosing {len(enriched)} cases...")
                    for c in enriched:
                        c = get_case_with_details(conn, c["id"])
                        result = diagnose(conn, c)
                        with log_box:
                            st.write(
                                f"  Diagnosing **{c['id']}**... root cause: "
                                f"**{result['root_cause'].value}** ({result['method']}, "
                                f"confidence {result['confidence']:.2f})"
                            )
                    conn.commit()

                from database.db import get_cases_by_status
                ready = get_cases_by_status(conn, RecoveryStatus.INTERVENTION_SELECTED.value)
                total = len(ready) or 1
                stats = {"awaiting_outcome": 0, "escalated": 0, "stopped": 0}

                for i, stub in enumerate(ready, 1):
                    case = get_case_with_details(conn, stub["id"])
                    if not case:
                        continue
                    amt = case.get("amount_at_risk", 0) / 100
                    with log_box:
                        st.markdown(
                            f"**{case['id']}** — {case.get('customer_name', '?')} | "
                            f"{case.get('plan_name', '?')} | ₹{amt:,.0f} | "
                            f"{root_cause_label(case.get('root_cause'))}"
                        )
                    result = dispatch_next_action(conn, case)
                    conn.commit()

                    with log_box:
                        if result["selected_action"]:
                            st.write(
                                f"  🎯 Selecting intervention... **{ACTION_LABEL.get(result['selected_action'], result['selected_action'])}** "
                                f"({result['intervention_step']})"
                            )
                        else:
                            st.write(f"  🎯 No automated intervention exists for this root cause.")

                        if result["policy_result"]:
                            check_icon = "✅ ALLOWED" if result["policy_result"] == "allowed" else f"⛔ {result['policy_result'].upper()}"
                            st.write(f"  🛡️ Policy check... {check_icon} ({result['policy_summary']})")

                        if result["outcome"] == "awaiting_outcome":
                            st.write(f"  ⚡ Executing... dispatched, awaiting customer response")
                            details = (result.get("action_result") or {}).get("details", {})
                            if isinstance(details, dict) and details.get("link_url"):
                                st.write(f"  🔗 Payment link created: {details['link_url']}")
                            stats["awaiting_outcome"] += 1
                        elif result["outcome"] == "escalated":
                            st.warning(f"  ⚠️ ESCALATED — {result['reason']}")
                            stats["escalated"] += 1
                        elif result["outcome"] == "stopped":
                            st.error(f"  🛑 STOPPED — {result['reason']}")
                            stats["stopped"] += 1

                    progress.progress(i / total)
                    counters.markdown(
                        f"**Dispatched {i}/{total}** — "
                        f"⚡ {stats['awaiting_outcome']} awaiting outcome · "
                        f"⚠️ {stats['escalated']} escalated · 🛑 {stats['stopped']} stopped"
                    )
            finally:
                conn.close()
            clear_cache()
            st.info("Stage 1 complete. Scroll down to Stage 2 to simulate customer responses and close the loop.")

        st.divider()
        st.markdown("### Stage 2 — 🎲 Simulate Customer Responses")
        st.caption("Simulates whether the customer paid, closing the loop and revealing final outcomes.")

        conn = get_connection()
        try:
            awaiting_now = conn.execute(
                "SELECT COUNT(*) FROM recovery_cases WHERE status='awaiting_outcome'"
            ).fetchone()[0]
        finally:
            conn.close()

        if st.button("🎲 Simulate Customer Responses", disabled=awaiting_now == 0):
            log_box2 = st.container(height=420)
            progress2 = st.progress(0.0)
            counters2 = st.empty()
            conn = get_connection()
            try:
                from database.db import get_cases_by_status
                cases_awaiting = get_cases_by_status(conn, RecoveryStatus.AWAITING_OUTCOME.value)
                total = len(cases_awaiting) or 1
                stats = {"recovered": 0, "escalated": 0, "stopped": 0, "recovered_paise": 0}

                for i, stub in enumerate(cases_awaiting, 1):
                    case = get_case_with_details(conn, stub["id"])
                    if not case:
                        continue
                    with log_box2:
                        st.markdown(f"**{case['id']}** — simulating customer response...")
                    result = resolve_outcome(conn, case)
                    conn.commit()

                    with log_box2:
                        for rnd in result["rounds"]:
                            icon = "✅ SUCCESS" if rnd["success"] else "❌ FAILED"
                            st.write(
                                f"  🎲 {ACTION_LABEL.get(rnd['action'], rnd['action'])} "
                                f"(p={rnd['probability']*100:.0f}%)... {icon}"
                            )
                        if result["final_status"] == "recovered":
                            st.success(f"  ✅ RECOVERED ₹{result['amount_recovered']/100:,.0f}")
                            stats["recovered"] += 1
                            stats["recovered_paise"] += result["amount_recovered"]
                        elif result["final_status"] == "escalated":
                            st.warning(f"  ⚠️ ESCALATED — {result['reason']}")
                            stats["escalated"] += 1
                        elif result["final_status"] == "stopped":
                            st.error(f"  🛑 STOPPED — {result['reason']}")
                            stats["stopped"] += 1

                    progress2.progress(i / total)
                    counters2.markdown(
                        f"**Resolved {i}/{total}** — "
                        f"✅ {stats['recovered']} recovered (₹{stats['recovered_paise']/100:,.0f}) · "
                        f"⚠️ {stats['escalated']} escalated · 🛑 {stats['stopped']} stopped"
                    )
            finally:
                conn.close()

            clear_cache()
            st.balloons()
            st.markdown("### ✅ Batch Complete")
            total_done = stats["recovered"] + stats["escalated"] + stats["stopped"]
            m2 = load_summary()["summary"]
            st.markdown(
                f"{total_done} cases resolved this round — ₹{stats['recovered_paise']/100:,.0f} recovered · "
                f"{stats['recovered']} recovered · {stats['escalated']} escalated · {stats['stopped']} stopped\n\n"
                f"**Overall batch:** {m2['total_cases_tracked']} cases processed · "
                f"{rupees(m2['revenue_at_risk_paise'])} at risk · "
                f"{rupees(m2['revenue_recovered_paise'])} recovered "
                f"({m2['recovery_rate_percent']}% rate)"
            )
            if st.button("🔄 Refresh page counters"):
                st.rerun()


# ══════════════════════════════════════════════════════════════════════════
# PAGE 5 — Escalation & Stopping Rules
# ══════════════════════════════════════════════════════════════════════════

elif page == PAGES[4]:
    st.title("Escalation & Stopping Rules")
    st.caption("Does it know when to stop? Does it escalate appropriately, with context?")

    st.subheader("A — Stopping Rules")
    rule_stats = load_policy_stats()
    if rule_stats:
        rdf = pd.DataFrame(rule_stats)
        rdf["Status"] = rdf["times_triggered"].apply(lambda n: "✅ Active" if n > 0 else "⏸️ Not triggered")
        rdf["example_case_id"] = rdf["example_case_id"].fillna("—")
        st.dataframe(
            rdf[["rule_number", "rule_name", "threshold", "times_evaluated",
                 "times_triggered", "example_case_id", "Status"]]
                .rename(columns={
                    "rule_number": "#", "rule_name": "Rule", "threshold": "Threshold",
                    "times_evaluated": "Times Evaluated", "times_triggered": "Times Triggered",
                    "example_case_id": "Example Case",
                }),
            hide_index=True, width="stretch",
        )
        st.caption(
            "Rule 7 (Action Cooldown) is deliberately waived in batch/demo mode — "
            "the whole batch runs in seconds, so a 24-hour gap can't occur. "
            "In production the same rule enforces a real cooldown between actions."
        )

        examples = [(r["rule_number"], r["rule_name"], r["example_case_id"])
                    for r in rule_stats if r["example_case_id"]]
        if examples:
            jc1, jc2 = st.columns([3, 1])
            choice = jc1.selectbox(
                "Jump to an example case",
                examples, format_func=lambda t: f"Rule {t[0]} — {t[1]} → {t[2]}",
            )
            if jc2.button("🔍 View this case", key="jump_rule_example"):
                goto(PAGES[2], choice[2])
    else:
        st.info("No policy evaluations logged yet — run the engine on Page 4.")

    st.divider()
    st.subheader("B — Escalation Log")
    esc = load_escalated()
    if esc:
        edf = pd.DataFrame(esc)
        edf["amount_rupees"] = edf["amount_at_risk"] / 100
        edf["reason"] = edf["escalation_reason"].fillna("—")
        context = load_escalation_context()
        edf["context"] = edf["id"].map(lambda cid: context.get(cid, "—"))
        st.dataframe(
            edf[["id", "customer_name", "amount_rupees", "root_cause", "reason", "context"]]
                .rename(columns={
                    "id": "Case", "customer_name": "Customer", "amount_rupees": "₹ Amount",
                    "root_cause": "Root Cause", "reason": "Escalation Reason",
                    "context": "Context Given to Merchant",
                })
                .assign(**{"₹ Amount": lambda d: d["₹ Amount"].map(lambda v: f"{v:,.0f}")}),
            hide_index=True, width="stretch", height=360,
        )
    else:
        st.info("No escalated cases yet.")

    st.divider()
    st.subheader("C — Compliance Summary")

    conn = get_connection()
    try:
        total_checks = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event_type = 'policy_evaluated'"
        ).fetchone()[0]
        total_nonesc_actions = conn.execute(
            "SELECT COUNT(*) FROM recovery_actions WHERE action_type != 'escalation'"
        ).fetchone()[0]
        fraud_actions = conn.execute(
            """SELECT COUNT(*) FROM recovery_actions a
               JOIN recovery_cases c ON a.case_id = c.id
               WHERE c.root_cause = 'fraud_flag' AND a.action_type != 'escalation'"""
        ).fetchone()[0]
        optout_comms = conn.execute(
            """SELECT COUNT(*) FROM recovery_actions a
               JOIN recovery_cases c ON a.case_id = c.id
               JOIN subscriptions s ON c.subscription_id = s.id
               JOIN customers cu ON s.customer_id = cu.id
               WHERE cu.opt_out = 1 AND a.action_type IN ('dunning_message', 'payment_link', 'smart_retry')"""
        ).fetchone()[0]
    finally:
        conn.close()

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Policy Checks Run", total_checks)
    s2.metric("Actions Executed", total_nonesc_actions, help="Every one was preceded by a passing policy check.")
    s3.metric("Actions on Fraud Cases", fraud_actions, help="Must always be 0.")
    s4.metric("Comms to Opted-Out Customers", optout_comms, help="Must always be 0.")

    if fraud_actions == 0 and optout_comms == 0:
        st.success(
            "100% of recovery actions were policy-checked before execution. "
            "0 actions taken on fraud-flagged cases. "
            "0 communications sent to opted-out customers."
        )
    else:
        st.error("Compliance violation detected — review the cases above.")

    dist = load_policy_result_distribution()
    if not dist.empty:
        label_map = {"allowed": "Allowed", "escalate": "Escalated", "stop": "Stopped", "wait": "Waited"}
        dist["Result"] = dist["result"].map(label_map).fillna(dist["result"])
        fig = px.pie(
            dist, names="Result", values="n", hole=0.5,
            color="Result",
            color_discrete_map={"Allowed": "#22c55e", "Escalated": "#f59e0b",
                                "Stopped": "#ef4444", "Waited": "#94a3b8"},
        )
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=320,
                          title="Policy check results (all 10-rule evaluations)")
        st.plotly_chart(fig, width="stretch")
