"""Phase K1: wallet sign-in for the admin site and the /admin routes. The chain is always mocked."""
import asyncio
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log
from agent.config import config
from backend import admin_auth, auth, main, models

ADMIN = Account.create()
OTHER = Account.create()
WALLET_V = "0x" + "c3" * 20
CHAIN = {"approver": ADMIN.address}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "admin.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    CHAIN["approver"] = ADMIN.address
    monkeypatch.setattr(admin_auth.contract, "approver", lambda: CHAIN["approver"])
    monkeypatch.setattr(main.contract, "pending_approver", lambda: "0x" + "00" * 20)
    monkeypatch.setattr(main.contract, "is_vendor_approved", lambda w: True)
    monkeypatch.setattr(main, "_chain_snapshot", lambda: {"chain_ok": True, "approver": CHAIN["approver"].lower()})


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _login(client, account=ADMIN):
    ch = client.post("/admin/auth/challenge", json={"address": account.address}).json()
    sig = account.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    sig = sig if sig.startswith("0x") else "0x" + sig
    return client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig})


def _bearer(client):
    r = _login(client)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


# ---- sign-in

def test_state_is_public_and_shows_the_chain_facts(client):
    body = client.get("/admin/auth/state").json()
    assert body["approver"] == ADMIN.address.lower() and body["chain_id"] == 5042002


def test_the_approver_can_sign_in_and_use_the_admin_routes(client):
    r = _login(client)
    assert r.status_code == 200 and r.json()["address"] == ADMIN.address.lower()
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/admin/overview", headers=h).json()["chain"]["chain_ok"] is True


def test_challenge_text_names_the_domain_nonce_and_chain(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    assert "admin.ceedebooks.xyz" in ch["message"] and ch["nonce"] in ch["message"] and "Chain ID: 5042002" in ch["message"]


def test_a_wallet_that_is_not_the_approver_is_refused(client):
    assert _login(client, OTHER).status_code == 401


def test_a_signature_from_a_different_key_is_refused(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    sig = OTHER.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig if sig.startswith("0x") else "0x" + sig})
    assert r.status_code == 401


def test_a_signature_over_other_text_is_refused(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    sig = ADMIN.sign_message(encode_defunct(text=ch["message"] + " ")).signature.hex()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig if sig.startswith("0x") else "0x" + sig})
    assert r.status_code == 401


def test_a_challenge_can_be_used_only_once(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    sig = ADMIN.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    sig = sig if sig.startswith("0x") else "0x" + sig
    assert client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig}).status_code == 200
    assert client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig}).status_code == 401


def test_an_expired_challenge_is_refused(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    admin_auth._challenges[ch["nonce"]]["expires"] = time.time() - 1
    sig = ADMIN.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig if sig.startswith("0x") else "0x" + sig})
    assert r.status_code == 401


def test_malformed_sign_in_input_is_a_422_not_a_crash(client):
    assert client.post("/admin/auth/challenge", json={"address": "nope"}).status_code == 422
    assert client.post("/admin/auth/verify", json={"nonce": "x", "signature": "y"}).status_code == 422


