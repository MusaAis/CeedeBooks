"""SQLite schema and queries for vendors, purchase orders, receipts, and invoices."""
import time
from decimal import Decimal
from typing import Optional

import aiosqlite

from agent.categories import HOME_SEED, MAX_ID
from agent.config import config

HOME_BUSINESS_ID = 1  # the original CeedeBooks business (the v1 contract); existing rows belong to it

_TABLES = """
CREATE TABLE IF NOT EXISTS businesses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    enforcer_address TEXT NOT NULL,
    approver_address TEXT NOT NULL DEFAULT '',
    agent_address TEXT NOT NULL DEFAULT '',
    circle_wallet_id TEXT NOT NULL DEFAULT '',
    contract_version INTEGER NOT NULL DEFAULT 2 CHECK (contract_version IN (1, 2)),
    external INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'archived')),
    created_tx_hash TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS business_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    owner_wallet TEXT NOT NULL,
    contact TEXT,
    ip_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepting', 'accepted', 'rejected', 'registered')),
    agent_wallet_id TEXT,
    agent_address TEXT,
    business_id INTEGER REFERENCES businesses(id),
    created_at REAL NOT NULL,
    decided_at REAL
);

CREATE TABLE IF NOT EXISTS categories (
    business_id INTEGER NOT NULL REFERENCES businesses(id),
    id INTEGER NOT NULL CHECK (id BETWEEN 0 AND 255),
    name TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (business_id, id)
);

CREATE TABLE IF NOT EXISTS vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    name TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    previous_wallet_address TEXT
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    po_number TEXT NOT NULL,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    amount_usdc REAL NOT NULL,
    category INTEGER NOT NULL,
    UNIQUE (business_id, po_number)
);

CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    po_id INTEGER NOT NULL REFERENCES purchase_orders(id),
    confirmed_by TEXT NOT NULL,
    confirmed_by_role TEXT NOT NULL,
    confirmed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    invoice_number TEXT NOT NULL,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
    po_id INTEGER REFERENCES purchase_orders(id),
    amount_usdc REAL NOT NULL,
    category INTEGER NOT NULL,
    doc_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reasoning_hash TEXT,
    onchain_tx_id TEXT,
    created_at REAL NOT NULL,
    origin TEXT NOT NULL DEFAULT 'agent',
    mode TEXT NOT NULL DEFAULT 'live',
    real_amount TEXT,
    real_currency TEXT,
    submitted_by TEXT,
    held_because TEXT,
    settled_at REAL,
    UNIQUE (business_id, invoice_number)
);

CREATE TABLE IF NOT EXISTS pause_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    paused INTEGER NOT NULL,
    reason TEXT NOT NULL,
    admin_address TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    reasoning_hash TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_verdicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    invoice_id INTEGER NOT NULL,
    reasoning_hash TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('agree', 'disagree')),
    admin_address TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (business_id, reasoning_hash),
    UNIQUE (business_id, invoice_id)
);

CREATE TABLE IF NOT EXISTS vendor_applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    business_name TEXT NOT NULL,
    wallet TEXT NOT NULL,
    contact TEXT,
    ip_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected')),
    vendor_id INTEGER REFERENCES vendors(id),
    created_at REAL NOT NULL,
    decided_at REAL
);

CREATE TABLE IF NOT EXISTS rejected_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    reason TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'agent',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    admin_address TEXT NOT NULL,
    action TEXT NOT NULL,
    ref TEXT,
    tx_hash TEXT UNIQUE,
    block INTEGER,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL DEFAULT 1,
    key_hash TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('buyer', 'vendor')),
    vendor_id INTEGER REFERENCES vendors(id),
    label TEXT NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    CHECK ((role = 'vendor' AND vendor_id IS NOT NULL) OR (role = 'buyer' AND vendor_id IS NULL))
);
"""

# Created after the migration, because on an old database the business_id column only exists once it has run.
_INDEXES = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_application_pending_wallet_v2
    ON vendor_applications(business_id, wallet) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS ux_bizapp_pending_wallet ON business_applications(owner_wallet) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS ux_bizapp_open_slug ON business_applications(slug) WHERE status IN ('pending', 'accepting', 'accepted');
