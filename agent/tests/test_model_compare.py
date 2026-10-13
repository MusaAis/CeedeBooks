"""Phase E3: the model's second opinion recorded beside the rules' verdict. The model is never called for real: llm.complete
and second_opinion.ask_model are patched. The rules verdict acts; the opinion is only recorded (hash format 3)."""
import asyncio
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from agent import decision_log, ledger, llm, payables, second_opinion as so
from agent.config import config
from backend import auth, main, models

REAL_ASK = so.ask_model          # conftest patches the module attribute for every test
ACME = "0x" + "bb" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "cmp.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    auth.request_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _sql(sql, args=()):
    con = sqlite3.connect(config.db_path)
    cur = con.execute(sql, args); con.commit(); con.close()
    return cur.lastrowid


def _rows(sql, args=()):
    con = sqlite3.connect(config.db_path); con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute(sql, args)]; con.close()
    return out


def _home():
    return asyncio.run(models.get_business(1))


def _add_acme():
    return _sql("INSERT INTO businesses (name, slug, enforcer_address, contract_version, status, created_at) "
                "VALUES ('Acme', 'acme', ?, 2, 'active', 0)", (ACME,))


async def _seed(business_id=1):
    vendor_id = await models.save_vendor(business_id, "V", "0xVENDOR")
    po_id = await models.save_purchase_order(business_id, "PO-1", vendor_id, 10.0, 0)
    await models.save_receipt(business_id, po_id, "someone", "ops_manager")


def _invoice(po_number="PO-1"):
    return payables.Invoice(business_id=1, id=1, invoice_number="INV-1", vendor_wallet="0xVENDOR", amount_usdc=10.0,
                            category=0, doc_hash="00" * 32, po_number=po_number)


def model_says(action, confidence=0.9, reasoning="The facts look consistent."):
    async def ask(facts):
        return {"action": action, "reasoning": reasoning, "confidence": confidence, "model": "test-model"}
    return ask


def _run(runway=30, po="PO-1"):
    return asyncio.run(payables.process_invoice(_invoice(po), runway, _home()))


def _audit(action):
    return _rows("SELECT * FROM audit_log WHERE action = ?", (action,))[0]


def _chain_ok(business_id=1):
    return asyncio.run(ledger.verify_chain(business_id))["ok"]


# ---- the model's answer

def test_a_usable_answer_is_parsed_and_reasoning_kept_when_it_has_no_new_figures():
    out = so.parse('{"action": "Pay", "reasoning": "PO and receipt agree.", "confidence": 0.8}', {"x": 1})
    assert out == {"action": "pay", "reasoning": "PO and receipt agree.", "confidence": 0.8}
    fenced = so.parse('```json\n{"action": "hold", "reasoning": "r", "confidence": 1}\n```', {})
    assert fenced["action"] == "hold" and fenced["confidence"] == 1.0


@pytest.mark.parametrize("content", [
    "", "no json here", '{"action": "refund", "reasoning": "r", "confidence": 0.5}', '{"action": "pay", "reasoning": "r"}',
    '{"action": "pay", "reasoning": "r", "confidence": 1.5}', '{"action": "pay", "reasoning": "r", "confidence": true}',
    '{"action": "pay", "reasoning": "r", "confidence": "high"}', '{"action": ["pay"], "reasoning": "r", "confidence": 0.5}', "{broken",
])
def test_an_unusable_answer_is_not_an_opinion(content):
    assert so.parse(content, {}) is None


def test_model_text_may_hold_words_never_figures_the_code_did_not_provide():
    facts = {"match_note": "Amount mismatch: PO 10.0 vs invoice 10.5"}
    assert so.clean_reasoning("It differs by 0.5 USDC.", facts) == so.WITHHELD          # 0.5 was not given
    assert so.clean_reasoning("The invoice 10.5 is above the PO 10.0.", facts).startswith("The invoice 10.5")
    assert so.clean_reasoning("About 99 percent sure.", {}) == so.WITHHELD
    assert len(so.clean_reasoning("x " * 500, {})) <= so.MAX_REASONING


