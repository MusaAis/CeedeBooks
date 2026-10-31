"""Phase R1 (v1.3.3): earned vendors, the rule and the record. A vendor graduates after N owner-approved shadow pay decisions in a
row; a disagreement revokes it. Both are ledger records only: nothing here pays anything. The chain is mocked (conftest.FakeChain)."""
import asyncio
import json

import aiosqlite
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log, earned, payables
from agent.config import config
from backend import admin_auth, auth, main, models, vendor_auth

ADMIN_1 = Account.create()
ADMIN_2 = Account.create()
ACME = "0x" + "bb" * 20
W1, W2 = "0x" + "c3" * 20, "0x" + "d4" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chains):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "earned.db"))
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


def _bearer(client, account=ADMIN_1, business=None):
    body = {"address": account.address} | ({"business": business} if business else {})
    ch = client.post("/admin/auth/challenge", json=body).json()
    sig = account.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig if sig.startswith("0x") else "0x" + sig})
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
    asyncio.run(models.add_category(asyncio.run(models.get_business_by_slug("acme"))["id"], "Hosting"))


_n = {"po": 0}


def _vendor(client, h, name="Lagos Hosting", wallet=W1):
    return client.post("/vendors", json={"name": name, "wallet_address": wallet}, headers=h).json()["id"]


def _shadow(client, h, vid, number, amount="0.5"):
    """A shadow invoice with its own PO and receipt. Returns the response."""
    _n["po"] += 1
    po = f"PO-{number}"
    p = client.post("/purchase-orders", json={"po_number": po, "vendor_id": vid, "amount_usdc": amount, "category": 0}, headers=h).json()
    assert client.post("/receipts", json={"po_id": p["id"]}, headers=h).status_code == 200
    return client.post("/invoices", json={
        "invoice_number": number, "vendor_id": vid, "amount_usdc": amount, "category": 0, "doc_hash": "aa" * 32, "po_number": po,
        "mode": "shadow", "real_amount": "1200.50", "real_currency": "NGN"}, headers=h)


def _approve(client, h, vid, number):
    iid = _shadow(client, h, vid, number).json()["invoice_id"]
    r = client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h)
    assert r.status_code == 200, r.text
    return iid


def _reject(client, h, vid, number):
    iid = _shadow(client, h, vid, number).json()["invoice_id"]
    assert client.post(f"/admin/invoices/{iid}/shadow-reject", headers=h).status_code == 200
    return iid


