"""Phase H3 (v1.3.2.3): the agent's release path for a MilestoneEscrow. The chain, Circle and GitHub are always mocked; the
commitment hash is also checked against a vector produced by the real contract on a local node."""
import asyncio
import inspect

import aiosqlite
import httpx
import pytest
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient
from web3 import Web3

from agent import contract, decision_log, escrow_chain, escrow_release, ledger, safe_fetch
from agent.config import config
from agent.escrow_release import Assessment, Refused, Verdict
from backend import admin_auth, auth, main, models, vendor_auth

ADMIN_1 = Account.create()
ADMIN_2 = Account.create()
HOME_AGENT = "0x" + "a1" * 20
ACME = "0x" + "bb" * 20
ACME_AGENT = "0x" + "ab" * 20
ESC = "0x" + "e1" * 20
PUBLIC = "140.82.112.5"
BODY = {"owner": "acme", "repo": "app", "pr_number": 7}
BIZ = {"agent_address": HOME_AGENT, "circle_wallet_id": "wallet-1"}


def snap(**kw):
    s = {"address": ESC, "agent": HOME_AGENT, "approver": ADMIN_1.address, "contractor": "0x" + "c4" * 20, "usdc": config.usdc_address,
         "paused": False, "agent_release_limit_usdc": "0.25", "balance_usdc": "1", "pending_usdc": "0.8", "withdrawable_usdc": "0.2",
         "milestones": [
             {"index": 0, "amount_usdc": "0.1", "requirements_hash": "0x" + "11" * 32, "status": "pending", "agent_can_release": True},
             {"index": 1, "amount_usdc": "0.5", "requirements_hash": "0x" + "22" * 32, "status": "pending", "agent_can_release": False},
             {"index": 2, "amount_usdc": "0.2", "requirements_hash": "0x" + "33" * 32, "status": "released", "agent_can_release": False}]}
    s.update(kw)
    return s


MERGED = {"kind": "github_pr_merged", "repo": "acme/app", "pr": 7, "merged": True, "merged_at": "2026-10-20T10:00:00Z",
          "merge_commit_sha": "c" * 40, "base_ref": "main", "author": "dev"}
OPEN = {**MERGED, "merged": False, "merged_at": None, "merge_commit_sha": None}


# ---- the agent's verdict (pure)

def test_everything_in_order_is_a_release():
    a = assess = escrow_release.assess(snap(), 0, MERGED, BIZ)
    assert a.verdict == Verdict.RELEASE and all(a.checks.values())


@pytest.mark.parametrize("change,facts,verdict", [
    ({"paused": True}, MERGED, Verdict.HOLD),
    ({"balance_usdc": "0.09"}, MERGED, Verdict.HOLD),
    ({}, OPEN, Verdict.HOLD),
    ({}, {"merged": "true"}, Verdict.HOLD),   # only the boolean True counts
    ({}, {}, Verdict.HOLD),
])
def test_holds(change, facts, verdict):
    assert escrow_release.assess(snap(**change), 0, facts, BIZ).verdict == verdict


def test_a_milestone_above_the_limit_goes_to_the_approver_even_with_good_evidence():
    a = escrow_release.assess(snap(), 1, MERGED, BIZ)
    assert a.verdict == Verdict.ESCALATE and "only the approver" in a.reason


def test_the_order_of_reasons_is_pause_then_limit_then_funding_then_evidence():
    assert escrow_release.assess(snap(paused=True, balance_usdc="0"), 1, OPEN, BIZ).checks["escrow_not_paused"] is False
    assert escrow_release.assess(snap(paused=True, balance_usdc="0"), 1, OPEN, BIZ).verdict == Verdict.HOLD
    assert escrow_release.assess(snap(balance_usdc="0"), 1, OPEN, BIZ).verdict == Verdict.ESCALATE
    assert "holds 0" in escrow_release.assess(snap(balance_usdc="0"), 0, OPEN, BIZ).reason
    assert "not merged" in escrow_release.assess(snap(), 0, OPEN, BIZ).reason


def test_the_amount_exactly_at_the_limit_is_allowed_and_one_unit_over_is_not():
    s = snap(agent_release_limit_usdc="0.1")
    assert escrow_release.assess(s, 0, MERGED, BIZ).verdict == Verdict.RELEASE
    s["milestones"][0]["amount_usdc"] = "0.100001"
    assert escrow_release.assess(s, 0, MERGED, BIZ).verdict == Verdict.ESCALATE


