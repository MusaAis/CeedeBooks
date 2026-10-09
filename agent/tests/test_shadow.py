"""Phase L4: shadow mode. A real bill is read and decided; the owner approves; only then a testnet payment mirrors it.
The chain is always mocked (conftest.FakeChain); the pipeline itself (match, rules, hashing, payment order) is real."""
import asyncio
import json
import sqlite3

import aiosqlite
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log, payables
from agent.config import config
from backend import admin_auth, auth, main, models, vendor_auth

ADMIN_1 = Account.create()
ADMIN_2 = Account.create()
ACME = "0x" + "bb" * 20
WALLET = "0x" + "c3" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chains):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "shadow.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    vendor_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    chains.get(config.budget_enforcer_address, 1).approver_addr = ADMIN_1.address
    chains.get(ACME, 2).approver_addr = ADMIN_2.address
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)
    runway = {"days": 42.0}

    async def fake_runway(amount, business):
        return runway["days"]

    monkeypatch.setattr(main.treasury, "runway_days", fake_runway)
    return runway


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _sign(account, message):
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _bearer(client, account=ADMIN_1, business=None):
    body = {"address": account.address} | ({"business": business} if business else {})
    ch = client.post("/admin/auth/challenge", json=body).json()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(account, ch["message"])})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _add_acme():
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, approver_address, agent_address, contract_version, status, created_at) "
                "VALUES ('Acme', 'acme', ?, ?, ?, 2, 'active', 0)", (ACME, ADMIN_2.address, "0x" + "ab" * 20))
            await db.commit()
    asyncio.run(go())


def _setup(client, h, number="PO-1", amount="0.5", receipt=True, wallet=WALLET):
    vid = client.post("/vendors", json={"name": "Lagos Hosting", "wallet_address": wallet}, headers=h).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": number, "vendor_id": vid, "amount_usdc": amount, "category": 0}, headers=h).json()
    if receipt:
        assert client.post("/receipts", json={"po_id": po["id"]}, headers=h).status_code == 200
    return vid


def _shadow(client, h, vid, number="INV-1", po="PO-1", amount="0.5", real="1200.50", cur="NGN", **extra):
    body = {"invoice_number": number, "vendor_id": vid, "amount_usdc": amount, "category": 0, "doc_hash": "aa" * 32,
            "po_number": po, "mode": "shadow", "real_amount": real, "real_currency": cur} | extra
    return client.post("/invoices", json=body, headers=h)


def _audit(action):
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def _invoice(invoice_id, business_id=1):
    return dict(asyncio.run(models.get_invoice(business_id, invoice_id)))


# ---- submitting

