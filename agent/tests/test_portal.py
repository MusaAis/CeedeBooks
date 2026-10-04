"""Phase K2: vendor applications, vendor wallet sign-in, the vendor's own view, and the submission metrics.
The chain and the money path are always mocked."""
import asyncio
import sqlite3
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log, payables
from agent.config import config
from backend import admin_auth, auth, main, models, vendor_auth

ADMIN = Account.create()
VENDOR = Account.create()
OTHER = Account.create()
CHAIN = {"approved": True}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "portal.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    vendor_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    CHAIN["approved"] = True
    monkeypatch.setattr(admin_auth.contract, "approver", lambda: ADMIN.address)
    monkeypatch.setattr(main.contract, "is_vendor_approved", lambda w: CHAIN["approved"])
    monkeypatch.setattr(main, "_chain_snapshot", lambda: {"chain_ok": True})

    async def fake_runway(amount):
        return 42.0

    async def fake_process(invoice, runway):
        return payables.Decision.HOLD

    async def fake_preflight(invoice, runway):
        return {"outcome": "pay", "reasons": [], "checks": {}}

    monkeypatch.setattr(main.treasury, "runway_days", fake_runway)
    monkeypatch.setattr(main.payables, "process_invoice", fake_process)
    monkeypatch.setattr(main.payables, "preflight", fake_preflight)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _sign(account, message):
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _apply_body(client, account=VENDOR, name="Acme Data", contact="acme@example.com", sign_with=None):
    ch = client.post("/apply/challenge", json={"address": account.address}).json()
    body = {"business_name": name, "wallet_address": account.address, "nonce": ch["nonce"],
            "signature": _sign(sign_with or account, ch["message"])}
    if contact:
        body["contact"] = contact
    return body


def _apply(client, **kw):
    return client.post("/apply", json=_apply_body(client, **kw))


