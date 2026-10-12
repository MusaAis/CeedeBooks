"""On-chain anchoring of each business's ledger head (Phase E2).

Every so often the business's own agent wallet sends LedgerAnchor.anchor(head, sequence), where head is the entry hash of
the newest ledger entry. The contract keeps, per sender, the last head and sequence, and the sequence must rise.

What an anchor proves: the chain up to that sequence had exactly that head by that block. Rewriting or dropping any
of those entries afterwards makes the check fail, even for someone holding both the ledger key and the database.
What it does not prove: anything about entries after the anchor (they wait for the next one), or that the agent
wallet's operator is independent (a hosted agent wallet is run by the same operator, so a whole rewritten history could
be anchored at a higher sequence; the earlier Anchored events stay on the explorer and show it).

Anchoring never blocks a decision or a payment: it runs apart from them, and a failure is only logged.
"""
import asyncio
import logging
import time
from typing import Optional

import aiosqlite

from agent import contract
from agent.config import config
from backend import models

log = logging.getLogger(__name__)

EVERY_ENTRIES = 25            # anchor after this many new entries...
EVERY_SECONDS = 6 * 3600      # ...or this long, whichever comes first, and only if the head moved
POLL_SECONDS = 600
MIN_GAS_UNITS = 10 ** 16      # 0.01 of the native (USDC) gas token, 18 decimals; an anchor costs far less

_TABLE = """
CREATE TABLE IF NOT EXISTS ledger_anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    head TEXT NOT NULL,
    tx_id TEXT,
    tx_hash TEXT,
    created_at REAL NOT NULL,
    UNIQUE (business_id, sequence)
)
"""


class AnchorError(RuntimeError):
    """An anchor could not be sent or confirmed. Nothing else is affected."""


async def ensure_schema() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_TABLE)
        await db.commit()


async def _rows(sql: str, args: tuple) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute(sql, args) as cur:
                return [dict(r) for r in await cur.fetchall()]
        except aiosqlite.OperationalError:
            return []  # table not created yet: nothing recorded


async def head_of(business_id: int) -> Optional[tuple]:
    """(sequence, entry_hash) of the newest ledger entry, or None."""
    rows = await _rows("SELECT sequence, entry_hash FROM ledger WHERE business_id = ? ORDER BY sequence DESC LIMIT 1", (business_id,))
    return (rows[0]["sequence"], rows[0]["entry_hash"]) if rows else None


async def entry_hash_at(business_id: int, sequence: int) -> Optional[str]:
    rows = await _rows("SELECT entry_hash FROM ledger WHERE business_id = ? AND sequence = ?", (business_id, sequence))
    return rows[0]["entry_hash"] if rows else None


async def last_recorded(business_id: int) -> Optional[dict]:
    rows = await _rows("SELECT * FROM ledger_anchors WHERE business_id = ? ORDER BY sequence DESC LIMIT 1", (business_id,))
    return rows[0] if rows else None


async def recorded(business_id: int, limit: int = 5) -> list:
    return await _rows(
        "SELECT sequence, head, tx_hash, created_at FROM ledger_anchors WHERE business_id = ? ORDER BY sequence DESC LIMIT ?",
        (business_id, limit))


async def _record(business_id: int, sequence: int, head: str, tx_id: Optional[str], tx_hash: Optional[str]) -> None:
    await ensure_schema()
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO ledger_anchors (business_id, sequence, head, tx_id, tx_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (business_id, sequence, head, tx_id, tx_hash, time.time()))
        await db.commit()


def due(head_sequence: int, last: Optional[dict], now: float) -> bool:
    """The background job's rule. A business that was never anchored is left for the first, confirmed-by-hand anchor."""
    if last is None or head_sequence < 1 or head_sequence <= last["sequence"]:
        return False
    return head_sequence - last["sequence"] >= EVERY_ENTRIES or now - last["created_at"] >= EVERY_SECONDS


# ---- reading: what /ledger/verify reports

