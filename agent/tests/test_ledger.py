"""Phase E1: the hash-chained, signed decision ledger. The chain is per business; every attack below must fail verification."""
import asyncio
import json
import sqlite3

import aiosqlite
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from agent import contract, decision_log, ledger, payables
from agent.config import config
from backend import auth, main, models

SEED = "11" * 32  # the test key set in conftest.py
OTHER_SEED = "22" * 32
ACME = "0x" + "bb" * 20


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "ledger.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    auth.request_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _business(business_id=1):
    return asyncio.run(models.get_business(business_id))


def _add_acme() -> int:
    con = sqlite3.connect(config.db_path)
    cur = con.execute("INSERT INTO businesses (name, slug, enforcer_address, contract_version, status, created_at) "
                      "VALUES ('Acme', 'acme', ?, 2, 'active', 0)", (ACME,))
    con.commit(); con.close()
    return cur.lastrowid


def _log(subject="INV-1", action="INVOICE_PAID", amount=5.0, business_id=1):
    return asyncio.run(decision_log.log_decision(action, subject, "because", model_used="rules", amount=amount, business=_business(business_id)))


def _verify(business_id=1):
    return asyncio.run(ledger.verify_chain(business_id))


def _sql(sql, args=()):
    con = sqlite3.connect(config.db_path)
    con.execute(sql, args); con.commit(); con.close()


def _rows(sql, args=()):
    con = sqlite3.connect(config.db_path); con.row_factory = sqlite3.Row
    out = [dict(r) for r in con.execute(sql, args)]; con.close()
    return out


def _codes(report):
    return {p["code"] for p in report["problems"]}


@pytest.fixture
def chain5():
    """Business 1 with five decisions, so entries 1 to 5 follow the genesis entry 0 and 2 to 4 are 'middle' entries."""
    return [_log(f"INV-{n}", amount=float(n)) for n in range(1, 6)]


# ---- the happy path

def test_every_decision_gets_one_signed_entry_in_order(chain5):
    report = _verify()
    assert report["ok"] and report["problems"] == []
    assert (report["entries"], report["decisions"]) == (6, 5)
    entries = _rows("SELECT * FROM ledger WHERE business_id = 1 ORDER BY sequence")
    assert [e["sequence"] for e in entries] == [0, 1, 2, 3, 4, 5]
    assert entries[0]["kind"] == "genesis" and entries[0]["prev_hash"] == ledger.genesis_prev(1)
    assert [e["reasoning_hash"] for e in entries[1:]] == chain5
    assert all(entries[i]["prev_hash"] == entries[i - 1]["entry_hash"] for i in range(1, 6))
    assert report["head"] == {"sequence": 5, "entry_hash": entries[5]["entry_hash"]}
    assert report["backfilled_entries"] == 0
    for e in entries:  # anyone with the public key can check a signature
        assert ledger.signature_ok(ledger.public_key_hex(), e["body"], e["signature"])


def test_an_empty_business_has_no_chain_and_still_verifies():
    report = _verify()
    assert report["ok"] and report["entries"] == 0 and report["head"] is None


def test_the_row_and_its_entry_are_stored_together_or_not_at_all(monkeypatch):
    _log("INV-1")
    real = ledger._insert
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ledger, "_insert", boom)
    with pytest.raises(RuntimeError):
        _log("INV-2")
    monkeypatch.setattr(ledger, "_insert", real)
    assert len(_rows("SELECT 1 FROM audit_log")) == 1  # the decision row was rolled back with the entry
    assert _verify()["ok"]


# ---- the five attacks