def test_funding_exactly_equal_to_the_milestone_is_enough():
    assert escrow_release.assess(snap(balance_usdc="0.1"), 0, MERGED, BIZ).verdict == Verdict.RELEASE
    assert escrow_release.assess(snap(balance_usdc="0.099999"), 0, MERGED, BIZ).verdict == Verdict.HOLD


@pytest.mark.parametrize("index,status", [(2, 409), (5, 404), (-1, 404)])
def test_requests_that_are_not_decisions_are_refused(index, status):
    with pytest.raises(Refused) as e:
        escrow_release.assess(snap(), index, MERGED, BIZ)
    assert e.value.status == status


def test_an_escrow_whose_agent_is_not_this_businesss_wallet_is_refused():
    with pytest.raises(Refused):
        escrow_release.assess(snap(agent="0x" + "99" * 20), 0, MERGED, BIZ)
    with pytest.raises(Refused):
        escrow_release.assess(snap(), 0, MERGED, {"agent_address": None})
    assert escrow_release.assess(snap(agent=HOME_AGENT.upper().replace("0X", "0x")), 0, MERGED, BIZ).verdict == Verdict.RELEASE


def test_the_reasoning_text_is_code_written_and_carries_the_evidence_facts():
    s = snap()
    a = escrow_release.assess(s, 0, MERGED, BIZ)
    text = escrow_release.reasoning_text(ESC, 0, s, a, MERGED, "0x" + "ab" * 32)
    assert ESC in text and "milestone 0" in text and "0.1 USDC" in text and "Verdict: release" in text
    assert '"merge_commit_sha":"' + "c" * 40 + '"' in text and "not a release condition" in text
    assert "0x" + "11" * 32 in text and "0x" + "ab" * 32 in text


# ---- the commitment must be exactly what the contract computes

VECTOR_ESCROW = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"  # a MilestoneEscrow deployed on a local anvil node (chain id 31337)
VECTOR_COMMITMENT = "4e004ff4a92cffb1026826c5c7c15823bd7459b23c3ba1a0cb56956817700994"  # what its commitmentFor(0, 0xab.., 0xcd..) returned


def test_the_commitment_matches_a_value_computed_by_the_real_contract():
    got = escrow_release.commitment_for(VECTOR_ESCROW, 0, "0x" + "ab" * 32, "cd" * 32, chain_id=31337)
    assert got.hex() == VECTOR_COMMITMENT


def test_the_commitment_is_bound_to_every_input():
    base = ("0x" + "e1" * 20, 0, "0x" + "ab" * 32, "cd" * 32)
    ref = escrow_release.commitment_for(*base)
    assert ref == Web3.keccak(encode(["address", "uint256", "uint256", "bytes32", "bytes32"],
                                     [Web3.to_checksum_address(base[0]), contract.CHAIN_ID, 0, b"\xab" * 32, b"\xcd" * 32]))
    assert escrow_release.commitment_for("0x" + "e2" * 20, 0, base[2], base[3]) != ref
    assert escrow_release.commitment_for(base[0], 1, base[2], base[3]) != ref
    assert escrow_release.commitment_for(base[0], 0, "0x" + "ac" * 32, base[3]) != ref
    assert escrow_release.commitment_for(base[0], 0, base[2], "ce" * 32) != ref
    assert escrow_release.commitment_for(*base, chain_id=1) != ref


@pytest.mark.parametrize("bad", ["", "0x12", "zz" * 32, "0x" + "ab" * 31])
def test_a_malformed_hash_never_becomes_a_commitment(bad):
    with pytest.raises(ValueError):
        escrow_release.commitment_for(ESC, 0, bad, "cd" * 32)
    with pytest.raises(ValueError):
        escrow_release.commitment_for(ESC, 0, "0x" + "ab" * 32, bad)


# ---- the two transactions

