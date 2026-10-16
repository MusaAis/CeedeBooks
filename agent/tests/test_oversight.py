"""Phase O (v1.3.0): maker-checker, pause with a reason, and figures written by code. The chain is always mocked."""
import asyncio
import re
import sqlite3

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import decision_log, figures, llm, payables
from agent.config import config
from backend import admin_auth, auth, main, models

ADMIN = Account.create()
WALLET_V = "0x" + "c3" * 20
KEY = "0x" + "ab" * 32


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chain):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "oversight.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    chain.approver_addr = ADMIN.address

    async def runway(amount, business):
        return 42.0
    monkeypatch.setattr(main.treasury, "runway_days", runway)
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _bearer(client):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN.address}).json()
    sig = ADMIN.sign_message(encode_defunct(text=ch["message"])).signature.hex()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": sig if sig.startswith("0x") else "0x" + sig})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _tx(chain, escalation=None, block=77):
    chain.admin_tx = {"sender": ADMIN.address.lower(), "to": config.budget_enforcer_address.lower(), "success": True,
                      "block": block, "escalation": escalation}


def _invoice(number="INV-X", status="escalated", submitted_by=None, **kw):
    vid = asyncio.run(models.get_vendor(1, 1))
    vid = vid["id"] if vid else asyncio.run(models.save_vendor(1, "V", WALLET_V))
    iid = asyncio.run(models.save_invoice(1, number, vid, None, 5.0, 1, "doc", submitted_by=submitted_by, **kw))
    biz = asyncio.run(models.get_business(1))
    h = asyncio.run(decision_log.log_decision("INVOICE_ESCALATED", number, "for a person", "rules", 5.0, business=biz))
    asyncio.run(decision_log.record_onchain_tx(h, "tx-esc", "0x" + "11" * 32))  # the escalation's own transaction
    asyncio.run(models.update_invoice_status(1, iid, status, reasoning_hash=h))
    return iid


def _hash_of(iid):
    return asyncio.run(models.get_invoice(1, iid))["reasoning_hash"]


def _rows(sql, args=()):
    with sqlite3.connect(config.db_path) as db:
        return db.execute(sql, args).fetchall()


# ---- figures written by code (rule 18)

def test_render_figures_only_has_the_figures_it_was_given():
    assert figures.render_figures(amount_usdc=5.5, po_amount_usdc=5) == {"amount": "5.5 USDC", "po_amount": "5 USDC"}
    assert figures.render_figures(amount_usdc=5.5) == {"amount": "5.5 USDC"}
    assert figures.render_figures() == {}


def test_fill_replaces_placeholders_with_code_figures():
    assert figures.fill("The invoice for {amount} needs a person.", {"amount": "5 USDC"}) == "The invoice for 5 USDC needs a person."


@pytest.mark.parametrize("text", [
    "This 5 USDC invoice is odd.", "Looks like 7 days of runway", "Amount {amount} on day 2", "A {made_up} figure",
    "Stray { brace", "Stray } brace", "", None, "Dated 2026",
])
def test_fill_refuses_model_text_with_its_own_digits_or_unknown_placeholders(text):
    assert figures.fill(text, {"amount": "5 USDC"}) is None


def test_a_placeholder_for_an_unknown_figure_is_refused_not_guessed():
    assert figures.fill("Over the {po_amount} order", figures.render_figures(amount_usdc=5)) is None


def test_the_prompt_tells_the_model_to_use_placeholders_and_no_digits():
    assert "placeholder" in llm.ESCALATION_SYSTEM_PROMPT and "no digits" in llm.ESCALATION_SYSTEM_PROMPT
    assert "specific numbers" not in llm.ESCALATION_SYSTEM_PROMPT


def _world_unregistered(chain):
    """A matched PO and receipt for a vendor the contract does not know: the rules escalate and the model writes the narrative."""
    chain.vendor_ok = False
    vid = asyncio.run(models.save_vendor(1, "V", WALLET_V))
    po = asyncio.run(models.save_purchase_order(1, "PO-1", vid, 10.0, 0))
    asyncio.run(models.save_receipt(1, po, "someone", "ops_manager"))
    return payables.Invoice(business_id=1, id=1, invoice_number="INV-1", vendor_wallet=WALLET_V, amount_usdc=10.0, category=0,
                            doc_hash="00" * 32, po_number="PO-1")


def _audit_reasoning():
    return _rows("SELECT reasoning FROM audit_log WHERE action = 'INVOICE_ESCALATED'")[0][0]


