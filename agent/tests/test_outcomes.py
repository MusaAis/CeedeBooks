"""Phase P1: public outcome figures. Every figure is read from one SQL statement; these tests seed rows directly and check
the numbers, the own/outside and origin splits, the null cases (rule 19) and that nothing private leaves the API."""
import asyncio
import sqlite3
import time

import aiosqlite
import pytest
from fastapi.testclient import TestClient

from agent import decision_log
from agent.config import config
from backend import auth, main, models, outcomes


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "outcomes.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())
    auth.request_limiter.reset()
    monkeypatch.setattr(config, "rate_limit_per_min", 1000)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def _business(slug="acme", address="0x" + "bb" * 20, external=0, status="active") -> int:
    async def go():
        async with aiosqlite.connect(config.db_path) as db:
            cur = await db.execute(
                "INSERT INTO businesses (name, slug, enforcer_address, contract_version, external, status, created_at) VALUES (?, ?, ?, 2, ?, ?, 0)",
                (slug.title(), slug, address, external, status))
            await db.commit()
            return cur.lastrowid
    return asyncio.run(go())


_n = [0]


def _invoice(business_id=1, status="paid", origin="agent", mode="live", amount=5.0, tx=True, age=10.0, released=False):
    """One invoice created `age` seconds before it was settled. `released`: a person released it on-chain."""
    _n[0] += 1
    vendor = asyncio.run(models.save_vendor(business_id, f"V{_n[0]}", "0x" + f"{_n[0]:040x}"))
    real = ("100", "NGN") if mode == "shadow" else (None, None)
    inv = asyncio.run(models.save_invoice(business_id, f"INV-{_n[0]}", vendor, None, amount, 0, "doc", origin=origin, mode=mode,
                                          real_amount=real[0], real_currency=real[1]))
    asyncio.run(models.update_invoice_status(business_id, inv, status, onchain_tx_id=("tx" if tx and status == "paid" else None)))
    with sqlite3.connect(config.db_path) as db:
        db.execute("UPDATE invoices SET created_at = ?, settled_at = CASE WHEN settled_at IS NULL THEN NULL ELSE ? END WHERE id = ?",
                   (1000.0, 1000.0 + age, inv))
    if released:
        asyncio.run(models.log_admin_action(business_id, "0x" + "a1" * 20, "approve_escalation", f"invoice {inv}"))
    return inv


def _dup(business_id=1, origin="agent"):
    asyncio.run(models.log_rejected_submission(business_id, "duplicate_invoice", origin))


def _out(client, path="/outcomes"):
    r = client.get(path)
    assert r.status_code == 200
    return r.json()


def test_empty_database_has_zeros_and_nulls_not_success(client):
    own = _out(client)["by_ownership"]["own"]["agent"]
    assert own["processed"] == 0 and own["volume_usdc"] == 0.0 and own["duplicates_caught"] == 0
    assert own["person_agreement"] == {"confirmed_agent": 0, "overrode_agent": 0, "n": 0, "rate": None}
    assert own["seconds_to_payment"] == {"median": None, "n": 0, "without_timing": 0}
    assert own["escalated"] == {"waiting": 0, "released": 0, "rejected": 0, "total": 0}


def test_decided_alone_versus_escalated_and_person_agreement(client):
    _invoice(status="paid"); _invoice(status="paid"); _invoice(status="held")
    _invoice(status="escalated")                       # still waiting
    _invoice(status="paid", released=True)             # a person released it: not "decided alone"
    _invoice(status="rejected"); _invoice(status="rejected")
    a = _out(client)["by_ownership"]["own"]["agent"]
    assert a["processed"] == 7
    assert (a["paid_by_agent"], a["held"], a["decided_alone"]) == (2, 1, 3)
    assert a["escalated"] == {"waiting": 1, "released": 1, "rejected": 2, "total": 4}
    assert a["person_agreement"] == {"confirmed_agent": 2, "overrode_agent": 1, "n": 3, "rate": pytest.approx(0.6667, abs=1e-4)}
    assert a["volume_usdc"] == 15.0                    # three paid, 5.0 each (a released payment is still money paid)


