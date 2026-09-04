# AI Revenue Recovery

Razorpay AI Hackathon 2026 — Track 03: AI Revenue Recovery

An agent that detects failed subscription payments, diagnoses why each one
failed, picks the right recovery action, checks it against 10 bounded
stopping rules, executes it (real Razorpay test-mode Payment Links and
Orders), and tracks measured ₹ recovered across the batch — with compliant
escalation and a full audit trail for every decision.

## What this is

- **Domain:** failed subscription payment recovery for SaaS/recurring billing.
- **Architecture:** DB-driven, not webhook-driven. A SQLite queue is polled by
  a Python engine — no ngrok, fully reproducible, same seed → same batch →
  same outcomes every run.
- **Pragmatic hybrid:** synthetic failure data shaped exactly like Razorpay's
  real error payloads (test mode alone can't produce 9 distinct failure
  types), but real Razorpay API calls for recovery actions, and a
  deterministic, seeded outcome simulator standing in for the customer
  (no real customers exist in test mode to click a link).
- **AI is bounded:** used for exactly three tasks — ambiguous root-cause
  reasoning, intervention explanation, and dunning-message text — each with
  a deterministic fallback so the pipeline never stalls on an AI failure.
  Detection, policy, and every stopping rule are plain, auditable Python.

## Current batch (seed 30, 80 cases: 50 active + 30 historical)

| Metric | Value |
|---|---|
| Revenue at risk | ₹2,52,045 |
| Revenue recovered | ₹82,464 |
| Recovery rate | 32.7% |
| Recovered / Escalated / Stopped | 36 / 24 / 20 |

All 10 stopping rules fire at least once in the active batch (rule 7,
cooldown, is deliberately waived in batch mode and labelled as such — the
whole batch runs in seconds, so a 24-hour gap can never occur).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in real values if you have them — see below
```

`.env` works empty. Without real credentials the system runs entirely on its
simulated fallbacks — every action is still logged, every payment link still
gets a `rzp.io/i/...`-shaped URL, and the dashboard labels each one honestly
as real or simulated. To make it real:

- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` — a Razorpay **test mode** key
  pair. With these set, Payment Links and Orders are created via the real
  Razorpay API.
- `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` — an OpenAI-compatible
  endpoint (e.g. a local Kilo Code proxy). Without it, diagnosis and
  messaging fall back to deterministic text.

## Running it

```bash
# generate the demo batch (50 active + 30 historical cases)
python -m data.generate_batch
python -m data.seed_historical

# the dashboard — reads the SQLite DB directly, no server required
streamlit run dashboard/app.py

# optional: the REST API, if something else needs to consume the data
python main.py
```

## Testing

```bash
python -m tests.test_pipeline   # full pipeline, 14 correctness checks
python -m tests.test_e2e        # end-to-end acceptance test — the pre-demo gate
python -m tests.test_api        # FastAPI endpoint smoke test
```

`test_e2e.py` is the one to run before showing this to anyone: it regenerates
the database from scratch, runs the complete pipeline, and verifies every
case reaches a terminal state, every logged state transition is legal, every
case has a complete audit trail, the recovered/escalated/stopped counts sum
to the total, payment links are honestly labelled real-or-simulated, zero
policy violations occurred, and every plan-mandated edge case (below-minimum
amount, opt-out, fraud, high-value, disputed, network-error retry success,
the retry-count cap) is provably handled.

## The dashboard — 5 pages

Each page maps directly to a judging criterion. `streamlit run dashboard/app.py`.

1. **Recovery Command Center** — hero ₹ metrics, a Sankey funnel
   (Detected → Diagnosed → Policy-checked/Executed → Recovered / Escalated /
   Stopped), recovery-by-root-cause, and a live activity feed.
2. **Recovery Batch (Case Explorer)** — the full batch, filterable by status/
   root cause/amount, with a status badge and reason preview per case.
3. **Case Audit Trail** — pick any case, get the complete chronological
   record of what happened, why, what was chosen, whether policy allowed it,
   and the outcome — every step expandable to its raw JSON.
4. **Live Recovery Engine** — the complete loop, live, in two visible steps:
   **Run Recovery Engine** (diagnose → select → 10-rule policy check →
   execute, logging each sub-step) and **Simulate Customer Responses**
   (resolves the outcome, closing the loop). A gated **Regenerate demo
   batch** control resets everything for a repeat run.
5. **Escalation & Stopping Rules** — all 10 rules with times-triggered and
   an example case each, the full escalation log with the context handed to
   the merchant, and a compliance summary (0 actions on fraud cases, 0
   messages to opted-out customers, verified live against the database).

## Demo script (5-7 minutes)

The engine itself is fast — regenerating the batch and running both live
stages together takes under two seconds end to end (measured, not
estimated). The timing below is pacing for a live audience, not software
latency.

**0:00 – 0:45 — Open on Page 1.**
"This is a recovery agent for Razorpay merchants. It's already processed 80
failed payments — ₹2,52,045 at risk, ₹82,464 recovered, a 32.7% recovery
rate." Point at the Sankey funnel: "Not every case gets the same treatment —
some get automated recovery, some get blocked by policy and escalated or
stopped immediately."

**0:45 – 1:30 — Root cause variety.**
Point at the "Recovery by Root Cause" chart. "Nine different failure types —
insufficient funds, expired cards, fraud flags, disputes — each gets a
different recovery strategy, not one-size-fits-all."

**1:30 – 2:30 — Case Explorer (Page 2).**
Filter to Escalated. "These are fraud, disputes, high-value cases — the
agent recognized it shouldn't act alone and handed them to a human, with
full context." Click a recovered case, jump to its audit trail.

**2:30 – 4:00 — Audit Trail (Page 3).**
Walk one case top to bottom: the failure, the diagnosis, why that
intervention was chosen, all 10 policy rules checked, the action taken —
click the payment link (labelled real or simulated, honestly), the
AI-generated dunning message, the outcome. "Every decision, explained."

**4:00 – 5:30 — Live Recovery Engine (Page 4).**
Regenerate a fresh batch. Click **Run Recovery Engine** — watch the live log:
diagnosis, intervention selection, the 10-rule policy check, execution. Point
out a fraud case getting blocked instantly and a disputed case getting
stopped, not retried. Click **Simulate Customer Responses** — watch cases
flip to terminal states live, the recovered counter tick up in real time.

**5:30 – 6:30 — Stopping Rules (Page 5).**
"It doesn't retry forever, doesn't spam opted-out customers, doesn't chase
₹35 at a ₹50+ cost." Show the rules table — every rule fired at least once,
each with a real example case. Show the compliance summary: zero actions on
fraud cases, zero messages to opted-out customers, verified against the
live database, not asserted.

**6:30 – 7:00 — Close.**
"In production: swap test keys for live keys, add a webhook listener writing
into the same database, and this runs unchanged on real payments."