CREATE UNIQUE INDEX IF NOT EXISTS ux_business_contract ON businesses(enforcer_address) WHERE enforcer_address != '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_category_name ON categories(business_id, lower(name));
CREATE INDEX IF NOT EXISTS ix_pause_log_business ON pause_log(business_id, id);
CREATE INDEX IF NOT EXISTS ix_invoices_business ON invoices(business_id, id);
CREATE INDEX IF NOT EXISTS ix_vendors_business ON vendors(business_id, id);
"""

_PO_COLS = "id, business_id, po_number, vendor_id, amount_usdc, category"
_INV_COLS = ("id, business_id, invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, status, "
             "reasoning_hash, onchain_tx_id, created_at, origin")


async def _columns(db, table: str) -> list:
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        return [r[1] for r in await cur.fetchall()]


async def _migrate(db) -> None:
    """v1.2.8.1: databases made before multi-business gain business_id (everything so far is business 1) and PO and
    invoice numbers become unique per business instead of globally. Idempotent."""
    if "origin" not in await _columns(db, "invoices"):  # v1.2.7
        await db.execute("ALTER TABLE invoices ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
    for table in ("vendors", "receipts", "vendor_applications", "rejected_submissions", "admin_actions", "api_keys"):
        if "business_id" not in await _columns(db, table):
            await db.execute(f"ALTER TABLE {table} ADD COLUMN business_id INTEGER NOT NULL DEFAULT 1")
    if "business_id" not in await _columns(db, "purchase_orders"):
        await db.execute("DROP INDEX IF EXISTS ux_application_pending_wallet")
        await db.execute(
            "CREATE TABLE purchase_orders_new (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL DEFAULT 1, "
            "po_number TEXT NOT NULL, vendor_id INTEGER NOT NULL REFERENCES vendors(id), amount_usdc REAL NOT NULL, "
            "category INTEGER NOT NULL, UNIQUE (business_id, po_number))"
        )
        await db.execute(f"INSERT INTO purchase_orders_new ({_PO_COLS}) SELECT id, 1, po_number, vendor_id, amount_usdc, category FROM purchase_orders")
        await db.execute("DROP TABLE purchase_orders")
        await db.execute("ALTER TABLE purchase_orders_new RENAME TO purchase_orders")
    if "business_id" not in await _columns(db, "invoices"):
        await db.execute(
            "CREATE TABLE invoices_new (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER NOT NULL DEFAULT 1, "
            "invoice_number TEXT NOT NULL, vendor_id INTEGER NOT NULL REFERENCES vendors(id), po_id INTEGER REFERENCES purchase_orders(id), "
            "amount_usdc REAL NOT NULL, category INTEGER NOT NULL, doc_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', "
            "reasoning_hash TEXT, onchain_tx_id TEXT, created_at REAL NOT NULL, origin TEXT NOT NULL DEFAULT 'agent', "
            "UNIQUE (business_id, invoice_number))"
        )
        await db.execute(
            f"INSERT INTO invoices_new ({_INV_COLS}) SELECT id, 1, invoice_number, vendor_id, po_id, amount_usdc, category, "
            "doc_hash, status, reasoning_hash, onchain_tx_id, created_at, origin FROM invoices"
        )
        await db.execute("DROP TABLE invoices")
        await db.execute("ALTER TABLE invoices_new RENAME TO invoices")
    await db.execute("DROP INDEX IF EXISTS ux_application_pending_wallet")  # was unique per wallet, now per business
    for column, ddl in (("mode", "TEXT NOT NULL DEFAULT 'live'"), ("real_amount", "TEXT"), ("real_currency", "TEXT"),  # v1.2.8.3
                        ("submitted_by", "TEXT"), ("held_because", "TEXT"),  # v1.3.0
                        ("settled_at", "REAL")):  # v1.3.1
        if column not in await _columns(db, "invoices"):
            await db.execute(f"ALTER TABLE invoices ADD COLUMN {column} {ddl}")


async def _seed_home_business(db) -> None:
    """Business 1 is the live v1 contract, recorded in the database (not only in .env) so its address stays after a move."""
    async with db.execute("SELECT 1 FROM businesses WHERE id = ?", (HOME_BUSINESS_ID,)) as cur:
        if await cur.fetchone() is not None:
            return
    await db.execute(
        "INSERT INTO businesses (id, name, slug, enforcer_address, approver_address, agent_address, circle_wallet_id, "
        "contract_version, external, status, created_at) VALUES (?, 'CeedeBooks', 'ceedebooks', ?, ?, ?, ?, 1, 0, 'active', ?)",
        (HOME_BUSINESS_ID, config.budget_enforcer_address, config.approver_address, config.agent_address,
         config.circle_treasury_wallet_id, time.time()),
    )


async def _seed_home_categories(db) -> None:
    """The original business used categories 0 and 1 before names were stored; seed them so its history reads the same. Idempotent."""
    for category_id, name in HOME_SEED:
        await db.execute(
            "INSERT OR IGNORE INTO categories (business_id, id, name, created_at) VALUES (?, ?, ?, ?)",
            (HOME_BUSINESS_ID, category_id, name, time.time()),
        )


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.executescript(_TABLES)
        await _migrate(db)
        await db.executescript(_INDEXES)
        await _seed_home_business(db)
        await _seed_home_categories(db)
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


# ---- every query below is scoped: business_id is a required argument, so forgetting it is a TypeError, not a leak

async def _one(sql: str, args: tuple) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return await cur.fetchone()


async def _all(sql: str, args: tuple = ()) -> list:
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ---- businesses

async def get_business(business_id: int) -> Optional[dict]:
    row = await _one("SELECT * FROM businesses WHERE id = ?", (business_id,))
    return dict(row) if row else None


async def get_business_by_slug(slug: str) -> Optional[dict]:
    row = await _one("SELECT * FROM businesses WHERE slug = ?", (slug,))
    return dict(row) if row else None


async def list_businesses(status: Optional[str] = None) -> list:
    if status is None:
        return await _all("SELECT * FROM businesses ORDER BY id")
    return await _all("SELECT * FROM businesses WHERE status = ? ORDER BY id", (status,))


# ---- categories (names for the on-chain uint8 keys; each business has its own, and ids never change or get reused)

async def list_categories(business_id: int) -> list:
    return await _all("SELECT id, name FROM categories WHERE business_id = ? ORDER BY id", (business_id,))


async def get_category(business_id: int, category_id: int) -> Optional[dict]:
    row = await _one("SELECT id, name FROM categories WHERE business_id = ? AND id = ?", (business_id, category_id))
    return dict(row) if row else None


async def add_category(business_id: int, name: str) -> int:
    """Gives the business's next free id (lowest unused, 0 to 255) to a new name. Raises sqlite3.IntegrityError if the
    business already has that name (any case) and ValueError when all 256 ids are taken."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT id FROM categories WHERE business_id = ?", (business_id,)) as cur:
            taken = {r[0] for r in await cur.fetchall()}
        free = next((i for i in range(MAX_ID + 1) if i not in taken), None)
        if free is None:
            await db.rollback()
            raise ValueError("This business has used all 256 category ids")
        try:
            await db.execute("INSERT INTO categories (business_id, id, name, created_at) VALUES (?, ?, ?, ?)", (business_id, free, name, time.time()))
        except Exception:
            await db.rollback()
            raise
        await db.commit()
        return free