def _admin(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(ADMIN, ch["message"])})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _vendor_login(client, account=VENDOR):
    ch = client.post("/vendor/auth/challenge", json={"address": account.address}).json()
    return client.post("/vendor/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(account, ch["message"])})


def _vendor_headers(client, account=VENDOR):
    r = _vendor_login(client, account)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _onboard(client, account=VENDOR, name="Acme Data"):
    """Apply, accept as admin; returns (vendor_id, admin headers)."""
    assert _apply(client, account=account, name=name).status_code == 200
    h = _admin(client)
    app_id = client.get("/admin/applications", headers=h).json()["applications"][0]["id"]
    r = client.post(f"/admin/applications/{app_id}/accept", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["vendor_id"], h


def _po(client, admin_h, vendor_id, number="PO-1", amount="5", category=2):
    r = client.post("/purchase-orders", headers=admin_h, json={"po_number": number, "vendor_id": vendor_id,
                                                              "amount_usdc": amount, "category": category})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _inv(vendor_id, number="INV-1", po="PO-1", **extra):
    body = {"invoice_number": number, "vendor_id": vendor_id, "amount_usdc": "5", "category": 2,
            "doc_hash": "ab" * 32, "po_number": po}
    body.update(extra)
    return body


# ---- applying

def test_a_signed_application_is_stored_and_only_the_admin_can_read_it(client):
    r = _apply(client)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert client.get("/admin/applications").status_code == 401
    h = _admin(client)
    apps = client.get("/admin/applications", headers=h).json()["applications"]
    assert len(apps) == 1 and apps[0]["business_name"] == "Acme Data" and apps[0]["wallet"] == VENDOR.address.lower()
    assert apps[0]["contact"] == "acme@example.com" and "ip_hash" not in apps[0]
    assert client.get("/admin/overview", headers=h).json()["pending_applications"] == 1


def test_a_signature_from_another_wallet_is_refused(client):
    r = _apply(client, sign_with=OTHER)
    assert r.status_code == 401
    assert asyncio.run(models.count_pending_applications()) == 0


def test_a_sign_in_signature_cannot_be_replayed_as_an_application(client):
    ch = client.post("/vendor/auth/challenge", json={"address": VENDOR.address}).json()
    body = {"business_name": "Acme", "wallet_address": VENDOR.address, "nonce": ch["nonce"], "signature": _sign(VENDOR, ch["message"])}
    assert client.post("/apply", json=body).status_code == 401


def test_a_challenge_is_single_use(client):
    body = _apply_body(client)
    assert client.post("/apply", json=body).status_code == 200
    assert client.post("/apply", json=body).status_code == 401


def test_one_pending_application_per_wallet(client):
    assert _apply(client).status_code == 200
    assert _apply(client).status_code == 409


def test_at_most_three_pending_applications_per_ip(client):
    for _ in range(3):
        assert _apply(client, account=Account.create()).status_code == 200
    assert _apply(client, account=Account.create()).status_code == 429


def test_at_most_five_submissions_per_hour_per_ip(client):
    for _ in range(5):
        assert _apply(client, sign_with=OTHER, account=Account.create()).status_code == 401  # attempts count, good or bad
    assert _apply(client, account=Account.create()).status_code == 429


def test_an_existing_vendor_wallet_cannot_apply_again(client):
    _onboard(client)
    assert _apply(client).status_code == 409


def test_application_input_is_validated(client):
    ch = client.post("/apply/challenge", json={"address": VENDOR.address}).json()
    base = {"wallet_address": VENDOR.address, "nonce": ch["nonce"], "signature": _sign(VENDOR, ch["message"])}
    assert client.post("/apply", json={**base, "business_name": "x"}).status_code == 422
    assert client.post("/apply", json={**base, "business_name": "Acme", "role": "buyer"}).status_code == 422
    assert client.post("/apply/challenge", json={"address": "nope"}).status_code == 422
    assert client.post("/apply", content=b"x" * 20000, headers={"Content-Type": "application/json"}).status_code == 413


# ---- the admin decides

def test_accepting_creates_one_vendor_and_only_once(client):
    vendor_id, h = _onboard(client)
    app_id = client.get("/admin/applications", headers=h).json()["applications"][0]["id"]
    assert client.post(f"/admin/applications/{app_id}/accept", headers=h).status_code == 409
    vendors = client.get("/admin/vendors", headers=h).json()["vendors"]
    assert [v["id"] for v in vendors] == [vendor_id]
    assert client.get("/admin/applications", headers=h).json()["applications"][0]["status"] == "accepted"
    actions = [a["action"] for a in client.get("/admin/actions", headers=h).json()["actions"]]
    assert "accept_application" in actions


def test_rejecting_marks_it_and_frees_the_wallet_to_apply_again(client):
    assert _apply(client).status_code == 200
    h = _admin(client)
    app_id = client.get("/admin/applications", headers=h).json()["applications"][0]["id"]
    assert client.post(f"/admin/applications/{app_id}/reject", headers=h).status_code == 200
    assert client.post(f"/admin/applications/{app_id}/reject", headers=h).status_code == 409
    assert _apply(client).status_code == 200


def test_only_the_admin_session_can_decide(client):
    assert _apply(client).status_code == 200
    key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(auth.hash_key(key), "buyer", None, "t"))
    assert client.post("/admin/applications/1/accept", headers={"X-API-Key": key}).status_code == 403
    assert client.post("/admin/applications/1/accept").status_code == 401
    assert client.post("/admin/applications/999/accept", headers=_admin(client)).status_code == 404


def test_vendors_stays_buyer_only(client):
    vendor_id, _ = _onboard(client)
    vh = _vendor_headers(client)
    r = client.post("/vendors", json={"name": "Me", "wallet_address": OTHER.address}, headers=vh)
    assert r.status_code == 403
    assert client.post("/vendors", json={"name": "Me", "wallet_address": OTHER.address}).status_code == 401


# ---- vendor sign-in

def test_an_accepted_vendor_signs_in_and_sees_only_its_own_data(client):
    a_id, h = _onboard(client)
    b_id, h = _onboard(client, account=OTHER, name="Other Co")  # a new admin sign-in replaces the old session
    _po(client, h, a_id, "PO-A"); _po(client, h, b_id, "PO-B")
    me = client.get("/vendor/me", headers=_vendor_headers(client)).json()
    assert me["vendor"]["id"] == a_id and me["vendor"]["name"] == "Acme Data" and me["approved_onchain"] is True
    assert [p["po_number"] for p in me["purchase_orders"]] == ["PO-A"]
    assert me["purchase_orders"][0]["received"] == 0 and me["purchase_orders"][0]["category_name"]


def test_an_unknown_wallet_cannot_sign_in(client):
    assert _vendor_login(client, account=OTHER).status_code == 401


def test_the_challenge_does_not_reveal_who_is_a_vendor(client):
    _onboard(client)
    known = client.post("/vendor/auth/challenge", json={"address": VENDOR.address})
    unknown = client.post("/vendor/auth/challenge", json={"address": OTHER.address})
    assert known.status_code == unknown.status_code == 200 and set(known.json()) == set(unknown.json())


def test_an_application_signature_cannot_sign_in(client):
    _onboard(client)
    ch = client.post("/apply/challenge", json={"address": VENDOR.address}).json()
    r = client.post("/vendor/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(VENDOR, ch["message"])})
    assert r.status_code == 401


def test_vendor_routes_need_a_vendor_session(client):
    assert client.get("/vendor/me").status_code == 401
    assert client.get("/vendor/me", headers={"Authorization": "Bearer cdb_vendor_nope"}).status_code == 401
    _onboard(client)
    assert client.get("/vendor/me", headers=_admin(client)).status_code == 403  # an admin session is not a vendor


def test_a_vendor_session_is_not_an_admin_session(client):
    _onboard(client)
    vh = _vendor_headers(client)
    assert client.get("/admin/overview", headers=vh).status_code == 403
    assert client.post("/admin/applications/1/accept", headers=vh).status_code == 403
    assert client.post("/receipts", json={"po_id": 1}, headers=vh).status_code == 403


def test_the_session_ends_when_the_wallet_on_file_changes(client):
    vendor_id, _ = _onboard(client)
    vh = _vendor_headers(client)
    assert client.get("/vendor/me", headers=vh).status_code == 200
    asyncio.run(models.update_vendor_wallet(vendor_id, OTHER.address))
    assert client.get("/vendor/me", headers=vh).status_code == 401


def test_the_session_ends_after_fifteen_idle_minutes_and_on_logout(client):
    _onboard(client)
    vh = _vendor_headers(client)
    for s in vendor_auth._sessions.values():
        s["seen"] -= vendor_auth.SESSION_IDLE + 1
    assert client.get("/vendor/me", headers=vh).status_code == 401
    vh = _vendor_headers(client)
    assert client.post("/vendor/auth/logout", headers=vh).status_code == 200
    assert client.get("/vendor/me", headers=vh).status_code == 401


def test_a_new_sign_in_replaces_the_old_session(client):
    _onboard(client)
    first, second = _vendor_headers(client), _vendor_headers(client)
    assert client.get("/vendor/me", headers=first).status_code == 401
    assert client.get("/vendor/me", headers=second).status_code == 200


# ---- what a signed-in vendor can do with invoices

def test_a_vendor_session_can_preflight_and_submit_its_own_invoice(client):
    vendor_id, h = _onboard(client)
    _po(client, h, vendor_id)
    vh = _vendor_headers(client)
    assert client.post("/invoices/preflight", json=_inv(vendor_id), headers=vh).json()["dry_run"] is True
    r = client.post("/invoices", json=_inv(vendor_id), headers=vh)
    assert r.status_code == 200 and r.json()["decision"] == "hold"
    assert client.get(f"/invoices/{r.json()['invoice_id']}", headers=vh).status_code == 200
    me = client.get("/vendor/me", headers=vh).json()
    assert me["purchase_orders"][0]["invoiced"] == 1 and len(me["invoices"]) == 1


def test_a_vendor_session_cannot_touch_another_vendors_invoices_or_pos(client):
    a_id, h = _onboard(client)
    b_id, h = _onboard(client, account=OTHER, name="Other Co")  # a new admin sign-in replaces the old session
    _po(client, h, b_id, "PO-B")
    vh = _vendor_headers(client)
    assert client.post("/invoices", json=_inv(b_id, po="PO-B"), headers=vh).status_code == 403
    assert client.post("/invoices", json=_inv(a_id, po="PO-B"), headers=vh).status_code == 403
    other_invoice = client.post("/invoices", json=_inv(b_id, "INV-B", "PO-B"), headers=h).json()["invoice_id"]
    assert client.get(f"/invoices/{other_invoice}", headers=vh).status_code == 404


def test_a_vendor_cannot_label_its_own_origin(client):
    vendor_id, h = _onboard(client)
    _po(client, h, vendor_id)
    vh = _vendor_headers(client)
    assert client.post("/invoices", json=_inv(vendor_id, origin="demo"), headers=vh).status_code == 403
    assert client.post("/invoices/preflight", json=_inv(vendor_id, origin="demo"), headers=vh).status_code == 403
    assert client.post("/invoices", json=_inv(vendor_id, origin="manual"), headers=h).status_code == 422


# ---- submission metrics

def _settle(invoice_id, status):
    asyncio.run(models.update_invoice_status(invoice_id, status))


def test_stats_split_invoices_volume_and_duplicates_by_origin(client):
    vendor_id, h = _onboard(client)
    _po(client, h, vendor_id)
    vh = _vendor_headers(client)
    real = client.post("/invoices", json=_inv(vendor_id, "INV-1"), headers=vh).json()["invoice_id"]
    demo = client.post("/invoices", json=_inv(vendor_id, "INV-2", origin="demo"), headers=h).json()["invoice_id"]
    held = client.post("/invoices", json=_inv(vendor_id, "INV-3"), headers=vh).json()["invoice_id"]
    _settle(real, "paid"); _settle(demo, "paid"); _settle(held, "held")
    assert client.post("/invoices", json=_inv(vendor_id, "INV-1"), headers=vh).status_code == 409      # duplicate, agent traffic
    assert client.post("/invoices", json=_inv(vendor_id, "INV-2", origin="demo"), headers=h).status_code == 409
    sub = client.get("/stats").json()["submissions"]
    assert sub["by_origin"]["agent"] == {"invoices_processed": 2, "payment_volume_usdc": 5.0, "duplicates_caught": 1}
    assert sub["by_origin"]["demo"] == {"invoices_processed": 1, "payment_volume_usdc": 5.0, "duplicates_caught": 1}
    assert sub["by_origin"]["manual"] == {"invoices_processed": 0, "payment_volume_usdc": 0.0, "duplicates_caught": 0}
    assert sub["invoices_processed"] == 3 and sub["payment_volume_usdc"] == 10.0 and sub["duplicates_caught"] == 2


def test_pending_and_errored_invoices_are_not_counted_as_processed(client):
    vendor_id, h = _onboard(client)
    _po(client, h, vendor_id)
    inv = client.post("/invoices", json=_inv(vendor_id), headers=h).json()["invoice_id"]
    _settle(inv, "error")
    assert client.get("/stats").json()["submissions"]["invoices_processed"] == 0


def test_stats_keep_their_old_fields(client):
    body = client.get("/stats").json()
    assert {"paid", "held", "escalated", "refused", "manual_paid", "submissions"} <= set(body)


def test_hand_run_payments_are_relabelled_manual_once():
    vendor_id = asyncio.run(models.save_vendor("Ops", "0x" + "d7" * 20))
    inv = asyncio.run(models.save_invoice("NAMECHEAP-1", vendor_id, None, 2.2, 1, "doc"))
    asyncio.run(models.update_invoice_status(inv, "paid", reasoning_hash="ff" * 32))
    with sqlite3.connect(config.db_path) as db:
        db.execute("INSERT INTO audit_log (action, subject, reasoning, amount_usdc, model_used, timestamp, hash_input, reasoning_hash)"
                   " VALUES ('INVOICE_PAID', 's', 'r', 2.2, 'manual', ?, 'h', ?)", (time.time(), "ff" * 32))
    asyncio.run(models.backfill_origin()); asyncio.run(models.backfill_origin())
    stats = asyncio.run(models.submission_stats())
    assert stats["by_origin"]["manual"]["invoices_processed"] == 1 and stats["by_origin"]["agent"]["invoices_processed"] == 0


def test_an_old_database_gains_the_origin_column(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT NOT NULL UNIQUE,"
                         " vendor_id INTEGER NOT NULL, po_id INTEGER, amount_usdc REAL NOT NULL, category INTEGER NOT NULL,"
                         " doc_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', reasoning_hash TEXT, onchain_tx_id TEXT,"
                         " created_at REAL NOT NULL); INSERT INTO invoices (invoice_number, vendor_id, amount_usdc, category, doc_hash,"
                         " status, created_at) VALUES ('OLD-1', 1, 3, 0, 'd', 'paid', 1);")
    monkeypatch.setattr(config, "db_path", path)
    asyncio.run(models.init_db()); asyncio.run(models.init_db())
    assert asyncio.run(models.submission_stats())["by_origin"]["agent"]["invoices_processed"] == 1


def test_the_manifest_points_vendors_at_wallet_sign_in(client):
    m = client.get("/.well-known/agent.json").json()
    assert "/apply" in m["vendor_flow"][0]["action"] and "wallet_signin" in m["auth"]
