"""Three-way match, rules-baseline payment decisions, and payment execution."""
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent import contract, decision_log
from agent.config import config
from agent.llm import escalation_reasoning
from backend import models


class Decision(str, Enum):
    PAY = "pay"
    HOLD = "hold"
    ESCALATE = "escalate"


@dataclass
class Invoice:
    id: int
    invoice_number: str
    vendor_wallet: str
    amount_usdc: float
    category: int
    doc_hash: str
    po_number: Optional[str]


async def three_way_match(invoice: Invoice) -> tuple[bool, str]:
    if not invoice.po_number:
        return False, "No PO number on invoice"

    po = await models.get_purchase_order(invoice.po_number)
    if po is None:
        return False, f"No matching PO for {invoice.po_number}"
    if abs(po["amount_usdc"] - invoice.amount_usdc) > 0.01:
        return False, f"Amount mismatch: PO {po['amount_usdc']} vs invoice {invoice.amount_usdc}"

    receipt = await models.get_receipt(po["id"])
    if receipt is None:
        return False, "No receipt on file"
    if receipt["confirmed_by_role"] == "agent":
        return False, "Receipt confirmed by the agent itself, not an independent party"

    vendor = await models.get_vendor(po["vendor_id"])
    if vendor["wallet_address"] != invoice.vendor_wallet:
        return False, "Vendor wallet does not match the vendor on file"

    return True, "PO, receipt, and vendor wallet all match"


async def decide(invoice: Invoice, treasury_runway_days: float) -> tuple[Decision, str]:
    if not contract.is_vendor_approved(invoice.vendor_wallet):
        return Decision.ESCALATE, "Vendor not registered on-chain"
    if treasury_runway_days < 7:
        return Decision.HOLD, f"Runway is {treasury_runway_days:.0f} days, holding non-critical spend"
    return Decision.PAY, "Matched PO and receipt, treasury healthy, vendor registered"


async def _log_and_escalate(
    invoice: Invoice, reason: str, model_used: str, amount_units: int, doc_hash: bytes, invoice_number_b32: bytes
) -> None:
    reasoning_hash_hex = await decision_log.log_decision(
        "INVOICE_ESCALATED", invoice.invoice_number, reason, model_used=model_used, amount=invoice.amount_usdc
    )
    tx_id = contract.escalate(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash,
        invoice.category, bytes.fromhex(reasoning_hash_hex), reason,
    )
    await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id)
    await models.update_invoice_status(invoice.id, "escalated", reasoning_hash=reasoning_hash_hex, onchain_tx_id=tx_id)


async def process_invoice(invoice: Invoice, treasury_runway_days: float) -> Decision:
    amount_units = int(round(invoice.amount_usdc * 1_000_000))
    doc_hash = bytes.fromhex(invoice.doc_hash)
    invoice_number_b32 = contract.to_bytes32(invoice.invoice_number)

    if contract.is_paid(contract.invoice_key(invoice.vendor_wallet, invoice_number_b32)):
        await models.update_invoice_status(invoice.id, "paid")
        return Decision.PAY

    matched, match_reason = await three_way_match(invoice)
    if not matched:
        await _log_and_escalate(invoice, match_reason, "rules", amount_units, doc_hash, invoice_number_b32)
        return Decision.ESCALATE

    decision, reason = await decide(invoice, treasury_runway_days)

    if decision == Decision.ESCALATE:
        narrative = await escalation_reasoning(f"Invoice {invoice.invoice_number}: {reason}")
        await _log_and_escalate(invoice, narrative, config.groq_llm_model, amount_units, doc_hash, invoice_number_b32)
        return decision

    if decision == Decision.HOLD:
        reasoning_hash_hex = await decision_log.log_decision(
            "INVOICE_HELD", invoice.invoice_number, reason, model_used="rules", amount=invoice.amount_usdc
        )
        await models.update_invoice_status(invoice.id, "held", reasoning_hash=reasoning_hash_hex)
        return decision

    reasoning_hash_hex = await decision_log.log_decision(
        "INVOICE_PAID", invoice.invoice_number, reason, model_used="rules", amount=invoice.amount_usdc
    )
    reasoning_hash = bytes.fromhex(reasoning_hash_hex)
    commitment = contract.commitment_for(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash, invoice.category, reasoning_hash
    )
    contract.commit_decision(commitment)
    tx_id = contract.pay(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash, invoice.category, reasoning_hash
    )
    await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id)
    await models.update_invoice_status(invoice.id, "paid", reasoning_hash=reasoning_hash_hex, onchain_tx_id=tx_id)
    return decision