def test_model_text_with_placeholders_is_stored_with_code_written_figures(chain, monkeypatch):
    async def model(context):
        assert "{amount}" in context  # the prompt names the placeholder it may use
        return "The vendor is unknown on-chain, so a person should review the {amount} payment."
    monkeypatch.setattr(payables, "escalation_reasoning", model)
    asyncio.run(payables.process_invoice(_world_unregistered(chain), 30, asyncio.run(models.get_business(1))))
    assert "review the 10 USDC payment" in _audit_reasoning()


def test_model_text_with_digits_of_its_own_is_replaced_by_the_code_written_reason(chain, monkeypatch):
    async def model(context):
        return "Pay 999 USDC only after 3 checks."
    monkeypatch.setattr(payables, "escalation_reasoning", model)
    asyncio.run(payables.process_invoice(_world_unregistered(chain), 30, asyncio.run(models.get_business(1))))
    stored = _audit_reasoning()
    assert "999" not in stored and "Vendor not registered on-chain" in stored and figures.WITHHELD in stored
    assert not re.search(r"\d", stored)  # nothing in this narrative carries a model-made digit


# ---- pause: payments hold, with one marker

def _live_world(chain):
    vid = asyncio.run(models.save_vendor(1, "V", WALLET_V))
    po = asyncio.run(models.save_purchase_order(1, "PO-1", vid, 10.0, 0))
    asyncio.run(models.save_receipt(1, po, "someone", "ops_manager"))
    iid = asyncio.run(models.save_invoice(1, "INV-1", vid, po, 10.0, 0, "00" * 32))
    inv = payables.Invoice(business_id=1, id=iid, invoice_number="INV-1", vendor_wallet=WALLET_V, amount_usdc=10.0, category=0,
                           doc_hash="00" * 32, po_number="PO-1")
    return iid, inv


def test_while_paused_a_payable_invoice_is_held_with_the_paused_marker_and_nothing_is_sent(chain):
    iid, inv = _live_world(chain)
    chain.paused = True
    assert asyncio.run(payables.process_invoice(inv, 30, asyncio.run(models.get_business(1)))) == payables.Decision.HOLD
    assert chain.calls == []  # no commit, no pay, no anchor
    row = asyncio.run(models.get_invoice(1, iid))
    assert row["status"] == "held" and row["held_because"] == "paused"
    assert _rows("SELECT action FROM audit_log") == [("INVOICE_HELD",)]


def test_an_unreadable_pause_state_pays_nothing(chain, monkeypatch):
    iid, inv = _live_world(chain)
    monkeypatch.setattr(chain, "is_paused", lambda: (_ for _ in ()).throw(RuntimeError("rpc down")))
    with pytest.raises(RuntimeError):
        asyncio.run(payables.process_invoice(inv, 30, asyncio.run(models.get_business(1))))
    assert chain.calls == [] and asyncio.run(models.get_invoice(1, iid))["status"] == "pending"


def test_when_not_paused_the_invoice_is_paid_and_has_no_marker(chain):
    iid, inv = _live_world(chain)
    assert asyncio.run(payables.process_invoice(inv, 30, asyncio.run(models.get_business(1)))) == payables.Decision.PAY
    row = asyncio.run(models.get_invoice(1, iid))
    assert row["status"] == "paid" and row["held_because"] is None


def test_preflight_says_would_hold_while_paused(client, chain):
    buyer = auth.generate_key("buyer")
    asyncio.run(models.save_api_key(1, auth.hash_key(buyer), "buyer", None, "t"))
    h = {"X-API-Key": buyer}
    vid = client.post("/vendors", json={"name": "A", "wallet_address": WALLET_V}, headers=h).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": vid, "amount_usdc": "5", "category": 1}, headers=h).json()["id"]
    client.post("/receipts", json={"po_id": po}, headers=h)
    body = {"invoice_number": "INV-1", "vendor_id": vid, "amount_usdc": "5", "category": 1, "doc_hash": "ab" * 32, "po_number": "PO-1"}
    chain.remaining = (100_000_000, 20_000_000)
    assert client.post("/invoices/preflight", json=body, headers=h).json()["outcome"] == "would_pay"
    chain.paused = True
    r = client.post("/invoices/preflight", json=body, headers=h).json()
    assert r["outcome"] == "would_hold" and r["checks"]["payments_active"] is False


