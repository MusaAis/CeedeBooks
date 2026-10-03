"""Off-chain audit log: every agent decision, hashed before any on-chain action."""
import hashlib
import json
import time
from typing import Optional

import aiosqlite

from agent.config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    amount_usdc REAL NOT NULL DEFAULT 0,
    treasury_balance_after REAL,
    model_used TEXT NOT NULL,
    timestamp REAL NOT NULL,
    hash_input TEXT NOT NULL,
    reasoning_hash TEXT NOT NULL UNIQUE,
    onchain_tx_id TEXT,
    chain_tx_hash TEXT
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
        async with db.execute("PRAGMA table_info(audit_log)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "chain_tx_hash" not in columns:  # databases created before v1.3.0
            await db.execute("ALTER TABLE audit_log ADD COLUMN chain_tx_hash TEXT")
        await db.commit()


def compute_reasoning_hash(
    action: str, subject: str, reasoning: str, amount: float, model_used: str, timestamp: float
) -> tuple[str, str]:
    payload = {
        "action": action,
        "subject": subject,
        "reasoning": reasoning,
        "amount_usdc": amount,
        "model_used": model_used,
        "timestamp": timestamp,
    }
    hash_input = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    reasoning_hash = hashlib.sha256(hash_input.encode()).hexdigest()
    return hash_input, reasoning_hash


async def log_decision(
    action: str,
    subject: str,
    reasoning: str,
    model_used: str,
    amount: float = 0,
    treasury_balance_after: Optional[float] = None,
) -> str:
    timestamp = time.time()
    hash_input, reasoning_hash = compute_reasoning_hash(action, subject, reasoning, amount, model_used, timestamp)

    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
        await db.execute(
            """INSERT INTO audit_log
               (action, subject, reasoning, amount_usdc, treasury_balance_after,
                model_used, timestamp, hash_input, reasoning_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action, subject, reasoning, amount, treasury_balance_after, model_used, timestamp, hash_input, reasoning_hash),
        )
        await db.commit()

    return reasoning_hash


async def record_onchain_tx(reasoning_hash: str, tx_id: str, chain_tx_hash: Optional[str] = None) -> None:
    """tx_id is the Circle transaction id; chain_tx_hash is the on-chain hash anyone can look up (may be unknown)."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "UPDATE audit_log SET onchain_tx_id = ?, chain_tx_hash = COALESCE(?, chain_tx_hash) WHERE reasoning_hash = ?",
            (tx_id, chain_tx_hash, reasoning_hash),
        )
        await db.commit()


async def recent(limit: int = 20) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT reasoning_hash, action, subject, model_used, amount_usdc, timestamp, chain_tx_hash "
            "FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]


async def stats() -> dict:
    """Counts of agent decisions. Manual entries (model_used = 'manual') are reported separately, never as agent decisions."""
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT action, model_used = 'manual', COUNT(*), COALESCE(SUM(amount_usdc), 0) "
            "FROM audit_log GROUP BY action, model_used = 'manual'"
        ) as cursor:
            rows = await cursor.fetchall()
    out = {"paid": 0, "held": 0, "escalated": 0, "paid_usdc": 0.0, "manual_paid": 0, "manual_paid_usdc": 0.0}
    for action, manual, count, usdc in rows:
        if manual:
            if action == "INVOICE_PAID":
                out["manual_paid"] += count
                out["manual_paid_usdc"] += usdc
        elif action == "INVOICE_PAID":
            out["paid"] += count
            out["paid_usdc"] += usdc
        elif action == "INVOICE_HELD":
            out["held"] += count
        elif action == "INVOICE_ESCALATED":
            out["escalated"] += count
    out["refused"] = out["held"] + out["escalated"]
    out["decisions"] = out["paid"] + out["refused"]
    out["refusal_rate"] = round(out["refused"] / out["decisions"], 4) if out["decisions"] else None
    out["paid_usdc"] = round(out["paid_usdc"], 6)
    out["manual_paid_usdc"] = round(out["manual_paid_usdc"], 6)
    return out


async def get_decision(reasoning_hash: str) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM audit_log WHERE reasoning_hash = ?", (reasoning_hash,)
        ) as cursor:
            return await cursor.fetchone()


async def verify_roundtrip(reasoning_hash: str) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT hash_input, reasoning_hash FROM audit_log WHERE reasoning_hash = ?", (reasoning_hash,)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return False
    return hashlib.sha256(row["hash_input"].encode()).hexdigest() == row["reasoning_hash"]

