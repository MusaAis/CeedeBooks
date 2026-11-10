"""Vendor trust view (Phase R, v1.3.3.2): each vendor's streak, graduated state, autopay state and revocations, for the proof page
and the admin site. Read only: it writes nothing and moves no figure. The records are the vendor's graduation, revocation and
autopay entries in the audit log (each is one signed ledger entry), so every line links to a hash anyone can verify.
"""
import aiosqlite

from agent import autopay, earned
from agent.config import config
from backend import models

ACTIONS = earned.ACTIONS + autopay.ACTIONS
HISTORY_LIMIT = 10


async def _records(business_id: int) -> dict:
    """vendor id -> that vendor's trust records, oldest first."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        marks = ",".join("?" * len(ACTIONS))
        async with db.execute(
            f"SELECT id, action, subject, timestamp, reasoning_hash FROM audit_log WHERE business_id = ? AND action IN ({marks}) "
            "ORDER BY id", (business_id, *ACTIONS)) as cur:
            rows = await cur.fetchall()
    out: dict = {}
    for r in rows:
        prefix, _, ident = (r["subject"] or "").partition(":")
        if prefix == "vendor" and ident.isdigit():
            out.setdefault(int(ident), []).append(
                {"action": r["action"], "at": r["timestamp"], "reasoning_hash": r["reasoning_hash"]})
    return out


async def _autopaid(business_id: int) -> dict:
    """vendor id -> how many of its invoices the agent paid by autopay (shadow payments only)."""
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT vendor_id, COUNT(*) FROM invoices WHERE business_id = ? AND auto = 1 AND status = 'paid' AND mode = 'shadow' "
            "GROUP BY vendor_id", (business_id,)) as cur:
            return {vid: n for vid, n in await cur.fetchall()}


async def overview(business: dict, *, only_with_trust: bool) -> dict:
    """One business's vendors with their trust state. `only_with_trust` (the public view) lists a vendor only once it has a streak
    or a record; the owner's view lists every vendor. Wallets, signers and signatures are never included."""
    bid = business["id"]
    records, autopaid = await _records(bid), await _autopaid(bid)
    vendors = []
    for v in await models.list_vendors(bid):
        state = await earned.vendor_state(bid, v["id"])
        history = records.get(v["id"], [])
        if only_with_trust and not history and not state["streak"]:
            continue
        switch = await models.get_autopay(bid, v["id"])
        on = bool(switch and switch["active"])
        revocations = [r for r in history if r["action"] == earned.ACTION_REVOKED]
        vendors.append({
            "id": v["id"], "name": v["name"], "streak": state["streak"], "graduated": state["graduated"],
            "record_hash": state["record_hash"], "record_at": state["record_at"],
            "graduations": sum(1 for r in history if r["action"] == earned.ACTION_GRADUATED),
            "revocations": len(revocations),
            "last_revocation": revocations[-1] if revocations else None,
            "autopay": {"on": on, "max_usdc": switch["max_usdc"] if on else None,
                        "record_hash": switch["reasoning_hash"] if on else None, "paid": autopaid.get(v["id"], 0)},
            "records": list(reversed(history[-HISTORY_LIMIT:])),
        })
    return {"earn_after": int(business["earn_after"]), "min": earned.MIN_EARN_AFTER, "max": earned.MAX_EARN_AFTER, "vendors": vendors}
