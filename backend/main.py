"""FastAPI app: invoice intake, vendor/PO/receipt setup, and the audit trail.

Access model (see backend/auth.py): buyer key for setup and receipts, vendor key for its own invoices only, no key for the read-only audit endpoints. Every credential belongs to ONE business and every query is scoped to it (backend/models.py takes business_id as a required argument). No client-supplied value can influence a payment decision: the receipt role comes from the key, the vendor wallet from the vendor on file, and the treasury runway from the chain.
"""
import asyncio
import hashlib
import logging
import re
import sqlite3
import time
from decimal import Decimal
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent import categories as category_rules
from agent import anchor, contract, decision_log, gateway, ledger, payables, treasury
from agent.config import config
from backend import admin_auth, auth, models, vendor_auth

log = logging.getLogger("ceedebooks.api")

app = FastAPI(title="CeedeBooks API")

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# USDC has 6 decimals; accept Decimal (JSON number or string), never binary-float arithmetic on the way in.
Amount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=6)]
ShortText = Annotated[str, Field(min_length=1, max_length=120)]
Ref = Annotated[str, Field(min_length=1, max_length=64)]
Category = Annotated[int, Field(ge=0, le=255)]  # uint8 on-chain
DecisionHash = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields (e.g. treasury_runway_days) are a 422, not ignored


def _check_address(v: str) -> str:
    if not _ADDRESS_RE.match(v):
        raise ValueError("must be a 0x-prefixed 40-hex-char address")
    return v


