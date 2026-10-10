"""Hash-chained, signed ledger over each business's decision log (Phase E1).

Every decision row gets one ledger entry. An entry commits to the row's fields and to the previous entry's hash, and is
signed with the Ed25519 ledger key. Each business has its own chain: sequence 0 is a genesis entry, then 1, 2, 3...

What this proves: nobody without the ledger key can edit, delete, reorder or insert entries, or edit a logged decision,
without verification failing. What it does not prove: the key lives on the same server as the database, so someone who
holds both can rewrite the whole chain, and the newest entries can be dropped together with their decision rows.
That gap is closed only by anchoring the head on-chain (a later piece).
"""
import hashlib
import json
import time
from typing import Optional

import aiosqlite
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agent.config import config

VERSION = 1
SIGN_PREFIX = b"ceedebooks-ledger-v1\n"
GENESIS_NOTE = ("Entries 1 to backfilled_entries were chained in one pass, in decision-log order, and signed when that pass ran. "
                "They show nothing about the period before it.")
_DECISION_FIELDS = ("reasoning_hash", "action", "subject", "amount_usdc", "model_used", "timestamp", "contract_address")
MAX_PROBLEMS = 20

_TABLE = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    sequence INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('genesis', 'decision')),
    reasoning_hash TEXT,
    body TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    key_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (business_id, sequence),
    UNIQUE (business_id, prev_hash),
    UNIQUE (business_id, reasoning_hash)
)
"""


class LedgerKeyError(RuntimeError):
    """The ledger key is missing or malformed. Nothing is logged, and so nothing is paid, without it (fail closed)."""


# ---- the key

def _private_key() -> Ed25519PrivateKey:
    seed = (config.ledger_signing_key or "").strip()
    if not seed:
        raise LedgerKeyError("LEDGER_SIGNING_KEY is not set; create one with scripts/ledger_key.py")
    try:
        raw = bytes.fromhex(seed)
        if len(raw) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError:
        raise LedgerKeyError("LEDGER_SIGNING_KEY must be 64 hex characters (a 32-byte Ed25519 seed)") from None


def public_key_hex() -> str:
    raw = _private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return raw.hex()


def key_id_of(public_key: str) -> str:
    return hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:16]


def _sign(body: str) -> str:
    return _private_key().sign(SIGN_PREFIX + body.encode()).hex()


def signature_ok(public_key: str, body: str, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(bytes.fromhex(signature), SIGN_PREFIX + body.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


# ---- entries

def genesis_prev(business_id: int) -> str:
    """The documented prev_hash of every business's genesis entry."""
    return hashlib.sha256(f"ceedebooks-ledger-genesis:v{VERSION}:business:{business_id}".encode()).hexdigest()


