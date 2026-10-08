"""Phase L3: a business applies, the operator accepts (the server makes its hosted agent wallet), the owner creates the
contract from their own wallet, and the server records it only after the chain proves it. The chain and Circle are mocked."""
import asyncio
import sqlite3

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from web3 import Web3

from agent import contract
from agent.config import config
from backend import admin_auth, auth, main, models, vendor_auth

OPERATOR = Account.create()   # approver of the home business = the platform operator
OWNER_A, OWNER_B, OWNER_C = Account.create(), Account.create(), Account.create()
AGENT_A = "0x" + "a1" * 20
AGENT_B = "0x" + "b2" * 20
CONTRACT_A = "0x" + "c3" * 20
CONTRACT_B = "0x" + "d4" * 20
TX_A = "0x" + "e5" * 32
TX_B = "0x" + "f6" * 32


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chains):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "onboard.db"))
    asyncio.run(models.init_db())
    admin_auth.reset(); vendor_auth.reset()
    auth.request_limiter.reset(); auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    monkeypatch.setattr(main, "BUSINESS_APPS_PER_HOUR_PER_IP", 100)  # the hourly cap has its own test
    chains.get(config.budget_enforcer_address, 1).approver_addr = OPERATOR.address
    wallets = {"made": 0, "next": [("w-1", AGENT_A), ("w-2", AGENT_B)]}

    def fake_create(label):
        wallets["made"] += 1
        wid, addr = wallets["next"].pop(0)
        return {"wallet_id": wid, "address": addr}

    monkeypatch.setattr(main.contract, "create_agent_wallet", fake_create)
    return wallets


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _sign(account, message):
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _apply(client, owner, slug="acme", name="Acme Traders", **over):
    ch = client.post("/business/apply/challenge", json={"address": owner.address}).json()
    body = {"name": name, "slug": slug, "wallet_address": owner.address, "nonce": ch["nonce"], "signature": _sign(owner, ch["message"])}
    return client.post("/business/apply", json=body | over)


def _status(client, owner):
    ch = client.post("/business/status/challenge", json={"address": owner.address}).json()
    return client.post("/business/status", json={"nonce": ch["nonce"], "signature": _sign(owner, ch["message"])})


def _operator(client):
    ch = client.post("/admin/auth/challenge", json={"address": OPERATOR.address}).json()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(OPERATOR, ch["message"])})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _accept(client, application_id):
    return client.post(f"/operator/business-applications/{application_id}/accept", headers=_operator(client))


def _creation(approver, agent, enforcer):
    return {"id": 1, "enforcer": enforcer, "approver": approver, "agent": agent, "block": 10}


@pytest.fixture
def registered(client, monkeypatch, chains):
    """Business 'acme' applied, accepted and registered on a (mocked) chain."""
    app_id = _apply(client, OWNER_A).json()["id"]
    assert _accept(client, app_id).status_code == 200
    monkeypatch.setattr(main.contract, "verify_business_creation", lambda tx, factory: _creation(OWNER_A.address, AGENT_A, CONTRACT_A))
    r = client.post("/business/register", json={"application_id": app_id, "tx_hash": TX_A})
    assert r.status_code == 200, r.text
    chains.get(CONTRACT_A, 2).approver_addr = OWNER_A.address
    return app_id


# ---- applying

def test_the_challenge_is_for_a_new_business_and_cannot_be_borrowed(client):
    ch = client.post("/business/apply/challenge", json={"address": OWNER_A.address}).json()
    assert "For a new business" in ch["message"] and "register a business" in ch["message"]
    # a vendor-apply or sign-in challenge cannot be replayed as a business application, and the other way round
    for path in ("/apply/challenge", "/vendor/auth/challenge"):
        other = client.post(path, json={"address": OWNER_A.address}).json()
        r = client.post("/business/apply", json={"name": "Acme", "slug": "acme", "wallet_address": OWNER_A.address,
                                                 "nonce": other["nonce"], "signature": _sign(OWNER_A, other["message"])})
        assert r.status_code == 401
    biz = client.post("/business/apply/challenge", json={"address": OWNER_A.address}).json()
    r = client.post("/apply", json={"business_name": "Acme", "wallet_address": OWNER_A.address, "nonce": biz["nonce"],
                                    "signature": _sign(OWNER_A, biz["message"])})
    assert r.status_code == 401


