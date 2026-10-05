"""Server-side treasury runway estimate. Never taken from a request.

Runway = (pool balance after this invoice) / (average daily spend over the last 30 days of this business's paid
invoices), capped at 365 days. With no spend history the cap applies. If the balance or the history cannot be read,
runway is 0 so the rules baseline HOLDs (fail closed).
"""
import asyncio
import logging
import time

from agent import contract
from backend import models

WINDOW_DAYS = 30
MAX_RUNWAY_DAYS = 365.0
_USDC_UNITS = 1_000_000

log = logging.getLogger(__name__)


def compute_runway(balance_usdc: float, spent_usdc: float, invoice_amount: float, window_days: int = WINDOW_DAYS) -> float:
    remaining = balance_usdc - invoice_amount
    if remaining < 0:
        return 0.0
    daily_burn = spent_usdc / window_days
    if daily_burn <= 0:
        return MAX_RUNWAY_DAYS
    return min(remaining / daily_burn, MAX_RUNWAY_DAYS)


async def runway_days(invoice_amount: float, business: dict) -> float:
    """Runway for one business: its own pool balance and its own paid invoices. Nothing from another business."""
    try:
        chain = contract.chain_for(business)
        balance_units = await asyncio.to_thread(chain.usdc_balance)
        spent = await models.paid_total_since(business["id"], time.time() - WINDOW_DAYS * 86400)
    except Exception:
        log.exception("Could not compute treasury runway; failing closed")
        return 0.0
    return compute_runway(balance_units / _USDC_UNITS, spent, invoice_amount)
