from uuid import uuid4


def confirmation_profile(api, *, new_client=True):
    tag = uuid4().hex[:12]
    plan = api.post("/api/fee-plans", json={"name": f"Import Plan {tag}", "fee_rate_percent": "20.00"})
    assert plan.status_code == 201, plan.text
    profile = {"fee_plan_id": plan.json()["id"], "start_date": "2026-01-01"}
    if new_client:
        fc = api.post("/api/fcs", json={"name": f"Import FC {tag}"})
        assert fc.status_code == 201, fc.text
        profile.update(fc_id=fc.json()["id"], management_start_date="2026-01-01")
    return profile
