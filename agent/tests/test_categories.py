"""E6 (v1.2.9.1): owner-created categories per business, and invoice amounts that are always entered.
The chain is always mocked (conftest.FakeChain); the pipeline itself (match, rules, hashing, payment order) is real."""
import asyncio
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
    monkeypatch.setattr(config, "db_path", str(tmp_path / "categories.db"))
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

    async def fake_runway(amount, business):
        return 42.0

    monkeypatch.setattr(main.treasury, "runway_days", fake_runway)


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
    return asyncio.run(models.get_business_by_slug("acme"))["id"]


def _two(client):
    """Home business (admin A) and Acme (admin B), both signed in."""
    _add_acme()
    return _bearer(client), _bearer(client, ADMIN_2, "acme")


def _category(client, h, name):
    r = client.post("/admin/categories", json={"name": name}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _setup(client, h, category, number="PO-1", amount="5", receipt=True, wallet=WALLET):
    vid = client.post("/vendors", json={"name": "Lagos Hosting", "wallet_address": wallet}, headers=h).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": number, "vendor_id": vid, "amount_usdc": amount, "category": category}, headers=h)
    assert po.status_code == 200, po.text
    if receipt:
        assert client.post("/receipts", json={"po_id": po.json()["id"]}, headers=h).status_code == 200
    return vid


def _invoice(vid, category, amount="5", number="INV-1", po="PO-1", **extra):
    return {"invoice_number": number, "vendor_id": vid, "amount_usdc": amount, "category": category,
            "doc_hash": "ab" * 32, "po_number": po} | extra


def _rows(sql, args=()):
    con = sqlite3.connect(config.db_path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


# ---- categories belong to one business

def test_the_home_business_keeps_0_and_1_and_a_new_business_starts_empty(client):
    acme = _add_acme()
    assert [(c["id"], c["name"]) for c in asyncio.run(models.list_categories(1))] == [(0, "Data Oracle"), (1, "Infrastructure")]
    assert asyncio.run(models.list_categories(acme)) == []
    asyncio.run(models.init_db())  # the seed is idempotent
    assert len(asyncio.run(models.list_categories(1))) == 2


def test_each_business_gets_its_own_ids_and_may_reuse_a_name(client):
    ha, hb = _two(client)
    assert _category(client, hb, "Hosting") == 0 and _category(client, hb, "Software") == 1
    assert _category(client, ha, "Hosting") == 2  # next free id after the seeded 0 and 1
    assert [c["name"] for c in client.get("/admin/categories", headers=hb).json()["categories"]] == ["Hosting", "Software"]
    assert [c["name"] for c in client.get("/admin/categories", headers=ha).json()["categories"]] == ["Data Oracle", "Infrastructure", "Hosting"]


def test_a_name_is_unique_within_a_business_in_any_case_and_must_be_plain_text(client):
    ha, _ = _two(client)
    _category(client, ha, "Hosting")
    assert client.post("/admin/categories", json={"name": "hosting"}, headers=ha).status_code == 409
    assert client.post("/admin/categories", json={"name": "  HOSTING  "}, headers=ha).status_code == 409
    for bad in ("", "   ", "x" * 41, "tab\there\x00", "bell\x07"):
        r = client.post("/admin/categories", json={"name": bad}, headers=ha)
        assert r.status_code == 422, (bad, r.status_code)
    assert client.post("/admin/categories", json={"name": "two\nlines"}, headers=ha).json()["name"] == "two lines"  # whitespace is tidied
    assert client.post("/admin/categories", json={"name": "ok", "id": 9}, headers=ha).status_code == 422  # the server picks the id


def test_ids_run_out_at_256_and_are_never_reused_for_another_name(client):
    ha, hb = _two(client)
    for i in range(256):
        asyncio.run(models.add_category(2, f"C{i}"))
    assert client.post("/admin/categories", json={"name": "one too many"}, headers=hb).status_code == 409
    assert [c["id"] for c in asyncio.run(models.list_categories(2))] == list(range(256))


def test_rename_changes_only_the_name_and_only_in_the_callers_business(client):
    ha, hb = _two(client)
    cid_b = _category(client, hb, "Hosting")  # id 0 in Acme
    r = client.patch("/admin/categories/0", json={"name": "Data feeds"}, headers=ha)  # id 0 also exists in the home business
    assert r.status_code == 200 and r.json() == {"id": 0, "name": "Data feeds"}
    assert asyncio.run(models.get_category(2, cid_b))["name"] == "Hosting"  # Acme's id 0 is untouched
    assert client.patch("/admin/categories/7", json={"name": "Nope"}, headers=ha).status_code == 404
    assert client.patch("/admin/categories/1", json={"name": "data feeds"}, headers=ha).status_code == 409
    assert client.patch("/admin/categories/0", json={"name": "x" * 41}, headers=ha).status_code == 422
    assert client.patch("/admin/categories/300", json={"name": "Too big"}, headers=ha).status_code == 422


def test_a_session_cannot_list_add_rename_or_use_another_businesss_categories(client):
    ha, hb = _two(client)
    only_a = _category(client, ha, "Marketing")  # id 2, which Acme does not have
    assert client.patch(f"/admin/categories/{only_a}", json={"name": "Stolen"}, headers=hb).status_code == 404
    assert asyncio.run(models.get_category(1, only_a))["name"] == "Marketing"
    assert all(c["name"] != "Marketing" for c in client.get("/admin/categories", headers=hb).json()["categories"])
    vid = client.post("/vendors", json={"name": "V", "wallet_address": WALLET}, headers=hb).json()["id"]
    r = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": vid, "amount_usdc": "1", "category": only_a}, headers=hb)
    assert r.status_code == 422 and "Unknown category" in r.text
    assert _rows("SELECT COUNT(*) FROM purchase_orders WHERE business_id = 2") == [(0,)]
    assert client.get("/admin/categories").status_code == 401  # no session, no list


def test_the_vendor_portal_shows_only_its_own_businesss_category_names(client, chains):
    ha, hb = _two(client)
    _category(client, hb, "Hosting")
    vid = _setup(client, hb, 0)
    key = auth.generate_key("vendor")
    asyncio.run(models.save_api_key(2, auth.hash_key(key), "vendor", vid, "t"))
    chains.get(ACME, 2).vendor_ok = True
    acct = Account.create()
    asyncio.run(_accept_vendor(2, vid, acct.address))
    ch = client.post("/vendor/auth/challenge", json={"address": acct.address, "business": "acme"}).json()
    tok = client.post("/vendor/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(acct, ch["message"])}).json()["token"]
    me = client.get("/vendor/me", headers={"Authorization": "Bearer " + tok}).json()
    assert [p["category_name"] for p in me["purchase_orders"]] == ["Hosting"]


async def _accept_vendor(business_id, vendor_id, wallet):
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("UPDATE vendors SET wallet_address = ? WHERE id = ? AND business_id = ?", (wallet, vendor_id, business_id))
        await db.commit()


# ---- usable only while the on-chain limit is above zero and readable

def test_a_purchase_order_needs_a_known_category_with_an_onchain_limit(client, chains):
    ha, hb = _two(client)
    chain = chains.get(ACME, 2)
    cid = _category(client, hb, "Hosting")
    vid = client.post("/vendors", json={"name": "V", "wallet_address": WALLET}, headers=hb).json()["id"]
    po = lambda n, c: client.post("/purchase-orders", json={"po_number": n, "vendor_id": vid, "amount_usdc": "1", "category": c}, headers=hb)
    chain.category_cap = 0
    r = po("PO-1", cid)
    assert r.status_code == 422 and "no daily limit" in r.text
    chain.category_cap = 1_000_000
    assert po("PO-1", cid).status_code == 200
    assert po("PO-2", 9).status_code == 422  # unknown id
    assert _rows("SELECT po_number FROM purchase_orders WHERE business_id = 2") == [("PO-1",)]


def test_an_unreadable_chain_refuses_the_purchase_order_and_saves_nothing(client, chains):
    _, hb = _two(client)
    cid = _category(client, hb, "Hosting")
    vid = client.post("/vendors", json={"name": "V", "wallet_address": WALLET}, headers=hb).json()["id"]
    chains.get(ACME, 2).broken = True
    r = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": vid, "amount_usdc": "1", "category": cid}, headers=hb)
    assert r.status_code == 503
    assert _rows("SELECT COUNT(*) FROM purchase_orders WHERE business_id = 2") == [(0,)]


def test_an_invoice_in_a_category_without_a_limit_or_with_an_unreadable_chain_is_refused_before_anything_is_saved(client, chains):
    _, hb = _two(client)
    chain = chains.get(ACME, 2)
    cid = _category(client, hb, "Hosting")
    vid = _setup(client, hb, cid)
    chain.category_cap = 0  # the owner lowered the limit to zero after the PO was raised
    r = client.post("/invoices", json=_invoice(vid, cid), headers=hb)
    assert r.status_code == 422 and "no daily limit" in r.text
    chain.category_cap, chain.broken = 5_000_000, True
    assert client.post("/invoices", json=_invoice(vid, cid), headers=hb).status_code == 503
    assert _rows("SELECT COUNT(*) FROM invoices WHERE business_id = 2") == [(0,)]
    assert chain.calls == []


def test_an_unknown_category_is_refused_on_invoices_and_dry_runs(client):
    _, hb = _two(client)
    vid = client.post("/vendors", json={"name": "V", "wallet_address": WALLET}, headers=hb).json()["id"]
    body = _invoice(vid, 3, po=None)
    body.pop("po_number")
    assert client.post("/invoices", json=body, headers=hb).status_code == 422
    assert client.post("/invoices/preflight", json=body, headers=hb).status_code == 422


def test_a_dry_run_only_needs_the_category_to_exist(client, chains):
    _, hb = _two(client)
    cid = _category(client, hb, "Hosting")
    vid = _setup(client, hb, cid)
    chains.get(ACME, 2).category_cap = 0
    r = client.post("/invoices/preflight", json=_invoice(vid, cid), headers=hb)
    assert r.status_code == 200 and r.json()["dry_run"] is True


def test_the_category_list_shows_limits_and_marks_an_unreadable_one_as_unknown(client, chains):
    _, hb = _two(client)
    chain = chains.get(ACME, 2)
    _category(client, hb, "Hosting"); _category(client, hb, "Software")
    chain.category_cap = 0
    rows = client.get("/admin/categories", headers=hb).json()
    assert rows["chain_ok"] is True and [c["usable"] for c in rows["categories"]] == [False, False]
    chain.category_cap = 4_000_000
    c0 = client.get("/admin/categories", headers=hb).json()["categories"][0]
    assert c0["daily_limit_usdc"] == 4.0 and c0["usable"] is True
    chain.broken = True
    rows = client.get("/admin/categories", headers=hb).json()
    assert rows["chain_ok"] is False and all(c["usable"] is None and c["daily_limit_usdc"] is None for c in rows["categories"])


def test_the_overview_lists_the_businesss_own_categories_with_their_limits(client, chains):
    _, hb = _two(client)
    _category(client, hb, "Hosting")
    chains.get(ACME, 2).category_cap = 7_000_000
    cats = client.get("/admin/overview", headers=hb).json()["chain"]["categories"]
    assert [(c["id"], c["name"], c["daily_limit_usdc"], c["usable"]) for c in cats] == [(0, "Hosting", 7.0, True)]


def test_creating_and_renaming_a_category_is_logged_and_never_touches_the_chain(client, chains):
    ha, _ = _two(client)
    cid = _category(client, ha, "Marketing")
    client.patch(f"/admin/categories/{cid}", json={"name": "Ads"}, headers=ha)
    actions = [a["action"] for a in client.get("/admin/actions", headers=ha).json()["actions"]]
    assert "create_category" in actions and "rename_category" in actions
    assert chains.get(config.budget_enforcer_address, 1).calls == []  # the limit is the owner's own signed transaction


def test_an_archived_business_takes_no_new_categories(client):
    ha, hb = _two(client)
    asyncio.run(models.set_business_archived(2, True))
    assert client.post("/admin/categories", json={"name": "Late"}, headers=hb).status_code == 403
    assert client.patch("/admin/categories/0", json={"name": "Late"}, headers=hb).status_code == 403


# ---- amounts are always entered; the 0.01 USDC tolerance is only a rounding allowance

def test_no_amount_has_a_default_anywhere_in_the_api():
    assert main.InvoiceIn.model_fields["amount_usdc"].is_required()
    assert main.PurchaseOrderIn.model_fields["amount_usdc"].is_required()


def test_an_invoice_or_po_without_an_amount_is_a_422(client):
    ha, _ = _two(client)
    vid = client.post("/vendors", json={"name": "V", "wallet_address": WALLET}, headers=ha).json()["id"]
    assert client.post("/purchase-orders", json={"po_number": "P", "vendor_id": vid, "category": 1}, headers=ha).status_code == 422
    assert client.post("/invoices", json={"invoice_number": "I", "vendor_id": vid, "category": 1, "doc_hash": "ab" * 32}, headers=ha).status_code == 422
    assert client.post("/invoices/preflight", json={"invoice_number": "I", "vendor_id": vid, "category": 1, "doc_hash": "ab" * 32}, headers=ha).status_code == 422


def test_the_tolerance_is_exact_to_the_micro_usdc():
    assert payables.AMOUNT_TOLERANCE_USDC == 0.01
    assert payables.amount_matches(5.0, 5.0) and payables.amount_matches(5.0, 5.01) and payables.amount_matches(5.01, 5.0)
    assert payables.amount_matches(0.30, 0.29)  # binary floats make this difference 0.010000000000000009
    assert not payables.amount_matches(5.0, 5.010001) and not payables.amount_matches(5.0, 4.98) and not payables.amount_matches(0.5, 0.52)


def test_an_amount_within_the_allowance_is_paid_and_one_further_off_goes_to_the_owner(client, chain):
    ha, _ = _two(client)
    vid = _setup(client, ha, 1, amount="5")
    assert client.post("/invoices", json=_invoice(vid, 1, amount="5.01"), headers=ha).json()["decision"] == "pay"
    chain.calls.clear()
    vid2 = client.post("/vendors", json={"name": "Two", "wallet_address": "0x" + "d4" * 20}, headers=ha).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": "PO-2", "vendor_id": vid2, "amount_usdc": "5", "category": 1}, headers=ha).json()
    client.post("/receipts", json={"po_id": po["id"]}, headers=ha)
    r = client.post("/invoices", json=_invoice(vid2, 1, amount="5.02", number="INV-2", po="PO-2"), headers=ha)
    assert r.json()["decision"] == "escalate"
    assert [c[0] for c in chain.calls] == ["escalate"]
    reason = _rows("SELECT reasoning FROM audit_log WHERE subject = 'INV-2'")[0][0]
    assert "Amount mismatch" in reason and "5.02" in reason


def test_the_dry_run_reports_an_amount_outside_the_allowance(client):
    ha, _ = _two(client)
    vid = _setup(client, ha, 1, amount="5")
    far = client.post("/invoices/preflight", json=_invoice(vid, 1, amount="5.5"), headers=ha).json()
    assert far["checks"]["amount_matches_po"] is False and far["outcome"] == "would_escalate"
    near = client.post("/invoices/preflight", json=_invoice(vid, 1, amount="5.01"), headers=ha).json()
    assert near["checks"]["amount_matches_po"] is True


def test_the_manifest_states_that_amounts_are_entered_and_what_the_tolerance_is(client):
    m = client.get("/.well-known/agent.json").json()
    assert "no default" in m["amounts"] and "0.01 USDC" in m["amounts"] and "rounding allowance" in m["amounts"]
    assert "own spending categories" in m["categories"]


# ---- the ledger and the hash format are untouched

def test_decisions_in_a_new_category_still_chain_and_verify(client, chains):
    ha, hb = _two(client)
    cid = _category(client, hb, "Hosting")
    vid = _setup(client, hb, cid)
    assert client.post("/invoices", json=_invoice(vid, cid), headers=hb).json()["decision"] == "pay"
    ok = client.get("/ledger/verify?business=acme").json()
    assert ok["ok"] is True and ok["problem_count"] == 0 and ok["entries"] >= 2  # genesis plus the decision
    row = _rows("SELECT hash_version FROM audit_log WHERE subject = 'INV-1'")[0]
    assert row == (3,)  # invoice decisions carry the model-vs-rules comparison (hash format 3)
