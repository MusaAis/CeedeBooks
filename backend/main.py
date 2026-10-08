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

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent import contract, decision_log, payables, treasury
from agent.config import config
from agent.categories import Category as SpendCategory
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

    @field_validator("vendor_wallet")
    @classmethod
    def valid_address(cls, v: Optional[str]) -> Optional[str]:
        return v if v is None else _check_address(v)


# ---- middleware

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


# ---- setup (buyer only)

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


@app.post("/purchase-orders")
async def create_purchase_order(body: PurchaseOrderIn, who: auth.Principal = Depends(auth.require("buyer"))):
    await _writable(who)
    if await models.get_vendor(who.business_id, body.vendor_id) is None:
        raise HTTPException(404, "Vendor not found")
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

async def _checked_submission(body: InvoiceIn, who: auth.Principal):
    """Access and consistency checks shared by /invoices and /invoices/preflight. Returns (vendor, po, business)."""
    business = await _writable(who)
    if who.role == "vendor" and who.vendor_id != body.vendor_id:
        raise HTTPException(403, "A vendor key can only submit invoices for its own vendor_id")
    if body.origin is not None and who.role != "buyer":
        raise HTTPException(403, "Only the buyer can label an invoice's origin")

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
    return vendor, po, business


@app.post("/invoices/preflight")
async def preflight_invoice(body: InvoiceIn, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    """Dry run: would this invoice be paid, held or escalated right now, and why? Nothing is saved, logged or sent
    on-chain, so an agent can fix problems before submitting. Same access rules as POST /invoices."""
    vendor, _, business = await _checked_submission(body, who)
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
    return {**result, "dry_run": True}


@app.post("/invoices")
async def submit_invoice(body: InvoiceIn, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    vendor, po, business = await _checked_submission(body, who)
    amount = float(body.amount_usdc)
    try:
        invoice_id = await models.save_invoice(
            who.business_id, body.invoice_number, body.vendor_id, po["id"] if po else None, amount, body.category, body.doc_hash,
            body.origin or "agent",
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
            "businesses": await models.business_counts()}


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
        },
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
    "set_vendor", "set_category_limit", "set_paused", "approve_escalation", "reject_escalation", "accept_approver"
]


class AdminActionIn(_Strict):
    action: AdminActionName
    tx_hash: str = Field(pattern=_TX_RE)
    invoice_id: Optional[int] = None


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


def _chain_snapshot(business: dict) -> dict:
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
        cats = []
        for c in SpendCategory:
            overall, in_cat = chain.remaining_today(int(c))
            cats.append({"id": int(c), "name": c.name.replace("_", " ").title(),
                         "daily_limit_usdc": chain.category_limit(int(c)) / 1_000_000, "remaining_usdc": in_cat / 1_000_000})
        snap["categories"] = cats
    except Exception:
        log.exception("admin overview chain read failed")
        snap["chain_ok"] = False
    return snap


@app.get("/admin/overview")
async def admin_overview(who: auth.Principal = Depends(auth.require_admin)):
    business = await models.get_business(who.business_id)
    snap, counts, vendors, pos, pending = await asyncio.gather(
        asyncio.to_thread(_chain_snapshot, business), models.invoice_counts(who.business_id), models.list_vendors(who.business_id),
        models.list_purchase_orders(who.business_id), models.count_pending_applications(who.business_id),
    )
    return {"chain": snap, "invoices": counts, "vendors": len(vendors), "purchase_orders": len(pos),
            "pending_applications": pending, "operator": who.business_id == models.HOME_BUSINESS_ID}


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


@app.get("/admin/invoices")
async def admin_invoices(who: auth.Principal = Depends(auth.require_admin)):
    return {"invoices": await models.list_invoices(who.business_id)}


@app.get("/admin/actions")
async def admin_actions(who: auth.Principal = Depends(auth.require_admin)):
    return {"actions": await models.list_admin_actions(who.business_id)}


@app.get("/admin/invoices/{invoice_id}/escalation")
async def admin_escalation(invoice_id: int, who: auth.Principal = Depends(auth.require_admin)):
    """The on-chain key the wallet needs to approve or reject this escalated invoice."""
    invoice = await models.get_invoice(who.business_id, invoice_id)
    if invoice is None or invoice["status"] != "escalated" or not invoice["reasoning_hash"]:
        raise HTTPException(404, "No escalation waiting for this invoice")
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
    return {"invoice_key": key}


@app.post("/admin/actions")
async def admin_record_action(body: AdminActionIn, who: auth.Principal = Depends(auth.require_admin)):
    """Record an on-chain admin transaction after the wallet sent it. The server checks the chain itself: the transaction
    must be to this business's contract, from this admin, and successful. An escalation result also settles the invoice's status,
    but only if the event on-chain carries that invoice's own reasoning hash."""
    business = await models.get_business(who.business_id)
    try:
        tx = await asyncio.to_thread(contract.chain_for(business).inspect_admin_tx, body.tx_hash)
    except Exception:
        raise HTTPException(404, "That transaction is not confirmed on-chain yet")
    if tx["sender"] != who.admin_address or tx["to"] != business["enforcer_address"].lower() or not tx["success"]:
        raise HTTPException(422, "That transaction is not a successful call to the contract from your wallet")

    ref = None
    if body.action in ("approve_escalation", "reject_escalation"):
        invoice = await models.get_invoice(who.business_id, body.invoice_id) if body.invoice_id is not None else None
        wanted = "EscalationApproved" if body.action == "approve_escalation" else "EscalationRejected"
        if invoice is None or tx["escalation"] is None or tx["escalation"][0] != wanted \
                or tx["escalation"][2] != (invoice["reasoning_hash"] or ""):
            raise HTTPException(422, "The transaction does not settle that invoice's escalation")
        ref = f"invoice {invoice['id']}"
    try:
        await models.log_admin_action(who.business_id, who.admin_address, body.action, ref, body.tx_hash.lower(), tx["block"])
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That transaction is already recorded")
    if ref:
        await models.update_invoice_status(who.business_id, invoice["id"], "paid" if body.action == "approve_escalation" else "rejected")
    return {"ok": True, "block": tx["block"]}


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
    categories = {int(c): c.name.replace("_", " ").title() for c in SpendCategory}
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
    """Active businesses with their contract and agent wallet addresses, so anyone can check them on the explorer."""
    return {"businesses": await models.list_public_businesses(), "counts": await models.business_counts(), "custody": CUSTODY_NOTICE}


# ---- operator (the home business's wallet-signed admin)

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


# ---- the owner's setup checklist (their own admin session, their own business only)

@app.get("/admin/onboarding")
async def admin_onboarding(who: auth.Principal = Depends(auth.require_admin)):
    business = await models.get_business(who.business_id)
    vendors, pos = await asyncio.gather(models.list_vendors(who.business_id), models.list_purchase_orders(who.business_id))

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
        step("category_limit", "Set a daily limit for at least one spending category", lambda: any(chain.category_limit(int(c)) > 0 for c in SpendCategory))
        return steps

    steps = await asyncio.to_thread(read)
    steps.append({"key": "vendor", "label": "Add a vendor and approve its wallet", "done": len(vendors) > 0})
    steps.append({"key": "purchase_order", "label": "Create a purchase order for it", "done": len(pos) > 0})
    return {"business": business["slug"], "contract": business["enforcer_address"], "agent_address": business["agent_address"] or None,
            "steps": steps, "custody": CUSTODY_NOTICE}