def test_a_paused_hold_can_be_rerun_only_after_payments_resume(client, chain):
    h = _bearer(client)
    iid, inv = _live_world(chain)
    chain.paused = True
    asyncio.run(payables.process_invoice(inv, 30, asyncio.run(models.get_business(1))))
    assert client.post(f"/admin/invoices/{iid}/rerun", headers=h).status_code == 409  # still paused
    assert asyncio.run(models.get_invoice(1, iid))["status"] == "held"
    chain.paused = False
    r = client.post(f"/admin/invoices/{iid}/rerun", headers=h)
    assert r.status_code == 200 and r.json()["decision"] == "pay"
    assert asyncio.run(models.get_invoice(1, iid))["status"] == "paid"
    assert client.post(f"/admin/invoices/{iid}/rerun", headers=h).status_code == 409  # one click only


def test_a_hold_for_any_other_reason_cannot_be_rerun(client, chain):
    h = _bearer(client)
    iid = _invoice("INV-H", status="held")
    assert client.post(f"/admin/invoices/{iid}/rerun", headers=h).status_code == 409


def test_pausing_does_not_block_a_persons_own_escalation_decision(client, chain):
    h = _bearer(client)
    iid = _invoice("INV-E", submitted_by="key:9")
    chain.paused = True
    chain.esc_key = KEY
    assert client.get(f"/admin/invoices/{iid}/escalation?purpose=approve", headers=h).status_code == 200
    _tx(chain, escalation=("EscalationRejected", "1" * 64, _hash_of(iid)))
    body = {"action": "reject_escalation", "tx_hash": "0x" + "ee" * 32, "invoice_id": iid}
    assert client.post("/admin/actions", json=body, headers=h).status_code == 200
    assert asyncio.run(models.get_invoice(1, iid))["status"] == "rejected"


def test_a_shadow_approval_while_paused_is_refused_and_the_invoice_keeps_waiting(client, chain):
    h = _bearer(client)
    iid = _invoice("INV-S", status="awaiting_owner", mode="shadow", real_amount="100", real_currency="NGN")
    chain.paused = True
    r = client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h)
    assert r.status_code == 409 and "paused" in r.json()["detail"]
    assert asyncio.run(models.get_invoice(1, iid))["status"] == "awaiting_owner"
    assert chain.calls == []


def test_a_shadow_approval_with_an_unreadable_pause_state_pays_nothing(client, chain, monkeypatch):
    h = _bearer(client)
    iid = _invoice("INV-S", status="awaiting_owner", mode="shadow", real_amount="100", real_currency="NGN")
    monkeypatch.setattr(chain, "is_paused", lambda: (_ for _ in ()).throw(RuntimeError("rpc down")))
    assert client.post(f"/admin/invoices/{iid}/shadow-approve", headers=h).status_code == 503
    assert chain.calls == [] and asyncio.run(models.get_invoice(1, iid))["status"] == "awaiting_owner"


# ---- pause: the reason, the ledger and the banner

def _pause_body(paused=True, reason="vendor wallet under review"):
    return {"action": "set_paused", "tx_hash": "0x" + "cd" * 32, "paused": paused, "reason": reason}


def test_pausing_records_the_reason_in_the_activity_log_the_ledger_and_the_banner(client, chain):
    h = _bearer(client)
    chain.paused = True
    _tx(chain)
    r = client.post("/admin/actions", json=_pause_body(), headers=h)
    assert r.status_code == 200 and r.json()["paused"] is True and r.json()["ledgered"] is True
    actions = client.get("/admin/actions", headers=h).json()["actions"]
    assert any(a["action"] == "set_paused" and a["ref"] == "paused: vendor wallet under review" and a["tx_hash"] == "0x" + "cd" * 32 for a in actions)
    action, reasoning, model = _rows("SELECT action, reasoning, model_used FROM audit_log WHERE action = 'ADMIN_PAUSED'")[0]
    assert "vendor wallet under review" in reasoning and "0x" + "cd" * 32 in reasoning and ADMIN.address.lower() in reasoning and model == "manual"
    banner = client.get("/pause-status").json()
    assert banner["paused"] is True and banner["reason"] == "vendor wallet under review" and banner["by"] == ADMIN.address.lower()
    # the pause entry is part of the signed chain and does not break it
    verify = client.get("/ledger/verify?business=ceedebooks").json()
    assert verify["ok"] is True and verify["problem_count"] == 0