@pytest.mark.parametrize("mutation, code", [
    ("UPDATE ledger SET body = replace(body, '\"amount_usdc\":3.0', '\"amount_usdc\":0.3') WHERE business_id = 1 AND sequence = 3", "bad_hash"),
    ("UPDATE ledger SET entry_hash = '" + "0" * 64 + "' WHERE business_id = 1 AND sequence = 3", "bad_hash"),
    ("UPDATE ledger SET signature = '" + "00" * 64 + "' WHERE business_id = 1 AND sequence = 3", "bad_signature"),
    ("UPDATE ledger SET prev_hash = '" + "0" * 64 + "' WHERE business_id = 1 AND sequence = 3", "broken_link"),
    ("UPDATE audit_log SET amount_usdc = 99 WHERE subject = 'INV-3'", "decision_changed"),
    ("UPDATE audit_log SET action = 'INVOICE_HELD' WHERE subject = 'INV-3'", "decision_changed"),
    ("UPDATE audit_log SET hash_input = replace(hash_input, 'because', 'edited') WHERE subject = 'INV-3'", "decision_changed"),
])
def test_tampering_with_a_middle_entry_or_its_decision_fails(chain5, mutation, code):
    _sql(mutation)
    report = _verify()
    assert not report["ok"] and code in _codes(report), report["problems"]


def test_a_forged_body_with_a_recomputed_hash_still_fails_on_the_signature_and_the_link(chain5):
    row = _rows("SELECT * FROM ledger WHERE business_id = 1 AND sequence = 3")[0]
    forged = row["body"].replace('"amount_usdc":3.0', '"amount_usdc":0.3')
    _sql("UPDATE ledger SET body = ?, entry_hash = ? WHERE id = ?", (forged, ledger._hash(forged), row["id"]))
    codes = _codes(_verify())
    assert {"bad_signature", "broken_link", "decision_changed"} <= codes


def test_deleting_a_middle_entry_fails(chain5):
    _sql("DELETE FROM ledger WHERE business_id = 1 AND sequence = 3")
    report = _verify()
    assert not report["ok"] and {"bad_sequence", "broken_link", "unchained_decision"} <= _codes(report)


def test_deleting_the_newest_entry_fails_because_its_decision_is_still_logged(chain5):
    _sql("DELETE FROM ledger WHERE business_id = 1 AND sequence = 5")
    report = _verify()
    assert not report["ok"] and _codes(report) == {"unchained_decision"}


def test_deleting_a_logged_decision_fails(chain5):
    _sql("DELETE FROM audit_log WHERE subject = 'INV-3'")
    assert "missing_decision" in _codes(_verify())


def test_an_unchained_decision_inserted_behind_the_chains_back_fails(chain5):
    _sql("INSERT INTO audit_log (action, subject, reasoning, amount_usdc, model_used, timestamp, hash_input, reasoning_hash, business_id) "
         "VALUES ('INVOICE_PAID', 'SNUCK', 'x', 1, 'rules', 1, '{}', ?, 1)", (ledger._hash("{}"),))
    assert "unchained_decision" in _codes(_verify())  # restarting does not quietly chain it: backfill only runs on a business with no chain
    asyncio.run(ledger.start())
    assert "unchained_decision" in _codes(_verify())


def test_reordering_entries_fails(chain5):
    _sql("UPDATE ledger SET sequence = 99 WHERE business_id = 1 AND sequence = 2")
    _sql("UPDATE ledger SET sequence = 2 WHERE business_id = 1 AND sequence = 3")
    _sql("UPDATE ledger SET sequence = 3 WHERE business_id = 1 AND sequence = 99")
    report = _verify()
    assert not report["ok"] and {"bad_body", "broken_link"} <= _codes(report)


def test_swapping_two_whole_entries_fails(chain5):
    a, b = _rows("SELECT * FROM ledger WHERE business_id = 1 AND sequence IN (2, 3) ORDER BY sequence")
    for row, seq in ((a, 3), (b, 2)):  # each entry moves to the other's slot, keeping its own signed content
        _sql("UPDATE ledger SET sequence = ? WHERE id = ?", (seq + 100, row["id"]))
    for row, seq in ((a, 3), (b, 2)):
        _sql("UPDATE ledger SET sequence = ? WHERE id = ?", (seq, row["id"]))
    assert not _verify()["ok"]


def test_an_entry_signed_by_another_key_fails(chain5):
    """Someone without the real key re-signs one entry with their own key."""
    other = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(OTHER_SEED))
    row = _rows("SELECT * FROM ledger WHERE business_id = 1 AND sequence = 3")[0]
    _sql("UPDATE ledger SET signature = ? WHERE id = ?", (other.sign(ledger.SIGN_PREFIX + row["body"].encode()).hex(), row["id"]))
    assert "bad_signature" in _codes(_verify())


