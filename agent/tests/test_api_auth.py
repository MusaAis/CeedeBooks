"""API access control: every denied path, plus server-side runway and input validation."""
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent import payables, treasury
from agent.config import config
from backend import auth, main, models

WALLET_A = "0x" + "a1" * 20
WALLET_B = "0x" + "b2" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "api.db"))
    asyncio.run(models.init_db())
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)


@pytest.fixture
def pipeline(monkeypatch):
    """Stub the money path so API tests never touch the chain; records what the API handed the pipeline."""
    seen = {}

    async def fake_runway(amount, business):
        seen["runway_for"], seen["business"] = amount, business["id"]
        return 42.0

    async def fake_process(invoice, runway, business):
        seen["invoice"], seen["runway"] = invoice, runway
        return payables.Decision.HOLD

    monkeypatch.setattr(main.treasury, "runway_days", fake_runway)
    monkeypatch.setattr(main.payables, "process_invoice", fake_process)
    return seen


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _key(role, vendor_id=None, label="t"):
    key = auth.generate_key(role)
    asyncio.run(models.save_api_key(1, auth.hash_key(key), role, vendor_id, label))
    return key


def _h(key):
    return {"X-API-Key": key}


@pytest.fixture
def world(client):
    """Two vendors, a buyer key, and a vendor key for each; vendor A has a PO."""
    buyer = _key("buyer", label="musa")
    a = client.post("/vendors", json={"name": "A", "wallet_address": WALLET_A}, headers=_h(buyer)).json()["id"]
    b = client.post("/vendors", json={"name": "B", "wallet_address": WALLET_B}, headers=_h(buyer)).json()["id"]
    po = client.post(
        "/purchase-orders",
        json={"po_number": "PO-1", "vendor_id": a, "amount_usdc": "5", "category": 2},
        headers=_h(buyer),
    ).json()["id"]
    return {"buyer": _h(buyer), "a": a, "b": b, "po": po, "key_a": _h(_key("vendor", a)), "key_b": _h(_key("vendor", b))}


def _invoice(vendor_id, number="INV-1", **extra):
    body = {"invoice_number": number, "vendor_id": vendor_id, "amount_usdc": "5", "category": 2,
            "doc_hash": "ab" * 32, "po_number": "PO-1"}
    body.update(extra)
    return body


# ---- authentication ----

@pytest.mark.parametrize("method,path", [
    ("post", "/vendors"), ("get", "/vendors/1"), ("post", "/purchase-orders"),
    ("post", "/receipts"), ("post", "/invoices"), ("get", "/invoices/1"),
])
def test_no_key_is_401_everywhere_except_audit(client, method, path):
    assert getattr(client, method)(path).status_code == 401


def test_invalid_key_is_401(client):
    r = client.post("/vendors", json={}, headers=_h("cdb_buyer_not-a-real-key"))
    assert r.status_code == 401


def test_revoked_key_is_401(client):
    key = _key("buyer")
    assert asyncio.run(models.revoke_api_key(1))
    assert client.get("/vendors/1", headers=_h(key)).status_code == 401


def test_keys_are_stored_hashed_only():
    key = _key("buyer")
    with sqlite3.connect(config.db_path) as db:
        stored = db.execute("SELECT key_hash FROM api_keys").fetchone()[0]
    assert stored == auth.hash_key(key) and key not in stored


def test_vendor_key_must_name_a_vendor_and_buyer_key_must_not():
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(models.save_api_key(1, "h1", "vendor", None, "bad"))
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(models.save_api_key(1, "h2", "buyer", 1, "bad"))