class Wire:
    """Stands in for Circle: records every contract call and can fail the n-th."""

    def __init__(self, monkeypatch, fail_on=None, error=RuntimeError):
        self.calls, self.waits, self.fail_on, self.error = [], [], fail_on, error
        monkeypatch.setattr(contract, "execute_from_wallet", self.execute)
        monkeypatch.setattr(contract, "wait_for_transaction", lambda tx, *a, **k: self.waits.append((tx, len(self.calls))))
        monkeypatch.setattr(contract, "chain_tx_hash", lambda tx: "0x" + "9" * 64)
        self.on_send = None

    def execute(self, wallet_id, to, sig, params):
        self.calls.append((wallet_id, to, sig, params))
        if self.fail_on == len(self.calls):
            raise self.error("circle said no")
        if self.on_send:  # only a call that went through changes what the chain says
            self.on_send(sig, params)
        return f"circle-tx-{len(self.calls)}"


@pytest.fixture
def refusals(monkeypatch):
    flagged = []

    async def mark(h):
        flagged.append(h)

    async def record(h, tx, ch=None):
        flagged.append(("tx", h, tx, ch))
    monkeypatch.setattr(decision_log, "mark_contract_refused", mark)
    monkeypatch.setattr(decision_log, "record_onchain_tx", record)
    return flagged


def test_commit_then_release_sends_exactly_two_calls_in_order_and_waits_between(monkeypatch, refusals):
    wire = Wire(monkeypatch)
    tx = asyncio.run(escrow_release.commit_then_release(BIZ, ESC, 3, "0x" + "ab" * 32, "cd" * 32))
    assert tx == "circle-tx-2"
    assert [c[2] for c in wire.calls] == ["commitDecision(bytes32)", "releaseMilestone(uint256,bytes32,bytes32)"]
    assert all(c[0] == "wallet-1" and c[1].lower() == ESC for c in wire.calls)
    assert wire.calls[0][3] == [Web3.to_hex(escrow_release.commitment_for(ESC, 3, "0x" + "ab" * 32, "cd" * 32))]
    assert wire.calls[1][3] == ["3", "0x" + "ab" * 32, "0x" + "cd" * 32]
    assert wire.waits == [("circle-tx-1", 1), ("circle-tx-2", 2)]  # the commit confirmed before the release was sent
    assert refusals == [("tx", "cd" * 32, "circle-tx-2", "0x" + "9" * 64)]


def test_a_failed_commit_means_no_release_and_the_decision_is_flagged(monkeypatch, refusals):
    wire = Wire(monkeypatch, fail_on=1)
    with pytest.raises(RuntimeError):
        asyncio.run(escrow_release.commit_then_release(BIZ, ESC, 0, "0x" + "ab" * 32, "cd" * 32))
    assert len(wire.calls) == 1 and refusals == ["cd" * 32]


def test_a_failed_release_is_flagged_and_raised(monkeypatch, refusals):
    wire = Wire(monkeypatch, fail_on=2)
    with pytest.raises(RuntimeError):
        asyncio.run(escrow_release.commit_then_release(BIZ, ESC, 0, "0x" + "ab" * 32, "cd" * 32))
    assert len(wire.calls) == 2 and refusals == ["cd" * 32]


def test_a_confirmation_that_never_arrives_is_not_called_a_refusal(monkeypatch, refusals):
    Wire(monkeypatch)

    def slow(tx, *a, **k):
        raise TimeoutError("no terminal state")
    monkeypatch.setattr(contract, "wait_for_transaction", slow)
    with pytest.raises(TimeoutError):
        asyncio.run(escrow_release.commit_then_release(BIZ, ESC, 0, "0x" + "ab" * 32, "cd" * 32))
    assert refusals == []  # unknown is not "the contract refused"


def test_no_wallet_means_nothing_is_sent(monkeypatch, refusals):
    monkeypatch.undo()
    # the real helper refuses an empty wallet id before any request is built
    with pytest.raises(RuntimeError):
        escrow_release.send_commit("", ESC, b"\x00" * 32)


def test_the_release_module_sends_only_these_two_functions_and_uses_no_model():
    src = inspect.getsource(escrow_release)
    assert src.count("execute_from_wallet(") == 2
    for sig in ('"commitDecision(bytes32)"', '"releaseMilestone(uint256,bytes32,bytes32)"'):
        assert sig in src
    for word in ("overrideRelease", "withdraw", "setPaused", "setAgent", "proposeApprover", "rejectMilestone", "private_key"):
        assert word not in src.replace("(overrideRelease)", "")
    for word in ("llm", "groq", "second_opinion", "escalation_reasoning"):
        assert word not in src.lower().replace("approver", "")
    assert "commitDecision" not in inspect.getsource(escrow_chain) and "releaseMilestone" not in inspect.getsource(escrow_chain)


