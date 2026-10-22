"""Phase P2: the demo path. An owner loads sample records into their own business and runs three invoices through the real
pipeline. Everything is origin `demo`: never in the agent decision totals, never traction. The chain is always mocked."""
import asyncio
import sqlite3
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log, payables
from agent.config import config
from backend import admin_auth, auth, main, models

ADMIN = Account.create()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chain):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "demo.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    chain.approver_addr = ADMIN.address
    chain.vendor_ok = False  # the demo vendor is not approved on-chain until the owner approves it

    async def runway(amount, business):
        return 42.0
    monkeypatch.setattr(main.treasury, "runway_days", runway)

    async def narrative(prompt):  # the model is never called: the narrative is the code-written reason
        return "The vendor is not registered on-chain, so the agent will not pay it itself."
    monkeypatch.setattr(payables, "escalation_reasoning", narrative)
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _bearer(client, account=ADMIN):
    ch = client.post("/admin/auth/challenge", json={"address": account.address}).json()
    sig = account.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig if sig.startswith("0x") else "0x" + sig})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _rows(sql, args=()):
    with sqlite3.connect(config.db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, args)]


def test_the_seed_runs_three_invoices_through_the_pipeline_all_labelled_demo(client):
    h = _bearer(client)
    r = client.post("/demo/seed", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "demo" and body["run"] == 1 and body["vendor_wallet"] == ADMIN.address.lower()
    assert [i["decision"] for i in body["invoices"]] == ["escalate", "escalate", "escalate"]   # vendor unapproved, no PO, wrong amount
    inv = _rows("SELECT invoice_number, origin, mode, status, submitted_by FROM invoices ORDER BY id")
    assert [i["invoice_number"] for i in inv] == ["DEMO-1-A", "DEMO-1-B", "DEMO-1-C"]
    assert all(i["origin"] == "demo" and i["mode"] == "live" and i["status"] == "escalated" and i["submitted_by"] == "demo-seed" for i in inv)


def test_the_reasons_are_the_real_ones(client):
    client.post("/demo/seed", headers=_bearer(client))
    reasons = {r["subject"]: r["reasoning"] for r in _rows("SELECT subject, reasoning FROM audit_log WHERE action = 'INVOICE_ESCALATED'")}
    assert "not registered" in reasons["DEMO-1-A"].lower()
    assert "No PO number" in reasons["DEMO-1-B"] and "Amount mismatch" in reasons["DEMO-1-C"]


def test_an_already_approved_demo_vendor_gets_paid_the_small_amount_at_the_owners_own_wallet(client, chain):
    chain.vendor_ok = True
    body = client.post("/demo/seed", headers=_bearer(client)).json()
    assert [i["decision"] for i in body["invoices"]] == ["pay", "escalate", "escalate"]
    pays = [c for c in chain.calls if c[0] == "pay"]
    assert len(pays) == 1 and pays[0][1][0].lower() == ADMIN.address.lower() and pays[0][1][1] == 10_000   # 0.01 USDC to the owner


def test_the_owner_can_release_a_demo_escalation_maker_checker_does_not_block_it(client):
    h = _bearer(client)
    client.post("/demo/seed", headers=h)
    row = _rows("SELECT id FROM invoices WHERE invoice_number = 'DEMO-1-B'")[0]
    assert asyncio.run(models.get_invoice(1, row["id"]))["submitted_by"] == "demo-seed"
    # the maker-checker refusal is for the wallet that submitted the invoice: demo-seed is not the admin's wallet
    from agent import contract
    contract.chain_for(asyncio.run(models.get_business(1))).esc_key = "0x" + "ee" * 32
    esc = client.get(f"/admin/invoices/{row['id']}/escalation?purpose=approve", headers=h)
    assert esc.status_code != 403, esc.text


def test_demo_decisions_never_count_in_the_agent_totals_or_traction(client):
    client.post("/demo/seed", headers=_bearer(client))
    stats = client.get("/stats").json()
    assert (stats["paid"], stats["held"], stats["escalated"], stats["decisions"]) == (0, 0, 0, 0)
    demo = client.get("/stats").json()["submissions"]["by_origin"]["demo"]
    assert demo["invoices_processed"] == 3
    out = client.get("/outcomes").json()["by_ownership"]["own"]
    assert out["agent"]["processed"] == 0 and out["demo"]["processed"] == 3 and out["demo"]["escalated"]["waiting"] == 3


def test_the_public_feed_marks_demo_rows_and_only_them(client):
    asyncio.run(decision_log.log_decision("INVOICE_PAID", "REAL-1", "ok", model_used="rules", amount=1.0,
                                          business=asyncio.run(models.get_business(1))))
    client.post("/demo/seed", headers=_bearer(client))
    feed = client.get("/decisions?limit=10").json()["decisions"]
    assert {d["subject"]: d["is_demo"] for d in feed if d["subject"] in ("REAL-1", "DEMO-1-A", "DEMO-1-B", "DEMO-1-C")} == {
        "REAL-1": False, "DEMO-1-A": True, "DEMO-1-B": True, "DEMO-1-C": True}


def test_every_demo_decision_is_in_the_signed_ledger_and_verifies(client):
    client.post("/demo/seed", headers=_bearer(client))
    v = client.get("/ledger/verify?business=ceedebooks").json()
    assert v["ok"] is True and v["problem_count"] == 0


def test_a_second_run_gets_new_numbers_and_the_same_vendor(client):
    h = _bearer(client)
    client.post("/demo/seed", headers=h)
    r = client.post("/demo/seed", headers=h)
    assert r.status_code == 200 and r.json()["run"] == 2
    assert [i["invoice_number"] for i in r.json()["invoices"]] == ["DEMO-2-A", "DEMO-2-B", "DEMO-2-C"]
    assert len(_rows("SELECT id FROM vendors WHERE name = ?", (models.DEMO_VENDOR,))) == 1


def test_it_stops_after_two_runs_a_day(client):
    h = _bearer(client)
    assert client.post("/demo/seed", headers=h).status_code == 200
    assert client.post("/demo/seed", headers=h).status_code == 200
    assert client.post("/demo/seed", headers=h).status_code == 429
    with sqlite3.connect(config.db_path) as db:   # a day later it runs again
        db.execute("UPDATE invoices SET created_at = ?", (time.time() - 90000,))
    assert client.post("/demo/seed", headers=h).status_code == 200


def test_only_a_wallet_signed_admin_can_seed(client):
    assert client.post("/demo/seed").status_code == 401
    key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(1, auth.hash_key(key), "buyer", None, "t"))
    assert client.post("/demo/seed", headers={"X-API-Key": key}).status_code == 403   # a buyer API key is not an admin session


