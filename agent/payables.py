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
    AWAIT_OWNER = "await_owner"  # shadow mode: the rules say pay, the owner has not approved yet


@dataclass
class Invoice:
    business_id: int
    id: int
    invoice_number: str
    vendor_wallet: str
    amount_usdc: float
    category: int
    doc_hash: str
    po_number: Optional[str]
    mode: str = "live"  # "shadow": a real bill mirrored as a testnet payment, paid only after the owner approves
    real_amount: Optional[str] = None  # exact decimal text of the real bill (shadow only)
    real_currency: Optional[str] = None


def _plain(amount: float) -> str:
    return f"{amount:.6f}".rstrip("0").rstrip(".")


def shadow_note(invoice: Invoice) -> str:
    """Code-written text added to the reasoning of a shadow invoice, so the hash covers the real amount and currency."""
    if invoice.mode != "shadow":
        return ""
    return f" Shadow mode: real bill {invoice.real_amount} {invoice.real_currency}, mirrored as {_plain(invoice.amount_usdc)} USDC."


async def three_way_match(invoice: Invoice) -> tuple[bool, str]:
    if not invoice.po_number:
        return False, "No PO number on invoice"

    po = await models.get_purchase_order(invoice.business_id, invoice.po_number)
    if po is None:
        return False, f"No matching PO for {invoice.po_number}"
    if abs(po["amount_usdc"] - invoice.amount_usdc) > 0.01:
        return False, f"Amount mismatch: PO {po['amount_usdc']} vs invoice {invoice.amount_usdc}"

    receipt = await models.get_receipt(invoice.business_id, po["id"])
    if receipt is None:
        return False, "No receipt on file"
    if receipt["confirmed_by_role"] == "agent":
        return False, "Receipt confirmed by the agent itself, not an independent party"

    vendor = await models.get_vendor(invoice.business_id, po["vendor_id"])
    if vendor is None or vendor["wallet_address"] != invoice.vendor_wallet:
        return False, "Vendor wallet does not match the vendor on file"

    return True, "PO, receipt, and vendor wallet all match"


async def decide(invoice: Invoice, treasury_runway_days: float, chain) -> tuple[Decision, str]:
    if not chain.is_vendor_approved(invoice.vendor_wallet):
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


async def _anchor_refusal(chain, reasoning_hash_hex: str) -> None:
    """Put a hold's reasoning hash on-chain (logDecision) so refusals are provable too. Best effort: a hold is safe either way."""
    try:
        tx_id = chain.log_decision(bytes.fromhex(reasoning_hash_hex))
        contract.wait_for_transaction(tx_id)
        await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id, _chain_hash(tx_id))
    except Exception:
        log.exception("Could not anchor hold %s on-chain; it stays in the off-chain audit log only", reasoning_hash_hex)


async def _log_and_escalate(
    invoice: Invoice, reason: str, model_used: str, amount_units: int, doc_hash: bytes, invoice_number_b32: bytes,
    chain, business: dict,
) -> None:
    reasoning_hash_hex = await decision_log.log_decision(
        "INVOICE_ESCALATED", invoice.invoice_number, reason + shadow_note(invoice), model_used=model_used, amount=invoice.amount_usdc, business=business
    )
    tx_id = chain.escalate(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash,
        invoice.category, bytes.fromhex(reasoning_hash_hex), reason,
    )
    contract.wait_for_transaction(tx_id)
    await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id, _chain_hash(tx_id))
    await models.update_invoice_status(invoice.business_id, invoice.id, "escalated", reasoning_hash=reasoning_hash_hex, onchain_tx_id=tx_id)


async def preflight(invoice: Invoice, treasury_runway_days: float, business: dict) -> dict:
    """Dry run of process_invoice: what would happen to this invoice right now, and why. Writes nothing.

    No audit row, no invoice row, no chain transaction. Every check is evaluated on its own (no short-circuit) so a
    vendor sees all the problems at once. Only booleans are returned: no balances, limits or other vendors' data.
    Chain reads that fail raise, so the caller can answer 503 instead of guessing.
    """
    chain = contract.chain_for(business)
    amount_units = int(round(invoice.amount_usdc * 1_000_000))
    invoice_number_b32 = contract.to_bytes32(invoice.invoice_number)
    already_paid = chain.is_paid(contract.invoice_key(invoice.vendor_wallet, invoice_number_b32))

    po = await models.get_purchase_order(invoice.business_id, invoice.po_number) if invoice.po_number else None
    receipt = await models.get_receipt(invoice.business_id, po["id"]) if po is not None else None
    vendor_registered = chain.is_vendor_approved(invoice.vendor_wallet)
    overall_left, category_left = chain.remaining_today(invoice.category)

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