def test_an_application_needs_the_owner_wallets_own_signature(client):
    assert _apply(client, OWNER_A).status_code == 200
    ch = client.post("/business/apply/challenge", json={"address": OWNER_B.address}).json()
    forged = client.post("/business/apply", json={"name": "Beta", "slug": "beta", "wallet_address": OWNER_B.address, "nonce": ch["nonce"],
                                                  "signature": _sign(OWNER_A, ch["message"])})  # signed by someone else
    assert forged.status_code == 401
    ch = client.post("/business/apply/challenge", json={"address": OWNER_B.address}).json()
    reuse = {"name": "Beta", "slug": "beta", "wallet_address": OWNER_B.address, "nonce": ch["nonce"], "signature": _sign(OWNER_B, ch["message"])}
    assert client.post("/business/apply", json=reuse).status_code == 200
    assert client.post("/business/apply", json=reuse).status_code == 401  # the challenge was single use


def test_names_are_unique_and_reserved_ones_are_refused(client):
    assert _apply(client, OWNER_A, slug="acme").status_code == 200
    assert _apply(client, OWNER_B, slug="acme").status_code == 409        # an open application holds the name
    assert _apply(client, OWNER_B, slug="ceedebooks").status_code == 409  # an existing business holds it
    assert _apply(client, OWNER_B, slug="admin").status_code == 409       # reserved
    assert _apply(client, OWNER_B, slug="Bad Name!").status_code == 422   # not a valid slug


def test_one_pending_application_per_wallet_and_per_client(client):
    assert _apply(client, OWNER_A, slug="one").status_code == 200
    assert _apply(client, OWNER_A, slug="two").status_code == 409         # same wallet, still pending
    assert _apply(client, OWNER_B, slug="beta").status_code == 200
    assert _apply(client, OWNER_C, slug="gamma").status_code == 429       # the third waiting application from one client


def test_the_hourly_application_cap_counts_every_attempt(client, monkeypatch):
    monkeypatch.setattr(main, "BUSINESS_APPS_PER_HOUR_PER_IP", 3)
    codes = [_apply(client, OWNER_A, slug=f"s{i}", name="Spam Co").status_code for i in range(4)]
    assert codes == [200, 409, 409, 429]  # one pending per wallet, then the cap


def test_the_zero_address_cannot_own_a_business(client):
    ch = client.post("/business/apply/challenge", json={"address": "0x" + "0" * 40}).json()
    zero = Account.from_key("0x" + "11" * 32)
    r = client.post("/business/apply", json={"name": "Zed", "slug": "zed", "wallet_address": "0x" + "0" * 40, "nonce": ch["nonce"],
                                             "signature": _sign(zero, ch["message"])})
    assert r.status_code in (401, 422)


def test_an_owner_sees_only_their_own_applications(client):
    _apply(client, OWNER_A, slug="acme")
    _apply(client, OWNER_B, slug="beta")
    mine = _status(client, OWNER_A).json()
    assert [a["slug"] for a in mine["applications"]] == ["acme"] and mine["applications"][0]["status"] == "pending"
    assert "agent_address" not in mine["applications"][0]  # nothing issued before acceptance
    assert mine["factory"] == config.budget_factory_address and "run your agent's wallet" in mine["custody"]
    assert _status(client, OWNER_C).json()["applications"] == []
    assert client.post("/business/status", json={"nonce": "0" * 32, "signature": "0x" + "0" * 130}).status_code == 401


# ---- the operator

def test_only_the_operator_can_accept_reject_or_mark(client, chains):
    app_id = _apply(client, OWNER_A).json()["id"]
    for method, path, body in (("get", "/operator/business-applications", None),
                               ("post", f"/operator/business-applications/{app_id}/accept", None),
                               ("post", f"/operator/business-applications/{app_id}/reject", None),
                               ("post", "/operator/businesses/1/external", {"external": True})):
        assert getattr(client, method)(path, **({"json": body} if body else {})).status_code == 401           # no session
    buyer_key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(1, auth.hash_key(buyer_key), "buyer", None, "k"))
    assert client.post(f"/operator/business-applications/{app_id}/accept", headers={"X-API-Key": buyer_key}).status_code == 403
    assert asyncio.run(models.get_business_application(app_id))["status"] == "pending"


