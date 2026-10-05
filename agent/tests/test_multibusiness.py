"""Phase L2: one backend, many businesses. Every route and every query is scoped to the caller's business.

Business 1 is the original CeedeBooks on the v1 contract; business 2 ("acme") is a v2 business with its own contract,
approver and agent. The chain is always mocked (see conftest.FakeChain).
"""
import asyncio
import hashlib
import sqlite3
import time

import aiosqlite
import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi.testclient import TestClient

from agent import contract, decision_log, payables
from agent.config import config
from backend import admin_auth, auth, main, models, vendor_auth

ADMIN_1 = Account.create()   # approver of business 1
ADMIN_2 = Account.create()   # approver of business 2
VENDOR = Account.create()    # a wallet that is a vendor in BOTH businesses
ACME_ADDRESS = "0x" + "bb" * 20
WALLET = "0x" + "c3" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, chains):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "multi.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    admin_auth.reset()
    vendor_auth.reset()
    auth.request_limiter.reset()
    auth.failed_auth_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)
    monkeypatch.setattr(config, "failed_auth_per_min", 1000)
    chains.get(config.budget_enforcer_address, 1).approver_addr = ADMIN_1.address
    chains.get(ACME_ADDRESS, 2).approver_addr = ADMIN_2.address
    # runway and the money pipeline are stubbed: these tests are about who can reach what, not about decisions
    seen = {"business_ids": []}

    async def fake_runway(amount, business):
        return 42.0

    async def fake_process(invoice, runway, business):
        seen["business_ids"].append((invoice.business_id, business["id"]))
        return payables.Decision.HOLD

    async def fake_preflight(invoice, runway, business):
        return {"outcome": "pay", "reasons": [], "checks": {}}

    monkeypatch.setattr(main.treasury, "runway_days", fake_runway)
    monkeypatch.setattr(main.payables, "process_invoice", fake_process)
    monkeypatch.setattr(main.payables, "preflight", fake_preflight)
    return seen


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _add_business(slug="acme", address=ACME_ADDRESS, version=2, status="active") -> int:
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            cur = await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, contract_version, status, created_at) VALUES (?, ?, ?, ?, ?, 0)",
                (slug.title(), slug, address, version, status))
            await db.commit()
            return cur.lastrowid
    return asyncio.run(go())


def _key(business_id, role="buyer", vendor_id=None):
    key = auth.generate_key(role)
    asyncio.run(models.save_api_key(business_id, auth.hash_key(key), role, vendor_id, "t"))
    return {"X-API-Key": key}


