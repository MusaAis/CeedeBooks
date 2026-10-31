"""Earned vendors (Phase R, v1.3.3): the rule and the record. Nothing here pays anything.

A payee is graduated after the owner has approved N of the agent's shadow pay decisions on it in a row. One disagreement
(a rejected pay, or an "I would not" verdict on that payee) ends it. Graduation and revocation are written only through
decision_log.log_decision (one signed ledger entry each), with code-written text (rule 18).
"""
import aiosqlite

from agent import decision_log
from agent.config import config

ACTION_GRADUATED = "VENDOR_GRADUATED"
ACTION_REVOKED = "VENDOR_REVOKED"
ACTIONS = (ACTION_GRADUATED, ACTION_REVOKED)
DEFAULT_EARN_AFTER = 5
MIN_EARN_AFTER = 2
MAX_EARN_AFTER = 50

PAY_AGREE, DISAGREE = "pay_agree", "disagree"


def subject_for(vendor_id: int) -> str:
    return f"vendor:{int(vendor_id)}"


def valid_threshold(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and MIN_EARN_AFTER <= n <= MAX_EARN_AFTER


def streak(events: list) -> int:
    """`events` are one vendor's countable verdicts, oldest first, each PAY_AGREE or DISAGREE. The streak is the number of
    PAY_AGREE at the end of the list with no DISAGREE after them."""
    count = 0
    for kind in reversed(events):
        if kind != PAY_AGREE:
            break
        count += 1
    return count


async def _events(db, business_id: int, vendor_id: int) -> list:
    """Oldest first: (kind, invoice_number, created_at). A shadow verdict counts for a vendor as follows. An "agree" on a pay
    decision the owner approved (the invoice is paid) is PAY_AGREE. Any "disagree" is DISAGREE. An "agree" on a held or
    escalated decision ("I would do the same") says nothing about paying that vendor, so it neither counts nor resets."""
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT d.verdict, i.status, i.invoice_number, d.created_at FROM decision_verdicts d "
        "JOIN invoices i ON i.id = d.invoice_id AND i.business_id = d.business_id "
        "WHERE d.business_id = ? AND i.vendor_id = ? AND i.mode = 'shadow' ORDER BY d.id", (business_id, vendor_id),
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        if r["verdict"] == "disagree":
            out.append((DISAGREE, r["invoice_number"], r["created_at"]))
        elif r["status"] == "paid":
            out.append((PAY_AGREE, r["invoice_number"], r["created_at"]))
    return out


async def _last_record(db, business_id: int, vendor_id: int):
    db.row_factory = aiosqlite.Row
    async with db.execute(
        "SELECT action, timestamp, reasoning_hash FROM audit_log WHERE business_id = ? AND subject = ? AND action IN (?, ?) "
        "ORDER BY id DESC LIMIT 1", (business_id, subject_for(vendor_id), *ACTIONS),
    ) as cur:
        return await cur.fetchone()


async def vendor_state(business_id: int, vendor_id: int) -> dict:
    """What is true now for one vendor, read from the verdicts and the ledger records. `graduated` is true only if the last record is
    a graduation AND no disagreement was recorded after it, so a revocation that failed to be written can never leave a vendor
    looking graduated (rule 17)."""
    async with aiosqlite.connect(config.db_path) as db:
        events = await _events(db, business_id, vendor_id)
        last = await _last_record(db, business_id, vendor_id)
    graduated = bool(last and last["action"] == ACTION_GRADUATED and
                     not any(kind == DISAGREE and at > last["timestamp"] for kind, _, at in events))
    return {"vendor_id": vendor_id, "streak": streak([k for k, _, _ in events]), "graduated": graduated,
            "record_hash": last["reasoning_hash"] if last else None, "record_action": last["action"] if last else None,
            "record_at": last["timestamp"] if last else None}


def graduation_text(vendor_name: str, vendor_id: int, wallet: str, n: int, invoices: list) -> str:
    return (f"Vendor {vendor_name} (id {vendor_id}, wallet {wallet}) graduated: the owner approved its last {n} shadow pay decisions "
            f"in a row ({', '.join(invoices)}), which meets the threshold of {n} the owner set. This record changes no limit and "
            f"pays nothing; the contract's limits and every check still apply to each payment.")


def revocation_text(vendor_name: str, vendor_id: int, invoice_number: str) -> str:
    return (f"Vendor {vendor_name} (id {vendor_id}) lost its graduation: the owner disagreed with the agent on invoice "
            f"{invoice_number}. The streak starts again from zero.")


async def evaluate_vendor(business: dict, vendor: dict) -> str | None:
    """Writes a graduation or a revocation if the vendor's state calls for one. Returns the action written, or None.
    Raises if the ledger cannot take the record (no usable key): the verdict stays recorded, the record is retried on the
    next verdict or threshold change, and vendor_state never reports a graduation a disagreement has ended."""
    business_id, vendor_id = business["id"], vendor["id"]
    async with aiosqlite.connect(config.db_path) as db:
        events = await _events(db, business_id, vendor_id)
        last = await _last_record(db, business_id, vendor_id)
    was_graduated = bool(last and last["action"] == ACTION_GRADUATED)
    kinds = [k for k, _, _ in events]
    if was_graduated:
        # a disagreement after the graduation record ends it
        after = [e for e in events if e[2] > last["timestamp"]]
        broke = next((e for e in reversed(after) if e[0] == DISAGREE), None)
        if broke is None:
            return None
        await decision_log.log_decision(
            ACTION_REVOKED, subject_for(vendor_id), revocation_text(vendor["name"], vendor_id, broke[1]), model_used="rules",
            business=business)
        return ACTION_REVOKED
    n = int(business.get("earn_after") or DEFAULT_EARN_AFTER)
    run = streak(kinds)
    if run < n:
        return None
    pays = [inv for k, inv, _ in events if k == PAY_AGREE][-n:]
    await decision_log.log_decision(
        ACTION_GRADUATED, subject_for(vendor_id), graduation_text(vendor["name"], vendor_id, vendor["wallet_address"], n, pays),
        model_used="rules", business=business)
    return ACTION_GRADUATED