def _set_n(client, h, n):
    r = client.post("/admin/earned-vendors/settings", json={"earn_after": n}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _records(business_id=1):
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_log WHERE business_id = ? AND action IN ('VENDOR_GRADUATED', 'VENDOR_REVOKED') "
                                  "ORDER BY id", (business_id,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def _state(vid, business_id=1):
    return asyncio.run(earned.vendor_state(business_id, vid))


# ---- the streak rule itself

def test_the_streak_counts_agreements_at_the_end_with_no_disagreement_after_them():
    P, D = earned.PAY_AGREE, earned.DISAGREE
    assert earned.streak([]) == 0
    assert earned.streak([P, P, P]) == 3
    assert earned.streak([P, P, D]) == 0
    assert earned.streak([P, D, P, P]) == 2
    assert earned.streak([D, D]) == 0


def test_the_threshold_has_bounds_in_code():
    assert earned.DEFAULT_EARN_AFTER == 5 and (earned.MIN_EARN_AFTER, earned.MAX_EARN_AFTER) == (2, 50)
    assert [earned.valid_threshold(x) for x in (1, 2, 50, 51, True, 2.0, "5", None)] == [False, True, True, False, False, False, False, False]


# ---- the setting

def test_the_default_is_five_and_the_bounds_are_enforced_by_the_route(client, chain):
    h = _bearer(client)
    view = client.get("/admin/earned-vendors", headers=h).json()
    assert view["earn_after"] == 5 and view["min"] == 2 and view["max"] == 50 and view["vendors"] == []
    for bad in (1, 0, -3, 51, "5", 5.5, True, None):
        assert client.post("/admin/earned-vendors/settings", json={"earn_after": bad}, headers=h).status_code == 422, bad
    assert client.post("/admin/earned-vendors/settings", json={"earn_after": 5, "extra": 1}, headers=h).status_code == 422
    assert client.post("/admin/earned-vendors/settings", json={"earn_after": 7}, headers=h).json()["earn_after"] == 7
    assert client.get("/admin/earned-vendors", headers=h).json()["earn_after"] == 7


def test_the_routes_need_an_owner_session(client, chain):
    assert client.get("/admin/earned-vendors").status_code in (401, 403)
    assert client.post("/admin/earned-vendors/settings", json={"earn_after": 3}).status_code in (401, 403)


def test_an_older_database_gains_the_column_with_the_default(client, chain):
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("ALTER TABLE businesses DROP COLUMN earn_after")
            await db.commit()
        await models.init_db()
        await models.init_db()  # idempotent
        return (await models.get_business(1))["earn_after"]
    assert asyncio.run(go()) == 5


# ---- graduation and revocation

def test_a_vendor_graduates_after_n_approved_pay_decisions_and_the_record_is_a_signed_ledger_entry(client, chain):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "G-1")
    assert _records() == [] and _state(vid)["streak"] == 1 and not _state(vid)["graduated"]
    calls_before = len(chain.calls)
    _approve(client, h, vid, "G-2")
    recs = _records()
    assert len(recs) == 1
    r = recs[0]
    assert r["action"] == "VENDOR_GRADUATED" and r["subject"] == f"vendor:{vid}" and r["amount_usdc"] == 0 and r["model_used"] == "rules"
    assert r["hash_version"] == 2 and r["chain_tx_hash"] is None and r["onchain_tx_id"] is None
    assert "last 2 shadow pay decisions" in r["reasoning"] and "G-1, G-2" in r["reasoning"] and "pays nothing" in r["reasoning"]
    assert [c[0] for c in chain.calls[calls_before:]] == ["commit", "pay"]  # only the owner-approved payment: the record sent nothing
    st = _state(vid)
    assert st["graduated"] and st["streak"] == 2 and st["record_hash"] == r["reasoning_hash"]
    ok = client.get("/ledger/verify").json()
    assert ok["ok"] and ok["problem_count"] == 0  # the entry is chained and signed like any decision
    v = client.get(f"/decisions/{r['reasoning_hash']}/verify").json()
    assert v["verified"] is True and v["onchain"]["checked"] is False
    # recorded once: a third approval does not write a second graduation
    _approve(client, h, vid, "G-3")
    assert len(_records()) == 1


def test_a_disagreement_revokes_and_the_streak_starts_again(client, chain):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "R-1"); _approve(client, h, vid, "R-2")
    assert _state(vid)["graduated"]
    _reject(client, h, vid, "R-3")
    recs = _records()
    assert [x["action"] for x in recs] == ["VENDOR_GRADUATED", "VENDOR_REVOKED"]
    assert "disagreed" in recs[1]["reasoning"] and "R-3" in recs[1]["reasoning"]
    st = _state(vid)
    assert not st["graduated"] and st["streak"] == 0 and st["record_action"] == "VENDOR_REVOKED"
    _approve(client, h, vid, "R-4")
    assert not _state(vid)["graduated"]
    _approve(client, h, vid, "R-5")
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED", "VENDOR_REVOKED", "VENDOR_GRADUATED"]
    assert _state(vid)["graduated"]
    assert client.get("/ledger/verify").json()["ok"]


def test_a_rejection_before_graduation_resets_the_count(client, chain):
    h = _bearer(client)
    _set_n(client, h, 3)
    vid = _vendor(client, h)
    _approve(client, h, vid, "B-1"); _approve(client, h, vid, "B-2")
    _reject(client, h, vid, "B-3")
    _approve(client, h, vid, "B-4"); _approve(client, h, vid, "B-5")
    assert _records() == []  # 2 in a row since the rejection, 3 are needed
    _approve(client, h, vid, "B-6")
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED"]


