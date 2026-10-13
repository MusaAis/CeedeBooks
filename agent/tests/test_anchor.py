"""Phase E2: anchoring each business's ledger head on-chain. The chain and the anchor contract are always mocked
(conftest.FakeChain, FakeAnchor); the ledger, the hashing and the anchoring rules are real."""
import asyncio
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from agent import anchor, decision_log, ledger
from agent.config import config
from backend import auth, main, models

ACME = "0x" + "bb" * 20
AGENT1, AGENT2 = "0x" + "a1" * 20, "0x" + "a2" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "anchor.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    asyncio.run(anchor.ensure_schema())
    auth.request_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _sql(sql, args=()):
    con = sqlite3.connect(config.db_path)
    con.execute(sql, args); con.commit(); con.close()


def _rows(sql, args=()):
    con = sqlite3.connect(config.db_path); con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute(sql, args)]; con.close()
    return out


def _business(business_id=1):
    return asyncio.run(models.get_business(business_id))


def _add_acme() -> int:
    con = sqlite3.connect(config.db_path)
    cur = con.execute("INSERT INTO businesses (name, slug, enforcer_address, contract_version, status, created_at) "
                      "VALUES ('Acme', 'acme', ?, 2, 'active', 0)", (ACME,))
    con.commit(); con.close()
    return cur.lastrowid


def _wire(chains, anchors, business_id=1, agent=AGENT1, wallet="w1"):
    """Gives a business an agent wallet that the fake chain and fake anchor contract both know."""
    _sql("UPDATE businesses SET circle_wallet_id = ?, agent_address = ? WHERE id = ?", (wallet, agent, business_id))
    chains.for_business(_business(business_id)).agent_addr = agent
    anchors.sender_of[wallet] = agent


def _log(n, business_id=1, amount=None):
    return asyncio.run(decision_log.log_decision("INVOICE_PAID", f"INV-{n}", "because", model_used="rules",
                                                 amount=float(n) if amount is None else amount, business=_business(business_id)))


def _head(business_id=1):
    return asyncio.run(anchor.head_of(business_id))


def _anchor(business_id=1, **kw):
    return asyncio.run(anchor.anchor_now(_business(business_id), **kw))


def _check(business_id=1):
    return asyncio.run(anchor.check(_business(business_id)))


@pytest.fixture
def five(chains, anchors):
    _wire(chains, anchors)
    for n in range(1, 6):
        _log(n)
    return _head()


# ---- when the background job anchors

def test_the_rule_waits_for_25_entries_or_6_hours_and_only_when_the_head_moved():
    last = {"sequence": 10, "created_at": 1000.0}
    assert not anchor.due(10, last, 1000.0 + 99 * 3600)            # the head did not move: nothing to anchor, however long it has been
    assert not anchor.due(34, last, 1000.0 + 60)                   # 24 new entries, just now
    assert anchor.due(35, last, 1000.0 + 60)                       # 25 new entries
    assert not anchor.due(11, last, 1000.0 + 6 * 3600 - 1)         # one new entry, almost 6 hours
    assert anchor.due(11, last, 1000.0 + 6 * 3600)                 # one new entry, 6 hours
    assert not anchor.due(50, None, 9e9)                           # never anchored: the first anchor is made by hand
    assert not anchor.due(0, last, 9e9)


def test_the_job_leaves_a_business_that_was_never_anchored_alone(chains, anchors, five):
    assert asyncio.run(anchor.run_once()) == {"ceedebooks": "not_due"}
    assert anchors.sent == []


def test_the_job_anchors_when_due_and_records_it(chains, anchors, five):
    _anchor(manual=True)                                            # the first anchor, by hand
    for n in range(6, 31):
        _log(n)                                                      # 25 more entries
    assert asyncio.run(anchor.run_once()) == {"ceedebooks": "anchored"}
    assert [s[2] for s in anchors.sent] == [5, 30]
    assert asyncio.run(anchor.last_recorded(1))["sequence"] == 30
    assert asyncio.run(anchor.run_once()) == {"ceedebooks": "not_due"}


def test_the_job_after_6_hours_with_a_moved_head(chains, anchors, five):
    _anchor(manual=True)
    _sql("UPDATE ledger_anchors SET created_at = ?", (time.time() - 7 * 3600,))
    assert asyncio.run(anchor.run_once()) == {"ceedebooks": "not_due"}   # the head has not moved
    _log(6)
    assert asyncio.run(anchor.run_once()) == {"ceedebooks": "anchored"}


def test_a_failing_anchor_is_only_logged_and_decisions_and_payments_go_on(chains, anchors, five):
    _anchor(manual=True)
    for n in range(6, 31):
        _log(n)
    anchors.fail_send = True
    assert asyncio.run(anchor.run_once()) == {"ceedebooks": "failed"}   # never raises
    assert asyncio.run(anchor.last_recorded(1))["sequence"] == 5
    _log(31)                                                         # the next decision is logged as normal
    assert ledger_ok()


