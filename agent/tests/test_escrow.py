"""Phase H (v1.3.2.1): registering and reading milestone escrows, and the evidence dry run. The chain and GitHub are
always mocked. No route here sends a transaction, and a test shows the escrow module has no way to."""
import asyncio

import aiosqlite
import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log, escrow_chain, safe_fetch
from agent.config import config
from backend import admin_auth, auth, main, models, vendor_auth

ADMIN_1 = Account.create()
ADMIN_2 = Account.create()
HOME_AGENT = "0x" + "a1" * 20
ACME = "0x" + "bb" * 20
ACME_AGENT = "0x" + "ab" * 20
ESC_HOME = "0x" + "e1" * 20
ESC_ACME = "0x" + "e2" * 20
PUBLIC = "140.82.112.5"


class FakeEscrow:
    """What an EscrowChain would read. `broken` is an unreachable RPC, `no_code` an address with nothing deployed."""
    registry = {}

    def __init__(self, address, **kw):
        self.address = address
        self.broken = self.no_code = False
        self.view = {"address": address, "agent": HOME_AGENT, "approver": ADMIN_1.address, "contractor": "0x" + "c4" * 20,
                     "usdc": config.usdc_address, "paused": False, "agent_release_limit_usdc": "0.25", "balance_usdc": "0",
                     "pending_usdc": "0.8", "withdrawable_usdc": "0",
                     "milestones": [{"index": 0, "amount_usdc": "0.1", "requirements_hash": "0x" + "11" * 32, "status": "pending", "agent_can_release": True}]}
        self.view.update(kw)

    def snapshot(self):
        if self.broken or self.no_code:
            raise RuntimeError("unreadable")
        return dict(self.view)


def fake(address, **kw):
    FakeEscrow.registry[address.lower()] = FakeEscrow(address, **kw)
    return FakeEscrow.registry[address.lower()]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chains):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "escrow.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    vendor_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    main._escrow_cache.clear()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    chains.get(config.budget_enforcer_address, 1).approver_addr = ADMIN_1.address
    chains.get(ACME, 2).approver_addr = ADMIN_2.address
    FakeEscrow.registry = {}
    monkeypatch.setattr(escrow_chain, "escrow_for", lambda address: FakeEscrow.registry[address.lower()])

    async def set_home_agent():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET agent_address = ? WHERE id = 1", (HOME_AGENT,))
            await db.commit()
    asyncio.run(set_home_agent())


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


def _add_acme(status="active"):
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, approver_address, agent_address, contract_version, status, created_at) "
                "VALUES ('Acme', 'acme', ?, ?, ?, 2, ?, 0)", (ACME, ADMIN_2.address, ACME_AGENT, status))
            await db.commit()
    asyncio.run(go())


def _both(client):
    _add_acme()
    fake(ESC_HOME)
    fake(ESC_ACME, agent=ACME_AGENT, approver=ADMIN_2.address)
    return _bearer(client), _bearer(client, ADMIN_2, "acme")


def _register(client, h, address):
    return client.post("/admin/escrows", json={"address": address}, headers=h)


# ---- registration

def test_registering_an_escrow_the_contract_itself_vouches_for(client):
    h = _bearer(client)
    fake(ESC_HOME)
    r = _register(client, h, ESC_HOME)
    assert r.status_code == 200, r.text
    assert r.json()["address"] == ESC_HOME and r.json()["business"] == "ceedebooks" and r.json()["escrow"]["pending_usdc"] == "0.8"
    assert [e["address"] for e in client.get("/admin/escrows", headers=h).json()["escrows"]] == [ESC_HOME]
    log = client.get("/admin/actions", headers=h).json()
    assert any(a["action"] == "register_escrow" and a["ref"] == ESC_HOME for a in (log["actions"] if isinstance(log, dict) else log))