Slug = Annotated[str, Field(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")]


async def _business_by_slug(slug: Optional[str], *, active_only: bool) -> dict:
    """The business a public request names (default: the home business). Unknown or inactive is a 404."""
    business = await models.get_business(models.HOME_BUSINESS_ID) if slug is None else await models.get_business_by_slug(slug)
    if business is None or (active_only and business["status"] != "active"):
        raise HTTPException(404, "Business not found")
    return business


async def _writable(who: "auth.Principal") -> dict:
    """The caller's own business, which must be active before anything is written or paid in it."""
    business = await models.get_business(who.business_id)
    if business is None:
        raise HTTPException(403, "This credential belongs to a business that does not exist")
    if business["status"] != "active":
        raise HTTPException(403, f"This business is {business['status']} and is not accepting activity")
    return business


class VendorIn(_Strict):
    name: ShortText
    wallet_address: str

    _addr = field_validator("wallet_address")(_check_address)


class PurchaseOrderIn(_Strict):
    po_number: Ref
    vendor_id: int = Field(gt=0)
    amount_usdc: Amount
    category: Category


class ReceiptIn(_Strict):
    po_id: int = Field(gt=0)
    confirmed_by: Optional[ShortText] = None  # free-text name; the ROLE is never taken from the body


class InvoiceIn(_Strict):
    invoice_number: Ref
    vendor_id: int = Field(gt=0)
    vendor_wallet: Optional[str] = None  # optional; if sent it must equal the wallet on file
    amount_usdc: Amount
    category: Category
    doc_hash: Annotated[str, Field(min_length=1, max_length=128)]
    po_number: Optional[Ref] = None
    origin: Optional[Literal["agent", "demo"]] = None  # buyer only: label a demo run so it never counts as real traffic
    mode: Literal["live", "shadow"] = "live"  # buyer only. shadow: a real bill mirrored as a testnet payment after the owner approves
    real_amount: Optional[Annotated[Decimal, Field(gt=0, max_digits=16, decimal_places=2)]] = None  # shadow only: the real bill
    real_currency: Optional[Annotated[str, Field(pattern=r"^[A-Z]{3}$")]] = None  # shadow only: ISO-style code, e.g. NGN

    @field_validator("vendor_wallet")
    @classmethod
    def valid_address(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _check_address(v)


# ---- middleware

def _plain_decimal(value: Optional[Decimal]) -> Optional[str]:
    """Exact decimal text with no exponent, so the same bill always reads the same in the hashed record."""
    return None if value is None else format(value, "f")


class _BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Rejects request bodies over config.max_body_bytes, by Content-Length and while streaming."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = config.max_body_bytes
        started = False

        async def tracking_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            return await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if not started:
                await JSONResponse({"detail": "Request body too large"}, status_code=413)(scope, receive, send)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if not auth.request_limiter.allow(auth.client_ip(request), config.rate_limit_per_min, 60.0):
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
    return await call_next(request)


app.add_middleware(BodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(config.cors_origins) + [config.admin_origin, config.portal_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Authorization", "Content-Type"],
)


@app.on_event("startup")
async def startup() -> None:
    await models.init_db()
    await decision_log.init_db()
    await models.backfill_origin()
    await ledger.start()  # no usable ledger key: the API does not start (nothing could be logged, so nothing could be paid)
    await anchor.ensure_schema()
    if config.ledger_anchor_auto:
        app.state.anchor_task = asyncio.create_task(anchor.run_forever())  # apart from decisions: a failure there never blocks a payment
    for business in await models.list_businesses():
        report = await ledger.verify_chain(business["id"])
        if not report["ok"]:
            log.error("Ledger for business %s FAILED verification: %s", business["slug"], report["problems"][:3])


# ---- setup (buyer only)

def _submitter(who: "auth.Principal") -> str:
    """Who made this submission, for maker-checker: the wallet of a signed-in admin, else the API key's id."""
    return who.admin_address.lower() if who.admin_address else f"key:{who.key_id}"


def _made_by(invoice, who: "auth.Principal") -> bool:
    """True when this admin wallet submitted the invoice. An invoice with no recorded submitter (made before v1.3.0) is not."""
    return bool(who.admin_address) and (invoice["submitted_by"] or "") == who.admin_address.lower()


async def _admin_log(who: auth.Principal, action: str, ref: str) -> None:
    """Record what a wallet-signed admin did off-chain. API-key callers are not logged here."""
    if who.admin_address:
        await models.log_admin_action(who.business_id, who.admin_address, action, ref)


@app.post("/vendors")
async def create_vendor(body: VendorIn, who: auth.Principal = Depends(auth.require("buyer"))):
    await _writable(who)
    vendor_id = await models.save_vendor(who.business_id, body.name, body.wallet_address)
    await _admin_log(who, "create_vendor", f"vendor {vendor_id}")
    return {"id": vendor_id}


@app.get("/vendors/{vendor_id}")
async def get_vendor(vendor_id: int, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    if who.role == "vendor" and who.vendor_id != vendor_id:
        raise HTTPException(404, "Vendor not found")
    vendor = await models.get_vendor(who.business_id, vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    return dict(vendor)


async def _require_category(business: dict, category: int, *, need_limit: bool = True) -> None:
    """The category must be one of THIS business's own. With need_limit it must also have a daily limit above zero
    on-chain right now; a limit that cannot be read refuses the request (fail closed, nothing is saved)."""
    if await models.get_category(business["id"], category) is None:
        raise HTTPException(422, "Unknown category for this business")
    if not need_limit:
        return
    try:
        limit = await asyncio.to_thread(contract.chain_for(business).category_limit, category)
    except Exception:
        log.exception("Could not read the category limit for business %s", business["id"])
        raise HTTPException(503, "Could not read this category's limit from the chain right now; nothing was saved")
    if not category_rules.usable(limit):
        raise HTTPException(422, "This category has no daily limit on-chain yet; the owner sets one in the admin site first")


@app.post("/purchase-orders")
async def create_purchase_order(body: PurchaseOrderIn, who: auth.Principal = Depends(auth.require("buyer"))):
    business = await _writable(who)
    if await models.get_vendor(who.business_id, body.vendor_id) is None:
        raise HTTPException(404, "Vendor not found")
    await _require_category(business, body.category)
    try:
        po_id = await models.save_purchase_order(who.business_id, body.po_number, body.vendor_id, float(body.amount_usdc), body.category)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"PO number '{body.po_number}' already exists")
    await _admin_log(who, "create_purchase_order", body.po_number)
    return {"id": po_id}


@app.post("/receipts")
async def create_receipt(body: ReceiptIn, who: auth.Principal = Depends(auth.require("buyer"))):
    """Only the buyer can confirm delivery. The role is set here from the API key, never from the request."""
    await _writable(who)
    if await models.get_purchase_order_by_id(who.business_id, body.po_id) is None:
        raise HTTPException(404, "Purchase order not found")
    if await models.get_receipt(who.business_id, body.po_id) is not None:
        raise HTTPException(409, "A receipt is already on file for this purchase order")
    receipt_id = await models.save_receipt(who.business_id, body.po_id, body.confirmed_by or who.label, who.role)
    await _admin_log(who, "confirm_receipt", f"po {body.po_id}")
    return {"id": receipt_id}


# ---- invoices (buyer, or the vendor itself)

async def _checked_submission(body: InvoiceIn, who: auth.Principal, *, need_limit: bool = True):
    """Access and consistency checks shared by /invoices and /invoices/preflight. Returns (vendor, po, business).
    A dry run (need_limit False) only checks that the category exists; its on-chain limit shows up in the outcome."""
    business = await _writable(who)
    if who.role == "vendor" and who.vendor_id != body.vendor_id:
        raise HTTPException(403, "A vendor key can only submit invoices for its own vendor_id")
    if body.origin is not None and who.role != "buyer":
        raise HTTPException(403, "Only the buyer can label an invoice's origin")
    if body.mode == "shadow" and who.role != "buyer":
        raise HTTPException(403, "Only the business owner can submit a shadow invoice")
    if body.mode == "shadow" and (body.real_amount is None or body.real_currency is None):
        raise HTTPException(422, "A shadow invoice needs the real amount and currency of the bill")
    if body.mode == "live" and (body.real_amount is not None or body.real_currency is not None):
        raise HTTPException(422, "The real amount and currency belong to shadow invoices only")
    if body.mode == "shadow" and body.origin is not None:
        raise HTTPException(422, "A shadow invoice cannot also carry an origin label")

    vendor = await models.get_vendor(who.business_id, body.vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    if body.vendor_wallet is not None and body.vendor_wallet.lower() != vendor["wallet_address"].lower():
        raise HTTPException(422, "vendor_wallet does not match the wallet on file for this vendor")

    po = await models.get_purchase_order(who.business_id, body.po_number) if body.po_number else None
    if po is not None and po["vendor_id"] != body.vendor_id:
        raise HTTPException(403, "That purchase order does not belong to this vendor")

    if po is not None and po["category"] != body.category:
        raise HTTPException(422, "Invoice category does not match the purchase order's category")
    await _require_category(business, body.category, need_limit=need_limit)
    return vendor, po, business


@app.post("/invoices/preflight")
async def preflight_invoice(body: InvoiceIn, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    """Dry run: would this invoice be paid, held or escalated right now, and why? Nothing is saved, logged or sent
    on-chain, so an agent can fix problems before submitting. Same access rules as POST /invoices."""
    vendor, _, business = await _checked_submission(body, who, need_limit=False)
    amount = float(body.amount_usdc)
    invoice = payables.Invoice(
        business_id=who.business_id, id=0, invoice_number=body.invoice_number, vendor_wallet=vendor["wallet_address"],
        amount_usdc=amount, category=body.category, doc_hash=body.doc_hash, po_number=body.po_number,
    )
    try:
        runway = await treasury.runway_days(amount, business)
        result = await payables.preflight(invoice, runway, business)
    except Exception:
        log.exception("Preflight failed for %s", body.invoice_number)
        raise HTTPException(503, "Could not read the chain right now; try again shortly")
    if body.mode == "shadow" and result["outcome"] == "would_pay":
        result = {**result, "outcome": "would_await_owner", "reasons": ["Shadow mode: the agent would pay; the owner approves first"]}
    return {**result, "dry_run": True}


@app.post("/invoices")
async def submit_invoice(body: InvoiceIn, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    vendor, po, business = await _checked_submission(body, who)
    amount = float(body.amount_usdc)
    try:
        invoice_id = await models.save_invoice(
            who.business_id, body.invoice_number, body.vendor_id, po["id"] if po else None, amount, body.category, body.doc_hash,
            body.origin or "agent", body.mode, _plain_decimal(body.real_amount), body.real_currency, _submitter(who),
        )
    except sqlite3.IntegrityError:
        await models.log_rejected_submission(who.business_id, "duplicate_invoice", body.origin or "agent")
        raise HTTPException(409, f"Invoice number '{body.invoice_number}' already exists")

    invoice = payables.Invoice(
        business_id=who.business_id,
        id=invoice_id,
        invoice_number=body.invoice_number,
        vendor_wallet=vendor["wallet_address"],  # always the wallet on file, never a client-supplied one
        amount_usdc=amount,
        category=body.category,
        doc_hash=body.doc_hash,
        po_number=body.po_number,
        mode=body.mode,
        real_amount=_plain_decimal(body.real_amount),
        real_currency=body.real_currency,
    )
    try:
        runway = await treasury.runway_days(amount, business)  # computed server-side from the chain; not a request field
        decision = await payables.process_invoice(invoice, runway, business)
    except Exception:
        log.exception("Invoice %s processing failed", body.invoice_number)
        await models.update_invoice_status(who.business_id, invoice_id, "error")
        raise HTTPException(500, "Processing failed; the failure was logged")
    return {"invoice_id": invoice_id, "decision": decision.value}


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    invoice = await models.get_invoice(who.business_id, invoice_id)
    # A vendor asking for someone else's invoice gets the same 404 as a missing one, so ids can't be probed.
    if invoice is None or (who.role == "vendor" and invoice["vendor_id"] != who.vendor_id):
        raise HTTPException(404, "Invoice not found")
    return dict(invoice)


# ---- public audit (no key, read-only)

@app.get("/decisions/{reasoning_hash}")
async def get_decision(reasoning_hash: DecisionHash):
    decision = await decision_log.get_decision(reasoning_hash)
    if decision is None:
        raise HTTPException(404, "Decision not found")
    return dict(decision)


async def _onchain_check(decision) -> dict:
    """Does the stored hash appear in a BudgetEnforcer event of the recorded on-chain transaction?"""
    tx_hash = decision["chain_tx_hash"]
    if not tx_hash:
        return {"checked": False, "match": None, "detail": "No on-chain transaction recorded for this decision"}
    business = await models.get_business(decision["business_id"])
    if business is None:
        return {"checked": False, "match": None, "chain_tx_hash": tx_hash, "detail": "The business for this decision is not on record"}
    try:
        events = await asyncio.to_thread(contract.chain_for(business).reasoning_events, tx_hash)
    except Exception:
        log.exception("On-chain lookup failed for %s", tx_hash)
        return {"checked": False, "match": None, "chain_tx_hash": tx_hash, "detail": "Could not read the chain right now"}
    hit = next((e for e in events if e["reasoning_hash"] == decision["reasoning_hash"] and e["success"]), None)
    if hit is None:
        return {"checked": True, "match": False, "chain_tx_hash": tx_hash, "detail": "No matching event in that transaction"}
    return {"checked": True, "match": True, "chain_tx_hash": tx_hash, "event": hit["event"], "block": hit["block"]}


@app.get("/decisions/{reasoning_hash}/verify")
async def verify_decision(reasoning_hash: DecisionHash):
    """verified: the stored hash_input hashes to the stored reasoning_hash. onchain: the same hash is in a chain event.
    The independent check is the one in the public page, which reads the chain directly from the browser."""
    verified = await decision_log.verify_roundtrip(reasoning_hash)
    decision = await decision_log.get_decision(reasoning_hash)
    onchain = await _onchain_check(decision) if decision is not None else None
    return {"reasoning_hash": reasoning_hash, "verified": verified, "onchain": onchain}


async def _public_scope(business: Optional[str]) -> Optional[int]:
    """None = the public aggregate over every business; a slug scopes a public read to that one business."""
    return None if business is None else (await _business_by_slug(business, active_only=False))["id"]


@app.get("/decisions")
async def list_decisions(limit: int = 20, business: Optional[Slug] = None):
    """Most recent audit entries, newest first (no reasoning text; fetch one by hash for that). `business` narrows it."""
    return {"decisions": await decision_log.recent(max(1, min(limit, 50)), await _public_scope(business))}


@app.get("/stats")
async def get_stats(business: Optional[Slug] = None):
    """Decision counts (as before) plus the submission metrics, split by origin: agent, manual (run by hand) and demo.
    Without `business` this is the aggregate over every business (counts only); with it, that one business."""
    scope = await _public_scope(business)
    return {**await decision_log.stats(scope), "submissions": await models.submission_stats(scope),
            "shadow": await models.shadow_stats(scope), "businesses": await models.business_counts()}


@app.get("/metrics/decisions")
async def decision_metrics(business: Optional[Slug] = None):
    """Rules verdict versus the model's second opinion, over decisions that recorded one. The rules verdict is the one that
    acts. `compared` counts only decisions a model answered; `agreement_rate` is null when none were. Live and shadow are
    kept apart. Without `business` this is the aggregate over visible businesses; with it, that one business."""
    scope = await _public_scope(business)
    metrics = await decision_log.comparison_metrics(scope)
    slugs = {b["id"]: b["slug"] for b in await models.list_businesses()}
    for group in metrics.values():
        group["by_business"] = {slugs[i]: v for i, v in group["by_business"].items() if i in slugs}
    return metrics


@app.get("/.well-known/ledger-key")
async def ledger_key():
    """The Ed25519 public key that signs every ledger entry. Public. The same server holds the private key, so this key alone
    is not independent proof; the on-chain anchor (later) is."""
    try:
        public_key = ledger.public_key_hex()
    except ledger.LedgerKeyError:
        raise HTTPException(503, "The ledger key is not configured")
    return {"algorithm": "Ed25519", "public_key": public_key, "key_id": ledger.key_id_of(public_key),
            "signed_message": "ceedebooks-ledger-v1 + newline + the entry body, exactly as stored"}


@app.get("/ledger/verify")
async def verify_ledger(business: Optional[Slug] = None):
    """Walks one business's hash chain (default: the home business; an archived business still verifies): links, hashes,
    signatures, and that every entry still matches its decision-log row and every decision has an entry."""
    biz = await _business_by_slug(business, active_only=False)
    try:
        report = await ledger.verify_chain(biz["id"])
    except ledger.LedgerKeyError:
        raise HTTPException(503, "The ledger key is not configured")
    return {"business": biz["slug"], **_with_anchor(report, await anchor.check(biz))}


def _with_anchor(report: dict, found: dict) -> dict:
    """Adds the on-chain anchor to a chain report. A newest anchor that the chain contradicts (a different head at that
    sequence, or a chain shorter than the anchored sequence) is a problem; never anchored or unreadable is not, but it
    is also not 'fully verified': that needs the chain intact and the anchor confirmed."""
    problems, count = list(report["problems"]), report["problem_count"]
    if found["status"] in ("mismatch", "short"):
        code = "anchor_mismatch" if found["status"] == "mismatch" else "anchored_entries_missing"
        problems = ([{"sequence": found["anchored_sequence"], "code": code, "detail": found["detail"]}] + problems)[:ledger.MAX_PROBLEMS]
        count += 1
    return {**report, "ok": count == 0, "problem_count": count, "problems": problems, "anchor": found,
            "fully_verified": count == 0 and found["status"] == "match"}


@app.get("/ledger/entries")
async def ledger_entries(business: Optional[Slug] = None, after: int = Query(-1, ge=-1), limit: int = Query(500, ge=1, le=500)):
    """One business's signed entries after a sequence, exactly as stored, so a browser can re-check hashes, links and
    signatures itself (site/verify.js). Public, like /decisions."""
    biz = await _business_by_slug(business, active_only=False)
    entries = await ledger.list_entries(biz["id"], after, limit)
    return {"business": biz["slug"], "entries": entries, "more": len(entries) == limit}


@app.get("/.well-known/agent.json")
async def agent_manifest():
    """Machine-readable description so another agent can discover how to work with this one. Public."""
    home = await models.get_business(models.HOME_BUSINESS_ID)
    active = await models.list_businesses("active")
    return {
        "name": "CeedeBooks",
        "description": "Autonomous accounts-payable agent. It pays an invoice only if a smart contract allows it.",
        "network": "Arc Testnet",
        "settlement": "testnet USDC (no market value)",
        "contract": home["enforcer_address"] if home else config.budget_enforcer_address,
        "businesses": [
            {"slug": b["slug"], "name": b["name"], "contract": b["enforcer_address"], "contract_version": b["contract_version"]}
            for b in active
        ],
        "custody": "CeedeBooks runs each business's agent wallet. It can only pay that business's approved vendors within its limits; the owner's own wallet controls the money and limits and can replace the agent at any time.",
        "business_scope": "Every key, session and record belongs to one business. Public reads default to all businesses; add ?business=<slug> to narrow them. Sign-in challenges accept an optional 'business' slug (default: the first business).",
        "auth": {
            "header": "X-API-Key", "roles": ["buyer", "vendor"], "public": ["/decisions", "/stats"],
            "wallet_signin": "A vendor can also sign in by signing a one-time message with its payee wallet (POST /vendor/auth/challenge then /vendor/auth/verify, EIP-191 personal_sign) and send the returned token as 'Authorization: Bearer <token>'. No key is issued.",
        },
        "vendor_flow": [
            {"step": 1, "who": "vendor", "action": "apply with a wallet signature (POST /apply/challenge then POST /apply); the buyer reviews it, registers the vendor and approves its wallet on-chain. /vendors itself is buyer-only"},
            {"step": 2, "who": "buyer", "action": "raise a purchase order (POST /purchase-orders)"},
            {"step": 3, "who": "vendor", "action": "deliver; the deliverable's SHA-256 becomes the invoice doc_hash"},
            {"step": 4, "who": "buyer", "action": "confirm receipt (POST /receipts); a vendor can never confirm its own delivery"},
            {"step": 5, "who": "vendor", "action": "optionally dry-run the invoice (POST /invoices/preflight)"},
            {"step": 6, "who": "vendor", "action": "submit the invoice (POST /invoices); the agent decides and pays or refuses"},
            {"step": 7, "who": "anyone", "action": "verify the decision (GET /decisions/{hash}/verify)"},
        ],
        "endpoints": {
            "POST /invoices/preflight": "vendor or buyer; dry run, writes nothing",
            "POST /invoices": "vendor (own invoices) or buyer; runs the full pipeline",
            "GET /invoices/{id}": "vendor (own) or buyer",
            "GET /decisions/{hash}": "public",
            "GET /decisions/{hash}/verify": "public",
            "GET /stats": "public",
            "GET /metrics/decisions": "public; rules verdict versus the model's second opinion, live and shadow apart, by origin and business (?business=<slug>)",
            "GET /ledger/verify": "public; walks one business's hash chain and compares it with its newest on-chain anchor (?business=<slug>)",
            "GET /ledger/entries": "public; the signed entries after a sequence, as stored (?business=<slug>&after=<n>)",
            "GET /.well-known/ledger-key": "public; the key that signs ledger entries",
        },
        "amounts": ("amount_usdc is always entered by the submitter (there is no default). The three-way match accepts a difference of up to "
                    f"{payables.AMOUNT_TOLERANCE_USDC} USDC from the purchase order as a rounding allowance; anything further goes to the owner."),
        "categories": "Each business creates and names its own spending categories and signs each one's daily limit from its own wallet. A category with no on-chain limit cannot be used.",
        "guarantees": [
            "The agent cannot pay outside the on-chain vendor registry, per-transaction, daily or category limits",
            "The reasoning hash is committed on-chain in an earlier block than the payment",
            "Refusals (holds and escalations) are logged and anchored, not hidden",
        ],
        "openapi": "/openapi.json",
    }


# ---- admin site (wallet-signed session; see backend/admin_auth.py)

_TX_RE = r"^0x[0-9a-fA-F]{64}$"


class AdminChallengeIn(_Strict):
    address: str
    business: Optional[Slug] = None  # which business to sign in to; default: the first business

    @field_validator("address")
    @classmethod
    def _addr(cls, v: str) -> str:
        return _check_address(v)


class AdminVerifyIn(_Strict):
    nonce: str = Field(min_length=32, max_length=32)
    signature: str = Field(min_length=132, max_length=132)


AdminActionName = Literal[
    "set_vendor", "set_category_limit", "set_paused", "approve_escalation", "reject_escalation", "reopen_escalation", "accept_approver"
]


class AdminActionIn(_Strict):
    action: AdminActionName
    tx_hash: str = Field(pattern=_TX_RE)
    invoice_id: Optional[int] = None
    paused: Optional[bool] = None  # set_paused only: the state the transaction set
    reason: Optional[str] = Field(default=None, max_length=200)  # set_paused only: why (shown on the banner and in the ledger)


@app.get("/admin/auth/state")
async def admin_auth_state(business: Optional[Slug] = None):
    """Public on-chain facts the connect screen needs: who is the approver, who is waiting to accept the role."""
    biz = await _business_by_slug(business, active_only=False)
    try:
        chain = contract.chain_for(biz)
        approver, pending = await asyncio.gather(asyncio.to_thread(chain.approver), asyncio.to_thread(chain.pending_approver))
    except Exception:
        log.exception("admin auth state read failed")
        raise HTTPException(503, "Could not read the chain right now")
    return {"approver": approver.lower(), "pending_approver": pending.lower(), "chain_id": admin_auth.CHAIN_ID,
            "domain": admin_auth.domain(), "business": biz["slug"], "contract": biz["enforcer_address"]}


@app.post("/admin/auth/challenge")
async def admin_challenge(body: AdminChallengeIn, request: Request):
    if not auth.request_limiter.allow("admin-challenge:" + auth.client_ip(request), 10, 60.0):
        raise HTTPException(429, "Too many sign-in attempts; try again in a minute")
    return admin_auth.make_challenge(body.address, await _business_by_slug(body.business, active_only=False))


@app.post("/admin/auth/verify")
async def admin_verify(body: AdminVerifyIn, request: Request):
    ip = auth.client_ip(request)
    if auth.failed_auth_limiter.blocked(ip, config.failed_auth_per_min, 60.0):
        raise HTTPException(429, "Too many failed attempts; try again later", headers={"Retry-After": "60"})
    session = await admin_auth.verify(body.nonce, body.signature)
    if session is None:
        auth.failed_auth_limiter.allow(ip, config.failed_auth_per_min, 60.0)
        raise HTTPException(401, "Sign-in failed")
    await models.log_admin_action(session["business_id"], session["address"], "sign_in", None)
    return session


@app.post("/admin/auth/logout")
async def admin_logout(request: Request, _: auth.Principal = Depends(auth.require_admin)):
    admin_auth.logout(request.headers.get("authorization", "")[7:].strip())
    return {"ok": True}


def _category_rows(business: dict, cats: list) -> list:
    """The business's own category names (database) with their limits (chain). Raises if the chain cannot be read."""
    chain = contract.chain_for(business)
    rows = []
    for c in cats:
        limit = chain.category_limit(c["id"])
        _, in_cat = chain.remaining_today(c["id"])
        rows.append({"id": c["id"], "name": c["name"], "daily_limit_usdc": limit / 1_000_000,
                     "remaining_usdc": in_cat / 1_000_000, "usable": category_rules.usable(limit)})
    return rows


def _chain_snapshot(business: dict, categories: list = ()) -> dict:
    snap = {"chain_ok": True, "business": business["slug"], "contract": business["enforcer_address"],
            "contract_version": business["contract_version"]}
    try:
        chain = contract.chain_for(business)
        daily, per_tx = chain.budget_limits()
        snap.update(
            approver=chain.approver().lower(),
            pending_approver=chain.pending_approver().lower(),
            paused=chain.is_paused(),
            balance_usdc=chain.usdc_balance() / 1_000_000,
            daily_limit_usdc=daily / 1_000_000,
            per_tx_limit_usdc=per_tx / 1_000_000,
        )
        weekly = chain.weekly_limit()
        if weekly is not None:  # v2 contracts only
            snap.update(weekly_limit_usdc=weekly / 1_000_000, remaining_week_usdc=chain.remaining_this_week() / 1_000_000)
        snap["categories"] = _category_rows(business, list(categories))
    except Exception:
        log.exception("admin overview chain read failed")
        snap["chain_ok"] = False
    return snap


@app.get("/admin/overview")
async def admin_overview(who: auth.Principal = Depends(auth.require_admin)):
    business = await models.get_business(who.business_id)
    cats = await models.list_categories(who.business_id)
    snap, counts, vendors, pos, pending, gw = await asyncio.gather(
        asyncio.to_thread(_chain_snapshot, business, cats), models.invoice_counts(who.business_id), models.list_vendors(who.business_id),
        models.list_purchase_orders(who.business_id), models.count_pending_applications(who.business_id),
        gateway.get_balance(business.get("agent_address") or ""),
    )
    return {"chain": snap, "gateway": {"domain": gateway.ARC_DOMAIN, "depositor": business.get("agent_address") or None, "balance_usdc": gw}, "invoices": counts, "vendors": len(vendors), "purchase_orders": len(pos),
            "pending_applications": pending, "operator": who.business_id == models.HOME_BUSINESS_ID,
            "business": {"name": business["name"], "slug": business["slug"]}}


@app.get("/admin/vendors")
async def admin_vendors(who: auth.Principal = Depends(auth.require_admin)):
    vendors = await models.list_vendors(who.business_id)
    chain = contract.chain_for(await models.get_business(who.business_id))

    def approved(w: str):
        try:
            return chain.is_vendor_approved(w)
        except Exception:
            return None  # unknown, shown as such

    flags = await asyncio.gather(*[asyncio.to_thread(approved, v["wallet_address"]) for v in vendors])
    return {"vendors": [{**v, "approved_onchain": f} for v, f in zip(vendors, flags)]}


@app.get("/admin/purchase-orders")
async def admin_purchase_orders(who: auth.Principal = Depends(auth.require_admin)):
    return {"purchase_orders": await models.list_purchase_orders(who.business_id)}


class CategoryNameIn(_Strict):
    name: str

    @field_validator("name")
    @classmethod
    def _clean(cls, v: str) -> str:
        return category_rules.clean_name(v)


@app.get("/admin/categories")
async def admin_categories(who: auth.Principal = Depends(auth.require_admin)):
    """This business's own categories and their on-chain limits. A limit that cannot be read is null and never counts as usable."""
    business = await models.get_business(who.business_id)
    cats = await models.list_categories(who.business_id)
    try:
        rows = await asyncio.to_thread(_category_rows, business, cats)
        return {"chain_ok": True, "categories": rows}
    except Exception:
        log.exception("admin categories chain read failed")
        return {"chain_ok": False, "categories": [{"id": c["id"], "name": c["name"], "daily_limit_usdc": None, "remaining_usdc": None, "usable": None} for c in cats]}


@app.post("/admin/categories")
async def admin_create_category(body: CategoryNameIn, who: auth.Principal = Depends(auth.require_admin)):
    """Names a new category and gives it the next free id. It cannot be used until the owner signs setCategoryDailyLimit
    for that id from their own wallet; the server never holds that key."""
    await _writable(who)
    try:
        category_id = await models.add_category(who.business_id, body.name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "You already have a category with that name")
    except ValueError as e:
        raise HTTPException(409, str(e))
    await _admin_log(who, "create_category", f"category {category_id}")
    return {"id": category_id, "name": body.name}


@app.patch("/admin/categories/{category_id}")
async def admin_rename_category(body: CategoryNameIn, category_id: int = Path(ge=0, le=255), who: auth.Principal = Depends(auth.require_admin)):
    """Only the name can change; the id is what the contract knows, so it never does."""
    await _writable(who)
    try:
        found = await models.rename_category(who.business_id, category_id, body.name)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "You already have a category with that name")
    if not found:
        raise HTTPException(404, "Category not found")
    await _admin_log(who, "rename_category", f"category {category_id}")
    return {"id": category_id, "name": body.name}


@app.get("/admin/invoices")
async def admin_invoices(who: auth.Principal = Depends(auth.require_admin)):
    out = []
    for row in await models.list_invoices(who.business_id):
        r = dict(row)
        r["submitted_by_you"] = _made_by(r, who)
        del r["submitted_by"]  # never shown: an API key id or a wallet; the page only needs to know if it is you
        out.append(r)
    return {"invoices": out}


@app.get("/admin/actions")
async def admin_actions(who: auth.Principal = Depends(auth.require_admin)):
    return {"actions": await models.list_admin_actions(who.business_id)}


@app.get("/admin/invoices/{invoice_id}/escalation")
async def admin_escalation(invoice_id: int, purpose: Optional[Literal["approve", "reject", "reopen"]] = None,
                           who: auth.Principal = Depends(auth.require_admin)):
    """The on-chain key the wallet needs to approve, reject or return this escalated invoice. Maker-checker: with
    purpose=approve the key is refused for an invoice this wallet submitted. Reject and return stay open (they release nothing)."""
    invoice = await models.get_invoice(who.business_id, invoice_id)
    if invoice is None or invoice["status"] != "escalated" or not invoice["reasoning_hash"]:
        raise HTTPException(404, "No escalation waiting for this invoice")
    if purpose == "approve" and _made_by(invoice, who):
        raise HTTPException(403, MAKER_CHECKER_MESSAGE)
    decision = await decision_log.get_decision(invoice["reasoning_hash"])
    if decision is None or not decision["chain_tx_hash"]:
        raise HTTPException(404, "This escalation has no recorded on-chain transaction (it predates v1.2.4)")
    try:
        key = await asyncio.to_thread(contract.chain_for(await models.get_business(who.business_id)).escalation_key, decision["chain_tx_hash"])
    except Exception:
        log.exception("escalation key lookup failed")
        raise HTTPException(503, "Could not read the chain right now")
    if key is None:
        raise HTTPException(404, "No escalation event found in that transaction")
    business = await models.get_business(who.business_id)
    return {"invoice_key": key, "can_reopen": int(business["contract_version"]) >= 2, "submitted_by_you": _made_by(invoice, who)}


MAKER_CHECKER_MESSAGE = ("You submitted this invoice from your own wallet, so you cannot release it. Reject it, or return it to the agent; "
                         "an invoice submitted through the vendor portal or an API key can be released by you")


class VerdictIn(_Strict):
    verdict: Literal["agree", "disagree"]


async def _shadow_invoice(who: auth.Principal, invoice_id: int) -> sqlite3.Row:
    invoice = await models.get_invoice(who.business_id, invoice_id)
    if invoice is None or invoice["mode"] != "shadow":
        raise HTTPException(404, "No shadow invoice with that id")
    return invoice


@app.post("/admin/invoices/{invoice_id}/shadow-approve")
async def admin_shadow_approve(invoice_id: int, who: auth.Principal = Depends(auth.require_admin)):
    """The owner approves a parked shadow decision: it is paid now as a testnet USDC payment through the normal
    commit-then-pay, so the contract still enforces vendor, limits and pause. The verdict (agree) is recorded only after
    the payment is confirmed."""
    business = await _writable(who)
    invoice = await _shadow_invoice(who, invoice_id)
    pending_hash = invoice["reasoning_hash"]
    if invoice["status"] != "awaiting_owner" or not pending_hash:
        raise HTTPException(409, "This invoice is not waiting for your approval")
    vendor = await models.get_vendor(who.business_id, invoice["vendor_id"])
    try:
        paused = await asyncio.to_thread(contract.chain_for(business).is_paused)
    except Exception:
        log.exception("pause state unreadable before a shadow payment")
        raise HTTPException(503, "Could not read the chain right now; nothing was paid")
    if paused:  # your decision stands; it is not turned into a hold, and you can approve again after payments resume
        raise HTTPException(409, "Payments are paused. This invoice stays waiting for your approval until you resume them")
    if vendor is None or not await models.claim_shadow_approval(who.business_id, invoice_id):
        raise HTTPException(409, "This invoice is not waiting for your approval")
    parked = payables.Invoice(
        business_id=who.business_id, id=invoice_id, invoice_number=invoice["invoice_number"], vendor_wallet=vendor["wallet_address"],
        amount_usdc=float(invoice["amount_usdc"]), category=invoice["category"], doc_hash=invoice["doc_hash"], po_number=None,
        mode="shadow", real_amount=invoice["real_amount"], real_currency=invoice["real_currency"],
    )
    try:
        paid_hash = await payables.pay_approved_shadow(parked, pending_hash, business)
    except payables.PaymentsPaused:
        await models.release_shadow_approval(who.business_id, invoice_id)
        raise HTTPException(409, "Payments are paused. This invoice stays waiting for your approval until you resume them")
    except Exception:
        log.exception("Shadow payment for invoice %s failed", invoice_id)
        await models.release_shadow_approval(who.business_id, invoice_id)
        raise HTTPException(502, "The payment was not completed. Nothing is recorded as paid; you can approve again")
    await models.save_verdict(who.business_id, invoice_id, pending_hash, "agree", who.admin_address)
    await _admin_log(who, "shadow_approve", f"invoice {invoice_id}")
    return {"ok": True, "status": "paid", "reasoning_hash": paid_hash}


@app.post("/admin/invoices/{invoice_id}/shadow-reject")
async def admin_shadow_reject(invoice_id: int, who: auth.Principal = Depends(auth.require_admin)):
    """The owner would not have paid this: nothing is paid, and the disagreement is recorded for good."""
    await _writable(who)
    invoice = await _shadow_invoice(who, invoice_id)
    pending_hash = invoice["reasoning_hash"]
    if invoice["status"] != "awaiting_owner" or not pending_hash or not await models.claim_shadow_approval(who.business_id, invoice_id):
        raise HTTPException(409, "This invoice is not waiting for your approval")
    await models.save_verdict(who.business_id, invoice_id, pending_hash, "disagree", who.admin_address)
    await models.update_invoice_status(who.business_id, invoice_id, "rejected")
    await _admin_log(who, "shadow_reject", f"invoice {invoice_id}")
    return {"ok": True, "status": "rejected"}


@app.post("/admin/decisions/{reasoning_hash}/verdict")
async def admin_decision_verdict(reasoning_hash: DecisionHash, body: VerdictIn, who: auth.Principal = Depends(auth.require_admin)):
    """Would the owner have done the same? Only for a held or escalated shadow decision of the owner's own business;
    a parked decision is judged by approving or rejecting it. One verdict per decision, final once recorded."""
    await _writable(who)
    invoice = await models.get_invoice_by_decision(who.business_id, reasoning_hash)
    if invoice is None or invoice["mode"] != "shadow":
        raise HTTPException(404, "No shadow decision with that hash in your business")
    if invoice["status"] not in ("held", "escalated"):
        raise HTTPException(409, "Only a held or escalated decision takes a verdict here; approve or reject a waiting one")
    try:
        await models.save_verdict(who.business_id, invoice["id"], reasoning_hash, body.verdict, who.admin_address)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A verdict is already recorded for this decision and cannot be changed")
    await _admin_log(who, "verdict_" + body.verdict, f"invoice {invoice['id']}")
    return {"ok": True, "verdict": body.verdict}


@app.post("/admin/actions")
async def admin_record_action(body: AdminActionIn, who: auth.Principal = Depends(auth.require_admin)):
    """Record an on-chain admin transaction after the wallet sent it. The server checks the chain itself: the transaction
    must be to this business's contract, from this admin, and successful. An escalation result also settles the invoice's status,
    but only if the event on-chain carries that invoice's own reasoning hash."""
    business = await _writable(who)
    try:
        tx = await asyncio.to_thread(contract.chain_for(business).inspect_admin_tx, body.tx_hash)
    except Exception:
        raise HTTPException(404, "That transaction is not confirmed on-chain yet")
    if tx["sender"] != who.admin_address or tx["to"] != business["enforcer_address"].lower() or not tx["success"]:
        raise HTTPException(422, "That transaction is not a successful call to the contract from your wallet")

    ref = None
    pausing = body.action == "set_paused"
    if pausing:
        reason = " ".join((body.reason or "").split())
        if body.paused is None or len(reason) < 3 or any(ord(c) < 32 for c in (body.reason or "")):
            raise HTTPException(422, "Pausing or resuming needs the state and a reason of at least 3 characters")
        try:
            on_chain = await asyncio.to_thread(contract.chain_for(business).is_paused)
        except Exception:
            log.exception("pause state unreadable while recording")
            raise HTTPException(503, "Could not read the chain right now")
        if bool(on_chain) != body.paused:
            raise HTTPException(422, "The contract is not in the state this record claims")
        ref = ("paused: " if body.paused else "resumed: ") + reason
    reopening = body.action == "reopen_escalation"
    if reopening and int(business["contract_version"]) < 2:
        raise HTTPException(422, "This business's contract has no reopenEscalation (only the multi-business contract does)")
    if body.action in ("approve_escalation", "reject_escalation", "reopen_escalation"):
        invoice = await models.get_invoice(who.business_id, body.invoice_id) if body.invoice_id is not None else None
        wanted = {"approve_escalation": "EscalationApproved", "reject_escalation": "EscalationRejected",
                  "reopen_escalation": "EscalationReopened"}[body.action]
        if invoice is None or tx["escalation"] is None or tx["escalation"][0] != wanted \
                or tx["escalation"][2] != (invoice["reasoning_hash"] or ""):
            raise HTTPException(422, "The transaction does not settle that invoice's escalation")
        ref = f"invoice {invoice['id']}"
    try:
        await models.log_admin_action(who.business_id, who.admin_address, body.action, ref, body.tx_hash.lower(), tx["block"])
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That transaction is already recorded")
    if pausing:
        admin_auth.forget_pause(who.business_id)
        return {"ok": True, "block": tx["block"], **await _record_pause(business, who, body.paused, reason, body.tx_hash.lower())}
    if reopening:
        # The owner returned it on-chain; the agent now decides afresh (new reasoning, new hash) through the normal pipeline.
        if not await models.claim_invoice_status(who.business_id, invoice["id"], "escalated", "retrying"):
            raise HTTPException(409, "That invoice is no longer escalated")
        return {"ok": True, "block": tx["block"], **await _rerun_invoice(business, invoice)}
    if ref:
        # Claim first: only one settlement can move the row out of 'escalated'.
        if not await models.claim_invoice_status(who.business_id, invoice["id"], "escalated",
                                                 "paid" if body.action == "approve_escalation" else "rejected"):
            raise HTTPException(409, "That invoice is no longer escalated")
        if body.action == "approve_escalation" and _made_by(invoice, who):
            # The contract has no maker-checker, so a wallet that builds the call by hand can get past the site. It is
            # recorded as it happened and flagged, never hidden.
            await models.log_admin_action(who.business_id, who.admin_address, "maker_checker_bypass", ref)
            return {"ok": True, "block": tx["block"], "warning": MAKER_CHECKER_MESSAGE}
    return {"ok": True, "block": tx["block"]}


async def _record_pause(business: dict, who: "auth.Principal", paused: bool, reason: str, tx_hash: str) -> dict:
    """Ledger entry (with the transaction hash) and the banner record for a pause or resume. An emergency stop never waits
    on the ledger: if the entry cannot be written the pause still stands and the answer says so."""
    reasoning_hash = None
    try:
        reasoning_hash = await decision_log.log_decision(
            "ADMIN_PAUSED" if paused else "ADMIN_RESUMED", f"business {business['slug']}",
            f"Payments {'paused' if paused else 'resumed'} by {who.admin_address}. Reason given: {reason}. Transaction {tx_hash}.",
            model_used="manual", business=business,
        )
    except Exception:
        log.exception("Could not write the ledger entry for a pause change")
    await models.save_pause_event(business["id"], paused, reason, who.admin_address, tx_hash, reasoning_hash)
    return {"paused": paused, "ledgered": reasoning_hash is not None}


@app.get("/pause-status")
async def pause_status(business: Optional[Slug] = None):
    """Public, read-only: are payments paused, and why. paused is null when the chain cannot be read (never a guess).
    The reason is shown publicly, so the admin site says so before it is typed."""
    biz = await _business_by_slug(business, active_only=False)
    paused = await admin_auth.public_paused(biz)
    out = {"business": biz["slug"], "paused": paused, "reason": None, "by": None, "since": None}
    if paused:
        event = await models.last_pause_event(biz["id"])
        if event is not None and event["paused"]:
            out.update(reason=event["reason"], by=event["admin_address"], since=event["created_at"])
    return out


async def _rerun_invoice(business: dict, invoice: sqlite3.Row) -> dict:
    """Runs a claimed invoice (status 'retrying') through the same pipeline as a new submission, from the stored row, so the
    wallet paid, category and amount are the ones on file. A failure puts it back to 'error' where it can be re-run."""
    vendor = await models.get_vendor(business["id"], invoice["vendor_id"])
    po = await models.get_purchase_order_by_id(business["id"], invoice["po_id"]) if invoice["po_id"] else None
    if vendor is None:
        await models.update_invoice_status(business["id"], invoice["id"], "error")
        return {"rerun": "error"}
    parked = payables.Invoice(
        business_id=business["id"], id=invoice["id"], invoice_number=invoice["invoice_number"], vendor_wallet=vendor["wallet_address"],
        amount_usdc=float(invoice["amount_usdc"]), category=invoice["category"], doc_hash=invoice["doc_hash"],
        po_number=po["po_number"] if po else None, mode=invoice["mode"], real_amount=invoice["real_amount"],
        real_currency=invoice["real_currency"],
    )
    try:
        runway = await treasury.runway_days(parked.amount_usdc, business)
        decision = await payables.process_invoice(parked, runway, business)
    except Exception:
        log.exception("Re-run of invoice %s failed", invoice["id"])
        await models.update_invoice_status(business["id"], invoice["id"], "error")
        return {"rerun": "error"}
    return {"rerun": "done", "decision": decision.value}


@app.post("/admin/invoices/{invoice_id}/rerun")
async def admin_rerun(invoice_id: int, who: auth.Principal = Depends(auth.require_admin)):
    """Return an invoice that ended in 'error' to the agent. Nothing was paid for it (the pipeline checks the chain first), so
    the agent decides again from the stored row. Own business only; one click at a time."""
    business = await _writable(who)
    invoice = await models.get_invoice(who.business_id, invoice_id)
    if invoice is None:
        raise HTTPException(404, "No invoice with that id")
    if invoice["status"] == "held" and invoice["held_because"] == "paused":
        try:
            if await asyncio.to_thread(contract.chain_for(business).is_paused):
                raise HTTPException(409, "Payments are still paused; resume them first")
        except HTTPException:
            raise
        except Exception:
            log.exception("pause state unreadable before a re-run")
            raise HTTPException(503, "Could not read the chain right now")
        if not await models.claim_paused_hold(who.business_id, invoice_id):
            raise HTTPException(409, "That invoice is no longer held")
    elif not await models.claim_invoice_status(who.business_id, invoice_id, "error", "retrying"):
        raise HTTPException(409, "Only an invoice in error, or held because payments were paused, can be re-run")
    await _admin_log(who, "rerun_invoice", f"invoice {invoice_id}")
    result = await _rerun_invoice(business, invoice)
    if result["rerun"] == "error":
        raise HTTPException(502, "The re-run did not complete; the invoice is back in error and the failure was logged")
    return {"ok": True, **result}


# ---- vendor applications and the vendor portal (v1.2.7; wallet signatures, see backend/vendor_auth.py)

MAX_PENDING_PER_IP = 3
APPLICATIONS_PER_HOUR_PER_IP = 5


def _ip_hash(request: Request) -> str:
    """Only used to count pending applications per client. Not reversible in practice, never returned by any route."""
    return hashlib.sha256(("ceedebooks-apply|" + auth.client_ip(request)).encode()).hexdigest()


class WalletChallengeIn(_Strict):
    address: str
    business: Optional[Slug] = None  # which business; default: the first business

    @field_validator("address")
    @classmethod
    def _addr(cls, v: str) -> str:
        return _check_address(v)


class ApplicationIn(_Strict):
    business_name: Annotated[str, Field(min_length=2, max_length=120)]
    wallet_address: str
    contact: Optional[Annotated[str, Field(min_length=3, max_length=120)]] = None
    nonce: str = Field(min_length=32, max_length=32)
    signature: str = Field(min_length=132, max_length=132)

    _addr = field_validator("wallet_address")(_check_address)

    @field_validator("business_name", "contact")
    @classmethod
    def _tidy(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = " ".join(v.split())
        if len(v) < 2 or any(ord(ch) < 32 for ch in v):
            raise ValueError("invalid text")
        return v


@app.post("/apply/challenge")
async def apply_challenge(body: WalletChallengeIn, request: Request):
    if not auth.request_limiter.allow("apply-challenge:" + auth.client_ip(request), 10, 60.0):
        raise HTTPException(429, "Too many attempts; try again in a minute")
    return vendor_auth.make_challenge(body.address, vendor_auth.APPLY, await _business_by_slug(body.business, active_only=True))


@app.post("/apply")
async def apply_to_be_a_vendor(body: ApplicationIn, request: Request):
    """Public. The wallet signature proves the applicant controls the payee address. Applications are private and
    start nothing by themselves: the admin accepts one, then approves the wallet on-chain with their own signature."""
    ip = auth.client_ip(request)
    if auth.failed_auth_limiter.blocked(ip, config.failed_auth_per_min, 60.0):
        raise HTTPException(429, "Too many failed attempts; try again later", headers={"Retry-After": "60"})
    if not auth.request_limiter.allow("apply:" + ip, APPLICATIONS_PER_HOUR_PER_IP, 3600.0):
        raise HTTPException(429, "Too many applications from this address; try again later", headers={"Retry-After": "3600"})
    consumed = vendor_auth.consume(body.nonce, body.signature, vendor_auth.APPLY)
    if consumed is None or consumed[0] != body.wallet_address.lower():
        auth.failed_auth_limiter.allow(ip, config.failed_auth_per_min, 60.0)
        raise HTTPException(401, "The wallet signature could not be verified; request a new challenge and sign again")
    signer, business_id = consumed  # the business the challenge was issued for, not one the caller can swap
    business = await models.get_business(business_id)
    if business is None or business["status"] != "active":
        raise HTTPException(404, "Business not found")
    if await models.get_vendor_by_wallet(business_id, signer) is not None:
        raise HTTPException(409, "This wallet is already a registered vendor. Sign in instead")
    ip_hash = _ip_hash(request)
    if await models.pending_applications_from(ip_hash) >= MAX_PENDING_PER_IP:
        raise HTTPException(429, "Too many applications from this address are still waiting for review")
    try:
        application_id = await models.save_application(business_id, body.business_name, signer, body.contact, ip_hash)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This wallet already has an application waiting for review")
    return {"id": application_id, "status": "pending"}


@app.get("/admin/applications")
async def admin_applications(who: auth.Principal = Depends(auth.require_admin)):
    return {"applications": await models.list_applications(who.business_id)}


@app.post("/admin/applications/{application_id}/accept")
async def admin_accept_application(application_id: int, who: auth.Principal = Depends(auth.require_admin)):
    """Creates the vendor record. It still cannot be paid until the admin approves its wallet on-chain."""
    application = await models.get_application(who.business_id, application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    if await models.get_vendor_by_wallet(who.business_id, application["wallet"]) is not None:
        raise HTTPException(409, "A vendor with this wallet already exists")
    vendor_id = await models.accept_application(who.business_id, application_id)
    if vendor_id is None:
        raise HTTPException(409, "This application was already decided")
    await _admin_log(who, "accept_application", f"application {application_id} -> vendor {vendor_id}")
    return {"ok": True, "vendor_id": vendor_id}


@app.post("/admin/applications/{application_id}/reject")
async def admin_reject_application(application_id: int, who: auth.Principal = Depends(auth.require_admin)):
    if await models.get_application(who.business_id, application_id) is None:
        raise HTTPException(404, "Application not found")
    if not await models.reject_application(who.business_id, application_id):
        raise HTTPException(409, "This application was already decided")
    await _admin_log(who, "reject_application", f"application {application_id}")
    return {"ok": True}


@app.post("/vendor/auth/challenge")
async def vendor_challenge(body: WalletChallengeIn, request: Request):
    """Answers the same for every address, so it cannot be used to find out which wallets are vendors."""
    if not auth.request_limiter.allow("vendor-challenge:" + auth.client_ip(request), 10, 60.0):
        raise HTTPException(429, "Too many sign-in attempts; try again in a minute")
    return vendor_auth.make_challenge(body.address, vendor_auth.SIGN_IN, await _business_by_slug(body.business, active_only=True))


@app.post("/vendor/auth/verify")
async def vendor_verify(body: AdminVerifyIn, request: Request):
    ip = auth.client_ip(request)
    if auth.failed_auth_limiter.blocked(ip, config.failed_auth_per_min, 60.0):
        raise HTTPException(429, "Too many failed attempts; try again later", headers={"Retry-After": "60"})
    consumed = vendor_auth.consume(body.nonce, body.signature, vendor_auth.SIGN_IN)
    business = await models.get_business(consumed[1]) if consumed else None
    vendor = await models.get_vendor_by_wallet(consumed[1], consumed[0]) if business and business["status"] == "active" else None
    if vendor is None:  # bad signature, unknown wallet and inactive business all look the same
        auth.failed_auth_limiter.allow(ip, config.failed_auth_per_min, 60.0)
        raise HTTPException(401, "Sign-in failed. If this wallet has not applied yet, apply first")
    return {**vendor_auth.start_session(business["id"], vendor["id"], consumed[0]), "name": vendor["name"], "business": business["slug"]}


@app.post("/vendor/auth/logout")
async def vendor_logout(request: Request, _: auth.Principal = Depends(auth.require("vendor"))):
    vendor_auth.logout(request.headers.get("authorization", "")[7:].strip())
    return {"ok": True}


@app.get("/vendor/me")
async def vendor_me(who: auth.Principal = Depends(auth.require("vendor"))):
    """Everything the portal shows: the vendor's own record, purchase orders and invoices. Nobody else's."""
    vendor = await models.get_vendor(who.business_id, who.vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    try:
        approved = await asyncio.to_thread(contract.chain_for(await models.get_business(who.business_id)).is_vendor_approved, vendor["wallet_address"])
    except Exception:
        approved = None  # unknown, shown as such
    pos, invoices = await asyncio.gather(
        models.list_vendor_purchase_orders(who.business_id, who.vendor_id), models.list_vendor_invoices(who.business_id, who.vendor_id))
    categories = {c["id"]: c["name"] for c in await models.list_categories(who.business_id)}
    for po in pos:
        po["category_name"] = categories.get(po["category"], f"Category {po['category']}")
    return {"vendor": {"id": vendor["id"], "name": vendor["name"], "wallet_address": vendor["wallet_address"]},
            "approved_onchain": approved, "purchase_orders": pos, "invoices": invoices}


# ---- Phase L3: onboarding a business. Apply (signed) -> operator accepts (server makes the hosted agent wallet) ->
# the owner creates the contract from their own wallet -> the server records it only after the chain proves it.

CUSTODY_NOTICE = ("We run your agent's wallet. It can only pay your approved vendors within your limits, and you can revoke it any time. "
                  "You keep the owner wallet that controls the money and the limits.")
MAX_PENDING_BUSINESS_APPS_PER_IP = 2
BUSINESS_APPS_PER_HOUR_PER_IP = 3
RESERVED_SLUGS = {"admin", "api", "portal", "www", "operator", "business", "businesses", "apply", "vendor", "vendors", "stats", "decisions", "ceedebooks-test"}
_ZERO_ADDRESS = "0x" + "0" * 40


class BusinessChallengeIn(_Strict):
    address: str
    _addr = field_validator("address")(_check_address)


class BusinessApplicationIn(_Strict):
    name: Annotated[str, Field(min_length=2, max_length=80)]
    slug: Slug
    wallet_address: str
    contact: Optional[Annotated[str, Field(min_length=3, max_length=120)]] = None
    nonce: str = Field(min_length=32, max_length=32)
    signature: str = Field(min_length=132, max_length=132)

    _addr = field_validator("wallet_address")(_check_address)


class BusinessStatusIn(_Strict):
    nonce: str = Field(min_length=32, max_length=32)
    signature: str = Field(min_length=132, max_length=132)


class RegisterBusinessIn(_Strict):
    application_id: int = Field(ge=1)
    tx_hash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")


class ExternalIn(_Strict):
    external: bool


class ArchiveIn(_Strict):
    archived: bool


def _business_view(business: dict) -> dict:
    return {"slug": business["slug"], "name": business["name"], "status": business["status"],
            "contract": business["enforcer_address"] or None, "agent_address": business["agent_address"] or None}


@app.post("/business/apply/challenge")
async def business_apply_challenge(body: BusinessChallengeIn, request: Request):
    if not auth.request_limiter.allow("bizapply-challenge:" + auth.client_ip(request), 10, 60.0):
        raise HTTPException(429, "Too many attempts; try again in a minute")
    return vendor_auth.make_challenge(body.address, vendor_auth.BUSINESS_APPLY, None)


@app.post("/business/apply")
async def business_apply(body: BusinessApplicationIn, request: Request):
    """Public. The signature proves the applicant controls the wallet that would own the business (its approver).
    Nothing starts by itself: the operator accepts, then the owner creates the contract with their own wallet."""
    ip = auth.client_ip(request)
    if auth.failed_auth_limiter.blocked(ip, config.failed_auth_per_min, 60.0):
        raise HTTPException(429, "Too many failed attempts; try again later", headers={"Retry-After": "60"})
    if not auth.request_limiter.allow("bizapply:" + ip, BUSINESS_APPS_PER_HOUR_PER_IP, 3600.0):
        raise HTTPException(429, "Too many applications from this address; try again later", headers={"Retry-After": "3600"})
    consumed = vendor_auth.consume(body.nonce, body.signature, vendor_auth.BUSINESS_APPLY)
    if consumed is None or consumed[0] != body.wallet_address.lower():
        auth.failed_auth_limiter.allow(ip, config.failed_auth_per_min, 60.0)
        raise HTTPException(401, "The wallet signature could not be verified; request a new challenge and sign again")
    signer = consumed[0]
    if signer == _ZERO_ADDRESS:
        raise HTTPException(422, "That is not a usable wallet address")
    if body.slug in RESERVED_SLUGS or await models.slug_in_use(body.slug):
        raise HTTPException(409, "That business address name is already taken; choose another")
    ip_hash = _ip_hash(request)
    if await models.pending_business_applications_from(ip_hash) >= MAX_PENDING_BUSINESS_APPS_PER_IP:
        raise HTTPException(429, "Too many applications from this address are still waiting for review")
    try:
        application_id = await models.save_business_application(body.name, body.slug, signer, body.contact, ip_hash)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "This wallet already has an application waiting, or that name was just taken")
    return {"id": application_id, "status": "pending"}


@app.post("/business/status/challenge")
async def business_status_challenge(body: BusinessChallengeIn, request: Request):
    if not auth.request_limiter.allow("bizstatus-challenge:" + auth.client_ip(request), 20, 60.0):
        raise HTTPException(429, "Too many attempts; try again in a minute")
    return vendor_auth.make_challenge(body.address, vendor_auth.BUSINESS_STATUS, None)


@app.post("/business/status")
async def business_status(body: BusinessStatusIn, request: Request):
    """Signed read: an owner sees only the applications made with their own wallet, and what to do next."""
    ip = auth.client_ip(request)
    if auth.failed_auth_limiter.blocked(ip, config.failed_auth_per_min, 60.0):
        raise HTTPException(429, "Too many failed attempts; try again later", headers={"Retry-After": "60"})
    consumed = vendor_auth.consume(body.nonce, body.signature, vendor_auth.BUSINESS_STATUS)
    if consumed is None:
        auth.failed_auth_limiter.allow(ip, config.failed_auth_per_min, 60.0)
        raise HTTPException(401, "The wallet signature could not be verified; request a new challenge and sign again")
    out = []
    for a in await models.list_business_applications(consumed[0]):
        item = {"id": a["id"], "name": a["name"], "slug": a["slug"], "status": a["status"], "created_at": a["created_at"]}
        if a["status"] in ("accepted", "registered") and a["business_id"]:
            business = await models.get_business(a["business_id"])
            item["agent_address"] = a["agent_address"]
            item["contract"] = (business or {}).get("enforcer_address") or None
        out.append(item)
    return {"wallet": consumed[0], "applications": out, "factory": config.budget_factory_address, "chain_id": vendor_auth.CHAIN_ID,
            "custody": CUSTODY_NOTICE}


@app.post("/business/register")
async def business_register(body: RegisterBusinessIn, request: Request):
    """Public, and safe to be: a business goes live only if the CHAIN shows our factory made it, the applicant's wallet is its
    approver and the agent wallet we issued is its agent. A transaction made by anyone else cannot satisfy those checks."""
    if not auth.request_limiter.allow("bizreg:" + auth.client_ip(request), 20, 3600.0):
        raise HTTPException(429, "Too many attempts; try again later", headers={"Retry-After": "3600"})
    application = await models.get_business_application(body.application_id)
    if application is None:
        raise HTTPException(404, "Application not found")
    tx_hash = body.tx_hash.lower()
    business = await models.get_business(application["business_id"]) if application["business_id"] else None
    if application["status"] == "registered":
        if business and (business["created_tx_hash"] or "").lower() == tx_hash:
            return {**_business_view(business), "admin_url": f"{config.admin_origin}/?business={business['slug']}"}
        raise HTTPException(409, "This application is already registered")
    if application["status"] != "accepted" or business is None or business["status"] != "pending":
        raise HTTPException(409, "This application is not waiting for its contract")
    try:
        info = await asyncio.to_thread(contract.verify_business_creation, tx_hash, config.budget_factory_address)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        log.exception("business registration: chain read failed")
        raise HTTPException(503, "Could not read the chain right now; try again in a minute")
    if info["approver"].lower() != application["owner_wallet"]:
        raise HTTPException(422, "That business was not created by the wallet that applied")
    if info["agent"].lower() != (application["agent_address"] or "").lower():
        raise HTTPException(422, "That business does not use the agent wallet we issued for it")
    try:
        ok = await models.activate_business(business["id"], application["id"], info["enforcer"], info["approver"], tx_hash)
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That contract is already registered")
    if not ok:
        raise HTTPException(409, "This application is not waiting for its contract")
    live = await models.get_business(business["id"])
    return {**_business_view(live), "admin_url": f"{config.admin_origin}/?business={live['slug']}"}


@app.get("/businesses")
async def public_businesses():
    """Active businesses with their contract and agent wallet addresses, so anyone can check them on the explorer.
    The approver is read from each contract now, never from the stored column: a rotated key must not show as the owner.
    If it cannot be read it is null (fail closed), never the old value."""
    listed = await models.list_public_businesses()
    live = await asyncio.gather(*(admin_auth.public_approver(
        {"id": b["id"], "enforcer_address": b["contract"], "contract_version": b["contract_version"]}) for b in listed))
    for b, approver in zip(listed, live):
        b["approver"] = approver
        if approver:
            await models.refresh_approver(b["id"], approver)
    return {"businesses": listed, "counts": {**await models.business_counts(), "archived": await models.archived_count()}, "custody": CUSTODY_NOTICE}


# ---- operator (the home business's wallet-signed admin)

EXPLORER_URL = "https://explorer.testnet.arc.io"


@app.get("/businesses/{slug}/traction")
async def business_traction(slug: Slug):
    """Public, no login. One business's outcomes with live and shadow kept apart, the agreement rate with how many
    verdicts it rests on, and its latest paid decisions with links anyone can check. No contacts or private fields."""
    business = await _business_by_slug(slug, active_only=True)
    paid = await models.latest_paid(business["id"])
    return {
        "business": {"name": business["name"], "slug": business["slug"], "contract": business["enforcer_address"],
                     "agent_wallet": business["agent_address"],
                     "kind": "own" if business["id"] == models.HOME_BUSINESS_ID else ("outside" if business["external"] else "unconfirmed")},
        "live": await models.live_counts(business["id"]),
        "shadow": await models.shadow_stats(business["id"]),
        "latest_paid": [
            {"invoice": r["invoice_number"], "mode": r["mode"], "usdc": r["amount_usdc"],
             "real_amount": r["real_amount"], "real_currency": r["real_currency"],
             "reasoning_hash": r["reasoning_hash"], "tx_hash": r["chain_tx_hash"], "time": r["timestamp"],
             "tx_url": f"{EXPLORER_URL}/tx/{r['chain_tx_hash']}" if r["chain_tx_hash"] else None}
            for r in paid
        ],
    }


@app.get("/operator/business-applications")
async def operator_business_applications(_: auth.Principal = Depends(auth.require_operator)):
    return {"applications": await models.list_business_applications(), "businesses": await models.list_businesses()}


@app.post("/operator/business-applications/{application_id}/accept")
async def operator_accept_business(application_id: int, who: auth.Principal = Depends(auth.require_operator)):
    """Creates the business's hosted agent wallet and records it. The application must be pending (a double click does nothing)."""
    claimed = await models.claim_business_application(application_id)
    if claimed is None:
        if await models.get_business_application(application_id) is None:
            raise HTTPException(404, "Application not found")
        raise HTTPException(409, "This application is not pending")
    try:
        if not claimed["agent_address"]:
            wallet = await asyncio.to_thread(contract.create_agent_wallet, claimed["slug"])
            await models.store_application_agent(application_id, wallet["wallet_id"], wallet["address"])
        business_id = await models.finish_accepting(application_id)
    except sqlite3.IntegrityError:
        await models.release_business_application(application_id)
        raise HTTPException(409, "That business name is already used by another business")
    except Exception:
        log.exception("accepting business application %s failed", application_id)
        await models.release_business_application(application_id)
        raise HTTPException(502, "The agent wallet could not be created right now; nothing was accepted. Try again in a minute")
    if business_id is None:
        await models.release_business_application(application_id)
        raise HTTPException(409, "This application could not be accepted; try again")
    await _admin_log(who, "accept_business", str(application_id))
    business = await models.get_business(business_id)
    return {"id": application_id, "status": "accepted", "business": _business_view(business)}


@app.post("/operator/business-applications/{application_id}/reject")
async def operator_reject_business(application_id: int, who: auth.Principal = Depends(auth.require_operator)):
    if await models.get_business_application(application_id) is None:
        raise HTTPException(404, "Application not found")
    if not await models.reject_business_application(application_id):
        raise HTTPException(409, "This application is not pending")
    await _admin_log(who, "reject_business", str(application_id))
    return {"id": application_id, "status": "rejected"}


@app.post("/operator/businesses/{business_id}/external")
async def operator_mark_external(business_id: int, body: ExternalIn, who: auth.Principal = Depends(auth.require_operator)):
    """Only the operator says whether a business is an outside party . The home business can never be marked."""
    if not await models.set_business_external(business_id, body.external):
        raise HTTPException(404, "Business not found, not active, or the operator's own")
    await _admin_log(who, "mark_external" if body.external else "unmark_external", str(business_id))
    return {"id": business_id, "external": body.external}


@app.post("/operator/businesses/{business_id}/archive")
async def operator_archive_business(business_id: int, body: ArchiveIn, who: auth.Principal = Depends(auth.require_operator)):
    """Hide a business from the public pages and make it read-only (or undo that). Its decisions and ledger still verify.
    The home business can never be archived."""
    if not await models.set_business_archived(business_id, body.archived):
        raise HTTPException(404, "Business not found, not in the right state, or the operator's own")
    await _admin_log(who, "archive_business" if body.archived else "unarchive_business", str(business_id))
    return {"id": business_id, "archived": body.archived}


# ---- the owner's setup checklist (their own admin session, their own business only)

@app.get("/admin/onboarding")
async def admin_onboarding(who: auth.Principal = Depends(auth.require_admin)):
    business = await models.get_business(who.business_id)
    vendors, pos, cats = await asyncio.gather(
        models.list_vendors(who.business_id), models.list_purchase_orders(who.business_id), models.list_categories(who.business_id))

    def read():
        steps = []
        chain = contract.chain_for(business)

        def step(key, label, fn):
            try:
                done = bool(fn())
            except Exception:
                done = None  # unreadable: shown as unknown, never as done
            steps.append({"key": key, "label": label, "done": done})

        agent = business["agent_address"]
        step("agent_gas", f"Send about 0.5 USDC to the agent wallet {agent} so it can pay network fees" if agent else "Agent wallet gas",
             (lambda: contract.native_balance(agent) >= contract.GAS_DUST_UNITS) if agent else (lambda: None))
        step("pool_funded", "Add USDC to your pool with the Add funds button below (a normal wallet send to the contract does not work)", lambda: chain.usdc_balance() > 0)
        step("category_limit", "Create a spending category and set its daily limit (Limits tab)",
             lambda: any(category_rules.usable(chain.category_limit(c["id"])) for c in cats))
        return steps

    steps = await asyncio.to_thread(read)
    steps.append({"key": "vendor", "label": "Add a vendor and approve its wallet", "done": len(vendors) > 0})
    steps.append({"key": "purchase_order", "label": "Create a purchase order for it", "done": len(pos) > 0})
    return {"business": business["slug"], "contract": business["enforcer_address"], "agent_address": business["agent_address"] or None,
            "steps": steps, "custody": CUSTODY_NOTICE}