def test_a_verdict_on_a_held_decision_counts_only_when_it_is_a_disagreement(client, chain, _env):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "H-1")
    _env["days"] = 1.0  # runway under 7 days: the rules hold the next one
    held = _shadow(client, h, vid, "H-2").json()
    assert held["decision"] == "hold"
    hash_ = asyncio.run(models.get_invoice(1, held["invoice_id"]))["reasoning_hash"]
    assert client.post(f"/admin/decisions/{hash_}/verdict", json={"verdict": "agree"}, headers=h).status_code == 200
    assert _state(vid)["streak"] == 1 and _records() == []  # "I would do the same" about a hold neither counts nor resets
    _env["days"] = 42.0
    _approve(client, h, vid, "H-3")
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED"]
    _env["days"] = 1.0
    held2 = _shadow(client, h, vid, "H-4").json()
    hash2 = asyncio.run(models.get_invoice(1, held2["invoice_id"]))["reasoning_hash"]
    assert client.post(f"/admin/decisions/{hash2}/verdict", json={"verdict": "disagree"}, headers=h).status_code == 200
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED", "VENDOR_REVOKED"]


def test_vendors_are_counted_on_their_own(client, chain):
    h = _bearer(client)
    _set_n(client, h, 2)
    a, b = _vendor(client, h, "A", W1), _vendor(client, h, "B", W2)
    _approve(client, h, a, "A-1"); _approve(client, h, a, "A-2")
    _reject(client, h, b, "B-1")
    assert _state(a)["graduated"] and not _state(b)["graduated"]
    assert [x["subject"] for x in _records()] == [f"vendor:{a}"]
    view = {v["id"]: v for v in client.get("/admin/earned-vendors", headers=h).json()["vendors"]}
    assert view[a]["graduated"] and view[a]["streak"] == 2 and view[b]["streak"] == 0 and not view[b]["graduated"]


def test_lowering_the_threshold_graduates_at_once_and_raising_it_takes_nothing_away(client, chain):
    h = _bearer(client)
    vid = _vendor(client, h)
    _approve(client, h, vid, "T-1"); _approve(client, h, vid, "T-2")
    assert _records() == []  # the default of 5 is not met
    out = _set_n(client, h, 2)
    assert out["written"] == [{"vendor_id": vid, "action": "VENDOR_GRADUATED"}] and out["vendors"][0]["graduated"]
    out = _set_n(client, h, 9)
    assert out["written"] == [] and out["vendors"][0]["graduated"] and len(_records()) == 1
    assert client.get("/admin/actions", headers=h).json()["actions"][0]["action"] == "earn_after_set"


def test_no_graduation_without_a_shadow_pay_decision(client, chain):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    assert _set_n(client, h, 2)["written"] == [] and _records() == [] and not _state(vid)["graduated"]


# ---- fail closed

def test_a_record_that_cannot_be_written_never_blocks_the_verdict_and_is_retried(client, chain, monkeypatch):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "F-1")
    real = decision_log.log_decision

    async def broken(action, *a, **k):
        if action in earned.ACTIONS:
            raise RuntimeError("no usable ledger key")
        return await real(action, *a, **k)

    monkeypatch.setattr(earned.decision_log, "log_decision", broken)
    _approve(client, h, vid, "F-2")  # the payment and the verdict stand
    assert _records() == [] and not _state(vid)["graduated"] and _state(vid)["streak"] == 2
    monkeypatch.setattr(earned.decision_log, "log_decision", real)
    assert _set_n(client, h, 2)["written"] == [{"vendor_id": vid, "action": "VENDOR_GRADUATED"}]


