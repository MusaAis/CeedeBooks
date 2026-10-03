"""FastAPI app: invoice intake, vendor/PO/receipt setup, and the audit trail.

Access model (see backend/auth.py): buyer key for setup and receipts, vendor key for its own invoices only, no key for the read-only audit endpoints. No client-supplied value can influence a payment decision: the receipt role comes from the key, the vendor wallet from the vendor on file, and the treasury runway from the chain.
"""
import asyncio
import logging
import re
import sqlite3
from decimal import Decimal
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent import contract, decision_log, payables, treasury
from agent.config import config
from backend import auth, models

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
    allow_origins=list(config.cors_origins),
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)


@app.on_event("startup")
async def startup() -> None:
    await models.init_db()
    await decision_log.init_db()


# ---- setup (buyer only)

@app.post("/vendors")
async def create_vendor(body: VendorIn, _: auth.Principal = Depends(auth.require("buyer"))):
    return {"id": await models.save_vendor(body.name, body.wallet_address)}


@app.get("/vendors/{vendor_id}")
async def get_vendor(vendor_id: int, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    if who.role == "vendor" and who.vendor_id != vendor_id:
        raise HTTPException(404, "Vendor not found")
    vendor = await models.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    return dict(vendor)


@app.post("/purchase-orders")
async def create_purchase_order(body: PurchaseOrderIn, _: auth.Principal = Depends(auth.require("buyer"))):
    if await models.get_vendor(body.vendor_id) is None:
        raise HTTPException(404, "Vendor not found")
    try:
        po_id = await models.save_purchase_order(body.po_number, body.vendor_id, float(body.amount_usdc), body.category)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"PO number '{body.po_number}' already exists")
    return {"id": po_id}


@app.post("/receipts")
async def create_receipt(body: ReceiptIn, who: auth.Principal = Depends(auth.require("buyer"))):
    """Only the buyer can confirm delivery. The role is set here from the API key, never from the request."""
    if await models.get_purchase_order_by_id(body.po_id) is None:
        raise HTTPException(404, "Purchase order not found")
    if await models.get_receipt(body.po_id) is not None:
        raise HTTPException(409, "A receipt is already on file for this purchase order")
    return {"id": await models.save_receipt(body.po_id, body.confirmed_by or who.label, who.role)}


# ---- invoices (buyer, or the vendor itself)

async def _checked_submission(body: InvoiceIn, who: auth.Principal):
    """Access and consistency checks shared by /invoices and /invoices/preflight. Returns (vendor, po)."""
    if who.role == "vendor" and who.vendor_id != body.vendor_id:
        raise HTTPException(403, "A vendor key can only submit invoices for its own vendor_id")

    vendor = await models.get_vendor(body.vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    if body.vendor_wallet is not None and body.vendor_wallet.lower() != vendor["wallet_address"].lower():
        raise HTTPException(422, "vendor_wallet does not match the wallet on file for this vendor")

    po = await models.get_purchase_order(body.po_number) if body.po_number else None
    if po is not None and po["vendor_id"] != body.vendor_id:
        raise HTTPException(403, "That purchase order does not belong to this vendor")

    if po is not None and po["category"] != body.category:
        raise HTTPException(422, "Invoice category does not match the purchase order's category")
    return vendor, po


@app.post("/invoices/preflight")
async def preflight_invoice(body: InvoiceIn, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    """Dry run: would this invoice be paid, held or escalated right now, and why? Nothing is saved, logged or sent
    on-chain, so an agent can fix problems before submitting. Same access rules as POST /invoices."""
    vendor, _ = await _checked_submission(body, who)
    amount = float(body.amount_usdc)
    invoice = payables.Invoice(
        id=0, invoice_number=body.invoice_number, vendor_wallet=vendor["wallet_address"],
        amount_usdc=amount, category=body.category, doc_hash=body.doc_hash, po_number=body.po_number,
    )
    try:
        runway = await treasury.runway_days(amount)
        result = await payables.preflight(invoice, runway)
    except Exception:
        log.exception("Preflight failed for %s", body.invoice_number)
        raise HTTPException(503, "Could not read the chain right now; try again shortly")
    return {**result, "dry_run": True}


@app.post("/invoices")
async def submit_invoice(body: InvoiceIn, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    vendor, po = await _checked_submission(body, who)
    amount = float(body.amount_usdc)
    try:
        invoice_id = await models.save_invoice(
            body.invoice_number, body.vendor_id, po["id"] if po else None, amount, body.category, body.doc_hash
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Invoice number '{body.invoice_number}' already exists")

    invoice = payables.Invoice(
        id=invoice_id,
        invoice_number=body.invoice_number,
        vendor_wallet=vendor["wallet_address"],  # always the wallet on file, never a client-supplied one
        amount_usdc=amount,
        category=body.category,
        doc_hash=body.doc_hash,
        po_number=body.po_number,
    )
    try:
        runway = await treasury.runway_days(amount)  # computed server-side from the chain; not a request field
        decision = await payables.process_invoice(invoice, runway)
    except Exception:
        log.exception("Invoice %s processing failed", body.invoice_number)
        await models.update_invoice_status(invoice_id, "error")
        raise HTTPException(500, "Processing failed; the failure was logged")
    return {"invoice_id": invoice_id, "decision": decision.value}


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int, who: auth.Principal = Depends(auth.require("buyer", "vendor"))):
    invoice = await models.get_invoice(invoice_id)
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
    try:
        events = await asyncio.to_thread(contract.reasoning_events, tx_hash)
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


@app.get("/decisions")
async def list_decisions(limit: int = 20):
    """Most recent audit entries, newest first (no reasoning text; fetch one by hash for that)."""
    return {"decisions": await decision_log.recent(max(1, min(limit, 50)))}


@app.get("/stats")
async def get_stats():
    return await decision_log.stats()


@app.get("/.well-known/agent.json")
async def agent_manifest():
    """Machine-readable description so another agent can discover how to work with this one. Public, static."""
    return {
        "name": "CeedeBooks",
        "description": "Autonomous accounts-payable agent. It pays an invoice only if a smart contract allows it.",
        "network": "Arc Testnet",
        "settlement": "testnet USDC (no market value)",
        "contract": config.budget_enforcer_address,
        "auth": {"header": "X-API-Key", "roles": ["buyer", "vendor"], "public": ["/decisions", "/stats"]},
        "vendor_flow": [
            {"step": 1, "who": "buyer", "action": "register the vendor (POST /vendors) and approve its wallet on-chain"},
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