def test_a_business_admin_is_not_the_operator(client, registered, chains):
    ch = client.post("/admin/auth/challenge", json={"address": OWNER_A.address, "business": "acme"}).json()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(OWNER_A, ch["message"])})
    assert r.status_code == 200
    h = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/operator/business-applications", headers=h).status_code == 403
    assert client.post("/operator/businesses/1/external", json={"external": True}, headers=h).status_code == 403
    assert client.get("/admin/overview", headers=h).json()["operator"] is False
    assert client.get("/admin/overview", headers=_operator(client)).json()["operator"] is True


def test_accepting_creates_the_hosted_agent_wallet_once(client, _env):
    app_id = _apply(client, OWNER_A).json()["id"]
    r = _accept(client, app_id)
    assert r.status_code == 200 and r.json()["business"]["agent_address"] == AGENT_A and r.json()["business"]["status"] == "pending"
    assert r.json()["business"]["contract"] is None
    assert _accept(client, app_id).status_code == 409                       # a second click does nothing
    assert _env["made"] == 1
    biz = asyncio.run(models.get_business_by_slug("acme"))
    assert (biz["approver_address"], biz["circle_wallet_id"], biz["contract_version"], biz["external"]) == (OWNER_A.address.lower(), "w-1", 2, 0)
    seen = _status(client, OWNER_A).json()["applications"][0]
    assert seen["status"] == "accepted" and seen["agent_address"] == AGENT_A and seen["contract"] is None


def test_a_circle_failure_accepts_nothing_and_can_be_retried(client, monkeypatch, _env):
    app_id = _apply(client, OWNER_A).json()["id"]
    calls = {"n": 0}

    def flaky(label):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("circle down")
        return {"wallet_id": "w-9", "address": AGENT_A}

    monkeypatch.setattr(main.contract, "create_agent_wallet", flaky)
    assert _accept(client, app_id).status_code == 502
    assert asyncio.run(models.get_business_application(app_id))["status"] == "pending"
    assert asyncio.run(models.get_business_by_slug("acme")) is None
    assert _accept(client, app_id).status_code == 200 and calls["n"] == 2


def test_a_crash_after_the_wallet_exists_never_creates_a_second_wallet(client, monkeypatch, _env):
    app_id = _apply(client, OWNER_A).json()["id"]
    real = models.finish_accepting
    state = {"fail": True}

    async def once_broken(application_id):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("db hiccup")
        return await real(application_id)

    monkeypatch.setattr(main.models, "finish_accepting", once_broken)
    assert _accept(client, app_id).status_code == 502
    assert asyncio.run(models.get_business_application(app_id))["agent_address"] == AGENT_A     # kept
    assert _accept(client, app_id).status_code == 200
    assert _env["made"] == 1                                                                   # the wallet was reused


def test_rejecting_closes_an_application_and_frees_the_name(client):
    app_id = _apply(client, OWNER_A).json()["id"]
    assert client.post(f"/operator/business-applications/{app_id}/reject", headers=_operator(client)).status_code == 200
    assert client.post(f"/operator/business-applications/{app_id}/reject", headers=_operator(client)).status_code == 409
    assert _accept(client, app_id).status_code == 409
    assert _apply(client, OWNER_B, slug="acme").status_code == 200


def test_an_unregistered_business_cannot_be_used_yet(client):
    app_id = _apply(client, OWNER_A).json()["id"]
    _accept(client, app_id)
    assert client.post("/vendor/auth/challenge", json={"address": OWNER_A.address, "business": "acme"}).status_code == 404
    assert client.post("/apply/challenge", json={"address": OWNER_A.address, "business": "acme"}).status_code == 404
    ch = client.post("/admin/auth/challenge", json={"address": OWNER_A.address, "business": "acme"}).json()
    assert client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(OWNER_A, ch["message"])}).status_code == 401
    assert client.get("/businesses").json()["businesses"][0]["slug"] == "ceedebooks" and len(client.get("/businesses").json()["businesses"]) == 1


# ---- registering from the chain

