"""SQLite schema and queries for vendors, purchase orders, receipts, and invoices."""
import time
from typing import Optional

import aiosqlite

from agent.config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    previous_wallet_address TEXT
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT NOT NULL UNIQUE,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    amount_usdc REAL NOT NULL,
    category INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    confirmed_by TEXT NOT NULL,
    confirmed_by_role TEXT NOT NULL,
    confirmed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    po_id INTEGER REFERENCES purchase_orders(id),
    amount_usdc REAL NOT NULL,
    category INTEGER NOT NULL,
    doc_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reasoning_hash TEXT,
    onchain_tx_id TEXT,
    created_at REAL NOT NULL
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def get_vendor(vendor_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vendors WHERE id = ?", (vendor_id,)) as cur:
            return await cur.fetchone()


async def get_purchase_order(po_number: str) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)) as cur:
            return await cur.fetchone()


async def get_receipt(po_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM receipts WHERE po_id = ?", (po_id,)) as cur:
            return await cur.fetchone()


async def save_invoice(
    invoice_number: str, vendor_id: int, po_id: Optional[int], amount_usdc: float, category: int, doc_hash: str
) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            """INSERT INTO invoices (invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, time.time()),
        )
        await db.commit()
        return cur.lastrowid


async def update_invoice_status(
    invoice_id: int, status: str, reasoning_hash: Optional[str] = None, onchain_tx_id: Optional[str] = None
) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            """UPDATE invoices
               SET status = ?, reasoning_hash = COALESCE(?, reasoning_hash), onchain_tx_id = COALESCE(?, onchain_tx_id)
               WHERE id = ?""",
            (status, reasoning_hash, onchain_tx_id, invoice_id),
        )
        await db.commit()


async def save_vendor(name: str, wallet_address: str) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "INSERT INTO vendors (name, wallet_address) VALUES (?, ?)", (name, wallet_address)
        )
        await db.commit()
        return cur.lastrowid


async def update_vendor_wallet(vendor_id: int, new_wallet_address: str) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "UPDATE vendors SET previous_wallet_address = wallet_address, wallet_address = ? WHERE id = ?",
            (new_wallet_address, vendor_id),
        )
        await db.commit()


async def save_purchase_order(po_number: str, vendor_id: int, amount_usdc: float, category: int) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "INSERT INTO purchase_orders (po_number, vendor_id, amount_usdc, category) VALUES (?, ?, ?, ?)",
            (po_number, vendor_id, amount_usdc, category),
        )
        await db.commit()
        return cur.lastrowid


async def save_receipt(po_id: int, confirmed_by: str, confirmed_by_role: str) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "INSERT INTO receipts (po_id, confirmed_by, confirmed_by_role, confirmed_at) VALUES (?, ?, ?, ?)",
            (po_id, confirmed_by, confirmed_by_role, time.time()),
        )
        await db.commit()
        return cur.lastrowid


async def get_invoice(invoice_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)) as cur:
            return await cur.fetchone()