def test_the_banner_clears_after_a_resume_and_keeps_the_history(client, chain):
    h = _bearer(client)
    chain.paused = True
    _tx(chain)
    client.post("/admin/actions", json=_pause_body(), headers=h)
    assert client.get("/pause-status").json()["paused"] is True
    chain.paused = False
    _tx(chain, block=78)
    body = _pause_body(False, "review finished") | {"tx_hash": "0x" + "ce" * 32}
    assert client.post("/admin/actions", json=body, headers=h).status_code == 200
    assert client.get("/pause-status").json() == {"business": "ceedebooks", "paused": False, "reason": None, "by": None, "since": None}
    assert [r[0] for r in _rows("SELECT action FROM audit_log ORDER BY id")] == ["ADMIN_PAUSED", "ADMIN_RESUMED"]
    assert [r[0] for r in _rows("SELECT paused FROM pause_log ORDER BY id")] == [1, 0]


@pytest.mark.parametrize("kw", [{"reason": None}, {"reason": "  "}, {"reason": "ab"}, {"reason": "line\nbreak"}, {"paused": None}])
def test_a_pause_record_needs_a_real_reason_and_a_state(client, chain, kw):
    h = _bearer(client)
    chain.paused = True
    _tx(chain)
    assert client.post("/admin/actions", json=_pause_body() | kw, headers=h).status_code == 422
    assert _rows("SELECT COUNT(*) FROM admin_actions WHERE action = 'set_paused'") == [(0,)]


def test_a_pause_record_must_match_what_the_contract_says(client, chain):
    h = _bearer(client)
    _tx(chain)
    chain.paused = False
    assert client.post("/admin/actions", json=_pause_body(True), headers=h).status_code == 422  # claims paused, contract is not
    assert _rows("SELECT COUNT(*) FROM pause_log") == [(0,)]


def test_an_unreadable_chain_refuses_the_pause_record(client, chain, monkeypatch):
    h = _bearer(client)
    _tx(chain)
    monkeypatch.setattr(chain, "is_paused", lambda: (_ for _ in ()).throw(RuntimeError("rpc down")))
    assert client.post("/admin/actions", json=_pause_body(), headers=h).status_code == 503


def test_an_emergency_stop_still_stands_if_the_ledger_cannot_be_written(client, chain, monkeypatch):
    h = _bearer(client)
    chain.paused = True
    _tx(chain)
    monkeypatch.setattr(config, "ledger_signing_key", "")
    r = client.post("/admin/actions", json=_pause_body(), headers=h)
    assert r.status_code == 200 and r.json()["ledgered"] is False
    assert client.get("/pause-status").json()["reason"] == "vendor wallet under review"


def test_the_banner_says_unknown_when_the_chain_cannot_be_read(client, chain):
    chain.broken = True
    assert client.get("/pause-status").json()["paused"] is None


def test_a_pause_that_was_not_made_through_the_site_has_no_reason(client, chain):
    chain.paused = True
    r = client.get("/pause-status").json()
    assert r["paused"] is True and r["reason"] is None


def test_the_pause_status_of_one_business_never_shows_anothers_reason(client, chain):
    h = _bearer(client)
    chain.paused = True
    _tx(chain)
    client.post("/admin/actions", json=_pause_body(), headers=h)
    assert client.get("/pause-status?business=no-such-business").status_code == 404


# ---- maker-checker

def test_a_submission_records_who_made_it(client, chain):
    admin = _bearer(client)
    vid = client.post("/vendors", json={"name": "A", "wallet_address": WALLET_V}, headers=admin).json()["id"]
    po = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": vid, "amount_usdc": "5", "category": 1}, headers=admin).json()["id"]
    client.post("/receipts", json={"po_id": po}, headers=admin)
    chain.remaining = (100_000_000, 20_000_000)
    body = {"invoice_number": "INV-A", "vendor_id": vid, "amount_usdc": "5", "category": 1, "doc_hash": "ab" * 32, "po_number": "PO-1"}
    assert client.post("/invoices", json=body, headers=admin).status_code == 200
    assert _rows("SELECT submitted_by FROM invoices WHERE invoice_number = 'INV-A'") == [(ADMIN.address.lower(),)]
    key = auth.generate_key("buyer")
    kid = asyncio.run(models.save_api_key(1, auth.hash_key(key), "buyer", None, "t"))
    assert client.post("/invoices", json=body | {"invoice_number": "INV-B"}, headers={"X-API-Key": key}).status_code == 200
    assert _rows("SELECT submitted_by FROM invoices WHERE invoice_number = 'INV-B'") == [(f"key:{kid}",)]