def test_no_usable_category_is_a_clear_refusal_and_nothing_is_created(client, chain):
    chain.category_cap = 0
    r = client.post("/demo/seed", headers=_bearer(client))
    assert r.status_code == 422 and "category" in r.json()["detail"].lower()
    assert _rows("SELECT id FROM invoices") == [] and _rows("SELECT id FROM purchase_orders") == []


def test_an_unreadable_chain_creates_nothing(client, chain):
    h = _bearer(client)
    chain.broken = True
    r = client.post("/demo/seed", headers=h)
    assert r.status_code in (503, 401)   # the session itself ends when the approver cannot be read; either way nothing is created
    assert _rows("SELECT id FROM invoices") == []


def test_a_failure_on_one_invoice_is_reported_and_does_not_hide_the_others(client, monkeypatch):
    real = payables.process_invoice

    async def flaky(invoice, runway, business):
        if invoice.invoice_number.endswith("-B"):
            raise RuntimeError("circle down")
        return await real(invoice, runway, business)
    monkeypatch.setattr(main.payables, "process_invoice", flaky)
    body = client.post("/demo/seed", headers=_bearer(client)).json()
    assert [i["decision"] for i in body["invoices"]] == ["escalate", "error", "escalate"]
    assert _rows("SELECT status FROM invoices WHERE invoice_number = 'DEMO-1-B'")[0]["status"] == "error"


def test_a_business_only_ever_seeds_itself(client):
    client.post("/demo/seed", headers=_bearer(client))
    assert {r["business_id"] for r in _rows("SELECT business_id FROM invoices")} == {1}
    assert {r["business_id"] for r in _rows("SELECT business_id FROM vendors")} == {1}


def test_the_admin_invoice_list_carries_the_origin_so_the_site_can_label_demo_rows(client):
    h = _bearer(client)
    client.post("/demo/seed", headers=h)
    rows = client.get("/admin/invoices", headers=h).json()["invoices"]
    assert {r["origin"] for r in rows} == {"demo"} and all("submitted_by" not in r for r in rows)