def test_no_model_a_timeout_or_an_error_means_no_opinion(monkeypatch):
    monkeypatch.setattr(config, "model_compare", False)
    assert asyncio.run(REAL_ASK({"a": 1})) is None
    monkeypatch.setattr(config, "model_compare", True)

    async def boom(system, user, max_tokens=200):
        raise RuntimeError("groq down")
    monkeypatch.setattr(llm, "complete", boom)
    assert asyncio.run(REAL_ASK({"a": 1})) is None

    async def slow(system, user, max_tokens=200):
        await asyncio.sleep(2)
        return '{"action": "pay", "reasoning": "r", "confidence": 0.5}'
    monkeypatch.setattr(llm, "complete", slow)
    monkeypatch.setattr(so, "TIMEOUT_SECONDS", 0.05)
    started = time.time()
    assert asyncio.run(REAL_ASK({"a": 1})) is None
    assert time.time() - started < 1

    async def fine(system, user, max_tokens=200):
        return '{"action": "pay", "reasoning": "ok", "confidence": 0.7}'
    monkeypatch.setattr(llm, "complete", fine)
    got = asyncio.run(REAL_ASK({"a": 1}))
    assert got["action"] == "pay" and got["model"] == config.groq_llm_model


def test_agreement_is_unknown_unless_a_model_answered():
    none = so.comparison("pay", None)
    assert none["model_action"] is None and none["agreed"] is None and none["model_confidence"] is None
    yes = so.comparison("pay", {"action": "pay", "reasoning": "r", "confidence": 0.5, "model": "m"})
    no = so.comparison("pay", {"action": "hold", "reasoning": "r", "confidence": 0.5, "model": "m"})
    assert yes["agreed"] is True and no["agreed"] is False


# ---- the rules verdict still acts

