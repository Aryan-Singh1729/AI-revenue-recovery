"""Quick database verification script."""
from database.db import get_connection, get_metrics_summary

conn = get_connection()

print("=== TABLE ROW COUNTS ===")
for table in ["customers", "subscriptions", "recovery_cases", "recovery_actions", "audit_log"]:
    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"  {table:20s} -> {count} rows")

print()
print("=== AGGREGATE METRICS ===")
m = get_metrics_summary(conn)
print(f"  Total cases:        {m['total_cases']}")
print(f"  Revenue at risk:    Rs {m['total_revenue_at_risk'] // 100:,}")
print(f"  Revenue recovered:  Rs {m['total_revenue_recovered'] // 100:,}")
print(f"  Recovery rate:      {m['recovery_rate']}%")
print(f"  Recovered:          {m['cases_recovered']}")
print(f"  Escalated:          {m['cases_escalated']}")
print(f"  Stopped:            {m['cases_stopped']}")
print(f"  In progress:        {m['cases_in_progress']}")
print(f"  Total actions:      {m['total_actions']}")

print()
print("=== ACTIVE CASES BY STATUS ===")
rows = conn.execute(
    "SELECT status, COUNT(*) as c FROM recovery_cases WHERE id LIKE 'RC-%' GROUP BY status"
).fetchall()
for r in rows:
    print(f"  {r[0]:20s} -> {r[1]}")

print()
print("=== HISTORICAL CASES BY STATUS ===")
rows = conn.execute(
    "SELECT status, COUNT(*) as c FROM recovery_cases WHERE id LIKE 'HRC-%' GROUP BY status"
).fetchall()
for r in rows:
    print(f"  {r[0]:20s} -> {r[1]}")

print()
print("=== ROOT CAUSE DISTRIBUTION (all cases) ===")
rows = conn.execute(
    "SELECT root_cause, COUNT(*) as c FROM recovery_cases WHERE root_cause IS NOT NULL GROUP BY root_cause ORDER BY c DESC"
).fetchall()
for r in rows:
    print(f"  {str(r[0] or 'NULL'):30s} -> {r[1]}")

# Active cases have NULL root_cause (to be filled by diagnoser)
null_count = conn.execute(
    "SELECT COUNT(*) FROM recovery_cases WHERE root_cause IS NULL"
).fetchone()[0]
print(f"  {'(undiagnosed - active)':30s} -> {null_count}")

conn.close()
print()
print("Verification complete.")