def test_a_whole_chain_rebuilt_under_another_key_fails_against_the_published_key(chain5, monkeypatch):
    published = ledger.public_key_hex()
    _sql("DELETE FROM ledger")
    monkeypatch.setattr(config, "ledger_signing_key", OTHER_SEED)  # the attacker's key signs a clean-looking chain
    async def rebuild():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("BEGIN IMMEDIATE"); await ledger.ensure_chain(db, 1); await db.commit()
    asyncio.run(rebuild())
    assert _verify()["ok"]  # valid under the attacker's key...
    entries = _rows("SELECT * FROM ledger WHERE business_id = 1 ORDER BY sequence")
    problems = ledger.analyse(1, entries, _rows("SELECT * FROM audit_log WHERE business_id = 1 ORDER BY id"), published)
    assert {p["code"] for p in problems} == {"wrong_key"}  # ...and every entry fails against the published one
    monkeypatch.setattr(config, "ledger_signing_key", SEED)
    assert "wrong_key" in _codes(_verify())


def test_the_database_refuses_a_second_child_of_an_entry(chain5):
    """A fork attempt: a second entry on top of an entry that already has one, even with a valid signature."""
    parent = _rows("SELECT * FROM ledger WHERE business_id = 1 AND sequence = 2")[0]  # entry 3 is already its child
    async def fork():
        async with aiosqlite.connect(config.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            await ledger._insert(db, 1, 6, parent["entry_hash"], "decision", {f: "x" for f in ledger._DECISION_FIELDS})
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(fork())
    assert _verify()["ok"]


def test_verification_flags_a_fork_even_if_the_database_let_one_in(chain5):
    entries = _rows("SELECT * FROM ledger WHERE business_id = 1 ORDER BY sequence")
    parent = entries[2]
    body = ledger._canonical({"v": 1, "kind": "decision", "business_id": 1, "sequence": 3, "prev_hash": parent["entry_hash"],
                              "key_id": parent["key_id"], **{f: None for f in ledger._DECISION_FIELDS}})
    sibling = {**entries[3], "body": body, "entry_hash": ledger._hash(body), "signature": ledger._sign(body), "reasoning_hash": None}
    entries.insert(4, sibling)  # a validly signed twin of entry 3
    problems = ledger.analyse(1, entries, _rows("SELECT * FROM audit_log WHERE business_id = 1 ORDER BY id"), ledger.public_key_hex())
    assert "fork" in {p["code"] for p in problems}


def test_concurrent_writers_cannot_fork_the_chain():
    biz = _business()
    async def burst():
        await asyncio.gather(*[decision_log.log_decision("INVOICE_PAID", f"C-{n}", "x", model_used="rules", amount=1.0, business=biz)
                               for n in range(25)])
    asyncio.run(burst())
    report = _verify()
    assert report["ok"] and (report["entries"], report["head"]["sequence"]) == (26, 25)
    seqs = [r["sequence"] for r in _rows("SELECT sequence FROM ledger WHERE business_id = 1")]
    assert sorted(seqs) == list(range(26)) and len({r["prev_hash"] for r in _rows("SELECT prev_hash FROM ledger")}) == 26


# ---- genesis and the one-time backfill

def _raw_decision(subject, business_id, n):
    _sql("INSERT INTO audit_log (action, subject, reasoning, amount_usdc, model_used, timestamp, hash_input, reasoning_hash, business_id) "
         "VALUES ('INVOICE_PAID', ?, 'old row', ?, 'rules', ?, ?, ?, ?)",
         (subject, float(n), 100.0 + n, f'{{"n":{business_id}{n}}}', ledger._hash(f'{{"n":{business_id}{n}}}'), business_id))


def test_existing_rows_are_chained_once_in_log_order_under_a_genesis_entry():
    """What the upgrade does to a database that already has decisions (written before the ledger existed)."""
    acme = _add_acme()
    for n in (1, 2, 3):
        _raw_decision(f"OLD-{n}", 1, n)
    _raw_decision("OLD-A", acme, 1)
    assert _rows("SELECT 1 FROM ledger") == []  # nothing is chained yet
    asyncio.run(ledger.start())
    for business_id, count in ((1, 3), (acme, 1)):
        report = _verify(business_id)
        assert report["ok"] and report["entries"] == count + 1 and report["backfilled_entries"] == count
    subjects = [r["subject"] for r in _rows("SELECT a.subject FROM ledger l JOIN audit_log a ON a.reasoning_hash = l.reasoning_hash "
                                            "WHERE l.business_id = 1 ORDER BY l.sequence")]
    assert subjects == ["OLD-1", "OLD-2", "OLD-3"]  # decision-log order
    before = _rows("SELECT * FROM ledger")
    asyncio.run(ledger.start())
    assert _rows("SELECT * FROM ledger") == before  # running it again changes nothing
    _log("NEW-1")
    report = _verify()
    assert report["ok"] and report["entries"] == 5 and report["backfilled_entries"] == 3


def test_the_first_decision_after_an_upgrade_chains_the_old_rows_itself():
    """If a script logs a decision before the API has restarted, the same one-time backfill runs inside that write."""
    for n in (1, 2):
        _raw_decision(f"OLD-{n}", 1, n)
    _log("NEW-1")
    report = _verify()
    assert report["ok"] and report["backfilled_entries"] == 2 and report["decisions"] == 3


def test_the_genesis_entry_says_how_many_entries_were_backfilled_and_what_that_means():
    _raw_decision("OLD-1", 1, 1)
    asyncio.run(ledger.start())
    genesis = json.loads(_rows("SELECT body FROM ledger WHERE sequence = 0")[0]["body"])
    assert genesis["backfilled_entries"] == 1 and "show nothing about the period before" in genesis["note"]


# ---- no key, no log, no payment

@pytest.mark.parametrize("bad_key", ["", "zz", "11" * 31])
def test_without_a_usable_key_nothing_is_logged(monkeypatch, bad_key):
    monkeypatch.setattr(config, "ledger_signing_key", bad_key)
    with pytest.raises(ledger.LedgerKeyError):
        _log("INV-1")
    assert _rows("SELECT 1 FROM audit_log") == []
    with pytest.raises(ledger.LedgerKeyError):
        asyncio.run(ledger.start())


def test_without_a_key_an_invoice_is_not_paid(chain, monkeypatch):
    """Rule 17: if the ledger cannot be written, the payment does not go out."""
    monkeypatch.setattr(payables.contract, "invoice_key", lambda v, i: b"\x01" * 32)
    vendor = asyncio.run(models.save_vendor(1, "V", "0xVENDOR"))
    po = asyncio.run(models.save_purchase_order(1, "PO-1", vendor, 10.0, 0))
    asyncio.run(models.save_receipt(1, po, "someone", "ops_manager"))
    invoice = payables.Invoice(business_id=1, id=1, invoice_number="INV-1", vendor_wallet="0xVENDOR", amount_usdc=10.0,
                               category=0, doc_hash="00" * 32, po_number="PO-1")
    monkeypatch.setattr(config, "ledger_signing_key", "")
    with pytest.raises(ledger.LedgerKeyError):
        asyncio.run(payables.process_invoice(invoice, 30, _business()))
    assert [c for c in chain.calls if c[0] in ("commit", "pay", "escalate")] == []


# ---- one chain per business

def test_each_business_has_its_own_chain_that_starts_at_zero():
    acme = _add_acme()
    _log("A-1", business_id=1); _log("B-1", business_id=acme); _log("A-2", business_id=1)
    one, two = _verify(1), _verify(acme)
    assert one["ok"] and two["ok"] and (one["entries"], two["entries"]) == (3, 2)
    bodies = {bid: [json.loads(r["body"]) for r in _rows("SELECT body FROM ledger WHERE business_id = ? ORDER BY sequence", (bid,))] for bid in (1, acme)}
    assert all(b["business_id"] == 1 for b in bodies[1]) and all(b["business_id"] == acme for b in bodies[acme])
    assert bodies[1][0]["prev_hash"] != bodies[acme][0]["prev_hash"]  # different genesis for each business
    assert one["head"]["entry_hash"] != two["head"]["entry_hash"]


def test_damage_to_one_businesss_chain_does_not_show_in_the_other():
    acme = _add_acme()
    _log("A-1", business_id=1); _log("B-1", business_id=acme); _log("B-2", business_id=acme)
    _sql("UPDATE audit_log SET amount_usdc = 99 WHERE subject = 'B-1'")
    assert _verify(1)["ok"] and not _verify(acme)["ok"]


def test_an_entry_moved_into_another_businesss_chain_fails_there():
    acme = _add_acme()
    _log("A-1", business_id=1); _log("A-2", business_id=1); _log("B-1", business_id=acme)
    _sql("UPDATE ledger SET business_id = ?, sequence = 2 WHERE business_id = 1 AND sequence = 2", (acme,))
    assert "bad_body" in _codes(_verify(acme)) and not _verify(1)["ok"]


# ---- the public endpoints

def test_the_public_key_endpoint_serves_the_key_and_never_the_seed(client):
    body = client.get("/.well-known/ledger-key")
    assert body.status_code == 200
    data = body.json()
    assert data["algorithm"] == "Ed25519" and data["public_key"] == ledger.public_key_hex()
    assert data["key_id"] == ledger.key_id_of(data["public_key"]) and SEED not in body.text


def test_the_key_endpoint_and_verify_say_503_when_no_key_is_configured(client, monkeypatch):
    monkeypatch.setattr(config, "ledger_signing_key", "")
    assert client.get("/.well-known/ledger-key").status_code == 503
    assert client.get("/ledger/verify").status_code == 503


def test_verify_endpoint_reports_a_clean_chain_then_a_tampered_one(client, chain5):
    ok = client.get("/ledger/verify").json()
    assert ok["ok"] and ok["business"] == "ceedebooks" and ok["entries"] == 6 and ok["problem_count"] == 0
    _sql("UPDATE audit_log SET amount_usdc = 99 WHERE subject = 'INV-2'")
    bad = client.get("/ledger/verify").json()
    assert not bad["ok"] and bad["problem_count"] >= 1 and bad["problems"][0]["code"] == "decision_changed"


def test_verify_endpoint_is_per_business_and_unknown_businesses_are_404(client):
    acme = _add_acme()
    _log("A-1", business_id=1); _log("B-1", business_id=acme)
    _sql("UPDATE audit_log SET amount_usdc = 99 WHERE subject = 'B-1'")
    assert client.get("/ledger/verify").json()["ok"] is True
    assert client.get("/ledger/verify?business=acme").json()["ok"] is False
    assert client.get("/ledger/verify?business=nobody").status_code == 404


def test_problems_listed_are_capped_but_counted(client):
    for n in range(30):
        _log(f"INV-{n}")
    _sql("UPDATE audit_log SET amount_usdc = 99")
    body = client.get("/ledger/verify").json()
    assert body["problem_count"] == 30 and len(body["problems"]) == ledger.MAX_PROBLEMS


def test_existing_hash_verification_is_unchanged(client):
    reasoning_hash = _log("INV-1")
    assert client.get(f"/decisions/{reasoning_hash}/verify").json()["verified"] is True


# ---- scripts/ledger_key.py

def test_the_key_script_writes_the_seed_to_the_env_file_without_printing_it(tmp_path, capsys):
    import importlib.util, os
    spec = importlib.util.spec_from_file_location("ledger_key", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "ledger_key.py"))
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nLEDGER_SIGNING_KEY=\n")
    tool.new(str(env))
    printed = capsys.readouterr().out
    seed = [l for l in env.read_text().splitlines() if l.startswith("LEDGER_SIGNING_KEY=")][0].split("=", 1)[1]
    assert len(seed) == 64 and seed not in printed and tool._public(seed) in printed and "OTHER=1" in env.read_text()
    assert oct(env.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SystemExit):  # never replaces an existing key: that would break every old entry
        tool.new(str(env))
    assert seed in env.read_text()