def _canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _hash(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


async def ensure_schema(db) -> None:
    await db.execute(_TABLE)


async def _head(db, business_id: int) -> Optional[tuple]:
    async with db.execute(
        "SELECT sequence, entry_hash FROM ledger WHERE business_id = ? ORDER BY sequence DESC LIMIT 1", (business_id,)
    ) as cur:
        row = await cur.fetchone()
    return (row[0], row[1]) if row else None


async def _insert(db, business_id: int, sequence: int, prev_hash: str, kind: str, fields: dict) -> str:
    """Writes one signed entry. Raises sqlite3.IntegrityError if the sequence, the parent or the decision is already taken."""
    key = key_id_of(public_key_hex())
    body = _canonical({"v": VERSION, "kind": kind, "business_id": business_id, "sequence": sequence,
                       "prev_hash": prev_hash, "key_id": key, **fields})
    entry_hash = _hash(body)
    await db.execute(
        "INSERT INTO ledger (business_id, sequence, kind, reasoning_hash, body, prev_hash, entry_hash, key_id, signature, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (business_id, sequence, kind, fields.get("reasoning_hash"), body, prev_hash, entry_hash, key, _sign(body), time.time()),
    )
    return entry_hash


async def ensure_chain(db, business_id: int) -> None:
    """The one-time ordered backfill. If this business has no chain yet: a genesis entry that records how many existing
    decisions it covers, then one entry per existing decision in log order. Call inside the caller's write transaction."""
    if await _head(db, business_id) is not None:
        return
    async with db.execute(f"SELECT {', '.join(_DECISION_FIELDS)} FROM audit_log WHERE business_id = ? ORDER BY id", (business_id,)) as cur:
        rows = await cur.fetchall()
    prev = await _insert(db, business_id, 0, genesis_prev(business_id), "genesis",
                         {"backfilled_entries": len(rows), "note": GENESIS_NOTE})
    for n, row in enumerate(rows, start=1):
        prev = await _insert(db, business_id, n, prev, "decision", dict(zip(_DECISION_FIELDS, row)))


async def append_decision(db, business_id: int, fields: dict) -> str:
    """Adds the next entry to this business's chain, on top of its current head. The caller holds the write lock
    (BEGIN IMMEDIATE) and inserts the decision row in the same transaction, so two writers can never fork the chain."""
    head = await _head(db, business_id)
    if head is None:
        raise RuntimeError("ensure_chain must run before append_decision")
    return await _insert(db, business_id, head[0] + 1, head[1], "decision", {f: fields[f] for f in _DECISION_FIELDS})


async def start() -> None:
    """At API start: refuse to run without a usable key, and chain any business that has decisions but no chain yet."""
    public_key_hex()
    async with aiosqlite.connect(config.db_path) as db:
        await ensure_schema(db)
        async with db.execute("SELECT DISTINCT business_id FROM audit_log") as cur:
            ids = [r[0] for r in await cur.fetchall()]
        for business_id in ids:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await ensure_chain(db, business_id)
                await db.commit()
            except BaseException:
                await db.rollback()
                raise


# ---- verification

def analyse(business_id: int, entries: list, decisions: list, public_key: str) -> list:
    """Pure check of one business's chain. `entries` are its ledger rows in sequence order, `decisions` its decision-log rows.
    Returns a list of problems (empty means the chain is intact)."""
    problems = []

    def bad(sequence, code, detail):
        problems.append({"sequence": sequence, "code": code, "detail": detail})

    want_key = key_id_of(public_key)
    by_hash = {d["reasoning_hash"]: d for d in decisions}
    chained, parents, previous = set(), {}, None
    for index, e in enumerate(entries):
        seq = e["sequence"]
        if e["prev_hash"] in parents:
            bad(seq, "fork", f"entry {parents[e['prev_hash']]} already has this parent")
        parents[e["prev_hash"]] = seq
        if seq != index:
            bad(seq, "bad_sequence", f"position {index} holds sequence {seq}")
        try:
            body = json.loads(e["body"])
        except ValueError:
            body = None
        if not isinstance(body, dict):
            bad(seq, "bad_body", "the signed body is not readable")
            previous = e["entry_hash"]
            continue
        if (body.get("v"), body.get("business_id"), body.get("sequence"), body.get("kind"), body.get("prev_hash"),
                body.get("reasoning_hash"), body.get("key_id")) != (VERSION, business_id, seq, e["kind"], e["prev_hash"],
                                                                     e["reasoning_hash"], e["key_id"]):
            bad(seq, "bad_body", "the signed body does not match the entry's columns")
        if _hash(e["body"]) != e["entry_hash"]:
            bad(seq, "bad_hash", "the entry hash is not the hash of its body")
        want_prev = genesis_prev(business_id) if index == 0 else previous
        if e["prev_hash"] != want_prev:
            bad(seq, "broken_link", "the entry does not continue the one before it" if index else "the first entry is not a genesis entry")
        if e["key_id"] != want_key:
            bad(seq, "wrong_key", "signed under a key other than the published ledger key")
        elif not signature_ok(public_key, e["body"], e["signature"]):
            bad(seq, "bad_signature", "the signature does not verify under the published ledger key")
        if (e["kind"] == "genesis") != (index == 0):
            bad(seq, "bad_genesis", "a genesis entry must be first, and only first")
        if e["kind"] == "decision":
            if e["reasoning_hash"] in chained:
                bad(seq, "duplicate_decision", "this decision is chained twice")
            chained.add(e["reasoning_hash"])
            d = by_hash.get(e["reasoning_hash"])
            if d is None:
                bad(seq, "missing_decision", "the decision this entry commits to is not in the decision log")
            else:
                if any(body.get(f) != d[f] for f in _DECISION_FIELDS):
                    bad(seq, "decision_changed", "the decision log no longer matches what this entry committed to")
                if _hash(d["hash_input"]) != d["reasoning_hash"]:
                    bad(seq, "decision_changed", "the decision's text no longer hashes to its reasoning hash")
        previous = e["entry_hash"]
    for d in decisions:
        if d["reasoning_hash"] not in chained:
            bad(None, "unchained_decision", f"decision {d['reasoning_hash'][:12]} has no ledger entry")
    return problems


async def verify_chain(business_id: int) -> dict:
    """Reads this business's chain and decision log (and only those) and checks them against the published key."""
    public_key = public_key_hex()
    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("SELECT * FROM ledger WHERE business_id = ? ORDER BY sequence, id", (business_id,)) as cur:
                entries = [dict(r) for r in await cur.fetchall()]
        except aiosqlite.OperationalError:
            entries = []  # no ledger table yet: nothing has been logged
        try:
            async with db.execute("SELECT * FROM audit_log WHERE business_id = ? ORDER BY id", (business_id,)) as cur:
                decisions = [dict(r) for r in await cur.fetchall()]
        except aiosqlite.OperationalError:
            decisions = []
    problems = analyse(business_id, entries, decisions, public_key)
    backfilled = None
    if entries:
        try:
            backfilled = json.loads(entries[0]["body"]).get("backfilled_entries")
        except (ValueError, AttributeError):
            pass
    head = {"sequence": entries[-1]["sequence"], "entry_hash": entries[-1]["entry_hash"]} if entries else None
    return {
        "ok": not problems, "entries": len(entries), "decisions": sum(1 for e in entries if e["kind"] == "decision"),
        "head": head, "backfilled_entries": backfilled, "key_id": key_id_of(public_key),
        "problem_count": len(problems), "problems": problems[:MAX_PROBLEMS],
    }