async def rename_category(business_id: int, category_id: int, name: str) -> bool:
    """Only the name changes. Raises sqlite3.IntegrityError on a duplicate name; returns False if this business has no such id."""
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute("UPDATE categories SET name = ? WHERE business_id = ? AND id = ?", (name, business_id, category_id))
        await db.commit()
        return cur.rowcount == 1


# ---- vendors, purchase orders, receipts, invoices

async def get_vendor(business_id: int, vendor_id: int) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM vendors WHERE id = ? AND business_id = ?", (vendor_id, business_id))


async def get_purchase_order(business_id: int, po_number: str) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM purchase_orders WHERE po_number = ? AND business_id = ?", (po_number, business_id))


async def get_purchase_order_by_id(business_id: int, po_id: int) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM purchase_orders WHERE id = ? AND business_id = ?", (po_id, business_id))


async def get_receipt(business_id: int, po_id: int) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM receipts WHERE po_id = ? AND business_id = ?", (po_id, business_id))


async def get_invoice(business_id: int, invoice_id: int) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM invoices WHERE id = ? AND business_id = ?", (invoice_id, business_id))


async def _insert(sql: str, args: tuple) -> int:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(sql, args)
        await db.commit()
        return cur.lastrowid


async def save_invoice(
    business_id: int, invoice_number: str, vendor_id: int, po_id: Optional[int], amount_usdc: float, category: int,
    doc_hash: str, origin: str = "agent", mode: str = "live", real_amount: Optional[str] = None, real_currency: Optional[str] = None,
    submitted_by: Optional[str] = None,
) -> int:
    """Raises sqlite3.IntegrityError if this business already has that invoice number. A shadow invoice must carry the real
    amount and currency; a live one must not."""
    if (mode == "shadow") != (real_amount is not None and real_currency is not None) or mode not in MODES:
        raise ValueError("shadow invoices need a real amount and currency; live invoices must not have them")
    return await _insert(
        "INSERT INTO invoices (business_id, invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, origin, mode, real_amount, "
        "real_currency, submitted_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (business_id, invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, origin, mode, real_amount, real_currency,
         submitted_by, time.time()),
    )


async def update_invoice_status(
    business_id: int, invoice_id: int, status: str, reasoning_hash: Optional[str] = None, onchain_tx_id: Optional[str] = None,
    held_because: Optional[str] = None,
) -> None:
    """held_because marks a hold with its one cause ('paused'); any other status clears it. settled_at is stamped once,
    only when this call records a confirmed payment of its own (status paid with its transaction id); a row that is only
    re-marked paid because the chain already had the payment gets no time, so the timing metric never counts it."""
    stamp = time.time() if status == "paid" and onchain_tx_id else None
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            """UPDATE invoices
               SET status = ?, reasoning_hash = COALESCE(?, reasoning_hash), onchain_tx_id = COALESCE(?, onchain_tx_id),
                   held_because = ?, settled_at = COALESCE(settled_at, ?)
               WHERE id = ? AND business_id = ?""",
            (status, reasoning_hash, onchain_tx_id, held_because if status == "held" else None, stamp, invoice_id, business_id),
        )
        await db.commit()


async def save_vendor(business_id: int, name: str, wallet_address: str) -> int:
    return await _insert("INSERT INTO vendors (business_id, name, wallet_address) VALUES (?, ?, ?)", (business_id, name, wallet_address))


async def update_vendor_wallet(business_id: int, vendor_id: int, new_wallet_address: str) -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "UPDATE vendors SET previous_wallet_address = wallet_address, wallet_address = ? WHERE id = ? AND business_id = ?",
            (new_wallet_address, vendor_id, business_id),
        )
        await db.commit()


async def save_purchase_order(business_id: int, po_number: str, vendor_id: int, amount_usdc: float, category: int) -> int:
    """Raises sqlite3.IntegrityError if this business already has that PO number."""
    return await _insert(
        "INSERT INTO purchase_orders (business_id, po_number, vendor_id, amount_usdc, category) VALUES (?, ?, ?, ?, ?)",
        (business_id, po_number, vendor_id, amount_usdc, category),
    )


async def save_receipt(business_id: int, po_id: int, confirmed_by: str, confirmed_by_role: str) -> int:
    return await _insert(
        "INSERT INTO receipts (business_id, po_id, confirmed_by, confirmed_by_role, confirmed_at) VALUES (?, ?, ?, ?, ?)",
        (business_id, po_id, confirmed_by, confirmed_by_role, time.time()),
    )


