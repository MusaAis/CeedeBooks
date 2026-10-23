"""Phase P3 fix: the per-business traction figures count the agent's own traffic only. Manual and demo invoices (the
relabelled test runs and the demo path) are shown apart in `not_traction`, never in `live` or `latest_paid` (rule 14)."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from agent import decision_log
from agent.config import config
from backend import auth, main, models


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "traction.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    auth.request_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


_n = [0]


def _invoice(status="paid", origin="agent", mode="live", amount=1.0, tx="tx"):
    _n[0] += 1
    vendor = asyncio.run(models.save_vendor(1, f"V{_n[0]}", "0x" + f"{_n[0]:040x}"))
    real = ("100", "NGN") if mode == "shadow" else (None, None)
    inv = asyncio.run(models.save_invoice(1, f"INV-{_n[0]}", vendor, None, amount, 0, "doc", origin=origin, mode=mode,
                                          real_amount=real[0], real_currency=real[1]))
    asyncio.run(models.update_invoice_status(1, inv, status, onchain_tx_id=(tx if status == "paid" else None)))
    return inv


def _traction(client):
    r = client.get("/businesses/ceedebooks/traction")
    assert r.status_code == 200
    return r.json()


def test_live_counts_only_the_agents_own_traffic(client):
    _invoice(origin="agent", amount=3.0); _invoice(origin="agent", status="escalated"); _invoice(origin="agent", status="held")
    _invoice(origin="demo", amount=0.01); _invoice(origin="demo", status="escalated")
    _invoice(origin="manual", amount=2.2)
    t = _traction(client)
    assert t["live"] == {"paid": 1, "held": 1, "escalated": 1, "volume_usdc": 3.0}


def test_demo_and_manual_are_shown_apart_and_never_added_in(client):
    _invoice(origin="demo", amount=0.01); _invoice(origin="demo", status="escalated")
    _invoice(origin="manual", amount=2.2)
    t = _traction(client)
    assert t["not_traction"]["demo"] == {"paid": 1, "held": 0, "escalated": 1, "volume_usdc": 0.01}
    assert t["not_traction"]["manual"] == {"paid": 1, "held": 0, "escalated": 0, "volume_usdc": 2.2}
    assert set(t["not_traction"]) == {"manual", "demo"}
    assert t["live"] == {"paid": 0, "held": 0, "escalated": 0, "volume_usdc": 0.0}


def test_the_latest_paid_list_leaves_out_demo_and_manual_payments(client):
    _invoice(origin="agent"); agent_no = f"INV-{_n[0]}"
    _invoice(origin="demo"); _invoice(origin="manual")
    _invoice(origin="agent", mode="shadow", amount=0.5); shadow_no = f"INV-{_n[0]}"
    paid = _traction(client)["latest_paid"]
    assert {p["invoice"] for p in paid} == {agent_no, shadow_no} and len(paid) == 2
    assert all(p["origin"] == "agent" for p in paid) and {p["mode"] for p in paid} == {"live", "shadow"}


def test_a_demo_payment_alone_leaves_the_traction_empty(client):
    _invoice(origin="demo", amount=0.01)
    t = _traction(client)
    assert t["live"]["paid"] == 0 and t["live"]["volume_usdc"] == 0.0 and t["latest_paid"] == []
