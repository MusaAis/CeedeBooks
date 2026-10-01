"""FastAPI app: invoice intake, vendor/PO/receipt setup, and the audit trail."""
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from agent import decision_log, payables
from backend import models

app = FastAPI(title="CeedeBooks API")

_ADDRESS_RE = r"^0x[a-fA-F0-9]{40}$"


class VendorIn(BaseModel):
    name: str
    wallet_address: str

    @field_validator("wallet_address")
    @classmethod
    def valid_address(cls, v: str) -> str:
        import re
        if not re.match(_ADDRESS_RE, v):
            raise ValueError("wallet_address must be a 0x-prefixed 40-hex-char address")
        return v


class PurchaseOrderIn(BaseModel):
    po_number: str
    vendor_id: int
    amount_usdc: float
    category: int


class ReceiptIn(BaseModel):
    po_id: int
    confirmed_by: str
    confirmed_by_role: str


class InvoiceIn(BaseModel):
    invoice_number: str
    vendor_id: int
    vendor_wallet: str
    amount_usdc: float
    category: int
    doc_hash: str
    po_number: Optional[str] = None
    treasury_runway_days: float = 30

    @field_validator("vendor_wallet")
    @classmethod
    def valid_address(cls, v: str) -> str:
        import re
        if not re.match(_ADDRESS_RE, v):
            raise ValueError("vendor_wallet must be a 0x-prefixed 40-hex-char address")
        return v


@app.on_event("startup")
async def startup() -> None:
    await models.init_db()
    await decision_log.init_db()


@app.post("/vendors")
async def create_vendor(body: VendorIn):
    return {"id": await models.save_vendor(body.name, body.wallet_address)}


@app.get("/vendors/{vendor_id}")
async def get_vendor(vendor_id: int):
    vendor = await models.get_vendor(vendor_id)
    if vendor is None:
        raise HTTPException(404, "Vendor not found")
    return dict(vendor)


@app.post("/purchase-orders")
async def create_purchase_order(body: PurchaseOrderIn):
    return {"id": await models.save_purchase_order(body.po_number, body.vendor_id, body.amount_usdc, body.category)}


@app.post("/receipts")
async def create_receipt(body: ReceiptIn):
    if body.confirmed_by_role == "agent":
        raise HTTPException(400, "Receipts cannot be confirmed by the agent role")
    return {"id": await models.save_receipt(body.po_id, body.confirmed_by, body.confirmed_by_role)}


@app.post("/invoices")
async def submit_invoice(body: InvoiceIn):
    po = await models.get_purchase_order(body.po_number) if body.po_number else None
    try:
        invoice_id = await models.save_invoice(
            body.invoice_number, body.vendor_id, po["id"] if po else None, body.amount_usdc, body.category, body.doc_hash
        )
    except Exception:
        raise HTTPException(409, f"Invoice number '{body.invoice_number}' already exists")
    invoice = payables.Invoice(
        id=invoice_id,
        invoice_number=body.invoice_number,
        vendor_wallet=body.vendor_wallet,
        amount_usdc=body.amount_usdc,
        category=body.category,
        doc_hash=body.doc_hash,
        po_number=body.po_number,
    )
    try:
        decision = await payables.process_invoice(invoice, body.treasury_runway_days)
    except Exception as e:
        await models.update_invoice_status(invoice_id, "error")
        raise HTTPException(500, f"Processing failed: {e}")
    return {"invoice_id": invoice_id, "decision": decision.value}


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: int):
    invoice = await models.get_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found")
    return dict(invoice)


@app.get("/decisions/{reasoning_hash}")
async def get_decision(reasoning_hash: str):
    decision = await decision_log.get_decision(reasoning_hash)
    if decision is None:
        raise HTTPException(404, "Decision not found")
    return dict(decision)


@app.get("/decisions/{reasoning_hash}/verify")
async def verify_decision(reasoning_hash: str):
    return {"reasoning_hash": reasoning_hash, "verified": await decision_log.verify_roundtrip(reasoning_hash)}