def test_failed_auth_attempts_get_throttled(client, monkeypatch):
    monkeypatch.setattr(config, "failed_auth_per_min", 3)
    codes = [client.get("/vendors/1", headers=_h("cdb_buyer_guess")).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


# ---- role checks ----

def test_vendor_cannot_create_vendors_pos_or_receipts(client, world):
    k = world["key_a"]
    assert client.post("/vendors", json={"name": "X", "wallet_address": WALLET_A}, headers=k).status_code == 403
    po = {"po_number": "PO-9", "vendor_id": world["a"], "amount_usdc": "1", "category": 0}
    assert client.post("/purchase-orders", json=po, headers=k).status_code == 403
    assert client.post("/receipts", json={"po_id": world["po"]}, headers=k).status_code == 403
    assert asyncio.run(models.get_receipt(1, world["po"])) is None


def test_buyer_receipt_role_is_set_by_the_server(client, world):
    r = client.post("/receipts", json={"po_id": world["po"], "confirmed_by": "Musa"}, headers=world["buyer"])
    assert r.status_code == 200
    assert asyncio.run(models.get_receipt(1, world["po"]))["confirmed_by_role"] == "buyer"


def test_receipt_body_cannot_pick_its_own_role(client, world):
    r = client.post("/receipts", json={"po_id": world["po"], "confirmed_by_role": "ops_manager"}, headers=world["buyer"])
    assert r.status_code == 422
    assert asyncio.run(models.get_receipt(1, world["po"])) is None


def test_receipt_for_missing_po_404_and_duplicate_409(client, world):
    assert client.post("/receipts", json={"po_id": 999}, headers=world["buyer"]).status_code == 404
    assert client.post("/receipts", json={"po_id": world["po"]}, headers=world["buyer"]).status_code == 200
    assert client.post("/receipts", json={"po_id": world["po"]}, headers=world["buyer"]).status_code == 409


# ---- invoices ----

def test_vendor_can_submit_only_its_own_invoice(client, world, pipeline):
    assert client.post("/invoices", json=_invoice(world["b"]), headers=world["key_a"]).status_code == 403
    assert "invoice" not in pipeline
    r = client.post("/invoices", json=_invoice(world["a"]), headers=world["key_a"])
    assert r.status_code == 200 and r.json()["decision"] == "hold"


def test_vendor_cannot_use_another_vendors_po(client, world, pipeline):
    r = client.post("/invoices", json=_invoice(world["b"]), headers=world["key_b"])  # PO-1 belongs to vendor A
    assert r.status_code == 403
    assert "invoice" not in pipeline


def test_runway_is_computed_server_side_and_not_accepted_from_the_client(client, world, pipeline):
    r = client.post("/invoices", json=_invoice(world["a"], treasury_runway_days=9999), headers=world["key_a"])
    assert r.status_code == 422
    assert "invoice" not in pipeline

    assert client.post("/invoices", json=_invoice(world["a"]), headers=world["key_a"]).status_code == 200
    assert pipeline["runway"] == 42.0 and pipeline["runway_for"] == 5.0


def test_pipeline_always_pays_the_wallet_on_file(client, world, pipeline):
    r = client.post("/invoices", json=_invoice(world["a"], vendor_wallet=WALLET_B), headers=world["key_a"])
    assert r.status_code == 422
    assert "invoice" not in pipeline

    client.post("/invoices", json=_invoice(world["a"], number="INV-2", vendor_wallet=WALLET_A.upper().replace("0X", "0x")),
                headers=world["key_a"])
    assert pipeline["invoice"].vendor_wallet == WALLET_A


def test_buyer_may_submit_on_behalf_of_a_vendor(client, world, pipeline):
    assert client.post("/invoices", json=_invoice(world["a"]), headers=world["buyer"]).status_code == 200


def test_duplicate_invoice_number_is_409(client, world, pipeline):
    assert client.post("/invoices", json=_invoice(world["a"]), headers=world["key_a"]).status_code == 200
    assert client.post("/invoices", json=_invoice(world["a"]), headers=world["key_a"]).status_code == 409


def test_processing_errors_do_not_leak_internals(client, world, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("secret rpc url http://internal:8545")

    monkeypatch.setattr(main.treasury, "runway_days", lambda amount, business: asyncio.sleep(0, 30.0))
    monkeypatch.setattr(main.payables, "process_invoice", boom)
    r = client.post("/invoices", json=_invoice(world["a"]), headers=world["key_a"])
    assert r.status_code == 500 and "internal" not in r.text
    assert asyncio.run(models.get_invoice(1, 1))["status"] == "error"


def test_vendor_reads_only_its_own_invoices_and_vendor_record(client, world, pipeline):
    client.post("/invoices", json=_invoice(world["a"]), headers=world["key_a"])
    assert client.get("/invoices/1", headers=world["key_a"]).status_code == 200
    assert client.get("/invoices/1", headers=world["key_b"]).status_code == 404  # same as missing
    assert client.get("/invoices/1", headers=world["buyer"]).status_code == 200
    assert client.get(f"/vendors/{world['a']}", headers=world["key_a"]).status_code == 200
    assert client.get(f"/vendors/{world['a']}", headers=world["key_b"]).status_code == 404


@pytest.mark.parametrize("amount", [0, -1, "0", "1.0000001", "abc", None, "NaN"])
def test_bad_amounts_are_rejected(client, world, pipeline, amount):
    r = client.post("/invoices", json=_invoice(world["a"], amount_usdc=amount), headers=world["key_a"])
    assert r.status_code == 422
    assert "invoice" not in pipeline


def test_json_number_amounts_keep_exact_decimals(client, world, pipeline):
    r = client.post("/invoices", json=_invoice(world["a"], amount_usdc=5.25), headers=world["key_a"])
    assert r.status_code == 200 and pipeline["invoice"].amount_usdc == 5.25


def test_invoice_category_must_match_the_purchase_order(client, world, pipeline):
    r = client.post("/invoices", json=_invoice(world["a"], category=0), headers=world["key_a"])  # PO-1 is category 2
    assert r.status_code == 422
    assert "invoice" not in pipeline


# ---- public audit ----

def test_audit_endpoints_need_no_key(client):
    assert client.get("/decisions/" + "0" * 64).status_code == 404
    assert client.get("/decisions/" + "0" * 64 + "/verify").json() == {"reasoning_hash": "0" * 64, "verified": False, "onchain": None}
    assert client.get("/decisions/not-a-hash").status_code == 422


# ---- transport hardening ----

def test_oversized_body_is_413(client, monkeypatch):
    monkeypatch.setattr(config, "max_body_bytes", 200)
    r = client.post("/vendors", content=b"x" * 500, headers={**_h(_key("buyer")), "Content-Type": "application/json"})
    assert r.status_code == 413


def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(config, "rate_limit_per_min", 3)
    codes = [client.get("/decisions/" + "0" * 64).status_code for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]


def test_cors_allows_only_the_site_origin(client):
    ok = client.get("/decisions/" + "0" * 64, headers={"Origin": "https://ceedebooks.xyz"})
    bad = client.get("/decisions/" + "0" * 64, headers={"Origin": "https://evil.example"})
    assert ok.headers.get("access-control-allow-origin") == "https://ceedebooks.xyz"
    assert "access-control-allow-origin" not in bad.headers


# ---- server-side runway ----

def _paid(amount, days_ago=1):
    import time
    async def go():
        vid = await models.save_vendor(1, "V", WALLET_A)
        inv = await models.save_invoice(1, f"I-{amount}-{days_ago}", vid, None, amount, 0, "h")
        await models.update_invoice_status(1, inv, "paid")
        import aiosqlite
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE invoices SET created_at = ? WHERE id = ?", (time.time() - days_ago * 86400, inv))
            await db.commit()
    asyncio.run(go())


def _home():
    return asyncio.run(models.get_business(1))


def test_runway_zero_when_pool_cannot_cover_invoice(chain):
    chain.balance = 4_000_000
    assert asyncio.run(treasury.runway_days(5.0, _home())) == 0.0


def test_runway_fails_closed_when_balance_unreadable(chain):
    chain.broken = True
    assert asyncio.run(treasury.runway_days(1.0, _home())) == 0.0


def test_runway_capped_with_no_spend_history(chain):
    chain.balance = 8_790_000
    assert asyncio.run(treasury.runway_days(5.0, _home())) == treasury.MAX_RUNWAY_DAYS


def test_runway_uses_trailing_burn_rate(chain):
    chain.balance = 100_000_000
    _paid(30.0, days_ago=2)    # 30 USDC in the window -> 1 USDC/day
    _paid(900.0, days_ago=45)  # outside the 30-day window, ignored
    assert asyncio.run(treasury.runway_days(10.0, _home())) == pytest.approx(90.0)  # (100 - 10) / 1 per day
