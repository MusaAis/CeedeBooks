"""The relabel tool (scripts/relabel_origin.py): lists first, changes nothing without the code and a typed yes, one business only."""
import asyncio
import importlib.util
import os
import sqlite3

import pytest

from agent.config import config
from backend import models

MINE = "0x" + "aa" * 20
THEIRS = "0x" + "bb" * 20
spec = importlib.util.spec_from_file_location("relabel_origin", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "relabel_origin.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "relabel.db"))
    asyncio.run(models.init_db())
    con = sqlite3.connect(config.db_path)
    con.execute("INSERT INTO businesses (name, slug, enforcer_address, contract_version, status, created_at) VALUES ('Acme', 'acme', ?, 2, 'active', 0)", ("0x" + "cc" * 20,))
    con.commit(); con.close()


async def _seed():
    ids = {}
    mine, theirs = await models.save_vendor(1, "Mine", MINE), await models.save_vendor(1, "Theirs", THEIRS)
    acme_vendor = await models.save_vendor(2, "Mine too", MINE)
    for name, vendor, business, origin, mode in [
        ("INV-TEST-1", mine, 1, "agent", "live"), ("INV-TEST-2", mine, 1, "agent", "live"), ("INV-TEST-3", theirs, 1, "agent", "live"),
        ("INV-TEST-4", mine, 1, "manual", "live"), ("REAL-1", mine, 1, "agent", "live"), ("INV-TEST-5", acme_vendor, 2, "agent", "live"),
    ]:
        extra = {}
        ids[name] = await models.save_invoice(business, name, vendor, None, 0.01, 0, "ab" * 32, origin=origin, mode=mode, **extra)
    return ids


def _origins():
    con = sqlite3.connect(config.db_path)
    out = dict(con.execute("SELECT invoice_number, origin FROM invoices")); con.close()
    return out


def _run(args, answer="yes"):
    lines = []
    code = asyncio.run(tool.run(args, ask=lambda prompt: answer, out=lines.append))
    return code, "\n".join(lines)


def _list_code(args=()):
    _, text = _run(["list", *args])
    return text.split("--code ")[1].split()[0], text


def test_list_changes_nothing_and_shows_the_wallets():
    ids = asyncio.run(_seed())
    before = _origins()
    code, text = _run(["list"])
    assert code == 0 and MINE in text and THEIRS in text and "Nothing was changed" in text
    assert "INV-TEST-4" not in text and "REAL-1" not in text and "INV-TEST-5" not in text  # manual, other prefix, other business
    assert _origins() == before and ids


def test_wallet_filter_lists_only_payments_to_those_wallets():
    asyncio.run(_seed())
    _, text = _run(["list", "--wallets", MINE])
    assert "INV-TEST-1" in text and "INV-TEST-2" in text and "INV-TEST-3" not in text


def test_apply_needs_the_code_and_a_typed_yes():
    ids = asyncio.run(_seed())
    before = _origins()
    code, text = _list_code(["--wallets", MINE])
    arg = f"{ids['INV-TEST-1']},{ids['INV-TEST-2']}"
    assert _run(["apply", "--ids", arg])[0] == 1                              # no code
    assert _run(["apply", "--ids", arg, "--code", "0000000000"])[0] == 1      # wrong code
    assert _run(["apply", "--ids", arg, "--code", code], answer="no")[0] == 1  # not confirmed
    assert _run(["apply", "--ids", str(ids["INV-TEST-1"]), "--code", code])[0] == 1  # a different set than was listed
    assert _origins() == before
    assert _run(["apply", "--ids", arg, "--code", code])[0] == 0
    now = _origins()
    assert (now["INV-TEST-1"], now["INV-TEST-2"]) == ("demo", "demo")
    assert (now["INV-TEST-3"], now["INV-TEST-4"], now["REAL-1"], now["INV-TEST-5"]) == ("agent", "manual", "agent", "agent")


def test_the_code_stops_working_if_the_rows_changed_after_listing():
    ids = asyncio.run(_seed())
    code, _ = _list_code()
    asyncio.run(models.update_invoice_status(1, ids["INV-TEST-1"], "paid"))
    all_ids = ",".join(str(ids[n]) for n in ("INV-TEST-1", "INV-TEST-2", "INV-TEST-3"))
    assert _run(["apply", "--ids", all_ids, "--code", code])[0] == 1
    assert _origins()["INV-TEST-1"] == "agent"


def test_another_businesss_invoices_cannot_be_relabelled_from_this_one():
    ids = asyncio.run(_seed())
    assert asyncio.run(models.relabel_agent_to_demo(1, [ids["INV-TEST-1"], ids["INV-TEST-5"]])) == 0  # all or nothing
    assert asyncio.run(models.relabel_agent_to_demo(1, [ids["INV-TEST-5"]])) == 0
    assert _origins()["INV-TEST-1"] == "agent" and _origins()["INV-TEST-5"] == "agent"
    code, text = _run(["apply", "--ids", str(ids["INV-TEST-5"]), "--code", "x"])
    assert code == 1 and _origins()["INV-TEST-5"] == "agent"


def test_relabelled_invoices_leave_the_agent_totals_and_the_action_is_logged():
    ids = asyncio.run(_seed())
    for n in ("INV-TEST-1", "INV-TEST-2", "INV-TEST-3"):
        asyncio.run(models.update_invoice_status(1, ids[n], "paid"))
    assert asyncio.run(models.submission_stats(1))["by_origin"]["agent"]["invoices_processed"] == 3  # the three paid tests (REAL-1 is still pending)
    code, _ = _list_code()
    assert _run(["apply", "--ids", ",".join(str(ids[n]) for n in ("INV-TEST-1", "INV-TEST-2", "INV-TEST-3")), "--code", code])[0] == 0
    stats = asyncio.run(models.submission_stats(1))["by_origin"]
    assert stats["demo"]["invoices_processed"] == 3 and stats["demo"]["payment_volume_usdc"] == 0.03
    assert [a["action"] for a in asyncio.run(models.list_admin_actions(1))] == ["relabel_demo"]
    assert asyncio.run(models.list_admin_actions(2)) == []