# ---- API keys (a key belongs to one business; looked up by hash, which is global)

async def save_api_key(business_id: int, key_hash: str, role: str, vendor_id: Optional[int], label: str) -> int:
    return await _insert(
        "INSERT INTO api_keys (business_id, key_hash, role, vendor_id, label, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (business_id, key_hash, role, vendor_id, label, time.time()),
    )


async def get_api_key_by_hash(key_hash: str) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,))


async def revoke_api_key(key_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
        await db.commit()
        return cur.rowcount > 0


# ---- treasury and admin

async def paid_total_since(business_id: int, since_ts: float) -> float:
    """Total USDC of this business's invoices with status 'paid' created at or after since_ts."""
    row = await _one(
        "SELECT COALESCE(SUM(amount_usdc), 0) AS total FROM invoices WHERE status = 'paid' AND created_at >= ? AND business_id = ?",
        (since_ts, business_id),
    )
    return float(row["total"])


async def log_admin_action(business_id: int, admin_address: str, action: str, ref: Optional[str] = None,
                           tx_hash: Optional[str] = None, block: Optional[int] = None) -> int:
    """Raises sqlite3.IntegrityError if this transaction hash was already recorded."""
    return await _insert(
        "INSERT INTO admin_actions (business_id, admin_address, action, ref, tx_hash, block, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (business_id, admin_address, action, ref, tx_hash, block, time.time()),
    )


async def list_admin_actions(business_id: int, limit: int = 100) -> list:
    return await _all("SELECT * FROM admin_actions WHERE business_id = ? ORDER BY id DESC LIMIT ?", (business_id, limit))


async def list_vendors(business_id: int) -> list:
    return await _all("SELECT id, name, wallet_address FROM vendors WHERE business_id = ? ORDER BY id", (business_id,))


async def list_purchase_orders(business_id: int, limit: int = 100) -> list:
    return await _all(
        """SELECT p.id, p.po_number, p.vendor_id, v.name AS vendor_name, p.amount_usdc, p.category,
                  EXISTS(SELECT 1 FROM receipts r WHERE r.po_id = p.id AND r.business_id = p.business_id) AS received
           FROM purchase_orders p JOIN vendors v ON v.id = p.vendor_id AND v.business_id = p.business_id
           WHERE p.business_id = ? ORDER BY p.id DESC LIMIT ?""",
        (business_id, limit),
    )


async def list_invoices(business_id: int, limit: int = 50) -> list:
    return await _all(
        """SELECT i.id, i.invoice_number, i.vendor_id, v.name AS vendor_name, i.amount_usdc, i.category,
                  i.status, i.reasoning_hash, i.created_at, i.mode, i.real_amount, i.real_currency, i.submitted_by, i.held_because, i.origin, d.verdict
           FROM invoices i JOIN vendors v ON v.id = i.vendor_id AND v.business_id = i.business_id
           LEFT JOIN decision_verdicts d ON d.invoice_id = i.id AND d.business_id = i.business_id
           WHERE i.business_id = ? ORDER BY i.id DESC LIMIT ?""",
        (business_id, limit),
    )


# ---- demo data (v1.3.1.1): sample records for an owner to try the flow. Every invoice is origin `demo`, never traction.

DEMO_PREFIX = "DEMO-"
DEMO_VENDOR = "Demo Supplies (demo)"


async def demo_invoices_since(business_id: int, since_ts: float) -> int:
    row = await _one(
        "SELECT COUNT(*) AS n FROM invoices WHERE business_id = ? AND origin = 'demo' AND substr(invoice_number, 1, ?) = ? AND created_at >= ?",
        (business_id, len(DEMO_PREFIX), DEMO_PREFIX, since_ts))
    return int(row["n"])


async def demo_runs(business_id: int) -> int:
    row = await _one("SELECT COUNT(*) AS n FROM purchase_orders WHERE business_id = ? AND substr(po_number, 1, ?) = ?",
                     (business_id, len(DEMO_PREFIX), DEMO_PREFIX))
    return int(row["n"])


async def demo_vendor(business_id: int, wallet: str) -> int:
    """The business's demo vendor, paid at the owner's own wallet; created once and reused."""
    row = await _one("SELECT id FROM vendors WHERE business_id = ? AND name = ? AND lower(wallet_address) = ?",
                     (business_id, DEMO_VENDOR, wallet.lower()))
    return int(row["id"]) if row else await save_vendor(business_id, DEMO_VENDOR, wallet)


async def invoice_counts(business_id: int) -> dict:
    rows = await _all("SELECT status, COUNT(*) AS n FROM invoices WHERE business_id = ? GROUP BY status", (business_id,))
    return {r["status"]: r["n"] for r in rows}


# ---- vendor portal (v1.2.7)

ORIGINS = ("agent", "manual", "demo")
MODES = ("live", "shadow")
# The public aggregate leaves out archived businesses; a read scoped to one business is never filtered.
_NOT_ARCHIVED = "AND business_id NOT IN (SELECT id FROM businesses WHERE status = 'archived')"


async def get_vendor_by_wallet(business_id: int, wallet: str) -> Optional[aiosqlite.Row]:
    return await _one(
        "SELECT * FROM vendors WHERE lower(wallet_address) = ? AND business_id = ? ORDER BY id LIMIT 1", (wallet.lower(), business_id)
    )


async def list_vendor_purchase_orders(business_id: int, vendor_id: int) -> list:
    """A vendor's own POs only. `received`: a receipt is on file. `invoiced`: an invoice already exists for it."""
    return await _all(
        """SELECT p.id, p.po_number, p.amount_usdc, p.category,
                  EXISTS(SELECT 1 FROM receipts r WHERE r.po_id = p.id AND r.business_id = p.business_id) AS received,
                  EXISTS(SELECT 1 FROM invoices i WHERE i.po_id = p.id AND i.business_id = p.business_id) AS invoiced
           FROM purchase_orders p WHERE p.vendor_id = ? AND p.business_id = ? ORDER BY p.id DESC LIMIT 100""",
        (vendor_id, business_id),
    )


async def list_vendor_invoices(business_id: int, vendor_id: int, limit: int = 50) -> list:
    return await _all(
        """SELECT id, invoice_number, amount_usdc, category, status, reasoning_hash, created_at
           FROM invoices WHERE vendor_id = ? AND business_id = ? ORDER BY id DESC LIMIT ?""",
        (vendor_id, business_id, limit),
    )


async def save_application(business_id: int, business_name: str, wallet: str, contact: Optional[str], ip_hash: str) -> int:
    """Raises sqlite3.IntegrityError if this wallet already has a pending application for this business."""
    return await _insert(
        "INSERT INTO vendor_applications (business_id, business_name, wallet, contact, ip_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (business_id, business_name, wallet.lower(), contact, ip_hash, time.time()),
    )


async def pending_applications_from(ip_hash: str) -> int:
    """Anti-spam only: counts one client's waiting applications across all businesses. Never returned to anyone."""
    row = await _one("SELECT COUNT(*) AS n FROM vendor_applications WHERE status = 'pending' AND ip_hash = ?", (ip_hash,))
    return int(row["n"])


async def count_pending_applications(business_id: int) -> int:
    row = await _one("SELECT COUNT(*) AS n FROM vendor_applications WHERE status = 'pending' AND business_id = ?", (business_id,))
    return int(row["n"])


async def list_applications(business_id: int, limit: int = 100) -> list:
    """Private: admin only. The ip hash is never returned."""
    return await _all(
        """SELECT id, business_name, wallet, contact, status, vendor_id, created_at, decided_at
           FROM vendor_applications WHERE business_id = ? ORDER BY (status = 'pending') DESC, id DESC LIMIT ?""",
        (business_id, limit),
    )


async def get_application(business_id: int, application_id: int) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM vendor_applications WHERE id = ? AND business_id = ?", (application_id, business_id))


async def accept_application(business_id: int, application_id: int) -> Optional[int]:
    """Creates the vendor and marks the application accepted in one transaction. Returns the new vendor id, or None if
    the application is not pending in this business any more (so two admin clicks cannot create two vendors)."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT * FROM vendor_applications WHERE id = ? AND business_id = ? AND status = 'pending'", (application_id, business_id)
        ) as cur:
            app_row = await cur.fetchone()
        if app_row is None:
            await db.rollback()
            return None
        cur = await db.execute(
            "INSERT INTO vendors (business_id, name, wallet_address) VALUES (?, ?, ?)",
            (business_id, app_row["business_name"], app_row["wallet"]),
        )
        vendor_id = cur.lastrowid
        await db.execute(
            "UPDATE vendor_applications SET status = 'accepted', vendor_id = ?, decided_at = ? WHERE id = ? AND business_id = ?",
            (vendor_id, time.time(), application_id, business_id),
        )
        await db.commit()
        return vendor_id


async def reject_application(business_id: int, application_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "UPDATE vendor_applications SET status = 'rejected', decided_at = ? WHERE id = ? AND business_id = ? AND status = 'pending'",
            (time.time(), application_id, business_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def log_rejected_submission(business_id: int, reason: str, origin: str = "agent") -> None:
    """Counts only (no vendor, amount or invoice data): feeds the 'duplicates caught' metric."""
    await _insert(
        "INSERT INTO rejected_submissions (business_id, reason, origin, created_at) VALUES (?, ?, ?, ?)",
        (business_id, reason, origin, time.time()),
    )


async def submission_stats(business_id: Optional[int] = None) -> dict:
    """Invoices processed, USDC paid and duplicates caught, split by origin. All three origins are always present.
    Live invoices only: shadow invoices (a real bill mirrored as a testnet payment) are reported apart, see shadow_stats.
    business_id=None is the public aggregate over every business (counts only); a number scopes it to one business."""
    out = {o: {"invoices_processed": 0, "payment_volume_usdc": 0.0, "duplicates_caught": 0} for o in ORIGINS}
    where, args = ("AND business_id = ?", (business_id,)) if business_id is not None else (_NOT_ARCHIVED, ())
    inv = await _all(
        "SELECT origin, COUNT(*) AS n, COALESCE(SUM(CASE WHEN status = 'paid' THEN amount_usdc ELSE 0 END), 0) AS volume "
        f"FROM invoices WHERE status IN ('paid', 'held', 'escalated', 'rejected') AND mode = 'live' {where} GROUP BY origin", args,
    )
    for r in inv:
        if r["origin"] in out:
            out[r["origin"]]["invoices_processed"], out[r["origin"]]["payment_volume_usdc"] = r["n"], round(float(r["volume"]), 6)
    dup = await _all(
        f"SELECT origin, COUNT(*) AS n FROM rejected_submissions WHERE reason = 'duplicate_invoice' {where} GROUP BY origin", args
    )
    for r in dup:
        if r["origin"] in out:
            out[r["origin"]]["duplicates_caught"] = r["n"]
    total = {k: sum(out[o][k] for o in ORIGINS) for k in ("invoices_processed", "payment_volume_usdc", "duplicates_caught")}
    total["payment_volume_usdc"] = round(total["payment_volume_usdc"], 6)
    return {**total, "by_origin": out}


# ---- business onboarding (Phase L3). Applications are private; a business is public only once it is active.

async def save_business_application(name: str, slug: str, owner_wallet: str, contact: Optional[str], ip_hash: str) -> int:
    """Raises sqlite3.IntegrityError if the wallet already has a pending application or the slug is taken by an open one."""
    return await _insert(
        "INSERT INTO business_applications (name, slug, owner_wallet, contact, ip_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, slug, owner_wallet.lower(), contact, ip_hash, time.time()),
    )


async def slug_in_use(slug: str) -> bool:
    row = await _one(
        "SELECT (SELECT COUNT(*) FROM businesses WHERE slug = ?) + (SELECT COUNT(*) FROM business_applications "
        "WHERE slug = ? AND status IN ('pending', 'accepting', 'accepted')) AS n", (slug, slug))
    return int(row["n"]) > 0


async def pending_business_applications_from(ip_hash: str) -> int:
    row = await _one("SELECT COUNT(*) AS n FROM business_applications WHERE status = 'pending' AND ip_hash = ?", (ip_hash,))
    return int(row["n"])


async def get_business_application(application_id: int) -> Optional[dict]:
    row = await _one("SELECT * FROM business_applications WHERE id = ?", (application_id,))
    return dict(row) if row else None


async def list_business_applications(owner_wallet: Optional[str] = None, limit: int = 100) -> list:
    """Private. With a wallet: only that owner's applications. Without: everything, for the operator. No ip hash ever."""
    cols = "id, name, slug, owner_wallet, contact, status, agent_address, business_id, created_at, decided_at"
    if owner_wallet is not None:
        return await _all(f"SELECT {cols} FROM business_applications WHERE owner_wallet = ? ORDER BY id DESC LIMIT ?", (owner_wallet.lower(), limit))
    return await _all(f"SELECT {cols} FROM business_applications ORDER BY (status = 'pending') DESC, id DESC LIMIT ?", (limit,))


async def claim_business_application(application_id: int) -> Optional[dict]:
    """pending -> accepting, once. Returns the application, or None if it is not pending (a double click claims nothing)."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("UPDATE business_applications SET status = 'accepting' WHERE id = ? AND status = 'pending'", (application_id,))
        await db.commit()
        if cur.rowcount == 0:
            return None
    return await get_business_application(application_id)


async def release_business_application(application_id: int) -> None:
    """accepting -> pending, after a failure before anything was kept (the wallet stored first is reused on the next try)."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("UPDATE business_applications SET status = 'pending' WHERE id = ? AND status = 'accepting'", (application_id,))
        await db.commit()


async def store_application_agent(application_id: int, wallet_id: str, address: str) -> None:
    """Kept immediately after the Circle wallet exists, so a crash before the accept finishes never creates a second one."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("UPDATE business_applications SET agent_wallet_id = ?, agent_address = ? WHERE id = ?", (wallet_id, address, application_id))
        await db.commit()


async def finish_accepting(application_id: int) -> Optional[int]:
    """accepting -> accepted and the pending business row, in one transaction. Returns the new business id."""
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT * FROM business_applications WHERE id = ? AND status = 'accepting'", (application_id,)) as cur:
            app_row = await cur.fetchone()
        if app_row is None or not app_row["agent_address"]:
            await db.rollback()
            return None
        cur = await db.execute(
            "INSERT INTO businesses (name, slug, enforcer_address, approver_address, agent_address, circle_wallet_id, contract_version, "
            "external, status, created_at) VALUES (?, ?, '', ?, ?, ?, 2, 0, 'pending', ?)",
            (app_row["name"], app_row["slug"], app_row["owner_wallet"], app_row["agent_address"], app_row["agent_wallet_id"], time.time()),
        )
        business_id = cur.lastrowid
        await db.execute("UPDATE business_applications SET status = 'accepted', business_id = ?, decided_at = ? WHERE id = ?",
                         (business_id, time.time(), application_id))
        await db.commit()
        return business_id


async def reject_business_application(application_id: int) -> bool:
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute("UPDATE business_applications SET status = 'rejected', decided_at = ? WHERE id = ? AND status = 'pending'",
                               (time.time(), application_id))
        await db.commit()
        return cur.rowcount > 0


async def activate_business(business_id: int, application_id: int, enforcer: str, approver: str, tx_hash: str) -> bool:
    """Called only after the chain has proven the contract (agent.contract.verify_business_creation). One transaction:
    the business gets its contract and goes active, the application is registered. False if it was not waiting for this."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "UPDATE businesses SET enforcer_address = ?, approver_address = ?, created_tx_hash = ?, status = 'active' "
            "WHERE id = ? AND status = 'pending' AND enforcer_address = ''", (enforcer, approver, tx_hash, business_id))
        if cur.rowcount == 0:
            await db.rollback()
            return False
        await db.execute("UPDATE business_applications SET status = 'registered' WHERE id = ? AND status = 'accepted'", (application_id,))
        await db.commit()
        return True