def ledger_ok(business_id=1):
    return asyncio.run(ledger.verify_chain(business_id))["ok"]


def test_archived_and_pending_businesses_are_not_anchored(chains, anchors, five):
    _anchor(manual=True)
    for n in range(6, 31):
        _log(n)
    _sql("UPDATE businesses SET status = 'archived' WHERE id = 1")
    assert asyncio.run(anchor.run_once()) == {}
    assert len(anchors.sent) == 1


# ---- anchoring by hand and the checks before anything is sent

def test_a_manual_anchor_sends_the_current_head_once_and_confirms_it_on_the_chain(chains, anchors, five):
    r = _anchor(manual=True)
    assert r["status"] == "anchored" and r["sequence"] == 5 and r["head"] == five[1] and r["tx_id"] == "anchor-tx-1"
    assert anchors.sent == [("w1", five[1], 5)]
    assert anchors.state[AGENT1] == (5, five[1])
    assert _rows("SELECT sequence, head FROM ledger_anchors") == [{"sequence": 5, "head": five[1]}]
    again = _anchor(manual=True)
    assert again["status"] == "already_anchored" and len(anchors.sent) == 1   # nothing sent twice


def test_nothing_is_anchored_for_a_ledger_with_only_a_genesis_entry(chains, anchors):
    _wire(chains, anchors)
    asyncio.run(ledger.start())
    _log(1); _sql("DELETE FROM ledger WHERE sequence > 0")
    assert _anchor(manual=True)["status"] == "nothing_to_anchor" and anchors.sent == []
    assert _anchor(manual=True, now=0)["status"] == "nothing_to_anchor"


@pytest.mark.parametrize("setup,message", [
    (lambda a, c: a.gas.update({AGENT1: 10 ** 15}), "too little gas"),
    (lambda a, c: setattr(c, "broken", True), "could not be read"),
    (lambda a, c: setattr(a, "broken", True), "could not be read"),
    (lambda a, c: setattr(c, "agent_addr", AGENT2), "not the agent"),
    (lambda a, c: a.state.update({AGENT1: (9, "ab" * 32)}), "beyond this ledger"),
    (lambda a, c: a.state.update({AGENT1: (5, "ab" * 32)}), "not this ledger's"),
])
def test_nothing_is_sent_when_a_check_fails(chains, anchors, five, setup, message):
    setup(anchors, chains.for_business(_business()))
    with pytest.raises(anchor.AnchorError, match=message):
        _anchor(manual=True)
    assert anchors.sent == []
    assert _rows("SELECT * FROM ledger_anchors") == []


def test_a_business_with_no_agent_wallet_cannot_anchor(chains, anchors, five):
    _sql("UPDATE businesses SET circle_wallet_id = '' WHERE id = 1")
    with pytest.raises(anchor.AnchorError, match="no agent wallet"):
        _anchor(manual=True)


def test_a_transaction_the_chain_does_not_show_is_not_recorded(chains, anchors, five):
    anchors.swallow = True                                           # it finished, but the contract holds nothing for the agent
    with pytest.raises(anchor.AnchorError, match="nothing was recorded"):
        _anchor(manual=True)
    assert _rows("SELECT * FROM ledger_anchors") == []


def test_a_failed_or_refused_send_raises_and_records_nothing(chains, anchors, five):
    anchors.fail_send = True
    with pytest.raises(anchor.AnchorError, match="did not complete"):
        _anchor(manual=True)
    assert _rows("SELECT * FROM ledger_anchors") == []


# ---- what /ledger/verify says

def test_a_business_that_was_never_anchored_is_intact_but_not_fully_verified(client, chains, anchors, five):
    r = client.get("/ledger/verify").json()
    assert r["ok"] is True and r["anchor"]["status"] == "none" and r["fully_verified"] is False


def test_a_matching_anchor_makes_the_chain_fully_verified_and_counts_the_entries_after_it(client, chains, anchors, five):
    _anchor(manual=True)
    for n in range(6, 9):
        _log(n)
    r = client.get("/ledger/verify").json()
    assert r["ok"] and r["fully_verified"] and r["problem_count"] == 0
    a = r["anchor"]
    assert a["status"] == "match" and a["anchored_sequence"] == 5 and a["entries_after_anchor"] == 3
    assert a["agent"] == AGENT1 and a["recorded"][0]["sequence"] == 5 and a["anchor_contract"] == config.ledger_anchor_address