def test_registration_goes_live_only_after_the_chain_checks_out(client, monkeypatch, chains):
    app_id = _apply(client, OWNER_A).json()["id"]
    _accept(client, app_id)
    seen = {}

    def verify(tx, factory):
        seen["args"] = (tx, factory)
        return _creation(OWNER_A.address, AGENT_A, CONTRACT_A)

    monkeypatch.setattr(main.contract, "verify_business_creation", verify)
    r = client.post("/business/register", json={"application_id": app_id, "tx_hash": TX_A.upper().replace("0X", "0x")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["contract"] == CONTRACT_A and r.json()["admin_url"].endswith("/?business=acme")
    assert seen["args"] == (TX_A, config.budget_factory_address)
    biz = asyncio.run(models.get_business_by_slug("acme"))
    assert (biz["status"], biz["enforcer_address"], biz["created_tx_hash"]) == ("active", CONTRACT_A, TX_A)
    assert asyncio.run(models.get_business_application(app_id))["status"] == "registered"
    listed = {b["slug"]: b for b in client.get("/businesses").json()["businesses"]}
    assert listed["acme"]["contract"] == CONTRACT_A and listed["acme"]["agent_address"] == AGENT_A and listed["acme"]["external"] == 0
    assert "approver_ip" not in listed["acme"] and "contact" not in listed["acme"]
    # the owner can now sign in to their own business
    chains.get(CONTRACT_A, 2).approver_addr = OWNER_A.address
    ch = client.post("/admin/auth/challenge", json={"address": OWNER_A.address, "business": "acme"}).json()
    assert client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(OWNER_A, ch["message"])}).status_code == 200


def test_registration_is_idempotent_and_final(client, registered):
    again = client.post("/business/register", json={"application_id": registered, "tx_hash": TX_A})
    assert again.status_code == 200 and again.json()["contract"] == CONTRACT_A
    assert client.post("/business/register", json={"application_id": registered, "tx_hash": TX_B}).status_code == 409


@pytest.mark.parametrize("approver,agent,code", [
    (OWNER_B.address, AGENT_A, 422),        # created by a different wallet than the one that applied
    (OWNER_A.address, AGENT_B, 422),        # not using the agent wallet we issued
])
def test_a_contract_that_does_not_match_the_application_is_refused(client, monkeypatch, approver, agent, code):
    app_id = _apply(client, OWNER_A).json()["id"]
    _accept(client, app_id)
    monkeypatch.setattr(main.contract, "verify_business_creation", lambda tx, f: _creation(approver, agent, CONTRACT_A))
    assert client.post("/business/register", json={"application_id": app_id, "tx_hash": TX_A}).status_code == code
    assert asyncio.run(models.get_business_by_slug("acme"))["status"] == "pending"


def test_chain_problems_fail_closed(client, monkeypatch):
    app_id = _apply(client, OWNER_A).json()["id"]
    _accept(client, app_id)

    def not_ours(tx, f):
        raise ValueError("That transaction did not go to the CeedeBooks factory")

    def down(tx, f):
        raise RuntimeError("rpc down secret-detail")

    monkeypatch.setattr(main.contract, "verify_business_creation", not_ours)
    r = client.post("/business/register", json={"application_id": app_id, "tx_hash": TX_A})
    assert r.status_code == 422 and "factory" in r.json()["detail"]
    monkeypatch.setattr(main.contract, "verify_business_creation", down)
    r = client.post("/business/register", json={"application_id": app_id, "tx_hash": TX_A})
    assert r.status_code == 503 and "secret-detail" not in r.text
    assert asyncio.run(models.get_business_by_slug("acme"))["status"] == "pending"


def test_registration_needs_an_accepted_application_and_a_real_one(client, monkeypatch):
    monkeypatch.setattr(main.contract, "verify_business_creation", lambda tx, f: _creation(OWNER_A.address, AGENT_A, CONTRACT_A))
    app_id = _apply(client, OWNER_A).json()["id"]
    assert client.post("/business/register", json={"application_id": app_id, "tx_hash": TX_A}).status_code == 409   # not accepted yet
    assert client.post("/business/register", json={"application_id": 999, "tx_hash": TX_A}).status_code == 404
    assert client.post("/business/register", json={"application_id": app_id, "tx_hash": "0x12"}).status_code == 422


def test_one_contract_can_only_be_registered_once(client, monkeypatch, registered):
    app_b = _apply(client, OWNER_B, slug="beta").json()["id"]
    _accept(client, app_b)
    monkeypatch.setattr(main.contract, "verify_business_creation", lambda tx, f: _creation(OWNER_B.address, AGENT_B, CONTRACT_A))
    assert client.post("/business/register", json={"application_id": app_b, "tx_hash": TX_B}).status_code == 409
    assert asyncio.run(models.get_business_by_slug("beta"))["status"] == "pending"


# ---- the verification itself (mocked receipts and reads)

