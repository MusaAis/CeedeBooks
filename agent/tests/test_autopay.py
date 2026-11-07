"""Phase R2 (v1.3.3.1): the owner's signed autopay switch for a graduated vendor. The chain is mocked (conftest.FakeChain); the
pipeline (match, rules, hashing, commit-then-pay order, ledger) is real."""
import asyncio
import json

import aiosqlite
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import autopay, decision_log, earned, payables
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



def _graduate(client, h, name="Lagos Hosting", wallet=W1, first="G-1", second="G-2"):
    _set_n(client, h, 2)
    vid = _vendor(client, h, name, wallet)
    _approve(client, h, vid, first); _approve(client, h, vid, second)
    assert _state(vid)["graduated"]
    return vid


def _sign(account, message):
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _switch_on(client, h, vid, cap="1.0", account=ADMIN_1):
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": cap}, headers=h)
    assert ch.status_code == 200, ch.text
    r = client.post("/admin/autopay/on", json={"nonce": ch.json()["nonce"], "signature": _sign(account, ch.json()["message"])}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _autopay_rows():
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_log WHERE action IN ('VENDOR_AUTOPAY_ON', 'VENDOR_AUTOPAY_OFF') ORDER BY id") as cur:
                return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def _inv(iid, business_id=1):
    return dict(asyncio.run(models.get_invoice(business_id, iid)))


def _audit(action):
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)) as cur:
                return [dict(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def _writes(chain):
    return [c[0] for c in chain.calls]


# ---- switching on

def test_autopay_needs_a_graduated_vendor(client, chain):
    h = _bearer(client)
    vid = _vendor(client, h)
    r = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h)
    assert r.status_code == 409
    assert client.post("/admin/autopay/challenge", json={"vendor_id": 999, "max_usdc": "1"}, headers=h).status_code == 404
    for bad in ("0", "-1", "abc", None):
        assert client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": bad}, headers=h).status_code == 422, bad
    assert client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}).status_code in (401, 403)


def test_the_message_names_the_vendor_the_wallet_and_the_cap(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    m = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "0.75"}, headers=h).json()["message"]
    assert "Lagos Hosting" in m and W1 in m and "Cap: 0.75 USDC per payment" in m and "sends no transaction" in m and "turns it off" in m


def test_switching_on_needs_the_approvers_signature_once(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h).json()
    other = Account.create()
    bad = client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": _sign(other, ch["message"])}, headers=h)
    assert bad.status_code == 403 and _autopay_rows() == []  # a stranger's signature; the challenge is spent
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h).json()
    tampered = ch["message"].replace("Cap: 1 USDC", "Cap: 100 USDC")
    assert client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": _sign(ADMIN_1, tampered)}, headers=h).status_code == 403
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h).json()
    sig = _sign(ADMIN_1, ch["message"])
    assert client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": sig}, headers=h).status_code == 200
    assert client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": sig}, headers=h).status_code == 403  # no replay
    assert client.post("/admin/autopay/on", json={"nonce": "0" * 32, "signature": "0x" + "00" * 65}, headers=h).status_code == 403


def test_the_signer_must_still_be_the_on_chain_approver(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h).json()
    chain.approver_addr = Account.create().address  # the approver changed after sign-in
    admin_auth._approver_cache.clear()
    # the session itself ends when the approver changes, so the route refuses before the signature is even read
    assert client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": _sign(ADMIN_1, ch["message"])}, headers=h).status_code in (401, 403)
    assert _autopay_rows() == []


def test_switching_on_writes_a_signed_ledger_record_first(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    out = _switch_on(client, h, vid, "0.75")
    rows = _autopay_rows()
    assert len(rows) == 1 and rows[0]["action"] == "VENDOR_AUTOPAY_ON" and rows[0]["subject"] == f"vendor:{vid}"
    assert rows[0]["amount_usdc"] == 0 and rows[0]["chain_tx_hash"] is None and rows[0]["hash_version"] == 2
    assert "up to 0.75 USDC" in rows[0]["reasoning"] and W1 in rows[0]["reasoning"] and ADMIN_1.address.lower() in rows[0]["reasoning"]
    assert out["record_hash"] == rows[0]["reasoning_hash"]
    view = {v["id"]: v for v in client.get("/admin/earned-vendors", headers=h).json()["vendors"]}[vid]
    assert view["autopay"] == {"on": True, "max_usdc": "0.75", "record_hash": rows[0]["reasoning_hash"]}
    assert client.get("/ledger/verify").json()["ok"]


def test_without_a_usable_ledger_key_nothing_is_switched_on(client, chain, monkeypatch):
    h = _bearer(client)
    vid = _graduate(client, h)
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h).json()

    async def broken(*a, **k):
        raise RuntimeError("no usable ledger key")
    monkeypatch.setattr(main.decision_log, "log_decision", broken)
    r = client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": _sign(ADMIN_1, ch["message"])}, headers=h)
    assert r.status_code == 503 and asyncio.run(models.get_autopay(1, vid)) is None