@pytest.mark.parametrize("field,value,words", [
    ("agent", "0x" + "99" * 20, "agent wallet"),
    ("approver", "0x" + "98" * 20, "approver"),
    ("usdc", "0x" + "97" * 20, "USDC"),
])
def test_an_escrow_the_contract_does_not_tie_to_you_is_refused(client, field, value, words):
    h = _bearer(client)
    fake(ESC_HOME, **{field: value})
    r = _register(client, h, ESC_HOME)
    assert r.status_code == 422 and words in r.json()["detail"]
    assert client.get("/admin/escrows", headers=h).json()["escrows"] == []


def test_a_businesss_own_budget_contract_is_not_an_escrow(client):
    h = _bearer(client)
    fake(config.budget_enforcer_address)
    assert _register(client, h, config.budget_enforcer_address).status_code == 422


def test_an_unreadable_chain_or_an_empty_address_registers_nothing(client):
    h = _bearer(client)
    fake(ESC_HOME).broken = True
    assert _register(client, h, ESC_HOME).status_code == 503
    fake(ESC_HOME).no_code = True
    assert _register(client, h, ESC_HOME).status_code == 503
    assert client.get("/admin/escrows", headers=h).json()["escrows"] == []


def test_the_same_escrow_cannot_be_registered_twice_or_by_two_businesses(client):
    h1, h2 = _both(client)
    assert _register(client, h1, ESC_HOME).status_code == 200
    assert _register(client, h1, ESC_HOME).status_code == 409
    assert _register(client, h2, ESC_HOME).status_code == 422  # its agent is not Acme's wallet
    fake(ESC_HOME, agent=ACME_AGENT, approver=ADMIN_2.address)  # even if a contract named Acme, the row is taken
    assert _register(client, h2, ESC_HOME).status_code == 409


def test_a_business_that_is_not_active_cannot_register(client):
    _add_acme()
    fake(ESC_ACME, agent=ACME_AGENT, approver=ADMIN_2.address)
    h2 = _bearer(client, ADMIN_2, "acme")

    async def archive():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'archived' WHERE slug = 'acme'")
            await db.commit()
    asyncio.run(archive())
    r = _register(client, h2, ESC_ACME)
    assert r.status_code == 403 and "archived" in r.json()["detail"]
    assert client.get("/admin/escrows", headers=h2).json()["escrows"] == []


def test_registration_needs_a_wallet_session_not_a_key_or_nothing(client):
    fake(ESC_HOME)
    assert client.post("/admin/escrows", json={"address": ESC_HOME}).status_code == 401
    for bad in ("0x123", "not-an-address", ESC_HOME + "00", ""):
        assert client.post("/admin/escrows", json={"address": bad}, headers=_bearer(client)).status_code == 422
    assert client.post("/admin/escrows", json={"address": ESC_HOME, "extra": 1}, headers=_bearer(client)).status_code == 422


def test_registration_is_rate_limited(client):
    h = _bearer(client)
    fake(ESC_HOME, agent="0x" + "99" * 20)
    codes = [_register(client, h, ESC_HOME).status_code for _ in range(11)]
    assert codes[:10] == [422] * 10 and codes[10] == 429


# ---- tenant isolation: A's session on B's escrow answers 404

def test_one_business_cannot_see_or_check_another_businesss_escrow(client, monkeypatch):
    h1, h2 = _both(client)
    assert _register(client, h1, ESC_HOME).status_code == 200
    assert _register(client, h2, ESC_ACME).status_code == 200
    home_id = client.get("/admin/escrows", headers=h1).json()["escrows"][0]["id"]
    acme_id = client.get("/admin/escrows", headers=h2).json()["escrows"][0]["id"]
    assert client.get(f"/admin/escrows/{home_id}", headers=h2).status_code == 404
    assert client.get(f"/admin/escrows/{acme_id}", headers=h1).status_code == 404
    body = {"owner": "acme", "repo": "app", "pr_number": 7}
    assert client.post(f"/admin/escrows/{home_id}/evidence-check", json=body, headers=h2).status_code == 404
    assert client.get(f"/admin/escrows/{home_id}", headers=h1).status_code == 200
    assert [e["address"] for e in client.get("/admin/escrows", headers=h2).json()["escrows"]] == [ESC_ACME]


# ---- reading