async def set_business_external(business_id: int, external: bool) -> bool:
    """Operator only. The home business can never be marked external (it is the operator's own)."""
    if business_id == HOME_BUSINESS_ID:
        return False
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute("UPDATE businesses SET external = ? WHERE id = ? AND status = 'active'", (1 if external else 0, business_id))
        await db.commit()
        return cur.rowcount > 0


async def set_business_archived(business_id: int, archived: bool) -> bool:
    """Operator only. Archived is the hidden, read-only state: no new activity (see _writable in main.py), gone from the public
    list and the public totals, but its records stay readable by slug and every decision still verifies. active <-> archived
    only (a business that is not live yet has nothing to hide), and the home business can never be archived."""
    if business_id == HOME_BUSINESS_ID:
        return False
    before, after = ("active", "archived") if archived else ("archived", "active")
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute("UPDATE businesses SET status = ? WHERE id = ? AND status = ?", (after, business_id, before))
        await db.commit()
        return cur.rowcount > 0


async def list_public_businesses() -> list:
    """Active businesses only. Everything here is already public on-chain, which is the point (judges check it on the explorer)."""
    return await _all(
        "SELECT id, name, slug, enforcer_address AS contract, agent_address, approver_address AS approver, contract_version, external, "
        "created_tx_hash, created_at FROM businesses WHERE status = 'active' ORDER BY id")


