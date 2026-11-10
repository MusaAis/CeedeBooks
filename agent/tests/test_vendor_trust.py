"""Phase R3 (v1.3.3.2): the public vendor-trust route and the owner's view of it. Read only: each vendor's streak, graduated state,
autopay state and revocations, taken from the audit records. The chain is mocked (conftest.FakeChain); the pipeline is real."""
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


# ---- R3: the public view

def _trust(client, slug="ceedebooks"):
    r = client.get(f"/businesses/{slug}/vendor-trust")
    assert r.status_code == 200, r.text
    return r.json()


def _public(client):
    out = {p: client.get(p).json() for p in ("/stats", "/outcomes", "/businesses/ceedebooks/traction", "/metrics/decisions", "/decisions?limit=50")}
    out["/outcomes"].pop("as_of")  # the only field that is the clock's, not a figure
    return out


def _counts():
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            out = []
            for t in ("audit_log", "ledger", "invoices", "decision_verdicts", "autopay_switches"):
                async with db.execute(f"SELECT COUNT(*) FROM {t}") as cur:
                    out.append((await cur.fetchone())[0])
            return out
    return asyncio.run(go())


def test_a_business_with_no_vendor_activity_lists_nobody_and_an_unknown_one_is_a_404(client, chain):
    h = _bearer(client)
    _vendor(client, h)  # registered, no verdicts, no record: not listed publicly
    t = _trust(client)
    assert t["vendors"] == [] and t["earn_after"] == 5 and t["business"]["slug"] == "ceedebooks"
    assert client.get("/businesses/nope/vendor-trust").status_code == 404


def test_a_vendor_part_way_shows_its_streak_against_the_threshold(client, chain):
    h = _bearer(client)
    _set_n(client, h, 3)
    vid = _vendor(client, h)
    _approve(client, h, vid, "S-1"); _approve(client, h, vid, "S-2")
    t = _trust(client)
    v = t["vendors"][0]
    assert t["earn_after"] == 3 and v["id"] == vid and v["streak"] == 2 and v["graduated"] is False
    assert v["graduations"] == 0 and v["revocations"] == 0 and v["last_revocation"] is None and v["records"] == []
    assert v["autopay"] == {"on": False, "max_usdc": None, "record_hash": None, "paid": 0}