def test_a_graduation_a_disagreement_has_ended_never_reads_as_graduated_even_if_the_revocation_was_not_written(client, chain, monkeypatch):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "X-1"); _approve(client, h, vid, "X-2")
    assert _state(vid)["graduated"]
    real = decision_log.log_decision

    async def broken(action, *a, **k):
        if action in earned.ACTIONS:
            raise RuntimeError("no usable ledger key")
        return await real(action, *a, **k)

    monkeypatch.setattr(earned.decision_log, "log_decision", broken)
    _reject(client, h, vid, "X-3")
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED"]  # revocation not written...
    assert not _state(vid)["graduated"]  # ...yet the state is already not graduated
    monkeypatch.setattr(earned.decision_log, "log_decision", real)
    _set_n(client, h, 2)
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED", "VENDOR_REVOKED"]


# ---- isolation (rule 20)

def test_another_business_sees_and_changes_only_its_own(client, chain):
    _add_acme()
    h1, h2 = _bearer(client), _bearer(client, ADMIN_2, business="acme")
    _set_n(client, h1, 2)
    vid = _vendor(client, h1)
    _approve(client, h1, vid, "I-1"); _approve(client, h1, vid, "I-2")
    assert _state(vid)["graduated"]
    view2 = client.get("/admin/earned-vendors", headers=h2).json()
    assert view2["earn_after"] == 5 and view2["vendors"] == []
    assert _set_n(client, h2, 3)["earn_after"] == 3
    assert client.get("/admin/earned-vendors", headers=h1).json()["earn_after"] == 2
    assert _records(business_id=2) == [] and len(_records(business_id=1)) == 1
    assert asyncio.run(earned.vendor_state(2, vid))["graduated"] is False  # the same id in another business means nothing


# ---- public figures do not move (rules 51, 53 and 61)

def _public(client):
    return {p: client.get(p).json() for p in ("/stats", "/outcomes", "/businesses/ceedebooks/traction", "/metrics/decisions")}


def test_a_graduation_or_a_revocation_moves_no_public_figure(client, chain):
    h = _bearer(client)
    vid = _vendor(client, h)
    before = _public(client)
    biz = asyncio.run(models.get_business(1))
    for action in (earned.ACTION_GRADUATED, earned.ACTION_REVOKED):
        asyncio.run(decision_log.log_decision(action, earned.subject_for(vid), "record", model_used="rules", business=biz))
    assert len(_records()) == 2
    assert _public(client) == before
    assert asyncio.run(decision_log.stats()) == asyncio.run(decision_log.stats(1))


def test_graduating_through_the_routes_moves_no_live_figure_and_shadow_stays_apart(client, chain):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "P-1")
    mid = _public(client)
    _approve(client, h, vid, "P-2")  # this one graduates the vendor
    after = _public(client)
    assert len(_records()) == 1
    for path in ("/stats",):
        live_mid = {k: mid[path][k] for k in ("paid", "paid_usdc", "held", "escalated", "refused", "decisions")}
        live_after = {k: after[path][k] for k in live_mid}
        assert live_mid == live_after == {"paid": 0, "paid_usdc": 0, "held": 0, "escalated": 0, "refused": 0, "decisions": 0}
    assert after["/businesses/ceedebooks/traction"]["live"] == {"paid": 0, "held": 0, "escalated": 0, "volume_usdc": 0.0}


def test_the_public_decision_list_shows_the_record_with_no_reasoning_text_leak_beyond_what_is_already_public(client, chain):
    h = _bearer(client)
    _set_n(client, h, 2)
    vid = _vendor(client, h)
    _approve(client, h, vid, "L-1"); _approve(client, h, vid, "L-2")
    rows = client.get("/decisions?limit=10").json()["decisions"]
    rec = next(d for d in rows if d["action"] == "VENDOR_GRADUATED")
    assert rec["subject"] == f"vendor:{vid}" and rec["chain_tx_hash"] is None and rec["amount_usdc"] == 0
    assert json.dumps(rec).count("0x" + "c3" * 20) == 0  # the wallet is in the record's reasoning, fetched by hash, not in the list
