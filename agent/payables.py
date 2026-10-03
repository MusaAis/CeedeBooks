"""Three-way match, rules-baseline payment decisions, and payment execution."""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from agent import contract, decision_log
from agent.config import config
from agent.llm import escalation_reasoning
from backend import models

log = logging.getLogger(__name__)


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


def _chain_hash(tx_id: str):
    """On-chain tx hash for the public audit page; None if Circle has not reported it (never blocks a payment)."""
    try:
        return contract.chain_tx_hash(tx_id)
    except Exception:
        return None


async def _anchor_refusal(reasoning_hash_hex: str) -> None:
    """Put a hold's reasoning hash on-chain (logDecision) so refusals are provable too. Best effort: a hold is safe either way."""
    try:
        tx_id = contract.log_decision(bytes.fromhex(reasoning_hash_hex))
        contract.wait_for_transaction(tx_id)
        await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id, _chain_hash(tx_id))
    except Exception:
        log.exception("Could not anchor hold %s on-chain; it stays in the off-chain audit log only", reasoning_hash_hex)


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
    contract.wait_for_transaction(tx_id)
    await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id, _chain_hash(tx_id))
    await models.update_invoice_status(invoice.id, "escalated", reasoning_hash=reasoning_hash_hex, onchain_tx_id=tx_id)


async def preflight(invoice: Invoice, treasury_runway_days: float) -> dict:
    """Dry run of process_invoice: what would happen to this invoice right now, and why. Writes nothing.

    No audit row, no invoice row, no chain transaction. Every check is evaluated on its own (no short-circuit) so a
    vendor sees all the problems at once. Only booleans are returned: no balances, limits or other vendors' data.
    Chain reads that fail raise, so the caller can answer 503 instead of guessing.
    """
    amount_units = int(round(invoice.amount_usdc * 1_000_000))
    invoice_number_b32 = contract.to_bytes32(invoice.invoice_number)
    already_paid = contract.is_paid(contract.invoice_key(invoice.vendor_wallet, invoice_number_b32))

    po = await models.get_purchase_order(invoice.po_number) if invoice.po_number else None
    receipt = await models.get_receipt(po["id"]) if po is not None else None
    vendor_registered = contract.is_vendor_approved(invoice.vendor_wallet)
    overall_left, category_left = contract.remaining_today(invoice.category)

    checks = {
        "not_already_paid": not already_paid,
        "po_found": po is not None,
        "amount_matches_po": po is not None and abs(po["amount_usdc"] - invoice.amount_usdc) <= 0.01,
        "receipt_on_file": receipt is not None,
        "receipt_independent": receipt is not None and receipt["confirmed_by_role"] != "agent",
        "vendor_registered_onchain": vendor_registered,
        "runway_ok": treasury_runway_days >= 7,
        "within_daily_limits": amount_units <= overall_left and amount_units <= category_left,
    }

    reasons = []
    if already_paid:
        return {"outcome": "already_paid", "reasons": ["This invoice number was already paid on-chain"], "checks": checks}
    if not invoice.po_number:
        reasons.append("No PO number on the invoice")
    elif po is None:
        reasons.append(f"No matching PO for {invoice.po_number}")
    elif not checks["amount_matches_po"]:
        reasons.append("Invoice amount does not match the PO")
    if po is not None and receipt is None:
        reasons.append("No receipt on file: the buyer must confirm delivery first")
    elif receipt is not None and not checks["receipt_independent"]:
        reasons.append("The receipt was confirmed by the agent itself, not an independent party")
    if reasons:
        return {"outcome": "would_escalate", "reasons": reasons, "checks": checks}
    if not vendor_registered:
        return {"outcome": "would_escalate", "reasons": ["Vendor is not registered on-chain yet"], "checks": checks}
    if not checks["runway_ok"]:
        return {"outcome": "would_hold", "reasons": ["Treasury runway is under the 7-day minimum"], "checks": checks}
    if not checks["within_daily_limits"]:
        return {
            "outcome": "would_be_refused_by_contract",
            "reasons": ["The amount exceeds today's remaining budget for this category, so the contract would refuse it"],
            "checks": checks,
        }
    return {"outcome": "would_pay", "reasons": ["PO, receipt and vendor match; treasury healthy; within limits"], "checks": checks}


async def process_invoice(invoice: Invoice, treasury_runway_days: float) -> Decision:
    amount_units = int(round(invoice.amount_usdc * 1_000_000))
    doc_hash = contract.to_bytes32(invoice.doc_hash)
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
        await _anchor_refusal(reasoning_hash_hex)
        return decision

    reasoning_hash_hex = await decision_log.log_decision(
        "INVOICE_PAID", invoice.invoice_number, reason, model_used="rules", amount=invoice.amount_usdc
    )
    reasoning_hash = bytes.fromhex(reasoning_hash_hex)
    commitment = contract.commitment_for(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash, invoice.category, reasoning_hash
    )
    commit_tx_id = contract.commit_decision(commitment)
    contract.wait_for_transaction(commit_tx_id)
    tx_id = contract.pay(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash, invoice.category, reasoning_hash
    )
    contract.wait_for_transaction(tx_id)
    await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id, _chain_hash(tx_id))
    await models.update_invoice_status(invoice.id, "paid", reasoning_hash=reasoning_hash_hex, onchain_tx_id=tx_id)
    return decision