def test_a_graduated_vendor_shows_its_record_and_the_hash_opens_the_decision(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    v = _trust(client)["vendors"][0]
    rec = _records()[0]
    assert v["graduated"] is True and v["streak"] == 2 and v["graduations"] == 1
    assert v["records"] == [{"action": "VENDOR_GRADUATED", "at": rec["timestamp"], "reasoning_hash": rec["reasoning_hash"]}]
    assert v["record_hash"] == rec["reasoning_hash"]
    assert client.get(f"/decisions/{rec['reasoning_hash']}").status_code == 200  # the page's Verify uses this hash


def test_a_revocation_is_listed_with_its_hash_and_the_streak_restarts(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _reject(client, h, vid, "R-1")
    v = _trust(client)["vendors"][0]
    recs = _records()
    assert [r["action"] for r in recs] == ["VENDOR_GRADUATED", "VENDOR_REVOKED"]
    assert v["graduated"] is False and v["streak"] == 0 and v["graduations"] == 1 and v["revocations"] == 1
    assert v["last_revocation"]["reasoning_hash"] == recs[1]["reasoning_hash"]
    assert [r["action"] for r in v["records"]] == ["VENDOR_REVOKED", "VENDOR_GRADUATED"]  # newest first
    _approve(client, h, vid, "R-2"); _approve(client, h, vid, "R-3")  # earns it again: a second graduation, the revocation stays
    v = _trust(client)["vendors"][0]
    assert v["graduated"] is True and v["graduations"] == 2 and v["revocations"] == 1


def test_autopay_state_cap_and_paid_count_are_shown_and_turning_it_off_is_recorded(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    out = _switch_on(client, h, vid, "0.75")
    v = _trust(client)["vendors"][0]
    assert v["autopay"] == {"on": True, "max_usdc": "0.75", "record_hash": out["record_hash"], "paid": 0}
    assert _shadow(client, h, vid, "A-1", amount="0.5").json()["decision"] == "auto_paid"
    assert _trust(client)["vendors"][0]["autopay"]["paid"] == 1
    assert client.post(f"/admin/autopay/{vid}/off", headers=h).json()["was_on"] is True
    v = _trust(client)["vendors"][0]
    assert v["autopay"]["on"] is False and v["autopay"]["max_usdc"] is None and v["autopay"]["paid"] == 1
    assert [r["action"] for r in v["records"]][:2] == ["VENDOR_AUTOPAY_OFF", "VENDOR_AUTOPAY_ON"]


def test_a_vendor_that_lost_graduation_shows_the_automatic_turn_off(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid, "0.75")
    iid = _shadow(client, h, vid, "L-1", amount="2.0").json()["invoice_id"]  # above the cap: waits for the owner
    assert client.post(f"/admin/invoices/{iid}/shadow-reject", headers=h).status_code == 200
    v = _trust(client)["vendors"][0]
    assert v["graduated"] is False and v["autopay"]["on"] is False and v["revocations"] == 1
    assert {"VENDOR_AUTOPAY_OFF", "VENDOR_REVOKED"} <= {r["action"] for r in v["records"]}
    assert v["records"][0]["action"] in ("VENDOR_AUTOPAY_OFF", "VENDOR_REVOKED")


def test_the_history_is_limited_to_the_latest_records_but_the_counts_cover_all(client, chain):
    h = _bearer(client)
    vid = _vendor(client, h)
    biz = asyncio.run(models.get_business(1))
    for _ in range(7):
        for action in (earned.ACTION_GRADUATED, earned.ACTION_REVOKED):
            asyncio.run(decision_log.log_decision(action, earned.subject_for(vid), "record", model_used="rules", business=biz))
    v = _trust(client)["vendors"][0]
    assert len(v["records"]) == 10 and v["graduations"] == 7 and v["revocations"] == 7
    assert v["records"][0]["action"] == "VENDOR_REVOKED"


def test_the_public_view_leaks_no_wallet_signer_signature_or_message(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid, "0.75")
    text = json.dumps(_trust(client)).lower()
    assert W1.lower() not in text and ADMIN_1.address.lower() not in text
    for word in ("signature", "signer", "wallet", "message_hash", "nonce"):
        assert word not in text


def test_the_route_is_read_only_and_moves_no_public_figure(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    _switch_on(client, h, vid, "0.75")
    _shadow(client, h, vid, "F-1", amount="0.5")
    before, rows = _public(client), _counts()
    for _ in range(3):
        _trust(client)
    assert _public(client) == before and _counts() == rows


def test_a_vendors_trust_records_move_no_live_figure_and_demo_rows_stay_out(client, chain):
    h = _bearer(client)
    before = _public(client)["/stats"]
    vid = _graduate(client, h)
    _switch_on(client, h, vid, "0.75")
    _shadow(client, h, vid, "F-2", amount="0.5")
    after = _public(client)["/stats"]
    for k in ("paid", "paid_usdc", "held", "escalated", "refused", "decisions"):
        assert after[k] == before[k] == 0
    assert after["shadow"]["auto_paid"] == 1  # shadow, apart


def test_another_business_sees_only_its_own_vendors(client, chain, chains):
    _add_acme()
    h1, h2 = _bearer(client), _bearer(client, ADMIN_2, "acme")
    _graduate(client, h1)
    assert _trust(client, "acme")["vendors"] == []
    assert [v["name"] for v in _trust(client)["vendors"]] == ["Lagos Hosting"]
    assert client.get("/admin/earned-vendors", headers=h2).json()["vendors"] == []
    # business A's records never appear under business B, and B's own show only for B
    acme = asyncio.run(models.get_business_by_slug("acme"))
    vid2 = client.post("/vendors", json={"name": "Acme Host", "wallet_address": W2}, headers=h2).json()["id"]
    asyncio.run(decision_log.log_decision(earned.ACTION_REVOKED, earned.subject_for(vid2), "record", model_used="rules", business=acme))
    assert [v["name"] for v in _trust(client, "acme")["vendors"]] == ["Acme Host"]
    assert [v["name"] for v in _trust(client)["vendors"]] == ["Lagos Hosting"]


def test_an_archived_business_is_a_404(client, chain):
    _add_acme()

    async def archive():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'archived' WHERE slug = 'acme'")
            await db.commit()
    asyncio.run(archive())
    assert client.get("/businesses/acme/vendor-trust").status_code == 404


def test_the_owner_view_has_the_same_fields_and_lists_every_vendor(client, chain):
    h = _bearer(client)
    vid = _graduate(client, h)
    other = _vendor(client, h, "Quiet Vendor", W2)
    _reject(client, h, vid, "O-1")
    mine = client.get("/admin/earned-vendors", headers=h).json()
    assert [v["id"] for v in mine["vendors"]] == [vid, other]
    first = mine["vendors"][0]
    assert first["revocations"] == 1 and first["last_revocation"]["reasoning_hash"] == _records()[-1]["reasoning_hash"]
    assert {k: first[k] for k in ("streak", "graduated", "graduations", "records")} == \
        {k: _trust(client)["vendors"][0][k] for k in ("streak", "graduated", "graduations", "records")}
    assert client.get("/admin/earned-vendors").status_code in (401, 403)


def test_a_record_filed_in_one_business_never_shows_under_a_vendor_id_of_another(client, chain):
    _add_acme()
    h2 = _bearer(client, ADMIN_2, "acme")
    vid2 = client.post("/vendors", json={"name": "Acme Host", "wallet_address": W2}, headers=h2).json()["id"]
    home = asyncio.run(models.get_business(1))
    # a (mis)filed record in the home business that names the other business's vendor id
    asyncio.run(decision_log.log_decision(earned.ACTION_REVOKED, earned.subject_for(vid2), "record", model_used="rules", business=home))
    assert _trust(client, "acme")["vendors"] == []
    assert client.get("/admin/earned-vendors", headers=h2).json()["vendors"][0]["revocations"] == 0