async def refresh_approver(business_id: int, approver: str) -> None:
    """Keep the stored approver equal to what the contract says (it changes on a hand-over). Display only; nothing authorises from it."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("UPDATE businesses SET approver_address = ? WHERE id = ? AND approver_address != ?", (approver, business_id, approver))
        await db.commit()


async def business_counts() -> dict:
    row = await _one("SELECT COUNT(*) AS total, COALESCE(SUM(external), 0) AS external FROM businesses WHERE status = 'active'", ())
    return {"total": int(row["total"]), "external": int(row["external"])}


async def archived_count() -> int:
    """How many businesses are archived (hidden). Only the number is public, never a name."""
    row = await _one("SELECT COUNT(*) AS n FROM businesses WHERE status = 'archived'", ())
    return int(row["n"])


# ---- shadow mode (v1.2.8.3): a real bill is read, decided and, once the owner approves, mirrored as a testnet payment

async def claim_invoice_status(business_id: int, invoice_id: int, from_status: str, to_status: str) -> bool:
    """from_status -> to_status in one statement, so two clicks can never both act on the same invoice."""
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "UPDATE invoices SET status = ? WHERE id = ? AND business_id = ? AND status = ?",
            (to_status, invoice_id, business_id, from_status),
        )
        await db.commit()
        return cur.rowcount == 1


async def claim_paused_hold(business_id: int, invoice_id: int) -> bool:
    """held (because payments were paused) -> retrying, atomically. A hold for any other reason is never claimed here."""
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "UPDATE invoices SET status = 'retrying', held_because = NULL WHERE id = ? AND business_id = ? AND status = 'held' "
            "AND held_because = 'paused'", (invoice_id, business_id),
        )
        await db.commit()
        return cur.rowcount == 1


async def save_pause_event(business_id: int, paused: bool, reason: str, admin_address: str, tx_hash: str,
                           reasoning_hash: Optional[str]) -> int:
    return await _insert(
        "INSERT INTO pause_log (business_id, paused, reason, admin_address, tx_hash, reasoning_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (business_id, int(paused), reason, admin_address.lower(), tx_hash.lower(), reasoning_hash, time.time()),
    )


async def last_pause_event(business_id: int) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM pause_log WHERE business_id = ? ORDER BY id DESC LIMIT 1", (business_id,))


async def claim_shadow_approval(business_id: int, invoice_id: int) -> bool:
    """awaiting_owner -> approving, atomically, so two clicks can never start two payments."""
    async with aiosqlite.connect(config.db_path) as db:
        cur = await db.execute(
            "UPDATE invoices SET status = 'approving' WHERE id = ? AND business_id = ? AND mode = 'shadow' AND status = 'awaiting_owner'",
            (invoice_id, business_id),
        )
        await db.commit()
        return cur.rowcount == 1


async def release_shadow_approval(business_id: int, invoice_id: int) -> None:
    """approving -> awaiting_owner after a failed payment attempt, so the owner can try again."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "UPDATE invoices SET status = 'awaiting_owner' WHERE id = ? AND business_id = ? AND status = 'approving'", (invoice_id, business_id)
        )
        await db.commit()


