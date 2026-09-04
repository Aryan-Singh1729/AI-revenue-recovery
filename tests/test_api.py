from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_api_endpoints():
    print("Testing API Endpoints...")

    # 1. Health check
    resp = client.get("/health")
    assert resp.status_code == 200, "Health check failed"
    print("✅ GET / -> OK")

    # 2. Get cases
    resp = client.get("/api/cases")
    assert resp.status_code == 200, "Failed to get cases"
    cases = resp.json()["cases"]
    assert len(cases) > 0, "No cases returned"
    print(f"✅ GET /api/cases -> {len(cases)} cases")

    # 3. Get single case
    first_case_id = cases[0]["id"]
    resp = client.get(f"/api/cases/{first_case_id}")
    assert resp.status_code == 200, "Failed to get single case"
    data = resp.json()
    assert "case" in data and "audit_trail" in data
    print(f"✅ GET /api/cases/{first_case_id} -> OK (Audit entries: {len(data['audit_trail'])})")

    # 4. Metrics Summary
    resp = client.get("/api/metrics/summary")
    assert resp.status_code == 200, "Failed to get metrics summary"
    summary = resp.json()
    assert "total_cases_tracked" in summary
    print(f"✅ GET /api/metrics/summary -> Revenue Recovered: {summary['revenue_recovered_paise']}")

    # 5. Metrics by root cause
    resp = client.get("/api/metrics/by-root-cause")
    assert resp.status_code == 200, "Failed to get by root cause"
    print(f"✅ GET /api/metrics/by-root-cause -> {len(resp.json()['data'])} groups")

    # 6. Metrics by intervention
    resp = client.get("/api/metrics/by-intervention")
    assert resp.status_code == 200, "Failed to get by intervention"
    print(f"✅ GET /api/metrics/by-intervention -> {len(resp.json()['data'])} groups")

    # 7. Metrics funnel
    resp = client.get("/api/metrics/funnel")
    assert resp.status_code == 200, "Failed to get funnel"
    print(f"✅ GET /api/metrics/funnel -> {len(resp.json()['data'])} status buckets")

    # 8. Recent activity
    resp = client.get("/api/activity/recent?limit=5")
    assert resp.status_code == 200, "Failed to get recent activity"
    print(f"✅ GET /api/activity/recent -> {len(resp.json()['events'])} events")

    print("\n✅ All API endpoints verified!")

if __name__ == "__main__":
    test_api_endpoints()