def test_pending_error_and_shadow_invoices_are_not_in_the_live_figures(client):
    _invoice(status="paid")
    _invoice(status="pending"); _invoice(status="error")
    _invoice(status="paid", mode="shadow"); _invoice(status="held", mode="shadow")
    body = _out(client)
    assert body["by_ownership"]["own"]["agent"]["processed"] == 1
    assert body["shadow"]["paid"] == 1 and body["shadow"]["held"] == 1     # reported apart, never added in


def test_origins_are_never_summed(client):
    _invoice(origin="agent"); _invoice(origin="demo", amount=7.0); _invoice(origin="manual", amount=2.2)
    _dup(origin="demo")
    own = _out(client)["by_ownership"]["own"]
    assert own["agent"]["processed"] == 1 and own["agent"]["volume_usdc"] == 5.0
    assert own["demo"]["processed"] == 1 and own["demo"]["volume_usdc"] == 7.0 and own["demo"]["duplicates_caught"] == 1
    assert own["manual"]["processed"] == 1 and own["manual"]["volume_usdc"] == 2.2 and own["agent"]["duplicates_caught"] == 0


def test_own_and_outside_businesses_are_split_and_the_count_is_plain(client):
    outside = _business("outsider", "0x" + "cc" * 20, external=1)
    _invoice(1); _invoice(outside, amount=9.0)
    body = _out(client)
    assert body["by_ownership"]["own"]["agent"]["volume_usdc"] == 5.0
    assert body["by_ownership"]["outside"]["agent"]["volume_usdc"] == 9.0
    assert body["outside_businesses"]["external"] == 1
    assert body["outside_businesses"]["statement"].startswith("Outside businesses: 1 of 2 active")


def test_with_no_outside_business_the_statement_says_zero_confirmed(client):
    _business("acme", external=0)                      # registered but not confirmed as outside
    st = _out(client)["outside_businesses"]
    assert st["external"] == 0 and st["total"] == 2
    assert st["statement"] == "Outside businesses: 0 confirmed. 2 active businesses, none confirmed as an outside party."


def test_duplicates_are_counted_per_origin(client):
    _dup(); _dup(); _dup(origin="demo")
    own = _out(client)["by_ownership"]["own"]
    assert own["agent"]["duplicates_caught"] == 2 and own["demo"]["duplicates_caught"] == 1


def test_median_seconds_uses_only_stamped_agent_payments(client):
    _invoice(age=10.0); _invoice(age=30.0); _invoice(age=20.0)
    _invoice(age=999.0, released=True)                 # a person's wait is not the agent's speed
    _invoice(age=5.0, tx=False)                        # re-marked paid, never stamped: not timed
    a = _out(client)["by_ownership"]["own"]["agent"]
    assert a["seconds_to_payment"] == {"median": 20.0, "n": 3, "without_timing": 1}


def test_median_of_an_even_count_and_the_unstamped_note(client):
    _invoice(age=10.0); _invoice(age=40.0)
    assert _out(client)["by_ownership"]["own"]["agent"]["seconds_to_payment"]["median"] == 25.0


def test_settled_at_is_stamped_once_and_only_with_a_transaction():
    vendor = asyncio.run(models.save_vendor(1, "V", "0x" + "ee" * 20))
    inv = asyncio.run(models.save_invoice(1, "INV-S", vendor, None, 1.0, 0, "doc"))
    asyncio.run(models.update_invoice_status(1, inv, "paid"))                       # chain already had it: no time
    row = asyncio.run(models.get_invoice(1, inv))
    assert row["settled_at"] is None
    asyncio.run(models.update_invoice_status(1, inv, "paid", onchain_tx_id="t1"))
    first = asyncio.run(models.get_invoice(1, inv))["settled_at"]
    assert first is not None and abs(first - time.time()) < 60
    time.sleep(0.01)
    asyncio.run(models.update_invoice_status(1, inv, "paid", onchain_tx_id="t2"))
    assert asyncio.run(models.get_invoice(1, inv))["settled_at"] == first          # never moved