def test_a_read_that_fails_says_unreadable_and_never_zero(client):
    h = _bearer(client)
    esc = fake(ESC_HOME)
    _register(client, h, ESC_HOME)
    main._escrow_cache.clear()
    esc.broken = True
    row = client.get("/admin/escrows", headers=h).json()["escrows"][0]
    assert row["chain_ok"] is False and row["escrow"] is None
    pub = client.get(f"/escrows/{ESC_HOME}").json()
    assert pub["chain_ok"] is False and pub["escrow"] is None


def test_a_failed_read_is_not_cached(client):
    h = _bearer(client)
    esc = fake(ESC_HOME)
    _register(client, h, ESC_HOME)
    main._escrow_cache.clear()
    esc.broken = True
    assert client.get(f"/escrows/{ESC_HOME}").json()["chain_ok"] is False
    esc.broken = False
    assert client.get(f"/escrows/{ESC_HOME}").json()["chain_ok"] is True


def test_reads_are_cached_briefly(client):
    h = _bearer(client)
    esc = fake(ESC_HOME)
    _register(client, h, ESC_HOME)
    main._escrow_cache.clear()
    client.get(f"/escrows/{ESC_HOME}")
    esc.view["paused"] = True
    assert client.get(f"/escrows/{ESC_HOME}").json()["escrow"]["paused"] is False  # still the cached read
    main._escrow_cache.clear()
    assert client.get(f"/escrows/{ESC_HOME}").json()["escrow"]["paused"] is True


def test_a_registration_attempt_does_not_fill_the_cache(client):
    h = _bearer(client)
    fake(ESC_HOME, agent="0x" + "99" * 20)
    _register(client, h, ESC_HOME)
    assert main._escrow_cache == {}


def test_the_public_routes_show_registered_escrows_of_active_businesses_only(client):
    h1, h2 = _both(client)
    _register(client, h1, ESC_HOME)
    _register(client, h2, ESC_ACME)
    fake("0x" + "e3" * 20)  # exists on-chain but nobody registered it
    assert client.get(f"/escrows/{'0x' + 'e3' * 20}").status_code == 404
    listed = client.get("/escrows").json()["escrows"]
    assert {(e["address"], e["business"]) for e in listed} == {(ESC_HOME, "ceedebooks"), (ESC_ACME, "acme")}
    assert [e["address"] for e in client.get("/escrows?business=acme").json()["escrows"]] == [ESC_ACME]
    assert client.get("/escrows?business=nope").status_code == 404
    assert client.get(f"/escrows/{ESC_ACME}").json()["business"] == "acme"
    assert client.get(f"/escrows/{ESC_ACME.upper().replace('0X', '0x')}").status_code == 200  # address case does not matter
    assert client.get("/escrows/0x123").status_code == 422

    async def archive():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'archived' WHERE slug = 'acme'")
            await db.commit()
    asyncio.run(archive())
    assert client.get(f"/escrows/{ESC_ACME}").status_code == 404
    assert [e["business"] for e in client.get("/escrows").json()["escrows"]] == ["ceedebooks"]


def test_the_public_response_carries_no_private_field(client):
    h = _bearer(client)
    fake(ESC_HOME)
    _register(client, h, ESC_HOME)
    body = client.get(f"/escrows/{ESC_HOME}").json()
    assert set(body) == {"address", "business", "registered_at", "chain_ok", "escrow"}
    text = str(body).lower()
    assert ADMIN_1.address.lower() not in str(client.get("/escrows").json()).lower()  # the list never names the registering wallet
    assert "registered_by" not in text


# ---- the evidence dry run

def _github(monkeypatch, handler=None):
    sent = []

    def serve(request):
        sent.append(str(request.url))
        if handler:
            return handler(request)
        return httpx.Response(200, json={"number": 7, "merged": True, "merged_at": "2026-10-20T10:00:00Z", "merge_commit_sha": "c" * 40,
                                         "base": {"ref": "main", "repo": {"full_name": "acme/app"}}, "user": {"login": "dev"}})

    async def resolver(host):
        return [PUBLIC]

    monkeypatch.setattr(safe_fetch, "_resolver", resolver)
    monkeypatch.setattr(safe_fetch, "_transport", httpx.MockTransport(serve))
    monkeypatch.setattr(safe_fetch, "_peer_ip", lambda response: PUBLIC)
    return sent