async def save_verdict(business_id: int, invoice_id: int, reasoning_hash: str, verdict: str, admin_address: str) -> int:
    """One verdict per decision, final once recorded. Raises sqlite3.IntegrityError on a second one."""
    return await _insert(
        "INSERT INTO decision_verdicts (business_id, invoice_id, reasoning_hash, verdict, admin_address, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (business_id, invoice_id, reasoning_hash, verdict, admin_address.lower(), time.time()),
    )


async def get_invoice_by_decision(business_id: int, reasoning_hash: str) -> Optional[aiosqlite.Row]:
    return await _one("SELECT * FROM invoices WHERE reasoning_hash = ? AND business_id = ?", (reasoning_hash, business_id))


async def shadow_stats(business_id: Optional[int] = None) -> dict:
    """Shadow invoices only, never summed with live ones. Agreement counts only verdicts the owner actually recorded."""
    where, args = ("AND business_id = ?", (business_id,)) if business_id is not None else (_NOT_ARCHIVED, ())
    rows = await _all(f"SELECT status, COUNT(*) AS n, COALESCE(SUM(amount_usdc), 0) AS usdc FROM invoices WHERE mode = 'shadow' {where} GROUP BY status", args)
    by = {r["status"]: r for r in rows}
    paid = by.get("paid", {"n": 0, "usdc": 0})
    real = {}
    for r in await _all(
        f"SELECT real_amount, real_currency FROM invoices WHERE mode = 'shadow' AND status = 'paid' {where}", args
    ):
        real[r["real_currency"]] = real.get(r["real_currency"], Decimal(0)) + Decimal(r["real_amount"])
    votes = {r["verdict"]: r["n"] for r in await _all(
        f"SELECT verdict, COUNT(*) AS n FROM decision_verdicts WHERE 1 = 1 {where} GROUP BY verdict", args)}
    agree, disagree = votes.get("agree", 0), votes.get("disagree", 0)
    n = agree + disagree
    return {
        "paid": paid["n"], "held": by.get("held", {"n": 0})["n"], "escalated": by.get("escalated", {"n": 0})["n"],
        "awaiting_owner": by.get("awaiting_owner", {"n": 0})["n"] + by.get("approving", {"n": 0})["n"],
        "rejected": by.get("rejected", {"n": 0})["n"],
        "mirrored_usdc": round(float(paid["usdc"]), 6),
        "real_totals": {cur: format(total, "f") for cur, total in sorted(real.items())},
        "agreement": {"agree": agree, "disagree": disagree, "n": n, "rate": round(agree / n, 4) if n else None},
    }