# ---- the routes

class FakeEscrow:
    registry = {}

    def __init__(self, address, **kw):
        self.address, self.broken = address, False
        self.view = snap(address=address, **kw)

    def snapshot(self):
        if self.broken:
            raise RuntimeError("unreadable")
        return {**self.view, "milestones": [dict(m) for m in self.view["milestones"]]}

    def release_events(self, tx_hash):
        return self.events

    events = []


def fake(address=ESC, **kw):
    FakeEscrow.registry[address.lower()] = FakeEscrow(address, **kw)
    return FakeEscrow.registry[address.lower()]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chains):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "release.db"))
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

    async def set_agent():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET agent_address = ?, circle_wallet_id = 'wallet-1' WHERE id = 1", (HOME_AGENT,))
            await db.commit()
    asyncio.run(set_agent())


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


def _github(monkeypatch, facts=None, status=200):
    sent = []

    def serve(request):
        sent.append(str(request.url))
        if status != 200:
            return httpx.Response(status, json={})
        data = {"number": 7, "merged": True, "merged_at": "2026-10-20T10:00:00Z", "merge_commit_sha": "c" * 40,
                "base": {"ref": "main", "repo": {"full_name": "acme/app"}}, "user": {"login": "dev"}}
        data.update(facts or {})
        return httpx.Response(200, json=data)

    async def resolver(host):
        return [PUBLIC]
    monkeypatch.setattr(safe_fetch, "_resolver", resolver)
    monkeypatch.setattr(safe_fetch, "_transport", httpx.MockTransport(serve))
    monkeypatch.setattr(safe_fetch, "_peer_ip", lambda response: PUBLIC)
    return sent


def _setup(client, monkeypatch, **kw):
    h = _bearer(client)
    esc = fake(ESC, **kw)
    r = client.post("/admin/escrows", json={"address": ESC}, headers=h)
    assert r.status_code == 200, r.text
    wire = Wire(monkeypatch)

    def landed(sig, params):  # a release that goes through changes what the escrow reads back
        if sig.startswith("releaseMilestone"):
            esc.view["milestones"][int(params[0])]["status"] = "released"
            esc.events = [{"event": "MilestoneReleased", "reasoning_hash": params[2][2:], "block": 9, "success": True}]
    wire.on_send = landed
    return h, r.json()["id"], esc, wire


def _dry(client, h, eid, index=0, body=BODY):
    return client.post(f"/admin/escrows/{eid}/milestones/{index}/decision", json=body, headers=h)


def _release(client, h, eid, index=0, evidence_hash=None, body=BODY):
    return client.post(f"/admin/escrows/{eid}/milestones/{index}/release", json={**body, "evidence_hash": evidence_hash}, headers=h)


def _audit(action_prefix="ESCROW"):
    import sqlite3
    db = sqlite3.connect(config.db_path)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute("SELECT * FROM audit_log WHERE action LIKE ? ORDER BY id", (action_prefix + "%",))]
    finally:
        db.close()