def _escrow_id(client, h):
    fake(ESC_HOME)
    _register(client, h, ESC_HOME)
    return client.get("/admin/escrows", headers=h).json()["escrows"][0]["id"]


def test_the_evidence_check_returns_facts_and_a_hash_and_sends_nothing_to_the_chain(client, monkeypatch, chains):
    h = _bearer(client)
    eid = _escrow_id(client, h)
    sent = _github(monkeypatch)
    r = client.post(f"/admin/escrows/{eid}/evidence-check", json={"owner": "acme", "repo": "app", "pr_number": 7}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["facts"]["merged"] is True and body["evidence_hash"].startswith("0x") and "not that the requirements are met" in body["note"]
    assert sent == ["https://api.github.com/repos/acme/app/pulls/7"]
    assert all(c.calls == [] for c in chains.by_address.values())


def test_evidence_check_input_is_validated_before_anything_is_fetched(client, monkeypatch):
    h = _bearer(client)
    eid = _escrow_id(client, h)
    sent = _github(monkeypatch)
    for body in ({"owner": "acme/../x", "repo": "app", "pr_number": 7}, {"owner": "acme", "repo": "..", "pr_number": 7},
                 {"owner": "acme", "repo": "app", "pr_number": 0}, {"owner": "acme", "repo": "app", "pr_number": "7; DROP"},
                 {"owner": "acme", "repo": "app", "pr_number": 7, "url": "http://127.0.0.1:8004/"}, {"url": "https://169.254.169.254/"}):
        assert client.post(f"/admin/escrows/{eid}/evidence-check", json=body, headers=h).status_code == 422, body
    assert sent == []


def test_a_refused_or_failed_fetch_is_reported_and_never_a_success(client, monkeypatch):
    h = _bearer(client)
    eid = _escrow_id(client, h)
    body = {"owner": "acme", "repo": "app", "pr_number": 7}
    _github(monkeypatch, handler=lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/"}))
    assert client.post(f"/admin/escrows/{eid}/evidence-check", json=body, headers=h).status_code == 422
    _github(monkeypatch, handler=lambda r: httpx.Response(404, json={}))
    r = client.post(f"/admin/escrows/{eid}/evidence-check", json=body, headers=h)
    assert r.status_code == 502 and "nothing was checked" in r.json()["detail"]
    _github(monkeypatch, handler=lambda r: httpx.Response(200, json={"number": 7}))
    assert client.post(f"/admin/escrows/{eid}/evidence-check", json=body, headers=h).status_code == 502


def test_the_evidence_check_is_rate_limited_per_admin_and_overall(client, monkeypatch):
    h = _bearer(client)
    eid = _escrow_id(client, h)
    _github(monkeypatch)
    body = {"owner": "acme", "repo": "app", "pr_number": 7}
    codes = [client.post(f"/admin/escrows/{eid}/evidence-check", json=body, headers=h).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    auth.request_limiter.reset()
    for _ in range(main.EVIDENCE_ALL_PER_HOUR):
        assert auth.request_limiter.allow("evidence-all", main.EVIDENCE_ALL_PER_HOUR, 3600.0)
    assert client.post(f"/admin/escrows/{eid}/evidence-check", json=body, headers=h).status_code == 429


def test_no_session_no_check(client, monkeypatch):
    _github(monkeypatch)
    assert client.post("/admin/escrows/1/evidence-check", json={"owner": "a", "repo": "b", "pr_number": 1}).status_code == 401


def test_nothing_in_the_escrow_code_can_send_a_transaction():
    import inspect
    src = inspect.getsource(escrow_chain)
    for word in ("execute_from_wallet", "send_transaction", ".transact(", "_execute", "create_developer_transaction", "private_key"):
        assert word not in src
    assert "commitDecision" not in src and "releaseMilestone" not in src