def test_the_rules_pay_and_the_model_agrees_both_are_recorded_in_the_hash(chain, monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(so, "ask_model", model_says("pay"))
    assert _run() == payables.Decision.PAY
    assert [c[0] for c in chain.calls] == ["commit", "pay"]
    row = _audit("INVOICE_PAID")
    cmp = json.loads(row["hash_input"])["comparison"]
    assert row["hash_version"] == 3 and json.loads(row["hash_input"])["hash_version"] == 3
    assert cmp["rules_action"] == "pay" and cmp["model_action"] == "pay" and cmp["agreed"] is True and cmp["model_name"] == "test-model"
    assert (row["rules_action"], row["model_action"], row["agreed"], row["model_confidence"]) == ("pay", "pay", 1, 0.9)
    assert decision_log.compute_reasoning_hash(row["action"], row["subject"], row["reasoning"], row["amount_usdc"], row["model_used"],
                                               row["timestamp"], _home(), cmp)[1] == row["reasoning_hash"]
    assert asyncio.run(decision_log.verify_roundtrip(row["reasoning_hash"])) and _chain_ok()


def test_the_model_disagreeing_changes_nothing_the_rules_still_pay(chain, monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(so, "ask_model", model_says("hold"))
    assert _run() == payables.Decision.PAY and [c[0] for c in chain.calls] == ["commit", "pay"]
    row = _audit("INVOICE_PAID")
    assert (row["rules_action"], row["model_action"], row["agreed"]) == ("pay", "hold", 0)


def test_the_model_saying_pay_cannot_release_what_the_rules_hold(chain, monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(so, "ask_model", model_says("pay"))
    assert _run(runway=3) == payables.Decision.HOLD
    assert [c for c in chain.calls if c[0] in ("commit", "pay")] == []
    row = _audit("INVOICE_HELD")
    assert (row["rules_action"], row["model_action"], row["agreed"]) == ("hold", "pay", 0)


def test_a_mismatch_is_escalated_by_the_rules_and_compared(chain, monkeypatch):
    monkeypatch.setattr(so, "ask_model", model_says("escalate"))
    assert _run(po="PO-MISSING") == payables.Decision.ESCALATE
    row = _audit("INVOICE_ESCALATED")
    assert (row["rules_action"], row["model_action"], row["agreed"]) == ("escalate", "escalate", 1)


def test_without_a_model_both_are_null_never_agreement_and_money_still_moves(chain):
    asyncio.run(_seed())
    assert _run() == payables.Decision.PAY and [c[0] for c in chain.calls] == ["commit", "pay"]
    row = _audit("INVOICE_PAID")
    cmp = json.loads(row["hash_input"])["comparison"]
    assert cmp["model_action"] is None and cmp["agreed"] is None and cmp["rules_action"] == "pay"
    assert (row["model_action"], row["agreed"], row["model_confidence"]) == (None, None, None) and row["hash_version"] == 3


def test_a_slow_model_adds_at_most_its_timeout_and_the_invoice_is_still_paid(chain, monkeypatch):
    asyncio.run(_seed())

    async def slow(system, user, max_tokens=200):
        await asyncio.sleep(5)
    monkeypatch.setattr(llm, "complete", slow)
    monkeypatch.setattr(so, "ask_model", REAL_ASK)
    monkeypatch.setattr(so, "TIMEOUT_SECONDS", 0.1)
    started = time.time()
    assert _run() == payables.Decision.PAY
    assert time.time() - started < 2 and [c[0] for c in chain.calls] == ["commit", "pay"]
    assert _audit("INVOICE_PAID")["agreed"] is None


def test_model_text_with_invented_figures_is_withheld_in_what_is_hashed(chain, monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(so, "ask_model", REAL_ASK)

    async def talk(system, user, max_tokens=200):
        return '{"action": "pay", "reasoning": "Looks fine, 99 percent sure.", "confidence": 0.5}'
    monkeypatch.setattr(llm, "complete", talk)
    _run()
    cmp = json.loads(_audit("INVOICE_PAID")["hash_input"])["comparison"]
    assert cmp["model_reasoning"] == so.WITHHELD and cmp["model_action"] == "pay"


def test_the_ledger_entry_does_not_change_shape(chain, monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(so, "ask_model", model_says("pay"))
    _run()
    body = json.loads(_rows("SELECT body FROM ledger WHERE kind = 'decision'")[0]["body"])
    assert set(body) == {"v", "kind", "business_id", "sequence", "prev_hash", "key_id", "reasoning_hash", "action", "subject",
                         "amount_usdc", "model_used", "timestamp", "contract_address"}


# ---- contract refusals

def test_a_transaction_the_contract_reverts_is_recorded_as_refused(chain, monkeypatch):
    asyncio.run(_seed())
    calls = []

    def revert(tx_id, *a, **k):
        calls.append(tx_id)
        if len(calls) == 2:
            raise RuntimeError("Transaction FAILED: execution reverted")
    monkeypatch.setattr(payables.contract, "wait_for_transaction", revert)
    with pytest.raises(RuntimeError):
        _run()
    row = _audit("INVOICE_PAID")
    assert row["contract_refused"] == 1
    assert decision_log.compute_reasoning_hash(row["action"], row["subject"], row["reasoning"], row["amount_usdc"], row["model_used"],
                                               row["timestamp"], _home(), json.loads(row["hash_input"])["comparison"])[1] == row["reasoning_hash"]
    assert _chain_ok()


def test_a_timeout_is_not_a_refusal(chain, monkeypatch):
    asyncio.run(_seed())

    def slow(tx_id, *a, **k):
        raise TimeoutError("did not finish")
    monkeypatch.setattr(payables.contract, "wait_for_transaction", slow)
    with pytest.raises(TimeoutError):
        _run()
    assert _audit("INVOICE_PAID")["contract_refused"] is None


# ---- old formats keep verifying

def test_hash_formats_1_and_2_are_unchanged():
    h1 = decision_log.compute_reasoning_hash("INVOICE_PAID", "INV-1", "Matched PO and receipt, treasury healthy, vendor registered",
                                             5.0, "rules", 1759400000.123456)
    assert h1[1] == "3a167f3ab8c8e6fe186e8d4a9a0815afb7c008ca1a5325ec2e695dcc5e39496b"
    acme = {"id": 2, "enforcer_address": "0x" + "BB" * 20}
    h2 = decision_log.compute_reasoning_hash("INVOICE_PAID", "INV-1", "Matched PO and receipt", 5.0, "rules", 1759400000.5, acme)
    assert h2[1] == "1869a4d81eba5c517c54a7d5ede760c02d98ea68683cd62376618d65ef4b9170"
    assert json.loads(h2[0])["hash_version"] == 2 and "comparison" not in json.loads(h2[0])
    h3 = decision_log.compute_reasoning_hash("INVOICE_PAID", "INV-1", "Matched PO and receipt", 5.0, "rules", 1759400000.5, acme,
                                             so.comparison("pay", None))
    assert json.loads(h3[0])["hash_version"] == 3 and h3[1] != h2[1]


def test_decisions_logged_without_a_comparison_stay_format_2_and_not_in_the_metrics(client):
    asyncio.run(decision_log.log_decision("INVOICE_PAID", "INV-X", "r", model_used="manual", amount=1.0, business=_home()))
    row = _audit("INVOICE_PAID")
    assert row["hash_version"] == 2 and row["rules_action"] is None
    assert client.get("/metrics/decisions").json()["live"]["total"] == 0


def test_an_old_database_gains_the_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "old.db"))
    con = sqlite3.connect(config.db_path)
    con.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, subject TEXT NOT NULL, "
                "reasoning TEXT NOT NULL, amount_usdc REAL NOT NULL DEFAULT 0, treasury_balance_after REAL, model_used TEXT NOT NULL, "
                "timestamp REAL NOT NULL, hash_input TEXT NOT NULL, reasoning_hash TEXT NOT NULL UNIQUE, onchain_tx_id TEXT, "
                "chain_tx_hash TEXT, business_id INTEGER NOT NULL DEFAULT 1, hash_version INTEGER NOT NULL DEFAULT 1, "
                "contract_address TEXT, contract_version INTEGER)")
    con.commit(); con.close()
    asyncio.run(decision_log.init_db())
    cols = {r["name"] for r in _rows("PRAGMA table_info(audit_log)")}
    assert {"rules_action", "model_action", "agreed", "model_confidence", "contract_refused"} <= cols


# ---- GET /metrics/decisions

def _decide(business_id, number, rules, model, origin="agent", mode="live", refused=False):
    business = asyncio.run(models.get_business(business_id))
    opinion = None if model is None else {"action": model, "reasoning": "r", "confidence": 0.5, "model": "m"}
    vendor = asyncio.run(models.save_vendor(business_id, f"V-{number}", f"0xV{number}"))
    real = {"real_amount": "10.00", "real_currency": "NGN"} if mode == "shadow" else {}
    asyncio.run(models.save_invoice(business_id, number, vendor, None, 1.0, 0, "00", origin=origin, mode=mode, **real))
    h = asyncio.run(decision_log.log_decision("INVOICE_PAID", number, "r", model_used="rules", amount=1.0, business=business,
                                              comparison=so.comparison(rules, opinion)))
    if refused:
        asyncio.run(decision_log.mark_contract_refused(h))
    return h


def test_metrics_count_compared_agreed_disagreed_and_refused_with_the_rate_and_its_base(client):
    _decide(1, "A", "pay", "pay")
    _decide(1, "B", "pay", "hold", refused=True)
    _decide(1, "C", "hold", None)
    m = client.get("/metrics/decisions").json()["live"]
    assert (m["total"], m["compared"], m["agreed"], m["disagreed"], m["contract_refused"], m["agreement_rate"]) == (3, 2, 1, 1, 1, 0.5)
    assert m["by_business"]["ceedebooks"]["compared"] == 2 and m["by_origin"]["agent"]["total"] == 3


def test_agreement_is_null_when_nothing_was_compared(client):
    _decide(1, "A", "pay", None)
    m = client.get("/metrics/decisions").json()["live"]
    assert m["total"] == 1 and m["compared"] == 0 and m["agreed"] == 0 and m["agreement_rate"] is None


def test_metrics_split_by_origin_and_keep_live_and_shadow_apart(client):
    _decide(1, "A", "pay", "pay", origin="agent")
    _decide(1, "B", "pay", "hold", origin="demo")
    _decide(1, "C", "pay", "pay", origin="manual")
    _decide(1, "D", "pay", "pay", mode="shadow")
    out = client.get("/metrics/decisions").json()
    live = out["live"]
    assert live["total"] == 3 and [live["by_origin"][o]["total"] for o in ("agent", "manual", "demo")] == [1, 1, 1]
    assert live["by_origin"]["demo"]["disagreed"] == 1 and live["by_origin"]["agent"]["agreement_rate"] == 1.0
    assert out["shadow"]["total"] == 1 and out["shadow"]["agreed"] == 1


def test_one_business_never_sees_anothers_counts_and_archived_ones_leave_the_aggregate(client):
    acme = _add_acme()
    _decide(1, "A", "pay", "pay")
    _decide(acme, "B", "pay", "hold")
    _decide(acme, "C", "pay", "hold")
    home = client.get("/metrics/decisions?business=ceedebooks").json()["live"]
    other = client.get("/metrics/decisions?business=acme").json()["live"]
    assert home["total"] == 1 and list(home["by_business"]) == ["ceedebooks"]
    assert other["total"] == 2 and list(other["by_business"]) == ["acme"] and other["disagreed"] == 2
    assert client.get("/metrics/decisions").json()["live"]["total"] == 3
    _sql("UPDATE businesses SET status = 'archived' WHERE id = ?", (acme,))
    agg = client.get("/metrics/decisions").json()["live"]
    assert agg["total"] == 1 and list(agg["by_business"]) == ["ceedebooks"]
    assert client.get("/metrics/decisions?business=acme").json()["live"]["total"] == 2   # still readable by slug, like other public reads
    assert client.get("/metrics/decisions?business=nobody").status_code == 404


def test_the_manifest_lists_the_metrics_route(client):
    assert "GET /metrics/decisions" in client.get("/.well-known/agent.json").json()["endpoints"]