async def live_counts(business_id: int) -> dict:
    """Live (non-shadow) invoice outcomes for one business, from the invoices table."""
    rows = await _all(
        "SELECT status, COUNT(*) AS n, COALESCE(SUM(amount_usdc), 0) AS usdc FROM invoices WHERE mode = 'live' AND business_id = ? GROUP BY status",
        (business_id,),
    )
    by = {r["status"]: r for r in rows}
    return {"paid": by.get("paid", {"n": 0})["n"], "held": by.get("held", {"n": 0})["n"], "escalated": by.get("escalated", {"n": 0})["n"],
            "volume_usdc": round(float(by.get("paid", {"usdc": 0})["usdc"]), 6)}


async def latest_paid(business_id: int, limit: int = 10) -> list:
    """The newest paid decisions with what a judge needs to check them. No contacts, no private fields."""
    return await _all(
        """SELECT i.invoice_number, i.amount_usdc, i.mode, i.real_amount, i.real_currency, i.reasoning_hash,
                  a.chain_tx_hash, a.timestamp
           FROM invoices i LEFT JOIN audit_log a ON a.reasoning_hash = i.reasoning_hash
           WHERE i.business_id = ? AND i.status = 'paid' ORDER BY i.id DESC LIMIT ?""",
        (business_id, limit),
    )


# ---- relabelling test runs (scripts/relabel_origin.py): rule 14, a payment to a wallet the owner controls is `demo`, not `agent`

async def list_relabel_candidates(business_id: int, prefix: str, wallets: Optional[list] = None) -> list:
    """Live invoices of this business still labelled `agent` whose number starts with `prefix`, newest last. With `wallets`,
    only those whose vendor's wallet (current or previous) is one of them. Read-only."""
    rows = await _all(
        """SELECT i.id, i.invoice_number, i.status, i.amount_usdc, i.created_at, v.name AS vendor_name,
                  v.wallet_address, v.previous_wallet_address
           FROM invoices i JOIN vendors v ON v.id = i.vendor_id AND v.business_id = i.business_id
           WHERE i.business_id = ? AND i.origin = 'agent' AND i.mode = 'live' AND substr(i.invoice_number, 1, ?) = ?
           ORDER BY i.id""",
        (business_id, len(prefix), prefix),
    )
    if wallets is None:
        return rows
    mine = {w.lower() for w in wallets}
    return [r for r in rows if r["wallet_address"].lower() in mine or (r["previous_wallet_address"] or "").lower() in mine]


async def relabel_agent_to_demo(business_id: int, invoice_ids: list) -> int:
    """agent -> demo for exactly these invoices of this business, all or nothing. Returns how many changed; if any id is not
    a live `agent` invoice of this business, nothing changes and 0 is returned."""
    ids = sorted(set(invoice_ids))
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            f"UPDATE invoices SET origin = 'demo' WHERE business_id = ? AND origin = 'agent' AND mode = 'live' AND id IN ({marks})",
            (business_id, *ids),
        )
        if cur.rowcount != len(ids):
            await db.rollback()
            return 0
        await db.commit()
        return cur.rowcount