class _Fn:
    def __init__(self, value): self.value = value
    def call(self): return self.value


class _Contract:
    def __init__(self, **values): self.values = values

    @property
    def functions(self):
        outer = self

        class F:
            def __getattr__(self, name):
                return lambda *a: _Fn(outer.values[name])
        return F()


def _receipt(factory, enforcer, approver, agent, status=1, to=None, topic=None, count=1):
    topic = topic or contract._BUSINESS_CREATED
    pad = lambda a: bytes.fromhex("00" * 12 + a[2:])
    log = {"address": factory, "topics": [bytes.fromhex(topic), (1).to_bytes(32, "big"), pad(enforcer), pad(approver)], "data": pad(agent)}
    return {"status": status, "to": to or factory, "blockNumber": 77, "logs": [log] * count}


FACTORY = Web3.to_checksum_address("0x" + "9a" * 20)
ENF, APR, AGT = (Web3.to_checksum_address("0x" + c * 20) for c in ("11", "22", "33"))


def _patch_chain(monkeypatch, receipt, is_business=True, approver=APR, agent=AGT):
    monkeypatch.setattr(contract._w3.eth, "get_transaction_receipt", lambda h: receipt)

    def make(address, abi):
        return _Contract(isBusiness=is_business, approver=approver, agent=agent)

    monkeypatch.setattr(contract._w3.eth, "contract", make)


def test_verify_business_creation_decodes_a_real_looking_receipt(monkeypatch):
    _patch_chain(monkeypatch, _receipt(FACTORY, ENF, APR, AGT))
    assert contract.verify_business_creation("0x" + "ab" * 32, FACTORY) == {"id": 1, "enforcer": ENF, "approver": APR, "agent": AGT, "block": 77}


@pytest.mark.parametrize("label,receipt,kw,message", [
    ("failed tx", _receipt(FACTORY, ENF, APR, AGT, status=0), {}, "failed"),
    ("other contract", _receipt(FACTORY, ENF, APR, AGT, to="0x" + "00" * 20), {}, "did not go to the CeedeBooks factory"),
    ("no event", _receipt(FACTORY, ENF, APR, AGT, topic="aa" * 32), {}, "exactly one"),
    ("two events", _receipt(FACTORY, ENF, APR, AGT, count=2), {}, "exactly one"),
    ("not a business", _receipt(FACTORY, ENF, APR, AGT), {"is_business": False}, "does not list"),
    ("approver moved", _receipt(FACTORY, ENF, APR, AGT), {"approver": "0x" + "44" * 20}, "does not match"),
    ("agent differs", _receipt(FACTORY, ENF, APR, AGT), {"agent": "0x" + "44" * 20}, "does not match"),
])
def test_verify_business_creation_refuses_everything_else(monkeypatch, label, receipt, kw, message):
    _patch_chain(monkeypatch, receipt, **kw)
    with pytest.raises(ValueError, match=message):
        contract.verify_business_creation("0x" + "ab" * 32, FACTORY)


def test_an_event_from_another_contract_does_not_count(monkeypatch):
    fake = _receipt("0x" + "77" * 20, ENF, APR, AGT, to=FACTORY)  # same event shape, emitted by some other contract
    _patch_chain(monkeypatch, fake)
    with pytest.raises(ValueError, match="exactly one"):
        contract.verify_business_creation("0x" + "ab" * 32, FACTORY)


# ---- operator flag, public listing, checklist

def test_only_the_operator_marks_a_business_external_and_never_their_own(client, registered):
    op = _operator(client)
    assert client.post("/operator/businesses/1/external", json={"external": True}, headers=op).status_code == 404   # home business
    biz = asyncio.run(models.get_business_by_slug("acme"))
    assert client.post(f"/operator/businesses/{biz['id']}/external", json={"external": True}, headers=op).status_code == 200
    assert client.get("/stats").json()["businesses"] == {"total": 2, "external": 1}
    assert {b["slug"]: b["external"] for b in client.get("/businesses").json()["businesses"]} == {"ceedebooks": 0, "acme": 1}
    assert client.post(f"/operator/businesses/{biz['id']}/external", json={"external": False}, headers=op).status_code == 200
    assert client.get("/stats").json()["businesses"]["external"] == 0
    assert client.post("/operator/businesses/999/external", json={"external": True}, headers=op).status_code == 404
    assert client.post(f"/operator/businesses/{biz['id']}/external", json={"external": "yes please", "extra": 1}, headers=op).status_code == 422


