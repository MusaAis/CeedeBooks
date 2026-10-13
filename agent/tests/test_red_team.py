"""E4: the red-team script keeps a refusal as a contract_refused audit row; an accepted payment is a loud failure."""
import asyncio
import importlib.util
import os
import sqlite3

import pytest

from agent import contract, decision_log
from agent.config import config
from backend import models

_spec = importlib.util.spec_from_file_location("red_team", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "red_team.py"))
red_team = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(red_team)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "rt.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())


def _run(chain_fixture, pay_fails):
    chain_fixture.vendor_ok = False  # a fresh address is not approved
    chain_fixture.agent_addr = "0x" + "a9" * 20

    def wait(tx):
        if tx == "tx-pay" and pay_fails:
            raise RuntimeError("Transaction tx-pay FAILED: Vendor not approved")
    contract.wait_for_transaction = wait
    business = asyncio.run(models.get_business(1))
    return asyncio.run(red_team.run(business, confirm=lambda m: None, wait_blocks=False))


def test_a_refused_payment_becomes_a_contract_refused_row(chain):
    r = _run(chain, pay_fails=True)
    assert r["refused"] is True
    assert [c[0] for c in chain.calls] == ["commit", "pay"]
    with sqlite3.connect(config.db_path) as db:
        action, refused, amount = db.execute("SELECT action, contract_refused, amount_usdc FROM audit_log WHERE reasoning_hash = ?", (r["reasoning_hash"],)).fetchone()
    assert (action, refused, amount) == ("RED_TEAM_REFUSED", 1, 0)


def test_a_payment_the_contract_accepts_is_a_loud_failure_and_not_marked_refused(chain):
    with pytest.raises(red_team.NotRefused):
        _run(chain, pay_fails=False)
    with sqlite3.connect(config.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_log WHERE contract_refused = 1").fetchone()[0] == 0


def test_declining_a_step_sends_nothing(chain):
    chain.vendor_ok = False
    business = asyncio.run(models.get_business(1))

    def no(message):
        raise SystemExit("Aborted")
    with pytest.raises(SystemExit):
        asyncio.run(red_team.run(business, confirm=no, wait_blocks=False))
    assert chain.calls == []