def test_an_old_database_gains_the_column():
    with sqlite3.connect(config.db_path) as db:
        db.execute("ALTER TABLE invoices DROP COLUMN settled_at")
    asyncio.run(models.init_db())
    with sqlite3.connect(config.db_path) as db:
        assert "settled_at" in [r[1] for r in db.execute("PRAGMA table_info(invoices)")]


def test_scoped_to_one_business_and_archived_ones_leave_the_aggregate(client):
    hidden = _business("old", "0x" + "dd" * 20, status="archived")
    _invoice(1); _invoice(hidden, amount=50.0)
    assert _out(client)["by_ownership"]["own"]["agent"]["processed"] == 1            # aggregate skips the archived one
    scoped = _out(client, "/outcomes?business=old")
    assert scoped["scope"] == "old" and scoped["by_ownership"]["own"]["agent"]["volume_usdc"] == 50.0
    assert scoped["outside_businesses"]["statement"] == "Figures for one business."
    assert client.get("/outcomes?business=nope").status_code == 404


def test_released_by_one_business_is_not_credited_to_another(client):
    other = _business("acme", "0x" + "bb" * 20)
    inv = _invoice(1)
    # an approval recorded for a different business, with the same ref text, must not mark business 1's invoice as released
    asyncio.run(models.log_admin_action(other, "0x" + "a2" * 20, "approve_escalation", f"invoice {inv}"))
    assert _out(client)["by_ownership"]["own"]["agent"]["released_by_person"] == 0


def test_the_response_carries_the_sql_and_definitions_and_no_private_fields(client):
    _invoice()
    body = _out(client)
    assert set(body["queries"]) == {"invoice_outcomes", "duplicates_caught", "seconds_to_payment"}
    assert all(q.lstrip().upper().startswith("SELECT") for q in body["queries"].values())
    assert {"processed", "decided_alone", "escalated", "person_agreement", "seconds_to_payment", "volume_usdc"} <= set(body["definitions"])
    text = str(body).lower()
    for private in ("0x" + "a1" * 20, "admin_address", "wallet", "submitted_by", "contact"):
        assert private not in text


def test_every_documented_query_runs_as_written(client):
    """The SQL shown to readers is the SQL that runs: execute each one on a scratch copy with the aggregate scope."""
    _invoice(); _invoice(released=True); _dup()
    scope = "AND {a}.business_id NOT IN (SELECT id FROM businesses WHERE status = 'archived')"
    with sqlite3.connect(config.db_path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(outcomes.QUERIES["invoice_outcomes"].format(scope=scope.format(a="i"))).fetchall()
        assert rows[0]["processed"] == 2 and rows[0]["paid_by_agent"] == 1 and rows[0]["released_by_person"] == 1
        assert db.execute(outcomes.QUERIES["duplicates_caught"].format(scope=scope.format(a="r"))).fetchall()[0]["n"] == 1
        assert len(db.execute(outcomes.QUERIES["seconds_to_payment"].format(scope=scope.format(a="i"))).fetchall()) == 1


def test_no_digits_are_written_by_a_model_into_the_statement():
    """Rule 18: the sentence on the page is built from counts by code only."""
    assert outcomes.outside_statement({"total": 1, "external": 0}, False) == \
        "Outside businesses: 0 confirmed. 1 active business, none confirmed as an outside party."
    assert outcomes.outside_statement({"total": 3, "external": 2}, False) == \
        "Outside businesses: 2 of 3 active, confirmed as outside parties by the operator."