def test_a_pending_business_cannot_be_marked_external(client):
    app_id = _apply(client, OWNER_A).json()["id"]
    _accept(client, app_id)
    biz = asyncio.run(models.get_business_by_slug("acme"))
    assert client.post(f"/operator/businesses/{biz['id']}/external", json={"external": True}, headers=_operator(client)).status_code == 404


def test_the_owner_checklist_reports_each_step_and_never_guesses(client, registered, chains, monkeypatch):
    chain = chains.get(CONTRACT_A, 2)
    chain.balance, chain.category_cap = 0, 0
    monkeypatch.setattr(main.contract, "native_balance", lambda a: 0)
    ch = client.post("/admin/auth/challenge", json={"address": OWNER_A.address, "business": "acme"}).json()
    token = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(OWNER_A, ch["message"])}).json()["token"]
    h = {"Authorization": "Bearer " + token}
    listed = client.get("/admin/onboarding", headers=h).json()["steps"]
    steps = {s["key"]: s["done"] for s in listed}
    assert steps == {"agent_gas": False, "pool_funded": False, "category_limit": False, "vendor": False, "purchase_order": False}
    pool = next(s["label"] for s in listed if s["key"] == "pool_funded")
    assert "Add funds" in pool and "send USDC to your contract" not in pool  # a plain send to the contract fails
    chain.balance, chain.category_cap = 5_000_000, 20_000_000
    monkeypatch.setattr(main.contract, "native_balance", lambda a: contract.GAS_DUST_UNITS)
    asyncio.run(models.save_vendor(2, "V", "0x" + "55" * 20))
    steps = {s["key"]: s["done"] for s in client.get("/admin/onboarding", headers=h).json()["steps"]}
    assert steps == {"agent_gas": True, "pool_funded": True, "category_limit": True, "vendor": True, "purchase_order": False}
    def boom(a): raise RuntimeError("rpc")
    monkeypatch.setattr(main.contract, "native_balance", boom)
    steps = {s["key"]: s["done"] for s in client.get("/admin/onboarding", headers=h).json()["steps"]}
    assert steps["agent_gas"] is None  # unreadable is shown as unknown, never as done
    body = client.get("/admin/onboarding", headers=h).json()
    assert body["agent_address"] == AGENT_A and body["contract"] == CONTRACT_A and "run your agent's wallet" in body["custody"]


def test_a_new_business_can_then_take_vendors_and_pay_through_its_own_contract(client, registered, chains, monkeypatch):
    """End of the road: the registered business is a normal business, isolated like any other."""
    key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(2, auth.hash_key(key), "buyer", None, "acme-ops"))
    r = client.post("/vendors", json={"name": "Acme vendor", "wallet_address": "0x" + "66" * 20}, headers={"X-API-Key": key})
    assert r.status_code == 200
    home_key = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(1, auth.hash_key(home_key), "buyer", None, "home"))
    assert client.get(f"/vendors/{r.json()['id']}", headers={"X-API-Key": home_key}).status_code == 404


def test_the_database_upgrade_adds_the_new_tables_and_keeps_every_row(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "keep.db"))
    asyncio.run(models.init_db())
    asyncio.run(models.save_vendor(1, "Kept", "0x" + "77" * 20))
    con = sqlite3.connect(config.db_path)
    con.execute("DROP TABLE business_applications")   # a v1.2.8.1 database does not have it
    con.execute("DROP INDEX IF EXISTS ux_business_contract")
    con.commit(); con.close()
    asyncio.run(models.init_db()); asyncio.run(models.init_db())
    con = sqlite3.connect(config.db_path)
    assert con.execute("SELECT COUNT(*) FROM business_applications").fetchone() == (0,)
    assert con.execute("SELECT COUNT(*) FROM vendors").fetchone() == (1,)
    assert con.execute("SELECT enforcer_address FROM businesses WHERE id = 1").fetchone()[0] == config.budget_enforcer_address


def test_the_agent_manifest_states_the_custody_model_and_lists_only_active_businesses(client, registered):
    body = client.get("/.well-known/agent.json").json()
    assert "runs each business's agent wallet" in body["custody"] and "replace the agent at any time" in body["custody"]
    assert [b["slug"] for b in body["businesses"]] == ["ceedebooks", "acme"]