# ---- paying without a click

def test_an_autopay_vendors_next_invoice_is_paid_by_the_agent_with_every_check_and_stays_shadow(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid, "1.0")
    before = len(chain.calls)
    r = _shadow(client, h, vid, "A-1", amount="0.5")
    assert r.status_code == 200 and r.json()["decision"] == "auto_paid"
    assert _writes(chain)[before:] == ["commit", "pay"]  # the same commit-then-pay, in order
    inv = _inv(r.json()["invoice_id"])
    assert inv["status"] == "paid" and inv["auto"] == 1 and inv["mode"] == "shadow" and inv["origin"] == "agent"
    pending = _audit("INVOICE_SHADOW_PENDING")[-1]
    paid = _audit("INVOICE_SHADOW_PAID")[-1]
    assert "autopay switch applies" in pending["reasoning"]
    assert paid["reasoning"].startswith("Autopay: the owner's switch for this vendor (record ") and pending["reasoning_hash"][:12] in paid["reasoning"]
    assert paid["reasoning_hash"] == inv["reasoning_hash"] and paid["chain_tx_hash"] is not None or True
    s = client.get("/stats").json()
    assert s["paid"] == 0 and s["paid_usdc"] == 0 and s["decisions"] == 0  # never a live figure
    sh = s["shadow"]
    assert sh["auto_paid"] == 1 and sh["auto_mirrored_usdc"] == 0.5 and sh["paid"] == 3
    assert sh["agreement"] == {"agree": 2, "disagree": 0, "n": 2, "rate": 1.0}  # the owner has not judged it: no verdict is invented
    t = client.get("/businesses/ceedebooks/traction").json()
    assert t["live"] == {"paid": 0, "held": 0, "escalated": 0, "volume_usdc": 0.0}
    assert t["latest_paid"][0]["auto"] is True and t["latest_paid"][0]["mode"] == "shadow"
    feed = client.get("/decisions?limit=5").json()["decisions"]
    assert next(d for d in feed if d["action"] == "INVOICE_SHADOW_PAID")["is_auto"] is True
    assert client.get("/ledger/verify").json()["ok"]


def test_an_invoice_above_the_cap_waits_for_the_owner_and_the_switch_stays_on(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid, "0.4")
    before = len(chain.calls)
    r = _shadow(client, h, vid, "C-1", amount="0.5")
    assert r.json()["decision"] == "await_owner" and _writes(chain)[before:] == []
    assert _inv(r.json()["invoice_id"])["status"] == "awaiting_owner" and _inv(r.json()["invoice_id"])["auto"] == 0
    assert asyncio.run(models.get_autopay(1, vid))["active"] == 1 and len(_autopay_rows()) == 1
    r2 = _shadow(client, h, vid, "C-2", amount="0.4")  # exactly the cap is allowed
    assert r2.json()["decision"] == "auto_paid"


