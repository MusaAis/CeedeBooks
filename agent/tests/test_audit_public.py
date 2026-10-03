"""Phase C: public audit feed, stats, on-chain verification and hold anchoring. The chain is always mocked."""
import asyncio

import pytest
from fastapi.testclient import TestClient
from web3 import Web3

from agent import contract, decision_log, payables
from agent.config import config
from backend import auth, main, models


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "audit.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    auth.request_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _log(action, model="rules", amount=5.0, subject="INV-1", reasoning="because"):
    return asyncio.run(decision_log.log_decision(action, subject, reasoning, model_used=model, amount=amount))


def test_event_topic_constants_match_the_contract_signatures():
    for name, topic0 in [(n, t) for t, (n, _) in contract._REASONING_EVENTS.items()]:
        assert Web3.keccak(text=contract.EVENT_SIGNATURES[name]).hex().removeprefix("0x") == topic0


def test_stats_separates_agent_decisions_from_manual_entries(client):
    _log("INVOICE_PAID", amount=5.0, subject="A")
    _log("INVOICE_HELD", amount=7.0, subject="B")
    _log("INVOICE_ESCALATED", amount=9.0, subject="C")
    _log("INVOICE_PAID", model="manual", amount=2.2, subject="DOMAIN")
    stats = client.get("/stats").json()  # public: no key
    assert (stats["paid"], stats["held"], stats["escalated"], stats["refused"], stats["decisions"]) == (1, 1, 1, 2, 3)
    assert stats["refusal_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert (stats["manual_paid"], stats["manual_paid_usdc"], stats["paid_usdc"]) == (1, 2.2, 5.0)


def test_stats_with_no_decisions_has_no_rate(client):
    stats = client.get("/stats").json()
    assert stats["decisions"] == 0 and stats["refusal_rate"] is None


def test_feed_is_public_newest_first_and_hides_reasoning(client):
    first, second = _log("INVOICE_PAID", subject="A"), _log("INVOICE_HELD", subject="B")
    body = client.get("/decisions?limit=5").json()["decisions"]
    assert [d["reasoning_hash"] for d in body] == [second, first]
    assert "reasoning" not in body[0] and "hash_input" not in body[0]


def test_old_databases_gain_the_chain_tx_column(tmp_path, monkeypatch):
    import sqlite3

    path = str(tmp_path / "old.db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, action TEXT, subject TEXT, reasoning TEXT, amount_usdc REAL, "
                "treasury_balance_after REAL, model_used TEXT, timestamp REAL, hash_input TEXT, reasoning_hash TEXT UNIQUE, onchain_tx_id TEXT)")
    con.commit(); con.close()
    monkeypatch.setattr(config, "db_path", path)
    asyncio.run(decision_log.init_db())
    asyncio.run(decision_log.init_db())  # idempotent
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(audit_log)")]
    assert "chain_tx_hash" in cols


def test_verify_reports_onchain_match(client, monkeypatch):
    h = _log("INVOICE_PAID")
    asyncio.run(decision_log.record_onchain_tx(h, "circle-1", "0xabc"))
    monkeypatch.setattr(main.contract, "reasoning_events",
                        lambda tx: [{"event": "PaymentMade", "reasoning_hash": h, "block": 7, "success": True}])
    body = client.get(f"/decisions/{h}/verify").json()
    assert body["verified"] is True
    assert body["onchain"] == {"checked": True, "match": True, "chain_tx_hash": "0xabc", "event": "PaymentMade", "block": 7}


def test_verify_flags_a_hash_that_is_not_in_the_transaction(client, monkeypatch):
    h = _log("INVOICE_PAID")
    asyncio.run(decision_log.record_onchain_tx(h, "circle-1", "0xabc"))
    monkeypatch.setattr(main.contract, "reasoning_events",
                        lambda tx: [{"event": "PaymentMade", "reasoning_hash": "f" * 64, "block": 7, "success": True}])
    assert client.get(f"/decisions/{h}/verify").json()["onchain"]["match"] is False


def test_verify_without_a_chain_tx_is_unchecked_not_matched(client):
    h = _log("INVOICE_HELD")
    assert client.get(f"/decisions/{h}/verify").json()["onchain"]["match"] is None


def test_verify_survives_an_rpc_failure(client, monkeypatch):
    h = _log("INVOICE_PAID")
    asyncio.run(decision_log.record_onchain_tx(h, "circle-1", "0xabc"))

    def boom(tx):
        raise RuntimeError("rpc down secret-detail")

    monkeypatch.setattr(main.contract, "reasoning_events", boom)
    r = client.get(f"/decisions/{h}/verify")
    assert r.status_code == 200 and r.json()["onchain"]["match"] is None
    assert "secret-detail" not in r.text


def test_a_hold_is_anchored_on_chain_and_its_tx_recorded(monkeypatch):
    seen = {}
    monkeypatch.setattr(payables.contract, "log_decision", lambda h: seen.setdefault("hash", h) and "tx-9")
    monkeypatch.setattr(payables.contract, "wait_for_transaction", lambda t: None)
    monkeypatch.setattr(payables.contract, "chain_tx_hash", lambda t: "0xdead")
    h = _log("INVOICE_HELD")
    asyncio.run(payables._anchor_refusal(h))
    assert seen["hash"] == bytes.fromhex(h)
    assert asyncio.run(decision_log.get_decision(h))["chain_tx_hash"] == "0xdead"


def test_a_failed_anchor_never_breaks_the_hold(monkeypatch):
    def boom(h):
        raise RuntimeError("circle down")

    monkeypatch.setattr(payables.contract, "log_decision", boom)
    asyncio.run(payables._anchor_refusal(_log("INVOICE_HELD")))  # must not raise
