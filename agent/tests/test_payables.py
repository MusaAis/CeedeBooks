"""Tests for the three-way match, rules-baseline decisions, and retry safety."""
import asyncio

import pytest

from agent import payables
from backend import models


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from agent.config import config
    monkeypatch.setattr(config, "db_path", str(tmp_path / "test.db"))
    asyncio.run(models.init_db())


async def _seed(receipt_role="ops_manager", wallet="0xVENDOR"):
    vendor_id = await models.save_vendor("Test Vendor", wallet)
    po_id = await models.save_purchase_order("PO-1", vendor_id, 10.0, 0)
    await models.save_receipt(po_id, "someone", receipt_role)
    return vendor_id, po_id


def _invoice(vendor_wallet="0xVENDOR", amount=10.0, po_number="PO-1"):
    return payables.Invoice(
        id=1, invoice_number="INV-1", vendor_wallet=vendor_wallet,
        amount_usdc=amount, category=0, doc_hash="00" * 32, po_number=po_number,
    )


def test_three_way_match_passes_on_matching_po_and_receipt():
    asyncio.run(_seed())
    matched, _ = asyncio.run(payables.three_way_match(_invoice()))
    assert matched


def test_three_way_match_fails_without_po():
    matched, reason = asyncio.run(payables.three_way_match(_invoice(po_number="PO-MISSING")))
    assert not matched
    assert "No matching PO" in reason


def test_three_way_match_fails_on_amount_mismatch():
    asyncio.run(_seed())
    matched, reason = asyncio.run(payables.three_way_match(_invoice(amount=999.0)))
    assert not matched
    assert "Amount mismatch" in reason


def test_three_way_match_rejects_agent_confirmed_receipt():
    asyncio.run(_seed(receipt_role="agent"))
    matched, reason = asyncio.run(payables.three_way_match(_invoice()))
    assert not matched
    assert "agent itself" in reason


def test_three_way_match_rejects_wallet_mismatch():
    asyncio.run(_seed(wallet="0xORIGINAL"))
    matched, reason = asyncio.run(payables.three_way_match(_invoice(vendor_wallet="0xCHANGED")))
    assert not matched
    assert "wallet does not match" in reason


def test_decide_holds_on_tight_runway(monkeypatch):
    monkeypatch.setattr(payables.contract, "is_vendor_approved", lambda v: True)
    decision, _ = asyncio.run(payables.decide(_invoice(), treasury_runway_days=3))
    assert decision == payables.Decision.HOLD


def test_decide_escalates_unregistered_vendor(monkeypatch):
    monkeypatch.setattr(payables.contract, "is_vendor_approved", lambda v: False)
    decision, _ = asyncio.run(payables.decide(_invoice(), treasury_runway_days=30))
    assert decision == payables.Decision.ESCALATE


def test_decide_pays_when_healthy(monkeypatch):
    monkeypatch.setattr(payables.contract, "is_vendor_approved", lambda v: True)
    decision, _ = asyncio.run(payables.decide(_invoice(), treasury_runway_days=30))
    assert decision == payables.Decision.PAY


def test_process_invoice_skips_replay_of_already_paid(monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(payables.contract, "to_bytes32", lambda s: b"\x00" * 32)
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)
    monkeypatch.setattr(payables.contract, "is_paid", lambda k: True)
    called = {"pay": False}
    monkeypatch.setattr(payables.contract, "pay", lambda *a, **k: called.__setitem__("pay", True))
    decision = asyncio.run(payables.process_invoice(_invoice(), treasury_runway_days=30))
    assert decision == payables.Decision.PAY
    assert called["pay"] is False


def test_process_invoice_escalates_and_logs_on_mismatch(monkeypatch):
    monkeypatch.setattr(payables.contract, "to_bytes32", lambda s: b"\x00" * 32)
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)
    monkeypatch.setattr(payables.contract, "is_paid", lambda k: False)
    escalated = {}
    monkeypatch.setattr(
        payables.contract, "escalate",
        lambda vendor, amount, inv, doc, cat, rhash, reason: escalated.setdefault("reason", reason) or "tx-1",
    )
    decision = asyncio.run(payables.process_invoice(_invoice(po_number="PO-MISSING"), treasury_runway_days=30))
    assert decision == payables.Decision.ESCALATE
    assert "No matching PO" in escalated["reason"]


def test_process_invoice_pays_end_to_end(monkeypatch):
    asyncio.run(_seed())
    monkeypatch.setattr(payables.contract, "to_bytes32", lambda s: b"\x00" * 32)
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)
    monkeypatch.setattr(payables.contract, "is_paid", lambda k: False)
    monkeypatch.setattr(payables.contract, "is_vendor_approved", lambda v: True)
    monkeypatch.setattr(payables.contract, "commitment_for", lambda *a: b"\x02" * 32)
    committed, paid = {}, {}
    monkeypatch.setattr(payables.contract, "commit_decision", lambda c: committed.setdefault("done", True))
    monkeypatch.setattr(payables.contract, "pay", lambda *a: paid.setdefault("tx", "tx-1") or "tx-1")

    decision = asyncio.run(payables.process_invoice(_invoice(), treasury_runway_days=30))

    assert decision == payables.Decision.PAY
    assert committed.get("done") is True
    assert paid.get("tx") == "tx-1"

