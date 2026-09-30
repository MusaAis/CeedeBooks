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
    onchain_tx_id TEXT
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
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


async def record_onchain_tx(reasoning_hash: str, tx_id: str) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "UPDATE audit_log SET onchain_tx_id = ? WHERE reasoning_hash = ?", (tx_id, reasoning_hash)
        )
        await db.commit()


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