def test_a_shadow_invoice_needs_the_real_amount_and_currency(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    base = {"invoice_number": "X", "vendor_id": vid, "amount_usdc": "0.5", "category": 0, "doc_hash": "aa", "po_number": "PO-1", "mode": "shadow"}
    assert client.post("/invoices", json=base, headers=h).status_code == 422
    assert client.post("/invoices", json=base | {"real_amount": "10"}, headers=h).status_code == 422
    assert client.post("/invoices", json=base | {"real_currency": "NGN"}, headers=h).status_code == 422


def test_real_amounts_are_exact_decimals_and_currencies_are_three_capitals(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    assert _shadow(client, h, vid, number="A", real="10.123", cur="NGN").status_code == 422   # 3 places
    assert _shadow(client, h, vid, number="B", real="0", cur="NGN").status_code == 422
    assert _shadow(client, h, vid, number="C", real="-5", cur="NGN").status_code == 422
    assert _shadow(client, h, vid, number="D", real="10", cur="ngn").status_code == 422
    assert _shadow(client, h, vid, number="E", real="10", cur="NAIRA").status_code == 422


def test_a_live_invoice_cannot_carry_a_real_amount_and_shadow_cannot_carry_an_origin(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    live = {"invoice_number": "L", "vendor_id": vid, "amount_usdc": "0.5", "category": 0, "doc_hash": "aa", "po_number": "PO-1"}
    assert client.post("/invoices", json=live | {"real_amount": "10", "real_currency": "NGN"}, headers=h).status_code == 422
    assert _shadow(client, h, vid, number="S", origin="demo").status_code == 422


def test_a_vendor_cannot_submit_a_shadow_invoice(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    key = auth.generate_key("vendor")
    asyncio.run(models.save_api_key(1, auth.hash_key(key), "vendor", vid, "v"))
    r = _shadow(client, {"X-API-Key": key}, vid)
    assert r.status_code == 403


def test_preflight_of_a_shadow_invoice_says_it_would_wait_for_the_owner(client, chain, monkeypatch):
    h = _bearer(client)
    vid = _setup(client, h)
    body = {"invoice_number": "P", "vendor_id": vid, "amount_usdc": "0.5", "category": 0, "doc_hash": "aa", "po_number": "PO-1",
            "mode": "shadow", "real_amount": "10", "real_currency": "NGN"}
    r = client.post("/invoices/preflight", json=body, headers=h).json()
    assert r["outcome"] == "would_await_owner"
    assert client.post("/invoices/preflight", json={k: v for k, v in body.items() if k not in ("mode", "real_amount", "real_currency")}, headers=h).json()["outcome"] == "would_pay"


# ---- the decision is parked, hashed, and nothing moves

def test_a_shadow_decision_to_pay_is_parked_hashed_and_sends_nothing(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    r = _shadow(client, h, vid)
    assert r.status_code == 200 and r.json()["decision"] == "await_owner"
    inv = _invoice(r.json()["invoice_id"])
    assert inv["status"] == "awaiting_owner" and inv["mode"] == "shadow" and inv["real_amount"] == "1200.50" and inv["real_currency"] == "NGN"
    assert chain.calls == []  # not committed, not paid, not escalated
    row = _audit("INVOICE_SHADOW_PENDING")[0]
    assert "real bill 1200.50 NGN, mirrored as 0.5 USDC" in row["reasoning"]
    assert json.loads(row["hash_input"])["reasoning"] == row["reasoning"]  # the hash covers the real amount
    assert decision_log.compute_reasoning_hash(row["action"], row["subject"], row["reasoning"], row["amount_usdc"], row["model_used"],
                                               row["timestamp"], asyncio.run(models.get_business(1)))[1] == row["reasoning_hash"]


def test_a_parked_shadow_decision_is_not_counted_as_paid_held_or_escalated(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    _shadow(client, h, vid)
    s = client.get("/stats").json()
    assert (s["paid"], s["held"], s["escalated"], s["decisions"]) == (0, 0, 0, 0)
    assert s["shadow"]["awaiting_owner"] == 1 and s["shadow"]["paid"] == 0
    assert s["submissions"]["invoices_processed"] == 0


# ---- approve: a real payment, limits still enforced by the contract

def test_approving_pays_through_commit_then_pay_and_records_agreement(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    pending = _invoice(iid)["reasoning_hash"]
    r = client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "paid"
    assert [c[0] for c in chain.calls] == ["commit", "pay"]  # commit first, then pay
    inv = _invoice(iid)
    assert inv["status"] == "paid" and inv["reasoning_hash"] != pending and inv["onchain_tx_id"] == "tx-pay"
    paid_row = _audit("INVOICE_SHADOW_PAID")[0]
    assert pending[:12] in paid_row["reasoning"] and "1200.50 NGN" in paid_row["reasoning"]
    s = client.get("/stats").json()
    assert s["paid"] == 0 and s["paid_usdc"] == 0  # live numbers untouched
    assert s["shadow"]["paid"] == 1 and s["shadow"]["mirrored_usdc"] == 0.5 and s["shadow"]["real_totals"] == {"NGN": "1200.50"}
    assert s["shadow"]["agreement"] == {"agree": 1, "disagree": 0, "n": 1, "rate": 1.0}
    assert s["submissions"]["payment_volume_usdc"] == 0  # never summed with live volume


def test_approving_twice_pays_once(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 200
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 409
    assert [c[0] for c in chain.calls].count("pay") == 1


def test_the_claim_is_atomic(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    assert asyncio.run(models.claim_shadow_approval(1, iid)) is True
    assert asyncio.run(models.claim_shadow_approval(1, iid)) is False
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 409  # approving, not awaiting


def test_a_failed_payment_leaves_it_waiting_with_no_verdict_and_can_be_retried(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    real_pay = chain.pay

    def refuse(*a):
        raise RuntimeError("Exceeds per-tx limit")
    chain.pay = refuse
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 502
    assert _invoice(iid)["status"] == "awaiting_owner"
    assert client.get("/stats").json()["shadow"]["agreement"]["n"] == 0
    chain.pay = real_pay
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 200
    assert _invoice(iid)["status"] == "paid"


def test_an_invoice_the_chain_already_paid_is_not_paid_twice(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    chain.paid = True
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 200
    assert chain.calls == []


# ---- reject

def test_rejecting_pays_nothing_and_records_the_disagreement(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    pending = _invoice(iid)["reasoning_hash"]
    assert client.post(f"/admin/invoices/{iid}/shadow-reject", headers=h).status_code == 200
    assert chain.calls == [] and _invoice(iid)["status"] == "rejected"
    s = client.get("/stats").json()["shadow"]
    assert s["agreement"] == {"agree": 0, "disagree": 1, "n": 1, "rate": 0.0} and s["rejected"] == 1 and s["paid"] == 0
    assert client.post(f"/admin/invoices/{iid}/shadow-reject", headers=h).status_code == 409
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 409
    assert client.post(f"/admin/decisions/{pending}/verdict", json={"verdict": "agree"}, headers=h).status_code == 409


# ---- verdicts on decisions that moved no money

def test_a_held_shadow_decision_takes_one_final_verdict(client, chain, _env):
    _env["days"] = 1.0  # runway under 7 days: the rules hold it
    h = _bearer(client)
    vid = _setup(client, h)
    r = _shadow(client, h, vid)
    assert r.json()["decision"] == "hold"
    held = _invoice(r.json()["invoice_id"])
    assert "Shadow mode: real bill 1200.50 NGN" in _audit("INVOICE_HELD")[0]["reasoning"]
    url = f"/admin/decisions/{held['reasoning_hash']}/verdict"
    assert client.post(url, json={"verdict": "agree"}, headers=h).status_code == 200
    assert client.post(url, json={"verdict": "disagree"}, headers=h).status_code == 409  # final
    assert client.post(url, json={"verdict": "maybe"}, headers=h).status_code == 422
    assert client.get("/stats").json()["shadow"]["agreement"] == {"agree": 1, "disagree": 0, "n": 1, "rate": 1.0}


def test_an_escalated_shadow_decision_carries_the_real_amount_and_takes_a_verdict(client, chain):
    h = _bearer(client)
    vid = _setup(client, h, receipt=False)  # no receipt: the three-way match fails
    r = _shadow(client, h, vid)
    assert r.json()["decision"] == "escalate"
    assert "real bill 1200.50 NGN" in _audit("INVOICE_ESCALATED")[0]["reasoning"]
    esc = _invoice(r.json()["invoice_id"])
    assert client.post(f"/admin/decisions/{esc['reasoning_hash']}/verdict", json={"verdict": "agree"}, headers=h).status_code == 200


def test_verdicts_are_only_for_shadow_decisions_that_are_settled_by_the_agent(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    live = client.post("/invoices", json={"invoice_number": "LIVE-1", "vendor_id": vid, "amount_usdc": "0.5", "category": 0,
                                          "doc_hash": "aa", "po_number": "PO-1"}, headers=h)
    assert live.status_code == 200
    live_hash = _invoice(live.json()["invoice_id"])["reasoning_hash"]
    assert client.post(f"/admin/decisions/{live_hash}/verdict", json={"verdict": "agree"}, headers=h).status_code == 404
    assert client.post("/admin/decisions/" + "0" * 64 + "/verdict", json={"verdict": "agree"}, headers=h).status_code == 404
    vid2 = _setup(client, h, number="PO-2", wallet="0x" + "d4" * 20)
    waiting = _invoice(_shadow(client, h, vid2, number="INV-2", po="PO-2").json()["invoice_id"])
    assert client.post(f"/admin/decisions/{waiting['reasoning_hash']}/verdict", json={"verdict": "agree"}, headers=h).status_code == 409


# ---- isolation between businesses

def test_another_business_admin_cannot_touch_a_shadow_invoice(client, chain):
    _add_acme()
    h1 = _bearer(client)
    vid = _setup(client, h1)
    iid = _shadow(client, h1, vid).json()["invoice_id"]
    pending = _invoice(iid)["reasoning_hash"]
    h2 = _bearer(client, ADMIN_2, "acme")
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h2).status_code == 404
    assert client.post(f"/admin/invoices/{iid}/shadow-reject", headers=h2).status_code == 404
    assert client.post(f"/admin/decisions/{pending}/verdict", json={"verdict": "agree"}, headers=h2).status_code == 404
    assert _invoice(iid)["status"] == "awaiting_owner" and chain.calls == []
    assert client.get("/stats?business=acme").json()["shadow"]["awaiting_owner"] == 0
    assert client.get("/stats?business=ceedebooks").json()["shadow"]["awaiting_owner"] == 1


def test_a_buyer_key_is_not_enough_for_the_shadow_admin_routes(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    iid = _shadow(client, h, vid).json()["invoice_id"]
    key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(1, auth.hash_key(key), "buyer", None, "k"))
    for path in (f"/admin/invoices/{iid}/shadow-approve", f"/admin/invoices/{iid}/shadow-reject"):
        assert client.post(path, headers={"X-API-Key": key}).status_code == 403
        assert client.post(path).status_code == 401
    assert client.post("/admin/decisions/" + "0" * 64 + "/verdict", json={"verdict": "agree"}).status_code == 401


def test_the_same_invoice_number_can_be_shadowed_in_two_businesses(client, chain):
    _add_acme()
    h1, h2 = _bearer(client), _bearer(client, ADMIN_2, "acme")
    v1 = _setup(client, h1)
    v2 = _setup(client, h2)
    assert _shadow(client, h1, v1).status_code == 200
    assert _shadow(client, h2, v2).status_code == 200


# ---- the public summary

def test_the_public_summary_keeps_live_and_shadow_apart_and_shows_n(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    a = _shadow(client, h, vid, number="S-1").json()["invoice_id"]
    vid2 = _setup(client, h, number="PO-2", wallet="0x" + "d4" * 20)
    b = _shadow(client, h, vid2, number="S-2", po="PO-2").json()["invoice_id"]
    client.post(f"/admin/invoices/{a}/shadow-approve", headers=h)
    client.post(f"/admin/invoices/{b}/shadow-reject", headers=h)
    t = client.get("/businesses/ceedebooks/traction").json()
    assert t["shadow"]["agreement"] == {"agree": 1, "disagree": 1, "n": 2, "rate": 0.5}
    assert t["shadow"]["paid"] == 1 and t["shadow"]["mirrored_usdc"] == 0.5
    assert t["live"] == {"paid": 0, "held": 0, "escalated": 0, "volume_usdc": 0.0}
    paid = t["latest_paid"][0]
    assert paid["mode"] == "shadow" and paid["real_amount"] == "1200.50" and paid["real_currency"] == "NGN" and paid["reasoning_hash"]
    assert t["business"]["kind"] == "own" and t["business"]["contract"] == config.budget_enforcer_address


def test_the_rate_is_null_with_no_verdicts_and_no_private_fields_leak(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    _shadow(client, h, vid)
    t = client.get("/businesses/ceedebooks/traction").json()
    assert t["shadow"]["agreement"] == {"agree": 0, "disagree": 0, "n": 0, "rate": None}
    text = json.dumps(t).lower()
    for private in ("contact", "ip_hash", "circle_wallet_id", "admin_address", "api_key", "owner_wallet"):
        assert private not in text
    assert client.get("/businesses/nope/traction").status_code == 404


def test_a_pending_business_has_no_public_summary(client, chain):
    _add_acme()

    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'pending' WHERE slug = 'acme'")
            await db.commit()
    asyncio.run(go())
    assert client.get("/businesses/acme/traction").status_code == 404


# ---- old data and hash format

def test_live_decisions_are_unchanged(client, chain):
    h = _bearer(client)
    vid = _setup(client, h)
    r = client.post("/invoices", json={"invoice_number": "LIVE-2", "vendor_id": vid, "amount_usdc": "0.5", "category": 0,
                                       "doc_hash": "aa", "po_number": "PO-1"}, headers=h)
    assert r.json()["decision"] == "pay"
    row = _audit("INVOICE_PAID")[0]
    assert row["reasoning"] == "Matched PO and receipt, treasury healthy, vendor registered" and "shadow" not in row["hash_input"].lower()
    assert json.loads(row["hash_input"])["hash_version"] == 2
    s = client.get("/stats").json()
    assert s["paid"] == 1 and s["shadow"]["paid"] == 0


def test_an_older_database_gains_the_shadow_columns_and_its_invoices_stay_live(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    monkeypatch.setattr(config, "db_path", path)
    asyncio.run(models.init_db())
    con = sqlite3.connect(path)
    con.executescript("""
        DROP TABLE invoices;
        CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL DEFAULT 1, invoice_number TEXT NOT NULL,
          vendor_id INTEGER NOT NULL, po_id INTEGER, amount_usdc REAL NOT NULL, category INTEGER NOT NULL, doc_hash TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', reasoning_hash TEXT, onchain_tx_id TEXT, created_at REAL NOT NULL,
          origin TEXT NOT NULL DEFAULT 'agent', UNIQUE (business_id, invoice_number));
        INSERT INTO invoices (invoice_number, vendor_id, amount_usdc, category, doc_hash, status, created_at) VALUES ('OLD-1', 1, 2.2, 1, 'x', 'paid', 0);
    """)
    con.commit(); con.close()
    asyncio.run(models.init_db())
    asyncio.run(models.init_db())  # idempotent
    row = sqlite3.connect(path).execute("SELECT mode, real_amount, real_currency FROM invoices WHERE invoice_number = 'OLD-1'").fetchone()
    assert row == ("live", None, None)


def test_the_model_layer_refuses_a_half_shadow_invoice():
    with pytest.raises(ValueError):
        asyncio.run(models.save_invoice(1, "X", 1, None, 1.0, 0, "d", mode="shadow"))
    with pytest.raises(ValueError):
        asyncio.run(models.save_invoice(1, "X", 1, None, 1.0, 0, "d", mode="live", real_amount="5", real_currency="NGN"))


def test_a_business_not_confirmed_as_outside_is_labelled_so_and_an_outside_one_only_when_the_operator_says(client, chain):
    _add_acme()
    assert client.get("/businesses/acme/traction").json()["business"]["kind"] == "unconfirmed"

    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET external = 1 WHERE slug = 'acme'")
            await db.commit()
    asyncio.run(go())
    assert client.get("/businesses/acme/traction").json()["business"]["kind"] == "outside"
    assert client.get("/businesses/ceedebooks/traction").json()["business"]["kind"] == "own"


def test_the_admin_overview_names_the_business_for_the_copy_block(client, chain, monkeypatch):
    monkeypatch.setattr(main, "_chain_snapshot", lambda business: {"chain_ok": True, "approver": ADMIN_1.address.lower()})
    h = _bearer(client)
    assert client.get("/admin/overview", headers=h).json()["business"]["slug"] == "ceedebooks"
