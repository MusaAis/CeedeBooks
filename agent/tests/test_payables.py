"""Tests for the three-way match, rules-baseline decisions, and retry safety."""
import asyncio

import pytest

from agent import payables
from agent.config import config
from backend import models

BIZ_B_ADDRESS = "0x" + "bb" * 20


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "test.db"))
    asyncio.run(models.init_db())


@pytest.fixture(autouse=True)
def _placeholder_wallets(monkeypatch):
    """These tests use placeholder wallets like 0xVENDOR, which are not real addresses to hash."""
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)


def _home():
    return asyncio.run(models.get_business(1))


async def _seed(receipt_role="ops_manager", wallet="0xVENDOR", business_id=1):
    vendor_id = await models.save_vendor(business_id, "Test Vendor", wallet)
    po_id = await models.save_purchase_order(business_id, "PO-1", vendor_id, 10.0, 0)
    await models.save_receipt(business_id, po_id, "someone", receipt_role)
    return vendor_id, po_id


def _invoice(vendor_wallet="0xVENDOR", amount=10.0, po_number="PO-1", business_id=1):
    return payables.Invoice(
        business_id=business_id, id=1, invoice_number="INV-1", vendor_wallet=vendor_wallet,
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


def test_decide_holds_on_tight_runway(chain):
    decision, _ = asyncio.run(payables.decide(_invoice(), 3, chain))
    assert decision == payables.Decision.HOLD


def test_decide_escalates_unregistered_vendor(chain):
    chain.vendor_ok = False
    decision, _ = asyncio.run(payables.decide(_invoice(), 30, chain))
    assert decision == payables.Decision.ESCALATE


def test_decide_pays_when_healthy(chain):
    decision, _ = asyncio.run(payables.decide(_invoice(), 30, chain))
    assert decision == payables.Decision.PAY


def test_process_invoice_skips_replay_of_already_paid(chain):
    asyncio.run(_seed())
    chain.paid = True
    decision = asyncio.run(payables.process_invoice(_invoice(), 30, _home()))
    assert decision == payables.Decision.PAY
    assert not [c for c in chain.calls if c[0] == "pay"]


def test_process_invoice_escalates_and_logs_on_mismatch(chain):
    decision = asyncio.run(payables.process_invoice(_invoice(po_number="PO-MISSING"), 30, _home()))
    assert decision == payables.Decision.ESCALATE
    escalations = [c for c in chain.calls if c[0] == "escalate"]
    assert len(escalations) == 1 and "No matching PO" in escalations[0][1][-1]


def test_process_invoice_pays_end_to_end(chain):
    asyncio.run(_seed())
    decision = asyncio.run(payables.process_invoice(_invoice(), 30, _home()))
    assert decision == payables.Decision.PAY
    assert [c[0] for c in chain.calls] == ["commit", "pay"]  # commit first, then pay


def test_a_payment_goes_to_its_own_businesses_contract_only(chains):
    """Business 2 has its own contract. Paying there must never touch business 1's contract."""
    biz_b = _second_business()
    asyncio.run(_seed(business_id=biz_b["id"]))
    home_chain = chains.get(config.budget_enforcer_address, 1)
    b_chain = chains.get(BIZ_B_ADDRESS, 2)
    decision = asyncio.run(payables.process_invoice(_invoice(business_id=biz_b["id"]), 30, biz_b))
    assert decision == payables.Decision.PAY
    assert [c[0] for c in b_chain.calls] == ["commit", "pay"]
    assert home_chain.calls == []


def test_the_same_po_and_invoice_numbers_work_in_two_businesses(chains):
    biz_b = _second_business()
    asyncio.run(_seed())                               # business 1: PO-1, vendor 0xVENDOR
    asyncio.run(_seed(business_id=biz_b["id"]))        # business 2: also PO-1, same wallet
    for business_id in (1, biz_b["id"]):
        matched, _ = asyncio.run(payables.three_way_match(_invoice(business_id=business_id)))
        assert matched
    # a PO that exists only in business 1 is invisible to business 2
    asyncio.run(models.save_purchase_order(1, "PO-ONLY-1", 1, 5.0, 0))
    matched, reason = asyncio.run(payables.three_way_match(_invoice(business_id=biz_b["id"], po_number="PO-ONLY-1")))
    assert not matched and "No matching PO" in reason


def _second_business() -> dict:
    import aiosqlite

    async def make():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, contract_version, status, created_at) "
                "VALUES ('Acme', 'acme', ?, 2, 'active', 0)", (BIZ_B_ADDRESS,))
            await db.commit()
        return await models.get_business_by_slug("acme")
    return asyncio.run(make())