def _sign(account, message):
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _admin_token(client, account, business=None):
    body = {"address": account.address} | ({"business": business} if business else {})
    ch = client.post("/admin/auth/challenge", json=body).json()
    r = client.post("/admin/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(account, ch["message"])})
    return r


def _vendor_login(client, account, business=None):
    body = {"address": account.address} | ({"business": business} if business else {})
    ch = client.post("/vendor/auth/challenge", json=body).json()
    return client.post("/vendor/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(account, ch["message"])})


@pytest.fixture
def two(client):
    """Business 1 and business 2, each with a buyer key, a vendor, a PO (same number PO-1) and an invoice (same number INV-1)."""
    b2 = _add_business()
    world = {"b2": b2}
    for n, bid in ((1, 1), (2, b2)):
        buyer = _key(bid)
        v = client.post("/vendors", json={"name": f"V{n}", "wallet_address": WALLET}, headers=buyer).json()["id"]
        po = client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": v, "amount_usdc": "5", "category": 0},
                         headers=buyer)
        assert po.status_code == 200, po.text
        inv = client.post("/invoices", json={"invoice_number": "INV-1", "vendor_id": v, "amount_usdc": "5", "category": 0,
                                             "doc_hash": "ab" * 32, "po_number": "PO-1"}, headers=buyer)
        assert inv.status_code == 200, inv.text
        world[n] = {"buyer": buyer, "vendor": v, "po": po.json()["id"], "invoice": inv.json()["invoice_id"],
                    "vkey": _key(bid, "vendor", v)}
    return world


# ---- the contract client: v1 and v2 commitments, copied from the real contracts (anvil, chain id 5042002)

def test_commitments_match_what_the_real_contracts_compute():
    inv, doc, rh = bytes.fromhex("aa" * 32), bytes.fromhex("bb" * 32), bytes.fromhex("cc" * 32)
    vendor = "0x" + "11" * 20
    v2 = contract.Chain("0x5FbDB2315678afecb367f032d93F642f64180aa3", 2, "")
    v1 = contract.Chain("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512", 1, "")
    assert v2.commitment_for(vendor, 2_200_000, inv, doc, 1, rh).hex() == "25630d90b25286e438cba0ffca48c2b8cd1f841cc4f0c32e82227d60b7de0179"
    assert v1.commitment_for(vendor, 2_200_000, inv, doc, 1, rh).hex() == "377453f2feb16902a671d9eee3388c96bf5e5ac38a3f792381cf0799a400633f"


def test_a_v2_commitment_cannot_be_reused_on_another_contract():
    args = ("0x" + "11" * 20, 1, b"\x01" * 32, b"\x02" * 32, 0, b"\x03" * 32)
    a, b = contract.Chain("0x" + "aa" * 20, 2, ""), contract.Chain("0x" + "bb" * 20, 2, "")
    assert a.commitment_for(*args) != b.commitment_for(*args)


def test_an_unknown_contract_version_is_refused():
    with pytest.raises(ValueError):
        contract.Chain("0x" + "aa" * 20, 3, "")


def test_only_events_from_this_businesss_contract_count(monkeypatch):
    mine, theirs = "0x" + "aa" * 20, "0x" + "bb" * 20
    topic = next(t for t, (n, _) in contract._REASONING_EVENTS.items() if n == "PaymentMade")
    forged = {"address": theirs, "topics": [bytes.fromhex(topic), b"\x00" * 32, bytes.fromhex("dd" * 32)]}
    real = {"address": mine, "topics": [bytes.fromhex(topic), b"\x00" * 32, bytes.fromhex("ee" * 32)]}
    monkeypatch.setattr(contract._w3.eth, "get_transaction_receipt", lambda h: {"logs": [forged, real], "status": 1, "blockNumber": 9})
    events = contract.Chain(mine, 2, "").reasoning_events("0xabc")
    assert [e["reasoning_hash"] for e in events] == ["ee" * 32]


# ---- the business registry and the migration

def test_business_one_is_the_v1_contract_from_the_environment():
    home = asyncio.run(models.get_business(1))
    assert (home["slug"], home["contract_version"], home["status"]) == ("ceedebooks", 1, "active")
    assert home["enforcer_address"] == config.budget_enforcer_address


def test_seeding_is_idempotent_and_never_overwrites_a_moved_business(monkeypatch):
    async def move():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'archived' WHERE id = 1")
            await db.commit()
    asyncio.run(move())
    asyncio.run(models.init_db())
    assert asyncio.run(models.get_business(1))["status"] == "archived"


OLD_SCHEMA = """CREATE TABLE IF NOT EXISTS vendors (
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
OLD_AUDIT = ("CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, subject TEXT NOT NULL, reasoning TEXT NOT NULL, "
             "amount_usdc REAL NOT NULL, treasury_balance_after REAL, model_used TEXT NOT NULL, timestamp REAL NOT NULL, hash_input TEXT NOT NULL, "
             "reasoning_hash TEXT NOT NULL UNIQUE, onchain_tx_id TEXT, chain_tx_hash TEXT)")


def test_a_v1_2_7_database_migrates_without_losing_a_row(tmp_path, monkeypatch):
    path = str(tmp_path / "prod.db")
    old_input = '{"action":"INVOICE_PAID","amount_usdc":2.2,"model_used":"manual","reasoning":"r","subject":"NAMECHEAP-1","timestamp":1.0}'
    old_hash = hashlib.sha256(old_input.encode()).hexdigest()
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute(OLD_AUDIT)
    con.execute("ALTER TABLE invoices ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent'")
    con.execute("INSERT INTO vendors (name, wallet_address) VALUES ('Namecheap', ?)", (WALLET,))
    con.execute("INSERT INTO purchase_orders (po_number, vendor_id, amount_usdc, category) VALUES ('PO-1', 1, 2.2, 1)")
    con.execute("INSERT INTO receipts (po_id, confirmed_by, confirmed_by_role, confirmed_at) VALUES (1, 'musa', 'buyer', 1)")
    con.execute("INSERT INTO invoices (invoice_number, vendor_id, po_id, amount_usdc, category, doc_hash, status, reasoning_hash, created_at, origin) "
                "VALUES ('NAMECHEAP-1', 1, 1, 2.2, 1, 'd', 'paid', ?, 1, 'manual')", (old_hash,))
    con.execute("INSERT INTO api_keys (key_hash, role, vendor_id, label, created_at) VALUES ('kh', 'buyer', NULL, 'musa', 1)")
    con.execute("INSERT INTO admin_actions (admin_address, action, tx_hash, created_at) VALUES ('0xabc', 'set_vendor', '0xtx', 1)")
    con.execute("INSERT INTO vendor_applications (business_name, wallet, ip_hash, created_at) VALUES ('Pending Co', ?, 'ip', 1)", ("0x" + "d4" * 20,))
    con.execute("INSERT INTO rejected_submissions (reason, origin, created_at) VALUES ('duplicate_invoice', 'agent', 1)")
    con.execute("INSERT INTO audit_log (action, subject, reasoning, amount_usdc, model_used, timestamp, hash_input, reasoning_hash) "
                "VALUES ('INVOICE_PAID', 'NAMECHEAP-1', 'r', 2.2, 'manual', 1.0, ?, ?)", (old_input, old_hash))
    con.commit(); con.close()

    monkeypatch.setattr(config, "db_path", path)
    asyncio.run(models.init_db()); asyncio.run(decision_log.init_db())
    asyncio.run(models.init_db()); asyncio.run(decision_log.init_db())  # twice: idempotent

    db = sqlite3.connect(path)
    for table in ("vendors", "purchase_orders", "receipts", "invoices", "api_keys", "admin_actions", "vendor_applications",
                  "rejected_submissions", "audit_log"):
        assert db.execute(f"SELECT COUNT(*), MIN(business_id), MAX(business_id) FROM {table}").fetchone() == (1, 1, 1), table
    assert db.execute("SELECT id, invoice_number, vendor_id, po_id, status, origin FROM invoices").fetchone() == (1, "NAMECHEAP-1", 1, 1, "paid", "manual")
    assert db.execute("SELECT hash_version, contract_address FROM audit_log").fetchone() == (1, None)
    # numbers are now unique per business, not globally
    db.execute("INSERT INTO invoices (business_id, invoice_number, vendor_id, amount_usdc, category, doc_hash, created_at) VALUES (2, 'NAMECHEAP-1', 1, 1, 0, 'd', 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO invoices (business_id, invoice_number, vendor_id, amount_usdc, category, doc_hash, created_at) VALUES (1, 'NAMECHEAP-1', 1, 1, 0, 'd', 1)")
    # the old public record still verifies, byte for byte
    assert asyncio.run(decision_log.verify_roundtrip(old_hash)) is True
    # a wallet may have one pending application per business, and the old per-wallet index is gone
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")]
    assert "ux_application_pending_wallet" not in names and "ux_application_pending_wallet_v2" in names


# ---- hash formats

def test_new_decisions_bind_the_business_and_old_ones_still_verify():
    home, acme = asyncio.run(models.get_business(1)), asyncio.run(models.get_business(_add_business()))
    h1 = asyncio.run(decision_log.log_decision("INVOICE_HELD", "INV-1", "why", "rules", 5.0, business=home))
    h2 = asyncio.run(decision_log.log_decision("INVOICE_HELD", "INV-1", "why", "rules", 5.0, business=acme))
    assert h1 != h2  # same invoice number, same words: different businesses never share a record
    rec = asyncio.run(decision_log.get_decision(h2))
    assert rec["hash_version"] == 2 and rec["business_id"] == acme["id"] and rec["contract_address"] == ACME_ADDRESS
    assert '"business_id":' + str(acme["id"]) in rec["hash_input"] and ACME_ADDRESS in rec["hash_input"]
    assert asyncio.run(decision_log.verify_roundtrip(h1)) and asyncio.run(decision_log.verify_roundtrip(h2))
    legacy_input, legacy_hash = decision_log.compute_reasoning_hash("INVOICE_PAID", "X", "r", 1.0, "manual", 1.0)
    assert "business_id" not in legacy_input and "hash_version" not in legacy_input


# ---- cross-business denial: a credential from one business never reaches another's data

def test_the_same_po_and_invoice_numbers_live_in_both_businesses(two):
    assert two[1]["po"] and two[2]["po"] and two[1]["invoice"] and two[2]["invoice"]


def test_a_duplicate_invoice_number_is_still_refused_inside_one_business(client, two):
    body = {"invoice_number": "INV-1", "vendor_id": two[2]["vendor"], "amount_usdc": "5", "category": 0, "doc_hash": "ab" * 32}
    assert client.post("/invoices", json=body, headers=two[2]["buyer"]).status_code == 409
    assert client.post("/purchase-orders", json={"po_number": "PO-1", "vendor_id": two[2]["vendor"], "amount_usdc": "1", "category": 0},
                       headers=two[2]["buyer"]).status_code == 409


def test_the_pipeline_only_ever_sees_the_callers_business(client, two, _env):
    seen = _env["business_ids"]
    assert (1, 1) in seen and (two["b2"], two["b2"]) in seen
    assert all(a == b for a, b in seen)  # the invoice's business and the business handed to the pipeline never differ


def test_a_buyer_cannot_read_or_change_another_businesss_records(client, two):
    a, b = two[1], two[2]
    # business 1's key against business 2's ids (ids are global, so these exist only on the other side)
    assert client.get(f"/vendors/{b['vendor']}", headers=a["buyer"]).status_code == 404
    assert client.get(f"/invoices/{b['invoice']}", headers=a["buyer"]).status_code == 404
    extra = client.post("/vendors", json={"name": "OnlyInB", "wallet_address": "0x" + "e5" * 20}, headers=b["buyer"]).json()["id"]
    po_b = client.post("/purchase-orders", json={"po_number": "PO-B-ONLY", "vendor_id": extra, "amount_usdc": "1", "category": 0}, headers=b["buyer"]).json()["id"]
    assert client.get(f"/vendors/{extra}", headers=a["buyer"]).status_code == 404
    assert client.post("/purchase-orders", json={"po_number": "PO-X", "vendor_id": extra, "amount_usdc": "1", "category": 0}, headers=a["buyer"]).status_code == 404
    assert client.post("/receipts", json={"po_id": po_b}, headers=a["buyer"]).status_code == 404
    body = {"invoice_number": "INV-9", "vendor_id": extra, "amount_usdc": "1", "category": 0, "doc_hash": "ab" * 32}
    assert client.post("/invoices", json=body, headers=a["buyer"]).status_code == 404
    assert client.post("/invoices/preflight", json=body, headers=a["buyer"]).status_code == 404
    inv_b = client.post("/invoices", json=body, headers=b["buyer"]).json()["invoice_id"]
    assert client.get(f"/invoices/{inv_b}", headers=a["buyer"]).status_code == 404
    assert client.get(f"/invoices/{inv_b}", headers=b["buyer"]).status_code == 200


def test_a_vendor_key_cannot_submit_for_a_vendor_in_another_business(client, two):
    extra = client.post("/vendors", json={"name": "OnlyInB", "wallet_address": "0x" + "e5" * 20}, headers=two[2]["buyer"]).json()["id"]
    body = {"invoice_number": "INV-5", "vendor_id": extra, "amount_usdc": "1", "category": 0, "doc_hash": "ab" * 32}
    assert client.post("/invoices", json=body, headers=two[1]["vkey"]).status_code == 403


def test_a_business_that_is_not_active_takes_no_activity(client, two):
    async def archive():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("UPDATE businesses SET status = 'archived' WHERE id = 1")
            await db.commit()
    asyncio.run(archive())
    h = two[1]["buyer"]
    assert client.post("/vendors", json={"name": "N", "wallet_address": "0x" + "f6" * 20}, headers=h).status_code == 403
    body = {"invoice_number": "INV-7", "vendor_id": two[1]["vendor"], "amount_usdc": "1", "category": 0, "doc_hash": "ab" * 32}
    assert client.post("/invoices", json=body, headers=h).status_code == 403
    assert client.post("/vendor/auth/challenge", json={"address": VENDOR.address, "business": "ceedebooks"}).status_code == 404
    other = body | {"invoice_number": "INV-8", "vendor_id": two[2]["vendor"]}
    assert client.post("/invoices", json=other, headers=two[2]["buyer"]).status_code == 200  # business 2 is unaffected


# ---- admin sessions are per business

def test_the_admin_message_names_the_business_and_signs_in_only_there(client, two):
    ch = client.post("/admin/auth/challenge", json={"address": ADMIN_2.address, "business": "acme"}).json()
    assert '"acme"' in ch["message"]
    assert _admin_token(client, ADMIN_2, "acme").status_code == 200
    assert _admin_token(client, ADMIN_2, "ceedebooks").status_code == 401   # not business 1's approver
    assert _admin_token(client, ADMIN_1, "acme").status_code == 401         # not business 2's approver
    assert _admin_token(client, ADMIN_1).status_code == 200                 # default business is the first one
    assert client.post("/admin/auth/challenge", json={"address": ADMIN_1.address, "business": "nope"}).status_code == 404


def test_an_admin_session_sees_only_its_own_business(client, two):
    t1 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_1).json()["token"]}
    t2 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_2, "acme").json()["token"]}
    client.post("/vendors", json={"name": "OnlyInB", "wallet_address": "0x" + "e5" * 20}, headers=two[2]["buyer"])
    assert [v["name"] for v in client.get("/admin/vendors", headers=t1).json()["vendors"]] == ["V1"]
    assert sorted(v["name"] for v in client.get("/admin/vendors", headers=t2).json()["vendors"]) == ["OnlyInB", "V2"]
    for path, key in (("/admin/invoices", "invoices"), ("/admin/purchase-orders", "purchase_orders")):
        assert len(client.get(path, headers=t1).json()[key]) == 1


def test_admin_actions_are_logged_and_listed_per_business(client, two, chains):
    t2 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_2, "acme").json()["token"]}
    t1 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_1).json()["token"]}
    assert [a["action"] for a in client.get("/admin/actions", headers=t2).json()["actions"]] == ["sign_in"]
    assert [a["action"] for a in client.get("/admin/actions", headers=t1).json()["actions"]] == ["sign_in"]
    rows = sqlite3.connect(config.db_path).execute("SELECT business_id FROM admin_actions ORDER BY id").fetchall()
    assert sorted(r[0] for r in rows) == [1, two["b2"]]


def test_a_transaction_to_another_businesss_contract_is_not_recorded(client, two, chains):
    t2 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_2, "acme").json()["token"]}
    chains.get(ACME_ADDRESS, 2).admin_tx = {"sender": ADMIN_2.address.lower(), "to": config.budget_enforcer_address.lower(),
                                            "success": True, "block": 5, "escalation": None}
    r = client.post("/admin/actions", json={"action": "set_paused", "tx_hash": "0x" + "ab" * 32}, headers=t2)
    assert r.status_code == 422  # it went to business 1's contract
    chains.get(ACME_ADDRESS, 2).admin_tx["to"] = ACME_ADDRESS
    assert client.post("/admin/actions", json={"action": "set_paused", "tx_hash": "0x" + "ab" * 32}, headers=t2).status_code == 200


def test_rotating_one_businesss_approver_ends_only_that_session(client, two, chains):
    t1 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_1).json()["token"]}
    t2 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_2, "acme").json()["token"]}
    chains.get(ACME_ADDRESS, 2).approver = lambda: Account.create().address
    admin_auth._approver_cache.clear()
    assert client.get("/admin/vendors", headers=t2).status_code == 401
    assert client.get("/admin/vendors", headers=t1).status_code == 200


def test_if_one_businesss_chain_is_unreadable_only_that_business_fails_closed(client, two, chains):
    chains.get(ACME_ADDRESS, 2).broken = True
    assert _admin_token(client, ADMIN_2, "acme").status_code == 401
    assert _admin_token(client, ADMIN_1).status_code == 200


def test_admin_cannot_decide_another_businesss_application(client, two):
    ch = client.post("/apply/challenge", json={"address": VENDOR.address, "business": "acme"}).json()
    r = client.post("/apply", json={"business_name": "Applicant", "wallet_address": VENDOR.address, "nonce": ch["nonce"],
                                    "signature": _sign(VENDOR, ch["message"])})
    assert r.status_code == 200
    app_id = r.json()["id"]
    t1 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_1).json()["token"]}
    t2 = {"Authorization": "Bearer " + _admin_token(client, ADMIN_2, "acme").json()["token"]}
    assert client.get("/admin/applications", headers=t1).json()["applications"] == []
    assert client.post(f"/admin/applications/{app_id}/accept", headers=t1).status_code == 404
    assert client.post(f"/admin/applications/{app_id}/reject", headers=t1).status_code == 404
    assert client.get("/admin/overview", headers=t2).json()["pending_applications"] == 1
    assert client.post(f"/admin/applications/{app_id}/accept", headers=t2).status_code == 200


# ---- vendor sessions are per business

def test_one_wallet_can_be_a_vendor_in_two_businesses_with_separate_sessions(client, two):
    asyncio.run(models.save_vendor(1, "Same wallet, business 1", VENDOR.address))
    asyncio.run(models.save_vendor(two["b2"], "Same wallet, business 2", VENDOR.address))
    r1, r2 = _vendor_login(client, VENDOR), _vendor_login(client, VENDOR, "acme")
    assert r1.status_code == r2.status_code == 200 and (r1.json()["business"], r2.json()["business"]) == ("ceedebooks", "acme")
    h1, h2 = ({"Authorization": "Bearer " + r.json()["token"]} for r in (r1, r2))
    assert client.get("/vendor/me", headers=h1).json()["vendor"]["name"] == "Same wallet, business 1"
    assert client.get("/vendor/me", headers=h2).json()["vendor"]["name"] == "Same wallet, business 2"
    assert client.get("/vendor/me", headers=h1).status_code == 200  # signing in to business 2 did not end business 1's session


def test_a_wallet_that_is_a_vendor_only_in_one_business_cannot_sign_in_to_the_other(client, two):
    asyncio.run(models.save_vendor(1, "Only here", VENDOR.address))
    assert _vendor_login(client, VENDOR).status_code == 200
    assert _vendor_login(client, VENDOR, "acme").status_code == 401


def test_a_vendor_session_sees_only_its_own_business_data(client, two):
    asyncio.run(models.save_vendor(two["b2"], "Same wallet", VENDOR.address))
    h = {"Authorization": "Bearer " + _vendor_login(client, VENDOR, "acme").json()["token"]}
    me = client.get("/vendor/me", headers=h).json()
    assert me["purchase_orders"] == [] and me["invoices"] == []
    assert client.get(f"/invoices/{two[1]['invoice']}", headers=h).status_code == 404


def test_a_signature_for_one_business_cannot_be_replayed_on_another(client, two):
    asyncio.run(models.save_vendor(1, "V", VENDOR.address)); asyncio.run(models.save_vendor(two["b2"], "V", VENDOR.address))
    ch = client.post("/vendor/auth/challenge", json={"address": VENDOR.address, "business": "acme"}).json()
    assert "Business: acme" in ch["message"]
    r = client.post("/vendor/auth/verify", json={"nonce": ch["nonce"], "signature": _sign(VENDOR, ch["message"])})
    assert r.json()["business"] == "acme"  # the challenge decides the business; the caller cannot swap it


# ---- public reads: aggregate by default, one business on request

def test_public_reads_aggregate_by_default_and_narrow_by_business(client, two):
    home, acme = asyncio.run(models.get_business(1)), asyncio.run(models.get_business(two["b2"]))
    asyncio.run(decision_log.log_decision("INVOICE_PAID", "A", "r", "rules", 5.0, business=home))
    asyncio.run(decision_log.log_decision("INVOICE_HELD", "B", "r", "rules", 7.0, business=acme))
    assert client.get("/stats").json()["decisions"] == 2
    assert (client.get("/stats?business=ceedebooks").json()["paid"], client.get("/stats?business=ceedebooks").json()["held"]) == (1, 0)
    assert (client.get("/stats?business=acme").json()["paid"], client.get("/stats?business=acme").json()["held"]) == (0, 1)
    assert len(client.get("/decisions").json()["decisions"]) == 2
    only = client.get("/decisions?business=acme").json()["decisions"]
    assert len(only) == 1 and only[0]["business_id"] == two["b2"]
    assert client.get("/stats?business=nope").status_code == 404


def test_the_manifest_lists_active_businesses_only():
    _add_business("pending-co", "0x" + "cc" * 20, status="pending")
    _add_business("acme")
    with TestClient(main.app) as c:
        body = c.get("/.well-known/agent.json").json()
    assert [b["slug"] for b in body["businesses"]] == ["ceedebooks", "acme"]
    assert body["contract"] == config.budget_enforcer_address


def test_verify_checks_the_chain_of_the_decisions_own_business(client, two, chains):
    acme = asyncio.run(models.get_business(two["b2"]))
    h = asyncio.run(decision_log.log_decision("INVOICE_PAID", "INV-1", "r", "rules", 5.0, business=acme))
    asyncio.run(decision_log.record_onchain_tx(h, "circle-1", "0xabc"))
    chains.get(config.budget_enforcer_address, 1).events = [{"event": "PaymentMade", "reasoning_hash": h, "block": 1, "success": True}]
    assert client.get(f"/decisions/{h}/verify").json()["onchain"]["match"] is False  # business 1's events do not confirm business 2's record
    chains.get(ACME_ADDRESS, 2).events = [{"event": "PaymentMade", "reasoning_hash": h, "block": 2, "success": True}]
    assert client.get(f"/decisions/{h}/verify").json()["onchain"]["match"] is True


def test_models_refuse_to_run_without_a_business():
    with pytest.raises(TypeError):
        asyncio.run(models.get_vendor(1))          # business_id and vendor_id are both required
    with pytest.raises(TypeError):
        asyncio.run(models.list_invoices())