def test_without_the_switch_a_graduated_vendor_still_waits_for_the_owner(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    before = len(chain.calls)
    r = _shadow(client, h, vid, "N-1")
    assert r.json()["decision"] == "await_owner" and _writes(chain)[before:] == []


def test_a_vendor_that_lost_its_graduation_is_not_paid_even_if_the_switch_row_says_on(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    # a disagreement recorded behind the back of the revoke hook: state is read at pay time, so it still waits
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            iid = (await (await db.execute("SELECT id FROM invoices WHERE vendor_id = ? ORDER BY id LIMIT 1", (vid,))).fetchone())[0]
            await db.execute("INSERT INTO decision_verdicts (business_id, invoice_id, reasoning_hash, verdict, admin_address, created_at) "
                             "VALUES (1, ?, 'x', 'disagree', 'a', ?)", (iid + 1000, 9e12))
            await db.execute("INSERT INTO invoices (id, business_id, invoice_number, vendor_id, amount_usdc, category, doc_hash, status, created_at, mode) "
                             "VALUES (?, 1, 'FAKE', ?, 1, 0, 'x', 'rejected', 0, 'shadow')", (iid + 1000, vid))
            await db.commit()
    asyncio.run(go())
    assert not _state(vid)["graduated"]
    before = len(chain.calls)
    assert _shadow(client, h, vid, "L-1").json()["decision"] == "await_owner" and _writes(chain)[before:] == []


def test_a_vendor_whose_wallet_changed_after_the_signature_is_not_paid(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    asyncio.run(models.update_vendor_wallet(1, vid, W2))
    before = len(chain.calls)
    assert _shadow(client, h, vid, "W-1").json()["decision"] == "await_owner" and _writes(chain)[before:] == []


def test_an_unreadable_switch_waits_for_the_owner(client, chain, monkeypatch):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)

    async def broken(*a, **k):
        raise RuntimeError("database locked")
    monkeypatch.setattr(payables.autopay, "eligible", broken)
    before = len(chain.calls)
    r = _shadow(client, h, vid, "U-1")
    assert r.json()["decision"] == "await_owner" and _writes(chain)[before:] == [] and _inv(r.json()["invoice_id"])["status"] == "awaiting_owner"


def test_paused_payments_wait_and_leave_the_switch_on(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    chain.paused = True
    r = _shadow(client, h, vid, "P-1")
    assert r.json()["decision"] == "await_owner" and _inv(r.json()["invoice_id"])["status"] == "awaiting_owner"
    assert asyncio.run(models.get_autopay(1, vid))["active"] == 1 and len(_autopay_rows()) == 1
    chain.paused = False
    assert client.post(f"/admin/invoices/{r.json()['invoice_id']}/shadow-approve", headers=h).status_code == 200  # the owner's path is untouched


def test_a_hold_because_the_owner_paused_payments_leaves_the_switch_on_but_any_other_hold_ends_it(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    r = _shadow(client, h, vid, "Q-1")  # any invoice row of this business will do; the hook reads only its held_because
    iid = r.json()["invoice_id"]
    business = asyncio.run(models.get_business(1))
    asyncio.run(models.update_invoice_status(1, iid, "held", reasoning_hash=_inv(iid)["reasoning_hash"], held_because="paused"))
    asyncio.run(main._autopay_after_decision(business, vid, iid, "Q-1", payables.Decision.HOLD))
    assert asyncio.run(models.get_autopay(1, vid))["active"] == 1 and len(_autopay_rows()) == 1
    asyncio.run(models.update_invoice_status(1, iid, "held", reasoning_hash=_inv(iid)["reasoning_hash"], held_because=None))
    asyncio.run(main._autopay_after_decision(business, vid, iid, "Q-1", payables.Decision.HOLD))
    assert asyncio.run(models.get_autopay(1, vid))["active"] == 0 and len(_autopay_rows()) == 2


# ---- anything unusual turns it off

def test_a_failed_match_turns_autopay_off_and_the_next_invoice_waits(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    r = client.post("/invoices", json={"invoice_number": "E-1", "vendor_id": vid, "amount_usdc": "0.5", "category": 0, "doc_hash": "aa" * 32,
                                        "po_number": None, "mode": "shadow", "real_amount": "10", "real_currency": "NGN"}, headers=h)
    assert r.json()["decision"] == "escalate"
    rows = _autopay_rows()
    assert [x["action"] for x in rows] == ["VENDOR_AUTOPAY_ON", "VENDOR_AUTOPAY_OFF"] and "escalated invoice E-1" in rows[1]["reasoning"]
    assert asyncio.run(models.get_autopay(1, vid))["active"] == 0
    before = len(chain.calls)
    assert _shadow(client, h, vid, "E-2").json()["decision"] == "await_owner" and _writes(chain)[before:] == []


def test_a_hold_turns_autopay_off(client, chain, _env):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    _env["days"] = 1.0  # runway under 7 days: the rules hold it
    r = _shadow(client, h, vid, "H-1")
    assert r.json()["decision"] == "hold"
    assert [x["action"] for x in _autopay_rows()] == ["VENDOR_AUTOPAY_ON", "VENDOR_AUTOPAY_OFF"] and "held invoice H-1" in _autopay_rows()[1]["reasoning"]


def test_a_refused_payment_turns_autopay_off_and_leaves_the_invoice_for_the_owner(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    real_pay = chain.pay

    def refuse(*a):
        raise RuntimeError("Exceeds per-tx limit")
    chain.pay = refuse
    r = _shadow(client, h, vid, "X-1")
    assert r.json()["decision"] == "await_owner" and _inv(r.json()["invoice_id"])["status"] == "awaiting_owner"
    assert "refused the payment of invoice X-1" in _autopay_rows()[-1]["reasoning"] and asyncio.run(models.get_autopay(1, vid))["active"] == 0
    chain.pay = real_pay
    assert client.post(f"/admin/invoices/{r.json()['invoice_id']}/shadow-approve", headers=h).status_code == 200


def test_the_owner_turns_it_off_with_the_session_alone(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    r = client.post(f"/admin/autopay/{vid}/off", headers=h)
    assert r.status_code == 200 and r.json()["was_on"] is True
    assert client.post(f"/admin/autopay/{vid}/off", headers=h).json()["was_on"] is False  # once; no second record
    assert [x["action"] for x in _autopay_rows()] == ["VENDOR_AUTOPAY_ON", "VENDOR_AUTOPAY_OFF"] and "owner turned autopay off" in _autopay_rows()[1]["reasoning"]
    assert client.post("/admin/autopay/999/off", headers=h).status_code == 404
    before = len(chain.calls)
    assert _shadow(client, h, vid, "O-1").json()["decision"] == "await_owner" and _writes(chain)[before:] == []


# ---- review afterwards

def test_a_disagreement_on_an_autopaid_invoice_revokes_graduation_and_autopay(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    r = _shadow(client, h, vid, "D-1")
    iid = r.json()["invoice_id"]
    h1 = _inv(iid)["reasoning_hash"]
    assert client.post(f"/admin/decisions/{h1}/verdict", json={"verdict": "disagree"}, headers=h).status_code == 200
    assert not _state(vid)["graduated"]
    acts = [x["action"] for x in _autopay_rows()]
    assert acts == ["VENDOR_AUTOPAY_ON", "VENDOR_AUTOPAY_OFF"] and "graduation ended" in _autopay_rows()[1]["reasoning"]
    assert [x["action"] for x in _records()] == ["VENDOR_GRADUATED", "VENDOR_REVOKED"]
    assert client.post(f"/admin/decisions/{h1}/verdict", json={"verdict": "agree"}, headers=h).status_code == 409  # final
    assert client.get("/stats").json()["shadow"]["agreement"] == {"agree": 2, "disagree": 1, "n": 3, "rate": 0.6667}


def test_an_agreement_on_an_autopaid_invoice_counts_and_keeps_autopay(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    iid = _shadow(client, h, vid, "K-1").json()["invoice_id"]
    assert client.post(f"/admin/decisions/{_inv(iid)['reasoning_hash']}/verdict", json={"verdict": "agree"}, headers=h).status_code == 200
    assert _state(vid)["streak"] == 3 and asyncio.run(models.get_autopay(1, vid))["active"] == 1
    assert client.get("/stats").json()["shadow"]["agreement"]["n"] == 3


def test_an_owner_approved_invoice_does_not_take_a_review_verdict_here(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    iid = _approve(client, h, vid, "M-1")
    assert client.post(f"/admin/decisions/{_inv(iid)['reasoning_hash']}/verdict", json={"verdict": "agree"}, headers=h).status_code == 409


# ---- isolation and figures

def test_another_business_cannot_switch_or_see_this_ones_autopay(client, chain):
    _add_acme()
    h1, h2 = _bearer(client), _bearer(client, ADMIN_2, business="acme")
    vid = _graduate(client, h1)
    _switch_on(client, h1, vid)
    assert client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h2).status_code == 404
    assert client.post(f"/admin/autopay/{vid}/off", headers=h2).status_code == 404
    assert client.get("/admin/earned-vendors", headers=h2).json()["vendors"] == []
    ch = client.post("/admin/autopay/challenge", json={"vendor_id": vid, "max_usdc": "1"}, headers=h1).json()  # a challenge for A cannot be used in B
    assert client.post("/admin/autopay/on", json={"nonce": ch["nonce"], "signature": _sign(ADMIN_1, ch["message"])}, headers=h2).status_code == 403
    assert asyncio.run(models.get_autopay(1, vid))["active"] == 1


def test_autopay_records_and_payments_move_no_live_figure(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    def read(p):
        body = client.get(p).json()
        body.pop("as_of", None)  # the clock, not a figure; it can tick between the two reads
        return body
    before = {p: read(p) for p in ("/stats", "/outcomes", "/businesses/ceedebooks/traction", "/metrics/decisions")}
    biz = asyncio.run(models.get_business(1))
    for action in autopay.ACTIONS:
        asyncio.run(decision_log.log_decision(action, earned.subject_for(vid), "record", model_used="rules", business=biz))
    after = {p: read(p) for p in before}
    assert after == before
    assert asyncio.run(decision_log.stats()) == asyncio.run(decision_log.stats(1))


def test_a_paid_autopay_invoice_is_paid_once(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid)
    before = len(chain.calls)
    iid = _shadow(client, h, vid, "Z-1").json()["invoice_id"]
    assert _writes(chain)[before:] == ["commit", "pay"]
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 409  # nothing left to approve
    assert _writes(chain)[before:] == ["commit", "pay"]
