"""Off-chain audit log: every agent decision, hashed before any on-chain action."""
import hashlib
import json
import time
from typing import Optional

import aiosqlite

from agent import ledger
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
    chain_tx_hash TEXT,
    business_id INTEGER NOT NULL DEFAULT 1,
    hash_version INTEGER NOT NULL DEFAULT 1,
    contract_address TEXT,
    contract_version INTEGER,
    rules_action TEXT,
    model_action TEXT,
    agreed INTEGER,
    model_confidence REAL,
    contract_refused INTEGER
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
        async with db.execute("PRAGMA table_info(audit_log)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "chain_tx_hash" not in columns:  # databases created before v1.3.0
            await db.execute("ALTER TABLE audit_log ADD COLUMN chain_tx_hash TEXT")
        if "business_id" not in columns:  # v1.2.8.1: existing rows are business 1, hash format 1, on the v1 contract
            await db.execute("ALTER TABLE audit_log ADD COLUMN business_id INTEGER NOT NULL DEFAULT 1")
            await db.execute("ALTER TABLE audit_log ADD COLUMN hash_version INTEGER NOT NULL DEFAULT 1")
            await db.execute("ALTER TABLE audit_log ADD COLUMN contract_address TEXT")
            await db.execute("ALTER TABLE audit_log ADD COLUMN contract_version INTEGER")
        if "rules_action" not in columns:  # v1.2.9.3: model-vs-rules columns; NULL means not compared (never a success)
            for col in ("rules_action TEXT", "model_action TEXT", "agreed INTEGER", "model_confidence REAL", "contract_refused INTEGER"):
                await db.execute(f"ALTER TABLE audit_log ADD COLUMN {col}")
        await db.execute("CREATE INDEX IF NOT EXISTS ix_audit_business ON audit_log(business_id, id)")
        await ledger.ensure_schema(db)
        await db.commit()


HASH_VERSION = 2
HASH_VERSION_COMPARED = 3  # adds the rules-versus-model comparison; formats 1 and 2 still verify
_VISIBLE_AND = "AND business_id NOT IN (SELECT id FROM businesses WHERE status = 'archived') "
_VISIBLE_WHERE = "WHERE business_id NOT IN (SELECT id FROM businesses WHERE status = 'archived') "


def compute_reasoning_hash(
    action: str, subject: str, reasoning: str, amount: float, model_used: str, timestamp: float, business: Optional[dict] = None,
    comparison: Optional[dict] = None,
) -> tuple[str, str]:
    """Hash format 1 (no business) is what every row before v1.2.8.1 used and still verifies. Format 2 also binds the
    business and its contract, so the same invoice number in two businesses can never produce the same record.
    Format 3 adds the rules-versus-model comparison."""
    payload = {
        "action": action,
        "subject": subject,
        "reasoning": reasoning,
        "amount_usdc": amount,
        "model_used": model_used,
        "timestamp": timestamp,
    }
    if business is not None:
        payload.update(hash_version=HASH_VERSION, business_id=business["id"], enforcer_address=business["enforcer_address"].lower())
        if comparison is not None:
            payload.update(hash_version=HASH_VERSION_COMPARED, comparison=comparison)
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
    *,
    business: dict,
    comparison: Optional[dict] = None,
) -> str:
    """Writes the audit row (hash format 2) for one business before anything touches its contract. The row and its signed
    ledger entry go in one transaction under the database write lock: both are stored or neither is, and concurrent writers
    queue behind each other, so the chain cannot fork. Without a usable ledger key this raises and nothing is logged."""
    timestamp = time.time()
    hash_input, reasoning_hash = compute_reasoning_hash(
        action, subject, reasoning, amount, model_used, timestamp, business, comparison
    )
    version = HASH_VERSION_COMPARED if comparison is not None else HASH_VERSION
    cmp = comparison or {}
    contract_address = business["enforcer_address"].lower()

    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(_SCHEMA)
        await ledger.ensure_schema(db)
        await db.execute("BEGIN IMMEDIATE")
        try:
            await ledger.ensure_chain(db, business["id"])
            await db.execute(
                """INSERT INTO audit_log
                   (action, subject, reasoning, amount_usdc, treasury_balance_after,
                    model_used, timestamp, hash_input, reasoning_hash, business_id, hash_version, contract_address, contract_version,
                    rules_action, model_action, agreed, model_confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (action, subject, reasoning, amount, treasury_balance_after, model_used, timestamp, hash_input, reasoning_hash,
                 business["id"], version, contract_address, business["contract_version"],
                 cmp.get("rules_action"), cmp.get("model_action"), None if cmp.get("agreed") is None else int(cmp["agreed"]),
                 cmp.get("model_confidence")),
            )
            await ledger.append_decision(db, business["id"], {
                "reasoning_hash": reasoning_hash, "action": action, "subject": subject, "amount_usdc": amount,
                "model_used": model_used, "timestamp": timestamp, "contract_address": contract_address,
            })
            await db.commit()
        except BaseException:
            await db.rollback()
            raise

    return reasoning_hash


async def record_onchain_tx(reasoning_hash: str, tx_id: str, chain_tx_hash: Optional[str] = None) -> None:
    """tx_id is the Circle transaction id; chain_tx_hash is the on-chain hash anyone can look up (may be unknown)."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute(
            "UPDATE audit_log SET onchain_tx_id = ?, chain_tx_hash = COALESCE(?, chain_tx_hash) WHERE reasoning_hash = ?",
            (tx_id, chain_tx_hash, reasoning_hash),
        )
        await db.commit()


