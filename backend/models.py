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

CREATE TABLE IF NOT EXISTS vendor_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name TEXT NOT NULL,
    wallet TEXT NOT NULL,
    contact TEXT,
    ip_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    vendor_id INTEGER REFERENCES vendors(id),
    created_at REAL NOT NULL,
    decided_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_application_pending_wallet ON vendor_applications(wallet) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS rejected_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reason TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'agent',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_address TEXT NOT NULL,
    action TEXT NOT NULL,
    ref TEXT,
    tx_hash TEXT UNIQUE,
    block INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('buyer', 'vendor')),
    vendor_id INTEGER REFERENCES vendors(id),
    label TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    CHECK ((role = 'vendor' AND vendor_id IS NOT NULL) OR (role = 'buyer' AND vendor_id IS NULL))
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.executescript(_SCHEMA)
        async with db.execute("PRAGMA table_info(invoices)") as cur:
            columns = [r[1] for r in await cur.fetchall()]
        if "origin" not in columns:  # v1.2.7: where an invoice came from (agent, manual, demo)
            await db.execute("ALTER TABLE invoices ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
        await db.commit()


async def backfill_origin() -> None:
    """Invoices paid by hand through scripts (audit model_used = 'manual') are labelled manual, once. Idempotent."""
    async with aiosqlite.connect(config.db_path) as db:
        try:
            await db.execute(
                "UPDATE invoices SET origin = 'manual' WHERE origin = 'agent' AND reasoning_hash IN "
                "(SELECT reasoning_hash FROM audit_log WHERE model_used = 'manual')"
            )
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # audit_log not created yet (fresh database): nothing to backfill


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
    invoice_number: str, vendor_id: int, po_id: Optional[int], amount_usdc: float, category: int, doc_hash: str,
    origin: str = "agent",
) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            """INSERT INTO invoices (invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, origin, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, origin, time.time()),
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



async def save_api_key(key_hash: str, role: str, vendor_id: Optional[int], label: str) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "INSERT INTO api_keys (key_hash, role, vendor_id, label, created_at) VALUES (?, ?, ?, ?, ?)",
            (key_hash, role, vendor_id, label, time.time()),
        )
        await db.commit()
        return cur.lastrowid


async def get_api_key_by_hash(key_hash: str) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)) as cur:
            return await cur.fetchone()


async def revoke_api_key(key_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        await db.commit()
        return cur.rowcount > 0


async def paid_total_since(since_ts: float) -> float:
    """Total USDC of invoices with status 'paid' created at or after since_ts."""
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(amount_usdc), 0) FROM invoices WHERE status = 'paid' AND created_at >= ?", (since_ts,)
        ) as cur:
            return float((await cur.fetchone())[0])


async def get_purchase_order_by_id(po_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,)) as cur:
            return await cur.fetchone()


async def log_admin_action(admin_address: str, action: str, ref: Optional[str] = None,
                           tx_hash: Optional[str] = None, block: Optional[int] = None) -> int:
    """Raises sqlite3.IntegrityError if this transaction hash was already recorded."""
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "INSERT INTO admin_actions (admin_address, action, ref, tx_hash, block, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (admin_address, action, ref, tx_hash, block, time.time()),
        )
        await db.commit()
        return cur.lastrowid


async def list_admin_actions(limit: int = 100) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM admin_actions ORDER BY id DESC LIMIT ?", (limit,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def list_vendors() -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name, wallet_address FROM vendors ORDER BY id") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def list_purchase_orders(limit: int = 100) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.id, p.po_number, p.vendor_id, v.name AS vendor_name, p.amount_usdc, p.category,
                      EXISTS(SELECT 1 FROM receipts r WHERE r.po_id = p.id) AS received
               FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id ORDER BY p.id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def list_invoices(limit: int = 50) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT i.id, i.invoice_number, i.vendor_id, v.name AS vendor_name, i.amount_usdc, i.category,
                      i.status, i.reasoning_hash, i.created_at
               FROM invoices i JOIN vendors v ON v.id = i.vendor_id ORDER BY i.id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def invoice_counts() -> dict:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute("SELECT status, COUNT(*) FROM invoices GROUP BY status") as cur:
            return {status: n for status, n in await cur.fetchall()}


# ---- vendor portal (v1.2.7)

ORIGINS = ("agent", "manual", "demo")


async def get_vendor_by_wallet(wallet: str) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM vendors WHERE lower(wallet_address) = ? ORDER BY id LIMIT 1", (wallet.lower(),)
        ) as cur:
            return await cur.fetchone()


async def list_vendor_purchase_orders(vendor_id: int) -> list:
    """A vendor's own POs only. `received`: a receipt is on file. `invoiced`: an invoice already exists for it."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT p.id, p.po_number, p.amount_usdc, p.category,
                      EXISTS(SELECT 1 FROM receipts r WHERE r.po_id = p.id) AS received,
                      EXISTS(SELECT 1 FROM invoices i WHERE i.po_id = p.id) AS invoiced
               FROM purchase_orders p WHERE p.vendor_id = ? ORDER BY p.id DESC LIMIT 100""",
            (vendor_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def list_vendor_invoices(vendor_id: int, limit: int = 50) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, invoice_number, amount_usdc, category, status, reasoning_hash, created_at
               FROM invoices WHERE vendor_id = ? ORDER BY id DESC LIMIT ?""",
            (vendor_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def save_application(business_name: str, wallet: str, contact: Optional[str], ip_hash: str) -> int:
    """Raises sqlite3.IntegrityError if this wallet already has a pending application."""
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "INSERT INTO vendor_applications (business_name, wallet, contact, ip_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (business_name, wallet.lower(), contact, ip_hash, time.time()),
        )
        await db.commit()
        return cur.lastrowid


