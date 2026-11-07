"""Autopay for an earned vendor (Phase R, v1.3.3.1).

The owner's wallet signs a message for one graduated vendor and a per-payment cap. From then on, a shadow invoice of that vendor
whose decision is "pay" is paid by the agent without an owner click, through the same commit-then-pay, so the three-way match,
the rules, the contract's own limits and the pause state all still apply. Payments stay shadow (labelled auto). Any hold,
escalation, contract refusal or owner disagreement turns the switch off at once, as a signed ledger record. Fail closed: if
anything cannot be read the invoice waits for the owner as before.
"""
import hashlib
import logging
from decimal import Decimal
from typing import Optional

from agent import decision_log, earned
from backend import models

log = logging.getLogger("ceedebooks.autopay")

ACTION_ON = "VENDOR_AUTOPAY_ON"
ACTION_OFF = "VENDOR_AUTOPAY_OFF"
ACTIONS = (ACTION_ON, ACTION_OFF)

REASONS = {
    "owner": "The owner turned autopay off.",
    "held": "The agent held invoice {n}.",
    "escalated": "The agent escalated invoice {n}.",
    "contract_refused": "The chain refused the payment of invoice {n}.",
    "graduation_lost": "The vendor's graduation ended after the owner disagreed with the agent.",
}


def message_hash(message: str) -> str:
    return hashlib.sha256(message.encode()).hexdigest()


def units(amount) -> int:
    return int((Decimal(str(amount)) * 1_000_000).to_integral_value())


def on_text(vendor_name: str, vendor_id: int, wallet: str, signer: str, max_usdc: str, msg_hash: str) -> str:
    return (f"Autopay switched on for vendor {vendor_name} (id {vendor_id}, wallet {wallet}) by the owner wallet {signer}, signed "
            f"message {msg_hash}. The agent may pay this vendor's shadow invoices itself, each up to {max_usdc} USDC, only while the "
            f"vendor stays graduated and every check passes: the three-way match, the rules, the contract's own limits and the pause "
            f"state. A hold, an escalation, a refused payment or a disagreement turns it off. Payments stay shadow and are labelled auto.")


def off_text(vendor_name: str, vendor_id: int, reason: str) -> str:
    return (f"Autopay switched off for vendor {vendor_name} (id {vendor_id}): {reason} Its invoices wait for the owner's approval "
            f"again.")


async def eligible(business_id: int, vendor_wallet: str, amount_usdc: float) -> Optional[dict]:
    """The active switch if this payment may be made without the owner, else None. Raises if something cannot be read; the
    caller then parks the invoice for the owner."""
    vendor = await models.get_vendor_by_wallet(business_id, vendor_wallet)
    if vendor is None:
        return None
    switch = await models.get_autopay(business_id, vendor["id"])
    if not switch or not switch["active"] or switch["wallet_address"].lower() != vendor["wallet_address"].lower():
        return None
    if units(amount_usdc) > units(switch["max_usdc"]):
        return None
    if not (await earned.vendor_state(business_id, vendor["id"]))["graduated"]:
        return None
    return {**switch, "vendor_name": vendor["name"]}


async def turn_off(business: dict, vendor_id: int, code: str, invoice_number: str = "") -> bool:
    """Off first (the safe direction), then the record. True if the switch was on. A record that cannot be written is logged:
    the switch stays off and the invoice waits for the owner."""
    if not await models.set_autopay_off(business["id"], vendor_id):
        return False
    try:
        vendor = await models.get_vendor(business["id"], vendor_id)
        await decision_log.log_decision(
            ACTION_OFF, earned.subject_for(vendor_id), off_text(vendor["name"], vendor_id, REASONS[code].format(n=invoice_number)),
            model_used="rules", business=business)
    except Exception:
        log.exception("Autopay-off record for vendor %s of business %s could not be written", vendor_id, business["id"])
    return True