def test_challenges_are_rate_limited(client):
    codes = [client.post("/admin/auth/challenge", json={"address": ADMIN.address}).status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_if_the_chain_cannot_be_read_nobody_signs_in(client, monkeypatch):
    def boom():
        raise RuntimeError("rpc down")

    monkeypatch.setattr(admin_auth.contract, "approver", boom)
    assert _login(client).status_code == 401


# ---- sessions

def test_every_admin_route_refuses_without_a_session(client):
    for path in ["/admin/overview", "/admin/vendors", "/admin/purchase-orders", "/admin/invoices", "/admin/actions", "/admin/invoices/1/escalation"]:
        assert client.get(path).status_code == 401, path
    assert client.post("/admin/actions", json={"action": "set_paused", "tx_hash": "0x" + "1" * 64}).status_code == 401
    assert client.post("/admin/auth/logout").status_code == 401
    assert client.get("/admin/overview", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_a_buyer_api_key_is_not_an_admin(client):
    key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(auth.hash_key(key), "buyer", None, "t"))
    assert client.get("/admin/overview", headers={"X-API-Key": key}).status_code == 403


def test_a_session_ends_when_the_approver_changes(client):
    h = _bearer(client)
    CHAIN["approver"] = OTHER.address
    admin_auth._approver_cache["at"] = 0  # let the next request re-read the chain
    assert client.get("/admin/overview", headers=h).status_code == 401


def test_a_session_ends_when_the_chain_cannot_be_read(client, monkeypatch):
    h = _bearer(client)
    monkeypatch.setattr(admin_auth.contract, "approver", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    admin_auth._approver_cache["at"] = 0
    assert client.get("/admin/overview", headers=h).status_code == 401


def test_idle_sessions_expire(client):
    h = _bearer(client)
    for s in admin_auth._sessions.values():
        s["seen"] = time.time() - admin_auth.SESSION_IDLE - 1
    assert client.get("/admin/overview", headers=h).status_code == 401


def test_sessions_have_an_absolute_limit(client):
    h = _bearer(client)
    for s in admin_auth._sessions.values():
        s["issued"] = time.time() - admin_auth.SESSION_MAX - 1
    assert client.get("/admin/overview", headers=h).status_code == 401


def test_logout_kills_the_session(client):
    h = _bearer(client)
    assert client.post("/admin/auth/logout", headers=h).status_code == 200
    assert client.get("/admin/overview", headers=h).status_code == 401


def test_a_new_sign_in_replaces_the_old_session(client):
    first = _bearer(client)
    second = _bearer(client)
    assert client.get("/admin/overview", headers=first).status_code == 401
    assert client.get("/admin/overview", headers=second).status_code == 200


# ---- what the admin can do

def test_the_admin_runs_the_buyer_flow_and_it_is_logged(client):
    h = _bearer(client)
    vid = client.post("/vendors", json={"name": "Acme", "wallet_address": WALLET_V}, headers=h).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": vid, "amount_usdc": "5", "category": 1}, headers=h)
    assert po.status_code == 200
    r = client.post("/receipts", json={"po_id": po.json()["id"]}, headers=h)
    assert r.status_code == 200
    log = client.get("/admin/actions", headers=h).json()["actions"]
    assert {"create_vendor", "create_purchase_order", "confirm_receipt", "sign_in"} <= {a["action"] for a in log}
    assert client.get("/admin/purchase-orders", headers=h).json()["purchase_orders"][0]["received"] == 1
    assert client.get("/admin/vendors", headers=h).json()["vendors"][0]["name"] == "Acme"


def _tx(monkeypatch, sender=None, to=None, success=True, escalation=None, block=77):
    monkeypatch.setattr(main.contract, "inspect_admin_tx", lambda h: {
        "sender": (sender or ADMIN.address).lower(), "to": (to or config.budget_enforcer_address).lower(),
        "success": success, "block": block, "escalation": escalation})


def test_an_admin_transaction_is_recorded_once(client, monkeypatch):
    h = _bearer(client)
    _tx(monkeypatch)
    body = {"action": "set_vendor", "tx_hash": "0x" + "ab" * 32}
    assert client.post("/admin/actions", json=body, headers=h).json() == {"ok": True, "block": 77}
    assert client.post("/admin/actions", json=body, headers=h).status_code == 409


@pytest.mark.parametrize("kw", [{"sender": OTHER.address}, {"to": "0x" + "9" * 40}, {"success": False}])
def test_a_transaction_that_is_not_ours_or_failed_is_refused(client, monkeypatch, kw):
    h = _bearer(client)
    _tx(monkeypatch, **kw)
    assert client.post("/admin/actions", json={"action": "set_paused", "tx_hash": "0x" + "cd" * 32}, headers=h).status_code == 422


def test_an_unconfirmed_transaction_is_a_404(client, monkeypatch):
    h = _bearer(client)
    monkeypatch.setattr(main.contract, "inspect_admin_tx", lambda h: (_ for _ in ()).throw(RuntimeError("not found")))
    assert client.post("/admin/actions", json={"action": "set_paused", "tx_hash": "0x" + "cd" * 32}, headers=h).status_code == 404


def _escalated_invoice():
    vid = asyncio.run(models.save_vendor("V", WALLET_V))
    iid = asyncio.run(models.save_invoice("INV-X", vid, None, 5.0, 1, "doc"))
    asyncio.run(models.update_invoice_status(iid, "escalated", reasoning_hash="e" * 64))
    return iid


def test_approving_an_escalation_settles_only_the_matching_invoice(client, monkeypatch):
    h = _bearer(client)
    iid = _escalated_invoice()
    _tx(monkeypatch, escalation=("EscalationApproved", "1" * 64, "f" * 64))  # a different invoice's hash
    body = {"action": "approve_escalation", "tx_hash": "0x" + "ee" * 32, "invoice_id": iid}
    assert client.post("/admin/actions", json=body, headers=h).status_code == 422
    assert asyncio.run(models.get_invoice(iid))["status"] == "escalated"
    _tx(monkeypatch, escalation=("EscalationApproved", "1" * 64, "e" * 64))
    assert client.post("/admin/actions", json=body, headers=h).status_code == 200
    assert asyncio.run(models.get_invoice(iid))["status"] == "paid"


def test_rejecting_needs_a_rejection_event(client, monkeypatch):
    h = _bearer(client)
    iid = _escalated_invoice()
    _tx(monkeypatch, escalation=("EscalationApproved", "1" * 64, "e" * 64))
    body = {"action": "reject_escalation", "tx_hash": "0x" + "ee" * 32, "invoice_id": iid}
    assert client.post("/admin/actions", json=body, headers=h).status_code == 422
    _tx(monkeypatch, escalation=("EscalationRejected", "1" * 64, "e" * 64))
    assert client.post("/admin/actions", json=body, headers=h).status_code == 200
    assert asyncio.run(models.get_invoice(iid))["status"] == "rejected"


def test_cors_allows_the_admin_origin_and_the_bearer_header(client):
    r = client.options("/admin/overview", headers={"Origin": "https://admin.ceedebooks.xyz",
                                                  "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert r.headers.get("access-control-allow-origin") == "https://admin.ceedebooks.xyz"
    bad = client.options("/admin/overview", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_a_flood_of_challenges_cannot_lock_the_admin_out(client, monkeypatch):
    monkeypatch.setattr(admin_auth, "MAX_CHALLENGES", 5)
    for _ in range(20):
        admin_auth.make_challenge(OTHER.address)
    assert len(admin_auth._challenges) == 5
    assert _login(client).status_code == 200


ORIGINAL_SNAPSHOT = main._chain_snapshot  # captured at import, before the fixtures stub it


def test_the_real_chain_snapshot_lists_the_spend_categories(monkeypatch):
    c = main.contract
    monkeypatch.setattr(c, "budget_limits", lambda: (100_000_000, 20_000_000))
    monkeypatch.setattr(c, "approver", lambda: ADMIN.address)
    monkeypatch.setattr(c, "pending_approver", lambda: "0x" + "00" * 20)
    monkeypatch.setattr(c, "is_paused", lambda: False)
    monkeypatch.setattr(c, "usdc_balance", lambda: 5_000_000)
    monkeypatch.setattr(c, "remaining_today", lambda cat: (1, 2_000_000))
    monkeypatch.setattr(c, "category_limit", lambda cat: 3_000_000)
    snap = ORIGINAL_SNAPSHOT()
    assert snap["chain_ok"] is True
    assert [x["name"] for x in snap["categories"]] == ["Data Oracle", "Infrastructure"]
