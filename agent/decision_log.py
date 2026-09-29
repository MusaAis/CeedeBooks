"""
Audit log for every agent decision — HELD, ESCALATED, PAID, RELEASED, all of it.

Corrected per plan §8 against three bugs in the original pseudocode:
  1. Sequencing: reasoning_hash must be computed and written BEFORE the payment/release
     call it justifies, not alongside or after it — callers must await log_decision()
     first and only then call the contract.
  2. Serialization: fields are pipe-delimited, not concatenated, so "12"+"3" and "1"+"23"
     can't collide.
  3. Split: the full record lives in SQLite (queryable via the API); only the hash goes
     on-chain as an event. A judge recomputes the hash from the SQLite row using this same
     serialization and checks it against the on-chain event — that round-trip IS the audit
     trail.

reasoning_hash (this file) is a DIFFERENT guarantee from invoice_hash (BudgetEnforcer's
idempotency key, built in payables.py in Phase 2) — schema keeps them as distinct columns,
never conflated.
"""
import hashlib
import time
from dataclasses import dataclass
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
    timestamp REAL NOT NULL,
    reasoning_hash TEXT NOT NULL UNIQUE,
    model_used TEXT NOT NULL,
    onchain_tx_hash TEXT
);
"""


@dataclass
class AuditEntry:
    action: str
    subject: str
    reasoning: str
    amount_usdc: float
    timestamp: float
    reasoning_hash: str
    model_used: str


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
        await db.commit()


def compute_reasoning_hash(action: str, subject: str, reasoning: str, amount: float, timestamp: float) -> str:
    """Pipe-delimited, not concatenated — see module docstring bug #2."""
    hash_input = f"{action}|{subject}|{reasoning}|{amount}|{timestamp}".encode()
    return hashlib.sha256(hash_input).hexdigest()


async def log_decision(
    action: str,
    subject: str,
    reasoning: str,
    amount: float = 0,
    treasury_balance_after: Optional[float] = None,
    emit_onchain=None,  # async callable(reasoning_hash: str) -> tx_hash, wired up once
                         # AuditLog.sol / BudgetEnforcer's event path is deployed
) -> str:
    """
    Writes the full record to SQLite, then (if emit_onchain is provided) emits the hash
    on-chain. Returns reasoning_hash so the caller can proceed to the payment/release call
    it justifies — never call the contract before this function returns.
    """
    timestamp = time.time()
    reasoning_hash = compute_reasoning_hash(action, subject, reasoning, amount, timestamp)
    model_used = "laya-finetuned" if "Laya:" in reasoning else config.groq_llm_model

    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
        await db.execute(
            """INSERT INTO audit_log
               (action, subject, reasoning, amount_usdc, treasury_balance_after,
                timestamp, reasoning_hash, model_used)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (action, subject, reasoning, amount, treasury_balance_after, timestamp, reasoning_hash, model_used),
        )
        await db.commit()

    if emit_onchain is not None:
        tx_hash = await emit_onchain(reasoning_hash)
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute(
                "UPDATE audit_log SET onchain_tx_hash = ? WHERE reasoning_hash = ?",
                (tx_hash, reasoning_hash),
            )
            await db.commit()

    return reasoning_hash


async def verify_roundtrip(reasoning_hash: str) -> bool:
    """The judge-legible check: pull the row, recompute the hash from the same
    serialization, confirm it matches what's stored (and, once wired up, what's on-chain)."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT action, subject, reasoning, amount_usdc, timestamp, reasoning_hash FROM audit_log WHERE reasoning_hash = ?",
            (reasoning_hash,),
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return False
    recomputed = compute_reasoning_hash(row["action"], row["subject"], row["reasoning"], row["amount_usdc"], row["timestamp"])
    return recomputed == row["reasoning_hash"]