async def mark_contract_refused(reasoning_hash: str) -> None:
    """The contract reverted the payment this decision led to. Set after the fact, so it is a column and not part of the hash."""
    async with aiosqlite.connect(config.db_path) as db:
        await db.execute("UPDATE audit_log SET contract_refused = 1 WHERE reasoning_hash = ?", (reasoning_hash,))
        await db.commit()


async def comparison_metrics(business_id: Optional[int] = None) -> dict:
    """Rules-versus-model counts over decisions that carry a comparison (hash format 3). `compared` counts only decisions a
    model answered; agreement is None when none were compared (rule 19). Live and shadow decisions are kept apart and split by
    origin and by business. business_id=None is the public aggregate (archived businesses left out); a number scopes it."""
    scope, args = ("AND a.business_id = ? ", (business_id,)) if business_id is not None else (
        "AND a.business_id NOT IN (SELECT id FROM businesses WHERE status = 'archived') ", ())
    sql = (
        "SELECT a.business_id AS business_id, COALESCE(i.mode, 'live') AS mode, COALESCE(i.origin, 'agent') AS origin, "
        "COUNT(*) AS total, SUM(a.model_action IS NOT NULL) AS compared, SUM(a.agreed = 1) AS agreed, "
        "SUM(a.agreed = 0) AS disagreed, SUM(a.contract_refused = 1) AS contract_refused "
        "FROM audit_log a LEFT JOIN invoices i ON i.business_id = a.business_id AND i.invoice_number = a.subject "
        "WHERE a.hash_version = 3 " + scope + "GROUP BY a.business_id, mode, origin"
    )
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, args) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    def blank():
        return {"total": 0, "compared": 0, "agreed": 0, "disagreed": 0, "contract_refused": 0}

    def add(bucket, r):
        for k in blank():
            bucket[k] += int(r[k] or 0)

    def finish(bucket):
        bucket["agreement_rate"] = round(bucket["agreed"] / bucket["compared"], 4) if bucket["compared"] else None
        return bucket

    out = {m: {**blank(), "by_origin": {o: blank() for o in ("agent", "manual", "demo")}, "by_business": {}} for m in ("live", "shadow")}
    for r in rows:
        group = out[r["mode"] if r["mode"] in out else "live"]
        add(group, r)
        if r["origin"] in group["by_origin"]:
            add(group["by_origin"][r["origin"]], r)
        add(group["by_business"].setdefault(r["business_id"], blank()), r)
    for group in out.values():
        finish(group)
        for b in list(group["by_origin"].values()) + list(group["by_business"].values()):
            finish(b)
    return out


async def recent(limit: int = 20, business_id: Optional[int] = None) -> list:
    """Newest first. business_id=None is the public aggregate; a number scopes it to one business."""
    sql = ("SELECT reasoning_hash, action, subject, model_used, amount_usdc, timestamp, chain_tx_hash, business_id{demo} FROM audit_log "
           "{where}ORDER BY id DESC LIMIT ?")
    demo = (", reasoning_hash IN (SELECT reasoning_hash FROM invoices WHERE origin = 'demo' AND reasoning_hash IS NOT NULL) AS is_demo")
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            if business_id is not None:
                cursor = await db.execute(sql.format(demo=demo, where="WHERE business_id = ? "), (business_id, limit))
            else:  # the public aggregate leaves out archived businesses
                cursor = await db.execute(sql.format(demo=demo, where=_VISIBLE_WHERE), (limit,))
        except aiosqlite.OperationalError:  # no businesses or invoices table (audit log used on its own)
            if business_id is not None:
                cursor = await db.execute(sql.format(demo="", where="WHERE business_id = ? "), (business_id, limit))
            else:
                cursor = await db.execute(sql.format(demo="", where=""), (limit,))
        async with cursor:
            return [{**dict(row), **({"is_demo": bool(row["is_demo"])} if "is_demo" in row.keys() else {})} for row in await cursor.fetchall()]


async def stats(business_id: Optional[int] = None) -> dict:
    """Counts of live agent decisions. Manual entries (model_used = 'manual') are reported separately, never as agent decisions.
    Shadow decisions (a real bill mirrored as a testnet payment, v1.2.8.3) and demo-run decisions (v1.3.1.1) are left out: shadow
    is reported apart by models.shadow_stats, demo is never counted as agent traffic. business_id=None is the public aggregate; a number scopes it to one business."""
    scope, args = ("AND business_id = ? ", (business_id,)) if business_id is not None else (_VISIBLE_AND, ())
    shadow_hashes = ("AND reasoning_hash NOT IN (SELECT reasoning_hash FROM invoices WHERE (mode = 'shadow' OR origin = 'demo') "
                     "AND reasoning_hash IS NOT NULL) ")
    query = ("SELECT action, model_used = 'manual', COUNT(*), COALESCE(SUM(amount_usdc), 0) FROM audit_log WHERE action IN "
             "('INVOICE_PAID', 'INVOICE_HELD', 'INVOICE_ESCALATED') {extra}GROUP BY action, model_used = 'manual'")
    async with aiosqlite.connect(config.db_path) as db:
        try:
            async with db.execute(query.format(extra=scope + shadow_hashes), args) as cursor:
                rows = await cursor.fetchall()
        except aiosqlite.OperationalError:  # no invoices or businesses table yet (audit log used on its own)
            async with db.execute(query.format(extra=scope if business_id is not None else ""), args) as cursor:
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