def test_the_dry_run_gives_a_verdict_and_a_hash_and_writes_nothing(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    r = _dry(client, h, eid)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["verdict"] == "release" and b["evidence_hash"].startswith("0x") and b["checks"]["evidence_pr_merged"] is True
    assert b["milestone"]["amount_usdc"] == "0.1" and "Nothing was recorded" in b["note"]
    assert wire.calls == [] and _audit() == []
    assert client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"] == []


def test_a_release_hashes_first_then_commits_then_releases(client, monkeypatch, chains):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    seen = {}
    wire_on_send = wire.on_send

    def watch(sig, params):
        seen.setdefault("rows_at_first_send", len(_audit()))
        wire_on_send(sig, params)
    wire.on_send = watch
    r = _release(client, h, eid, evidence_hash=ev)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["sent"] is True and b["verdict"] == "release" and b["milestone_status_after"] == "released" and b["onchain_tx_id"] == "circle-tx-2"
    assert seen["rows_at_first_send"] == 1  # the decision was already written when the first transaction went out
    assert [c[2] for c in wire.calls] == ["commitDecision(bytes32)", "releaseMilestone(uint256,bytes32,bytes32)"]
    row = _audit()[0]
    assert row["action"] == "ESCROW_RELEASED" and row["subject"] == f"escrow:{ESC}:milestone:0" and row["model_used"] == "rules"
    assert row["reasoning_hash"] == b["reasoning_hash"] and ev in row["reasoning"] and row["chain_tx_hash"] == "0x" + "9" * 64
    assert wire.calls[1][3] == ["0", ev, "0x" + b["reasoning_hash"]]
    assert wire.calls[0][3] == [Web3.to_hex(escrow_release.commitment_for(ESC, 0, ev, b["reasoning_hash"]))]
    rel = client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"]
    assert [(x["milestone_index"], x["status"], x["onchain_tx_id"]) for x in rel] == [(0, "released", "circle-tx-2")]
    assert all(c.calls == [] for c in chains.by_address.values())  # the business's budget contract was never touched


def test_the_record_verifies_and_its_event_is_found_on_the_escrow_not_the_budget_contract(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    b = _release(client, h, eid, evidence_hash=ev).json()
    v = client.get(f"/decisions/{b['reasoning_hash']}/verify").json()
    assert v["verified"] is True and v["onchain"]["checked"] is True and v["onchain"]["match"] is True
    assert v["onchain"]["event"] == "MilestoneReleased"
    esc.events = [{"event": "MilestoneReleased", "reasoning_hash": "00" * 32, "block": 9, "success": True}]
    assert client.get(f"/decisions/{b['reasoning_hash']}/verify").json()["onchain"]["match"] is False
    esc.events = [{"event": "MilestoneReleased", "reasoning_hash": b["reasoning_hash"], "block": 9, "success": False}]
    assert client.get(f"/decisions/{b['reasoning_hash']}/verify").json()["onchain"]["match"] is False

    async def unregister():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("DELETE FROM escrows")
            await db.commit()
    asyncio.run(unregister())
    out = client.get(f"/decisions/{b['reasoning_hash']}/verify").json()["onchain"]
    assert out["checked"] is False and "not registered" in out["detail"]


def test_the_ledger_still_verifies_after_an_escrow_decision_and_agent_totals_do_not_move(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    before = client.get("/stats").json()
    ev = _dry(client, h, eid).json()["evidence_hash"]
    _release(client, h, eid, evidence_hash=ev)
    after = client.get("/stats").json()
    for k in ("paid", "held", "escalated", "paid_usdc", "decisions"):
        assert after[k] == before[k]
    assert after["submissions"] == before["submissions"]
    led = client.get("/ledger/verify?business=ceedebooks").json()
    assert led["ok"] is True and led["problems"] == []


def test_the_evidence_must_still_be_what_the_admin_saw(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    _github(monkeypatch, facts={"user": {"login": "someone-else"}})
    r = _release(client, h, eid, evidence_hash=ev)
    assert r.status_code == 409 and "changed" in r.json()["detail"]
    assert wire.calls == [] and _audit() == []
    assert _release(client, h, eid, evidence_hash="0x" + "00" * 32).status_code == 409


def test_a_pull_request_that_is_not_merged_is_held_and_recorded_and_nothing_is_sent(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch, facts={"merged": False, "merged_at": None, "merge_commit_sha": None})
    d = _dry(client, h, eid).json()
    assert d["verdict"] == "hold"
    r = _release(client, h, eid, evidence_hash=d["evidence_hash"])
    assert r.status_code == 200 and r.json()["verdict"] == "hold" and r.json()["sent"] is False
    assert wire.calls == [] and [a["action"] for a in _audit()] == ["ESCROW_HELD"]
    assert client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"] == []  # nothing was claimed


@pytest.mark.parametrize("index,change,verdict,action", [
    (1, {}, "escalate", "ESCROW_ESCALATED"),
    (0, {"paused": True}, "hold", "ESCROW_HELD"),
    (0, {"balance_usdc": "0.05"}, "hold", "ESCROW_HELD"),
])
def test_other_verdicts_are_recorded_and_send_nothing(client, monkeypatch, index, change, verdict, action):
    h, eid, esc, wire = _setup(client, monkeypatch, **change)
    _github(monkeypatch)
    ev = _dry(client, h, eid, index).json()["evidence_hash"]
    r = _release(client, h, eid, index, evidence_hash=ev)
    assert r.status_code == 200 and r.json()["verdict"] == verdict and r.json()["sent"] is False
    assert wire.calls == [] and [a["action"] for a in _audit()] == [action]
    acts = client.get("/admin/actions", headers=h).json()
    assert any(a["action"] == "escrow_" + verdict for a in (acts["actions"] if isinstance(acts, dict) else acts))


def test_a_milestone_already_released_or_missing_is_not_a_decision(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    assert _dry(client, h, eid, 2).status_code == 409
    assert _dry(client, h, eid, 9).status_code == 404
    assert client.post(f"/admin/escrows/{eid}/milestones/50/decision", json=BODY, headers=h).status_code == 422
    assert client.post(f"/admin/escrows/{eid}/milestones/-1/decision", json=BODY, headers=h).status_code == 422
    assert _audit() == [] and wire.calls == []


def test_a_second_click_cannot_release_twice(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    assert asyncio.run(models.claim_escrow_release(1, eid, 0, ADMIN_1.address, ev)) is True  # another click is mid-release
    r = _release(client, h, eid, evidence_hash=ev)
    assert r.status_code == 409
    assert wire.calls == [] and _audit() == []  # and it wrote no decision that never went anywhere


def test_a_release_that_landed_cannot_be_released_again(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    assert _release(client, h, eid, evidence_hash=ev).status_code == 200
    assert _release(client, h, eid, evidence_hash=ev).status_code == 409  # the chain now says Released
    assert len(wire.calls) == 2 and len(_audit()) == 1


def test_a_failed_release_is_flagged_answered_502_and_can_be_tried_again(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    wire.fail_on = 2
    r = _release(client, h, eid, evidence_hash=ev)
    assert r.status_code == 502 and "recorded and flagged" in r.json()["detail"]
    row = _audit()[0]
    assert row["contract_refused"] == 1
    rel = client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"]
    assert rel[0]["status"] == "failed" and rel[0]["reasoning_hash"] == row["reasoning_hash"]
    wire.fail_on = None
    r2 = _release(client, h, eid, evidence_hash=ev)
    assert r2.status_code == 200 and r2.json()["milestone_status_after"] == "released"
    assert client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"][0]["status"] == "released"


def test_a_confirmation_that_timed_out_but_landed_is_read_from_the_chain(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]

    def slow(tx, *a, **k):
        if tx == "circle-tx-2":
            raise TimeoutError("no terminal state")
    monkeypatch.setattr(contract, "wait_for_transaction", slow)
    r = _release(client, h, eid, evidence_hash=ev)
    assert r.status_code == 200 and r.json()["milestone_status_after"] == "released" and "confirmation was not seen" in r.json()["warning"]
    assert client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"][0]["status"] == "released"
    assert _audit()[0]["contract_refused"] in (None, 0)


def test_a_timeout_that_did_not_land_is_a_failure_not_a_success(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    wire.on_send = None  # the release never lands on the fake chain

    def slow(tx, *a, **k):
        if tx == "circle-tx-2":
            raise TimeoutError("no terminal state")
    monkeypatch.setattr(contract, "wait_for_transaction", slow)
    assert _release(client, h, eid, evidence_hash=ev).status_code == 502
    assert client.get(f"/admin/escrows/{eid}/releases", headers=h).json()["releases"][0]["status"] == "failed"


def test_nothing_happens_when_the_chain_or_github_cannot_be_read(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch, status=404)
    assert _dry(client, h, eid).status_code == 502
    assert _release(client, h, eid, evidence_hash="0x" + "00" * 32).status_code == 502
    _github(monkeypatch)
    esc.broken = True
    assert _dry(client, h, eid).status_code == 503
    assert _release(client, h, eid, evidence_hash="0x" + "00" * 32).status_code == 503
    assert wire.calls == [] and _audit() == []


def test_a_refused_or_invalid_target_fetches_nothing(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    sent = _github(monkeypatch)
    for body in ({"owner": "a/../b", "repo": "app", "pr_number": 7}, {"owner": "acme", "repo": "app", "pr_number": 0},
                 {**BODY, "url": "http://127.0.0.1/"}, {"url": "https://169.254.169.254/"}):
        assert _dry(client, h, eid, body=body).status_code == 422
    assert client.post(f"/admin/escrows/{eid}/milestones/0/release", json={**BODY, "evidence_hash": "not-a-hash"}, headers=h).status_code == 422
    assert client.post(f"/admin/escrows/{eid}/milestones/0/release", json=BODY, headers=h).status_code == 422
    assert sent == [] and wire.calls == []


def test_without_a_ledger_key_nothing_is_decided_or_sent(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    ev = _dry(client, h, eid).json()["evidence_hash"]
    monkeypatch.setattr(config, "ledger_signing_key", "")
    r = _release(client, h, eid, evidence_hash=ev)
    assert r.status_code == 503 and "ledger key" in r.json()["detail"]
    assert wire.calls == [] and _audit() == []
    monkeypatch.setattr(config, "ledger_signing_key", "11" * 32)
    assert _release(client, h, eid, evidence_hash=ev).status_code == 200  # the claim was given back


def test_these_routes_need_a_wallet_session_and_an_active_business(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    for path in (f"/admin/escrows/{eid}/milestones/0/decision", f"/admin/escrows/{eid}/milestones/0/release"):
        assert client.post(path, json={**BODY, "evidence_hash": "0x" + "00" * 32}).status_code == 401
    assert client.get(f"/admin/escrows/{eid}/releases").status_code == 401

    async def archive():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'archived' WHERE id = 1")
            await db.commit()
    asyncio.run(archive())
    r = _dry(client, h, eid)
    assert r.status_code == 403 and "archived" in r.json()["detail"]
    assert _release(client, h, eid, evidence_hash="0x" + "00" * 32).status_code == 403
    assert wire.calls == []


def test_one_business_cannot_decide_on_another_businesss_escrow(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)

    async def add_acme():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, approver_address, agent_address, contract_version, status, created_at) "
                "VALUES ('Acme', 'acme', ?, ?, ?, 2, 'active', 0)", (ACME, ADMIN_2.address, ACME_AGENT))
            await db.commit()
    asyncio.run(add_acme())
    h2 = _bearer(client, ADMIN_2, "acme")
    assert _dry(client, h2, eid).status_code == 404
    assert _release(client, h2, eid, evidence_hash="0x" + "00" * 32).status_code == 404
    assert client.get(f"/admin/escrows/{eid}/releases", headers=h2).status_code == 404
    assert wire.calls == [] and _audit() == []


def test_an_escrow_whose_agent_was_rotated_away_cannot_be_released_by_this_business(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    esc.view["agent"] = "0x" + "99" * 20
    r = _dry(client, h, eid)
    assert r.status_code == 422 and "agent" in r.json()["detail"]
    assert wire.calls == [] and _audit() == []


def test_the_decision_routes_share_the_evidence_rate_limits(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    codes = [_dry(client, h, eid).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    assert wire.calls == []


def test_a_claim_can_be_taken_only_when_it_is_free(monkeypatch):
    c = lambda i=0: asyncio.run(models.claim_escrow_release(1, 5, i, ADMIN_1.address, "0x" + "ab" * 32))
    assert c() is True and c() is False                                     # a live claim is not taken again
    asyncio.run(models.finish_escrow_release(5, 0, "failed"))
    assert c() is True                                                      # a failed attempt can be retried
    asyncio.run(models.finish_escrow_release(5, 0, "released", reasoning_hash="ab" * 32, onchain_tx_id="t"))
    assert c() is False                                                     # a released milestone never
    assert c(1) is True                                                     # another milestone is independent
    monkeypatch.setattr(models, "RELEASE_CLAIM_STALE_SECONDS", -1)
    assert c(1) is True                                                     # a claim left behind by a crash expires
    assert c(0) is False                                                    # but never a released one


def test_a_release_record_naming_another_businesss_escrow_is_not_confirmed_by_it(client, monkeypatch):
    h, eid, esc, wire = _setup(client, monkeypatch)
    _github(monkeypatch)
    b = _release(client, h, eid, evidence_hash=_dry(client, h, eid).json()["evidence_hash"]).json()

    async def move():  # the escrow now belongs to a different business than the one that wrote the record
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, approver_address, agent_address, contract_version, status, created_at) "
                "VALUES ('Acme', 'acme', ?, ?, ?, 2, 'active', 0)", (ACME, ADMIN_2.address, ACME_AGENT))
            await db.execute("UPDATE escrows SET business_id = (SELECT id FROM businesses WHERE slug = 'acme')")
            await db.commit()
    asyncio.run(move())
    out = client.get(f"/decisions/{b['reasoning_hash']}/verify").json()["onchain"]
    assert out["checked"] is False and out["match"] is None