def test_the_approver_cannot_get_the_release_key_for_an_invoice_they_submitted(client, chain):
    h = _bearer(client)
    chain.esc_key = KEY
    iid = _invoice("INV-M", submitted_by=ADMIN.address.lower())
    r = client.get(f"/admin/invoices/{iid}/escalation?purpose=approve", headers=h)
    assert r.status_code == 403 and "cannot release" in r.json()["detail"]
    # rejecting and returning release nothing, so they stay open
    assert client.get(f"/admin/invoices/{iid}/escalation?purpose=reject", headers=h).status_code == 200
    plain = client.get(f"/admin/invoices/{iid}/escalation?purpose=reopen", headers=h)
    assert plain.status_code == 200 and plain.json()["submitted_by_you"] is True


@pytest.mark.parametrize("submitted_by", ["key:9", None])
def test_an_invoice_from_a_key_or_from_before_v1_3_0_can_be_released(client, chain, submitted_by):
    h = _bearer(client)
    chain.esc_key = KEY
    iid = _invoice("INV-K", submitted_by=submitted_by)
    r = client.get(f"/admin/invoices/{iid}/escalation?purpose=approve", headers=h)
    assert r.status_code == 200 and r.json()["submitted_by_you"] is False


def test_the_invoice_list_says_which_ones_you_submitted_and_never_shows_who_else_did(client, chain):
    h = _bearer(client)
    mine = _invoice("INV-1", submitted_by=ADMIN.address.lower())
    other = _invoice("INV-2", submitted_by="key:3")
    rows = {r["id"]: r for r in client.get("/admin/invoices", headers=h).json()["invoices"]}
    assert rows[mine]["submitted_by_you"] is True and rows[other]["submitted_by_you"] is False
    assert all("submitted_by" not in r for r in rows.values())


def test_a_release_made_by_hand_on_chain_is_recorded_flagged_and_not_hidden(client, chain):
    h = _bearer(client)
    iid = _invoice("INV-M", submitted_by=ADMIN.address.lower())
    _tx(chain, escalation=("EscalationApproved", "1" * 64, _hash_of(iid)))
    r = client.post("/admin/actions", json={"action": "approve_escalation", "tx_hash": "0x" + "ee" * 32, "invoice_id": iid}, headers=h)
    assert r.status_code == 200 and "cannot release" in r.json()["warning"]
    assert asyncio.run(models.get_invoice(1, iid))["status"] == "paid"  # the chain did release it; the books say so
    names = [a["action"] for a in client.get("/admin/actions", headers=h).json()["actions"]]
    assert "approve_escalation" in names and "maker_checker_bypass" in names


def test_a_normal_release_carries_no_warning(client, chain):
    h = _bearer(client)
    iid = _invoice("INV-N", submitted_by="key:2")
    _tx(chain, escalation=("EscalationApproved", "1" * 64, _hash_of(iid)))
    r = client.post("/admin/actions", json={"action": "approve_escalation", "tx_hash": "0x" + "ee" * 32, "invoice_id": iid}, headers=h)
    assert r.status_code == 200 and "warning" not in r.json()
    assert "maker_checker_bypass" not in [a["action"] for a in client.get("/admin/actions", headers=h).json()["actions"]]


def test_a_second_settlement_of_the_same_escalation_cannot_act(client, chain):
    h = _bearer(client)
    iid = _invoice("INV-D", submitted_by="key:2")
    _tx(chain, escalation=("EscalationApproved", "1" * 64, _hash_of(iid)))
    first = {"action": "approve_escalation", "tx_hash": "0x" + "ee" * 32, "invoice_id": iid}
    assert client.post("/admin/actions", json=first, headers=h).status_code == 200
    _tx(chain, escalation=("EscalationRejected", "1" * 64, _hash_of(iid)))
    second = {"action": "reject_escalation", "tx_hash": "0x" + "ef" * 32, "invoice_id": iid}
    assert client.post("/admin/actions", json=second, headers=h).status_code == 409
    assert asyncio.run(models.get_invoice(1, iid))["status"] == "paid"


def test_maker_checker_is_scoped_to_the_business_of_the_session(client, chain):
    """The submitter comparison reads only this business's invoice row: an id from another business is a 404."""
    h = _bearer(client)
    assert client.get("/admin/invoices/9999/escalation?purpose=approve", headers=h).status_code == 404


def test_an_old_database_gains_the_new_columns():
    cols = [r[1] for r in _rows("PRAGMA table_info(invoices)")]
    assert "submitted_by" in cols and "held_because" in cols
    assert _rows("SELECT name FROM sqlite_master WHERE name = 'pause_log'") == [("pause_log",)]