def test_dropping_the_newest_entries_with_their_decisions_is_caught_by_the_anchor(client, chains, anchors, five):
    _anchor(manual=True)
    assert ledger_ok()
    _sql("DELETE FROM ledger WHERE sequence >= 4")
    _sql("DELETE FROM audit_log WHERE subject IN ('INV-4', 'INV-5')")
    assert ledger_ok()                                               # the chain alone cannot tell: it is a valid, shorter chain
    r = client.get("/ledger/verify").json()
    assert r["ok"] is False and r["fully_verified"] is False and r["anchor"]["status"] == "short"
    assert r["problems"][0]["code"] == "anchored_entries_missing" and r["problems"][0]["sequence"] == 5


def test_rewriting_the_whole_chain_with_the_key_and_the_database_is_caught_by_the_anchor(client, chains, anchors, five):
    _anchor(manual=True)
    _sql("DELETE FROM ledger WHERE business_id = 1"); _sql("DELETE FROM audit_log WHERE business_id = 1")
    for n in range(1, 6):
        _log(n, amount=99.0)                                         # a different history, validly signed and chained
    assert ledger_ok()
    r = client.get("/ledger/verify").json()
    assert r["ok"] is False and r["anchor"]["status"] == "mismatch" and r["problems"][0]["code"] == "anchor_mismatch"
    assert r["anchor"]["anchored_head"] != r["anchor"]["chain_head_at_sequence"]


def test_an_unreadable_anchor_is_unknown_not_verified_and_does_not_fail_the_chain(client, chains, anchors, five):
    anchors.broken = True
    r = client.get("/ledger/verify").json()
    assert r["anchor"] == {"status": "unreadable", "checked": False, "anchor_contract": config.ledger_anchor_address}
    assert r["ok"] is True and r["fully_verified"] is False


def test_one_business_anchor_never_vouches_for_another(client, chains, anchors, five):
    acme = _add_acme()
    _wire(chains, anchors, acme, AGENT2, "w2")
    for n in range(1, 4):
        _log(n, acme)
    _anchor(manual=True)                                             # only business 1 is anchored
    assert client.get("/ledger/verify?business=acme").json()["anchor"]["status"] == "none"
    assert _anchor(acme, manual=True)["sequence"] == 3
    assert anchors.state[AGENT1][0] == 5 and anchors.state[AGENT2][0] == 3
    both = [client.get(f"/ledger/verify{q}").json() for q in ("", "?business=acme")]
    assert all(r["fully_verified"] for r in both) and both[0]["anchor"]["agent"] == AGENT1 and both[1]["anchor"]["agent"] == AGENT2
    _sql("UPDATE ledger SET entry_hash = ? WHERE business_id = ? AND sequence = 3", ("00" * 32, acme))
    assert client.get("/ledger/verify").json()["fully_verified"]     # tampering in Acme's chain is not visible in the home business's


# ---- the entries a browser re-checks

def test_entries_are_served_in_order_per_business_and_paged(client, chains, anchors, five):
    acme = _add_acme()
    for n in range(1, 3):
        _log(n, acme)
    all_home = client.get("/ledger/entries").json()
    assert [e["sequence"] for e in all_home["entries"]] == [0, 1, 2, 3, 4, 5] and all_home["more"] is False and all_home["business"] == "ceedebooks"
    assert set(all_home["entries"][0]) == {"sequence", "kind", "body", "prev_hash", "entry_hash", "key_id", "signature"}
    page = client.get("/ledger/entries?after=1&limit=2").json()
    assert [e["sequence"] for e in page["entries"]] == [2, 3] and page["more"] is True
    assert [e["sequence"] for e in client.get("/ledger/entries?business=acme").json()["entries"]] == [0, 1, 2]
    assert client.get("/ledger/entries?business=nobody").status_code == 404
    assert client.get("/ledger/entries?limit=0").status_code == 422 and client.get("/ledger/entries?limit=501").status_code == 422
    assert client.get("/ledger/entries?after=-2").status_code == 422


def test_an_archived_business_still_serves_its_entries_and_its_anchor_check(client, chains, anchors, five):
    _anchor(manual=True)
    _sql("UPDATE businesses SET status = 'archived' WHERE id = 1")
    assert client.get("/ledger/entries?business=ceedebooks").status_code == 200
    assert client.get("/ledger/verify?business=ceedebooks").json()["anchor"]["status"] == "match"


def test_the_anchor_job_is_off_unless_asked_for(monkeypatch):
    monkeypatch.setattr(config, "ledger_anchor_auto", False)         # the shell that runs the tests may have it set
    main.app.state._state.pop("anchor_task", None)
    with TestClient(main.app):
        assert not hasattr(main.app.state, "anchor_task")
    from agent import config as cfg
    assert cfg._ARC_TESTNET_ANCHOR == "0x008217BeC86462E76126F94e50eBe68fb4a41444"


def test_the_manifest_lists_the_new_ledger_routes(client):
    m = client.get("/.well-known/agent.json").json()["endpoints"]
    assert "GET /ledger/entries" in m and "anchor" in m["GET /ledger/verify"]