async def check(business: dict) -> dict:
    """Compares the newest on-chain anchor of this business's agent with its ledger. status:
    none (never anchored), match, mismatch (the anchored head is not what the chain holds at that sequence),
    short (the chain is shorter than the anchored sequence: newest entries were dropped), unreadable (the chain could not be read)."""
    try:
        agent = await asyncio.to_thread(lambda: contract.chain_for(business).agent())
        sequence, head = await asyncio.to_thread(contract.anchor_state, agent)
    except Exception:
        log.exception("Could not read the ledger anchor for %s", business["slug"])
        return {"status": "unreadable", "checked": False, "anchor_contract": config.ledger_anchor_address}
    out = {"checked": True, "agent": str(agent).lower(), "anchor_contract": config.ledger_anchor_address,
           "anchored_sequence": sequence, "recorded": await recorded(business["id"])}
    if sequence == 0:
        return {**out, "status": "none"}
    mine = await entry_hash_at(business["id"], sequence)
    if mine is None:
        return {**out, "status": "short", "anchored_head": head,
                "detail": f"the chain has no entry {sequence}, but it was anchored on-chain"}
    if mine != head:
        return {**out, "status": "mismatch", "anchored_head": head, "chain_head_at_sequence": mine,
                "detail": f"entry {sequence} is not what was anchored on-chain"}
    newest = await head_of(business["id"])
    return {**out, "status": "match", "anchored_head": head, "entries_after_anchor": (newest[0] - sequence) if newest else 0}


# ---- writing

async def anchor_now(business: dict, *, manual: bool = False, now: Optional[float] = None) -> dict:
    """Anchors this business's current head. The background job (manual False) only anchors a business that was anchored
    before and is due; `manual` is the confirmed-by-hand path used for the first anchor. Raises AnchorError when it cannot
    send or confirm; sends nothing it has not checked."""
    now = time.time() if now is None else now
    head = await head_of(business["id"])
    if head is None or head[0] < 1:
        return {"status": "nothing_to_anchor"}
    last = await last_recorded(business["id"])
    if not manual and not due(head[0], last, now):
        return {"status": "not_due"}
    if not business.get("circle_wallet_id"):
        raise AnchorError("this business has no agent wallet to anchor from")
    try:
        agent = await asyncio.to_thread(lambda: contract.chain_for(business).agent())
        gas = await asyncio.to_thread(contract.native_balance, agent)
        onchain_sequence, onchain_head = await asyncio.to_thread(contract.anchor_state, agent)
    except Exception as e:
        raise AnchorError(f"the chain could not be read: {e}") from e
    if business.get("agent_address") and business["agent_address"].lower() != str(agent).lower():
        raise AnchorError("the business's agent wallet is not the agent its contract names")
    if onchain_sequence > head[0]:
        raise AnchorError(f"the chain was already anchored at sequence {onchain_sequence}, beyond this ledger's newest entry {head[0]}")
    if onchain_sequence == head[0]:
        if onchain_head != head[1]:
            raise AnchorError("the head anchored at this sequence is not this ledger's")
        await _record(business["id"], head[0], head[1], None, None)
        return {"status": "already_anchored", "sequence": head[0], "head": head[1]}
    if gas < MIN_GAS_UNITS:
        raise AnchorError(f"the agent wallet {agent} has too little gas to anchor")
    try:
        tx_id = await asyncio.to_thread(contract.anchor_ledger, business["circle_wallet_id"], head[1], head[0])
        await asyncio.to_thread(contract.wait_for_transaction, tx_id)
        tx_hash = await asyncio.to_thread(contract.chain_tx_hash, tx_id)
        sequence, read_head = await asyncio.to_thread(contract.anchor_state, agent)
    except Exception as e:
        raise AnchorError(f"the anchor transaction did not complete: {e}") from e
    if sequence != head[0] or read_head != head[1]:
        raise AnchorError("the anchor transaction finished but the chain does not show this head for the agent; nothing was recorded")
    await _record(business["id"], head[0], head[1], tx_id, tx_hash)
    return {"status": "anchored", "sequence": head[0], "head": head[1], "tx_id": tx_id, "tx_hash": tx_hash}


async def run_once(now: Optional[float] = None) -> dict:
    """One pass over the active businesses. A failure for one is logged and never raised."""
    results = {}
    for business in await models.list_businesses("active"):
        try:
            results[business["slug"]] = (await anchor_now(business, manual=False, now=now))["status"]
        except Exception:
            log.exception("Ledger anchoring failed for %s; decisions and payments are not affected", business["slug"])
            results[business["slug"]] = "failed"
    return results


async def run_forever() -> None:
    await ensure_schema()
    await asyncio.sleep(60)
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ledger anchoring pass failed")
        await asyncio.sleep(POLL_SECONDS)
