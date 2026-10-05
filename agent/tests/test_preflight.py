"""Invoice pre-flight (dry run) and the public agent manifest."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from agent import payables
from agent.config import config
from backend import auth, main, models

WALLET_A = "0x" + "a1" * 20
WALLET_B = "0x" + "b2" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "pf.db"))
    asyncio.run(models.init_db())
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)


class _State(dict):
    """dict view over the FakeChain, so tests can write chain["paid"] = True and read chain["writes"]."""

    def __init__(self, fake, runway):
        super().__init__()
        self.fake, self.runway_days = fake, runway

    _MAP = {"paid": "paid", "registered": "vendor_ok", "left": "remaining"}

    def __setitem__(self, key, value):
        if key == "runway":
            self.runway_days = value
        else:
            setattr(self.fake, self._MAP[key], value)

    def __getitem__(self, key):
        if key == "writes":
            return [c[0] for c in self.fake.calls]
        return getattr(self.fake, self._MAP[key])


@pytest.fixture
def chain(chains, monkeypatch):
    """Steer the home contract's answers through the dict; `writes` lists any write attempt."""
    state = _State(chains.get(config.budget_enforcer_address, 1), 42.0)
    state.fake.remaining = (100_000_000, 20_000_000)

    async def runway(amount, business):
        return state.runway_days

    monkeypatch.setattr(main.treasury, "runway_days", runway)
    return state


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _key(role, vendor_id=None):
    key = auth.generate_key(role)
    asyncio.run(models.save_api_key(1, auth.hash_key(key), role, vendor_id, "t"))
    return {"X-API-Key": key}


@pytest.fixture
def world(client):
    buyer = _key("buyer")
    a = client.post("/vendors", json={"name": "A", "wallet_address": WALLET_A}, headers=buyer).json()["id"]
    b = client.post("/vendors", json={"name": "B", "wallet_address": WALLET_B}, headers=buyer).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": a, "amount_usdc": "5", "category": 2},
                     headers=buyer).json()["id"]
    return {"buyer": buyer, "a": a, "b": b, "po": po, "key_a": _key("vendor", a), "key_b": _key("vendor", b)}


def _body(vendor_id, **extra):
    body = {"invoice_number": "INV-1", "vendor_id": vendor_id, "amount_usdc": "5", "category": 2,
            "doc_hash": "ab" * 32, "po_number": "PO-1"}
    body.update(extra)
    return body


def _confirm_receipt(client, world, role_key=None):
    r = client.post("/receipts", json={"po_id": world["po"]}, headers=role_key or world["buyer"])
    assert r.status_code == 200


def _preflight(client, world, key="key_a", **extra):
    return client.post("/invoices/preflight", json=_body(world["a"], **extra), headers=world[key])


def test_would_pay_when_everything_matches(client, world, chain):
    _confirm_receipt(client, world)
    r = _preflight(client, world)
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "would_pay" and body["dry_run"] is True
    assert all(body["checks"].values())


def test_preflight_never_writes_anywhere(client, world, chain):
    _confirm_receipt(client, world)
    _preflight(client, world)
    assert chain["writes"] == []
    assert asyncio.run(models.get_invoice(1, 1)) is None  # nothing saved, so the invoice number is still free
    again = client.post("/invoices", json=_body(world["a"]), headers=world["key_a"])
    assert again.status_code != 409


def test_missing_receipt_would_escalate_with_reason(client, world, chain):
    body = _preflight(client, world).json()
    assert body["outcome"] == "would_escalate"
    assert body["checks"]["receipt_on_file"] is False
    assert any("receipt" in reason.lower() for reason in body["reasons"])


def test_amount_mismatch_would_escalate(client, world, chain):
    _confirm_receipt(client, world)
    body = _preflight(client, world, amount_usdc="9").json()
    assert body["outcome"] == "would_escalate" and body["checks"]["amount_matches_po"] is False


def test_unregistered_vendor_would_escalate(client, world, chain):
    _confirm_receipt(client, world)
    chain["registered"] = False
    assert _preflight(client, world).json()["outcome"] == "would_escalate"


def test_low_runway_would_hold(client, world, chain):
    _confirm_receipt(client, world)
    chain["runway"] = 3.0
    assert _preflight(client, world).json()["outcome"] == "would_hold"


def test_over_limit_is_flagged_before_the_contract_refuses(client, world, chain):
    _confirm_receipt(client, world)
    chain["left"] = (100_000_000, 1_000_000)  # 1 USDC left in the category, invoice is 5
    body = _preflight(client, world).json()
    assert body["outcome"] == "would_be_refused_by_contract"
    assert body["checks"]["within_daily_limits"] is False


def test_already_paid_is_reported(client, world, chain):
    chain["paid"] = True
    assert _preflight(client, world).json()["outcome"] == "already_paid"


def test_response_leaks_no_balances_or_limits(client, world, chain):
    _confirm_receipt(client, world)
    text = _preflight(client, world).text
    for secret in ("100000000", "20000000", "42"):
        assert secret not in text


def test_all_checks_are_reported_even_when_the_first_one_fails(client, world, chain):
    chain["registered"] = False
    chain["runway"] = 3.0
    checks = _preflight(client, world).json()["checks"]
    assert checks["receipt_on_file"] is False and checks["vendor_registered_onchain"] is False and checks["runway_ok"] is False


# ---- access control: same rules as POST /invoices

def test_no_key_is_401(client, world, chain):
    assert client.post("/invoices/preflight", json=_body(world["a"])).status_code == 401


def test_vendor_cannot_preflight_for_another_vendor(client, world, chain):
    r = client.post("/invoices/preflight", json=_body(world["a"]), headers=world["key_b"])
    assert r.status_code == 403


def test_vendor_cannot_use_another_vendors_po(client, world, chain):
    r = client.post("/invoices/preflight", json=_body(world["b"]), headers=world["key_b"])
    assert r.status_code == 403


def test_buyer_may_preflight_for_any_vendor(client, world, chain):
    assert _preflight(client, world, key="buyer").status_code == 200


def test_unknown_fields_are_rejected(client, world, chain):
    assert _preflight(client, world, treasury_runway_days=99).status_code == 422


def test_chain_failure_is_503_not_a_guess(client, world, chain, monkeypatch):
    chain.fake.broken = True
    assert _preflight(client, world).status_code == 503


# ---- manifest

def test_manifest_is_public_and_points_at_the_contract(client):
    r = client.get("/.well-known/agent.json")
    assert r.status_code == 200
    body = r.json()
    assert body["contract"] == config.budget_enforcer_address
    assert "POST /invoices/preflight" in body["endpoints"]
    assert [s["step"] for s in body["vendor_flow"]] == list(range(1, 8))