async def process_invoice(invoice: Invoice, treasury_runway_days: float, business: dict) -> Decision:
    """Runs one invoice for one business: its contract, its Circle wallet, its audit rows. If the contract cannot be
    reached the exception propagates and nothing is paid (fail closed)."""
    chain = contract.chain_for(business)
    amount_units = int(round(invoice.amount_usdc * 1_000_000))
    doc_hash = contract.to_bytes32(invoice.doc_hash)
    invoice_number_b32 = contract.to_bytes32(invoice.invoice_number)

    if chain.is_paid(contract.invoice_key(invoice.vendor_wallet, invoice_number_b32)):
        await models.update_invoice_status(invoice.business_id, invoice.id, "paid")
        return Decision.PAY

    matched, match_reason = await three_way_match(invoice)
    if not matched:
        await _log_and_escalate(invoice, match_reason, "rules", amount_units, doc_hash, invoice_number_b32, chain, business)
        return Decision.ESCALATE

    decision, reason = await decide(invoice, treasury_runway_days, chain)

    if decision == Decision.ESCALATE:
        narrative = await escalation_reasoning(f"Invoice {invoice.invoice_number}: {reason}")
        await _log_and_escalate(invoice, narrative, config.groq_llm_model, amount_units, doc_hash, invoice_number_b32, chain, business)
        return decision

    if decision == Decision.HOLD:
        reasoning_hash_hex = await decision_log.log_decision(
            "INVOICE_HELD", invoice.invoice_number, reason + shadow_note(invoice), model_used="rules", amount=invoice.amount_usdc, business=business
        )
        await models.update_invoice_status(invoice.business_id, invoice.id, "held", reasoning_hash=reasoning_hash_hex)
        await _anchor_refusal(chain, reasoning_hash_hex)
        return decision

    if invoice.mode == "shadow":
        # The agent decided to pay, but this is a real bill the owner still pays their own way: park it for the owner.
        # Off-chain only: nothing is committed, escalated or sent, and it is not counted as paid or refused.
        pending_hash = await decision_log.log_decision(
            "INVOICE_SHADOW_PENDING", invoice.invoice_number, reason + shadow_note(invoice) + " Waiting for the owner to approve.",
            model_used="rules", amount=invoice.amount_usdc, business=business,
        )
        await models.update_invoice_status(invoice.business_id, invoice.id, "awaiting_owner", reasoning_hash=pending_hash)
        return Decision.AWAIT_OWNER

    await _commit_then_pay(invoice, "INVOICE_PAID", reason, chain, business, amount_units, doc_hash, invoice_number_b32)
    return decision


async def _commit_then_pay(
    invoice: Invoice, action: str, reason: str, chain, business: dict, amount_units: int, doc_hash: bytes, invoice_number_b32: bytes
) -> str:
    """Hash first, then commit, wait, pay, wait. Returns the reasoning hash. The contract re-checks everything."""
    reasoning_hash_hex = await decision_log.log_decision(
        action, invoice.invoice_number, reason, model_used="rules", amount=invoice.amount_usdc, business=business
    )
    reasoning_hash = bytes.fromhex(reasoning_hash_hex)
    commitment = chain.commitment_for(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash, invoice.category, reasoning_hash
    )
    commit_tx_id = chain.commit_decision(commitment)
    contract.wait_for_transaction(commit_tx_id)
    tx_id = chain.pay(
        invoice.vendor_wallet, amount_units, invoice_number_b32, doc_hash, invoice.category, reasoning_hash
    )
    contract.wait_for_transaction(tx_id)
    await decision_log.record_onchain_tx(reasoning_hash_hex, tx_id, _chain_hash(tx_id))
    await models.update_invoice_status(invoice.business_id, invoice.id, "paid", reasoning_hash=reasoning_hash_hex, onchain_tx_id=tx_id)
    return reasoning_hash_hex


async def pay_approved_shadow(invoice: Invoice, pending_hash: str, business: dict) -> str:
    """The owner approved a parked shadow decision: pay it now through the normal commit-then-pay. The contract still
    enforces vendor approval, limits, pause and once-only payment, so approval can never exceed what the owner configured.
    Raises if the chain refuses; the caller puts the invoice back to awaiting_owner."""
    chain = contract.chain_for(business)
    amount_units = int(round(invoice.amount_usdc * 1_000_000))
    doc_hash = contract.to_bytes32(invoice.doc_hash)
    invoice_number_b32 = contract.to_bytes32(invoice.invoice_number)
    if chain.is_paid(contract.invoice_key(invoice.vendor_wallet, invoice_number_b32)):
        await models.update_invoice_status(invoice.business_id, invoice.id, "paid")
        return pending_hash
    reason = f"Owner approved shadow decision {pending_hash[:12]}." + shadow_note(invoice)
    return await _commit_then_pay(invoice, "INVOICE_SHADOW_PAID", reason, chain, business, amount_units, doc_hash, invoice_number_b32)