async def pending_applications_from(ip_hash: str) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM vendor_applications WHERE status = 'pending' AND ip_hash = ?", (ip_hash,)
        ) as cur:
            return int((await cur.fetchone())[0])


async def count_pending_applications() -> int:
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM vendor_applications WHERE status = 'pending'") as cur:
            return int((await cur.fetchone())[0])


async def list_applications(limit: int = 100) -> list:
    """Private: admin only. The ip hash is never returned."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT id, business_name, wallet, contact, status, vendor_id, created_at, decided_at
               FROM vendor_applications ORDER BY (status = 'pending') DESC, id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_application(application_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vendor_applications WHERE id = ?", (application_id,)) as cur:
            return await cur.fetchone()


async def accept_application(application_id: int) -> Optional[int]:
    """Creates the vendor and marks the application accepted in one transaction. Returns the new vendor id, or None if
    the application is not pending any more (so two admin clicks cannot create two vendors)."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT * FROM vendor_applications WHERE id = ? AND status = 'pending'", (application_id,)) as cur:
            app_row = await cur.fetchone()
        if app_row is None:
            await db.rollback()
            return None
        cur = await db.execute("INSERT INTO vendors (name, wallet_address) VALUES (?, ?)", (app_row["business_name"], app_row["wallet"]))
        vendor_id = cur.lastrowid
        await db.execute(
            "UPDATE vendor_applications SET status = 'accepted', vendor_id = ?, decided_at = ? WHERE id = ?",
            (vendor_id, time.time(), application_id),
        )
        await db.commit()
        return vendor_id


async def reject_application(application_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "UPDATE vendor_applications SET status = 'rejected', decided_at = ? WHERE id = ? AND status = 'pending'",
            (time.time(), application_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def log_rejected_submission(reason: str, origin: str = "agent") -> None:
    """Counts only (no vendor, amount or invoice data): feeds the 'duplicates caught' metric."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("INSERT INTO rejected_submissions (reason, origin, created_at) VALUES (?, ?, ?)", (reason, origin, time.time()))
        await db.commit()


async def submission_stats() -> dict:
    """Invoices processed, USDC paid and duplicates caught, split by origin. All three origins are always present."""
    out = {o: {"invoices_processed": 0, "payment_volume_usdc": 0.0, "duplicates_caught": 0} for o in ORIGINS}
    async with aiosqlite.connect(config.db_path) as db:
        async with db.execute(
            "SELECT origin, COUNT(*), COALESCE(SUM(CASE WHEN status = 'paid' THEN amount_usdc ELSE 0 END), 0) "
            "FROM invoices WHERE status IN ('paid', 'held', 'escalated', 'rejected') GROUP BY origin"
        ) as cur:
            for origin, n, volume in await cur.fetchall():
                if origin in out:
                    out[origin]["invoices_processed"], out[origin]["payment_volume_usdc"] = n, round(float(volume), 6)
        async with db.execute("SELECT origin, COUNT(*) FROM rejected_submissions WHERE reason = 'duplicate_invoice' GROUP BY origin") as cur:
            for origin, n in await cur.fetchall():
                if origin in out:
                    out[origin]["duplicates_caught"] = n
    total = {k: sum(out[o][k] for o in ORIGINS) for k in ("invoices_processed", "payment_volume_usdc", "duplicates_caught")}
    total["payment_volume_usdc"] = round(total["payment_volume_usdc"], 6)
    return {**total, "by_origin": out}
