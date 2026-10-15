"""F2: the Gateway deposit script. The chain and Circle are mocked; the audit row, the ledger, the checks and the order of the
steps are real. Every check fails closed: nothing is sent when a read is wrong or missing."""
import asyncio
import importlib.util
import json
import os
import sqlite3

import httpx
import pytest
from web3 import Web3

from agent import contract, decision_log, gateway, ledger
from agent.config import Config, config
from backend import models

_spec = importlib.util.spec_from_file_location("gateway_deposit", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "gateway_deposit.py"))
gd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gd)

AGENT = "0x32B59A6988DCA955c641D529Ee80484200DE37b0"
GATEWAY_WALLET = "0x0077777d7EBA4688BDeF3E311b846F25870A19B9"


class FakeGateway:
    """USDC, the allowance, the Gateway Wallet and the two sends. `sent` records every write in order."""

    def __init__(self):
        self.balance = 40_000_000
        self.allowance = 0
        self.code = {GATEWAY_WALLET.lower(): True, config.usdc_address.lower(): True}
        self.sent = []
        self.broken = False
        self.fail_deposit = False
        self.approve_no_effect = False

    def usdc_balance_of(self, address):
        if self.broken:
            raise RuntimeError("rpc down")
        return self.balance

    def usdc_allowance(self, owner, spender):
        if self.broken:
            raise RuntimeError("rpc down")
        return self.allowance

    def has_code(self, address):
        return self.code.get(address.lower(), False)

    def approve_usdc(self, wallet_id, spender, units):
        self.sent.append(("approve", wallet_id, spender, units))
        if not self.approve_no_effect:
            self.allowance = units
        return "tx-approve"

    def gateway_deposit(self, wallet_id, units):
        self.sent.append(("deposit", wallet_id, units))
        if self.fail_deposit:
            raise RuntimeError("circle refused")
        return "tx-deposit"


@pytest.fixture
def fg(monkeypatch):
    fake = FakeGateway()
    for name in ("usdc_balance_of", "usdc_allowance", "has_code", "approve_usdc", "gateway_deposit"):
        monkeypatch.setattr(contract, name, getattr(fake, name))
    monkeypatch.setattr(contract, "chain_tx_hash", lambda tx: "0x" + "ab" * 32 if tx == "tx-deposit" else None)
    return fake


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "db_path", str(tmp_path / "gd.db"))
    asyncio.run(models.init_db())
    asyncio.run(decision_log.init_db())


@pytest.fixture
def business(chain):
    chain.agent_addr = AGENT
    return {**asyncio.run(models.get_business(1)), "agent_address": AGENT, "circle_wallet_id": "wallet-1"}


def _run(business, units=1_000_000, confirm=lambda m: None, dry_run=False):
    return asyncio.run(gd.run(business, units, confirm=confirm, dry_run=dry_run, wait_blocks=False, poll=(2, 0)))


def _audit():
    with sqlite3.connect(config.db_path) as db:
        return db.execute("SELECT action, subject, amount_usdc, model_used, onchain_tx_id, chain_tx_hash, reasoning FROM audit_log WHERE action = 'GATEWAY_DEPOSIT'").fetchall()


# ---- the amount

@pytest.mark.parametrize("text,units", [("1", 1_000_000), ("0.5", 500_000), ("5", 5_000_000), ("0.000001", 1), ("2.250000", 2_250_000)])
def test_good_amounts(text, units):
    assert gd.parse_amount(text) == units


@pytest.mark.parametrize("text", ["0", "-1", "abc", "", "5.000001", "6", "1.0000001", "nan", "inf", "1e1"])
def test_bad_amounts_are_refused(text):
    with pytest.raises(gd.DepositError):
        gd.parse_amount(text)


# ---- the Circle addresses and calls

def test_the_gateway_wallet_default_is_circles_testnet_address():
    assert config.gateway_wallet_address == GATEWAY_WALLET
    assert Web3.to_checksum_address(config.gateway_wallet_address) == GATEWAY_WALLET


def test_the_address_can_be_overridden_from_the_environment(monkeypatch):
    other = "0x" + "12" * 20
    monkeypatch.setenv("GATEWAY_WALLET_ADDRESS", other)
    assert Config.load().gateway_wallet_address == other


def test_the_two_calls_use_the_documented_signatures(monkeypatch):
    seen = []
    monkeypatch.setattr(contract, "execute_from_wallet", lambda *a: seen.append(a) or "tx")
    contract.approve_usdc("w1", GATEWAY_WALLET, 1_500_000)
    contract.gateway_deposit("w1", 1_500_000)
    usdc = Web3.to_checksum_address(config.usdc_address)
    assert seen[0] == ("w1", usdc, "approve(address,uint256)", [GATEWAY_WALLET, "1500000"])
    assert seen[1] == ("w1", GATEWAY_WALLET, "deposit(address,uint256)", [usdc, "1500000"])
    assert Web3.keccak(text="approve(address,uint256)")[:4].hex() == "095ea7b3"
    assert Web3.keccak(text="deposit(address,uint256)")[:4].hex() == "47e7ef24"


def test_nothing_in_the_deposit_path_sends_a_plain_transfer():
    """A plain USDC transfer to the Gateway Wallet loses the funds (Circle's warning), so no code path may build one."""
    src = open(gd.__file__).read() + open(contract.__file__).read()
    assert "transfer(address,uint256)" not in src


# ---- dry run

def test_a_dry_run_reads_only(business, fg, capsys):
    r = _run(business, dry_run=True)
    assert r["dry_run"] is True and fg.sent == [] and _audit() == []
    out = capsys.readouterr().out
    assert GATEWAY_WALLET in out and "1.000000 USDC" in out and "Dry run" in out


def test_a_dry_run_still_refuses_what_a_real_run_would(business, fg):
    fg.balance = 500_000
    with pytest.raises(gd.DepositError):
        _run(business, dry_run=True)


# ---- the deposit

def test_the_deposit_runs_approve_then_deposit_after_the_audit_row(business, fg):
    order = []

    def confirm(message):
        order.append(("confirm", message.split(":")[0], len(_audit()), len(fg.sent)))

    r = _run(business, confirm=confirm)
    assert [s[0] for s in fg.sent] == ["approve", "deposit"]
    assert fg.sent[0] == ("approve", "wallet-1", GATEWAY_WALLET, 1_000_000)  # the exact amount, to the Gateway Wallet
    assert fg.sent[1] == ("deposit", "wallet-1", 1_000_000)
    # step 1 is asked before any row; the row exists before step 2 is asked; nothing is sent before step 2
    assert order == [("confirm", "STEP 1", 0, 0), ("confirm", "STEP 2", 1, 0), ("confirm", "STEP 3", 1, 1)]
    (action, subject, amount, model, tx_id, chain_hash, reasoning), = _audit()
    assert (action, subject, amount, model, tx_id) == ("GATEWAY_DEPOSIT", "GATEWAY", 1.0, "manual", "tx-deposit")
    assert chain_hash is None  # a deposit has no payment event for the proof page to match
    assert "not a payment to a vendor" in reasoning and AGENT in reasoning and GATEWAY_WALLET in reasoning
    assert r["tx_hash"] == "0x" + "ab" * 32 and r["circle_tx_id"] == "tx-deposit"


def test_the_audit_row_is_in_the_signed_ledger_chain(business, fg):
    _run(business)
    result = asyncio.run(ledger.verify_chain(business["id"]))
    assert result["ok"] is True and result["problem_count"] == 0


def test_an_existing_allowance_skips_the_approve(business, fg):
    fg.allowance = 3_000_000
    _run(business)
    assert [s[0] for s in fg.sent] == ["deposit"]


def test_a_smaller_allowance_is_topped_up_with_an_exact_approve(business, fg):
    fg.allowance = 500_000
    _run(business, units=1_000_000)
    assert fg.sent[0] == ("approve", "wallet-1", GATEWAY_WALLET, 1_000_000)


def test_the_new_gateway_balance_is_reported(business, fg, monkeypatch, capsys):
    seen = iter(["0", "0", "1.000000"])  # before, then two reads after (the second sees the credit)

    def handler(request):
        return httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": AGENT, "balance": next(seen)}]})

    gateway._transport = httpx.MockTransport(handler)
    r = _run(business)
    assert r["gateway_before"] == 0.0 and r["gateway_after"] == 1.0
    assert "Gateway balance now: 1.000000 USDC" in capsys.readouterr().out


def test_an_unreadable_gateway_api_does_not_block_or_invent_a_balance(business, fg, capsys):
    r = _run(business)  # the autouse fixture makes the Gateway API answer 503
    assert r["gateway_before"] is None and r["gateway_after"] is None
    out = capsys.readouterr().out
    assert "unreadable" in out and "after finality" in out
    assert [s[0] for s in fg.sent] == ["approve", "deposit"]


def test_a_balance_that_has_not_moved_is_not_called_a_success(business, fg, capsys):
    gateway._transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": AGENT, "balance": "0"}]}))
    r = _run(business)
    assert r["gateway_after"] == 0.0
    assert "after finality" in capsys.readouterr().out


# ---- fail closed: nothing is written or sent

def _nothing_happened(fg):
    assert fg.sent == [] and _audit() == []


def test_an_unreadable_chain_sends_nothing(business, fg):
    fg.broken = True
    with pytest.raises(gd.DepositError, match="Could not read the chain"):
        _run(business)
    _nothing_happened(fg)


def test_a_balance_that_cannot_cover_the_deposit_and_gas_sends_nothing(business, fg):
    fg.balance = 1_050_000  # 1.05 is not enough for 1.00 plus the 0.10 gas reserve
    with pytest.raises(gd.DepositError, match="needs 1.100000"):
        _run(business)
    _nothing_happened(fg)


def test_the_wallet_must_be_the_contracts_agent(business, fg, chain):
    chain.agent_addr = "0x" + "99" * 20
    with pytest.raises(gd.DepositError, match="agent\\(\\)"):
        _run(business)
    _nothing_happened(fg)


def test_no_contract_at_the_gateway_address_sends_nothing(business, fg):
    fg.code[GATEWAY_WALLET.lower()] = False
    with pytest.raises(gd.DepositError, match="Gateway Wallet address"):
        _run(business)
    _nothing_happened(fg)


def test_a_business_without_an_agent_wallet_sends_nothing(business, fg):
    with pytest.raises(gd.DepositError, match="no agent wallet"):
        _run({**business, "circle_wallet_id": None})
    _nothing_happened(fg)


# ---- declining and failing part-way

def test_declining_step_one_writes_and_sends_nothing(business, fg):
    def no(message):
        raise SystemExit("Aborted")
    with pytest.raises(SystemExit):
        _run(business, confirm=no)
    _nothing_happened(fg)


def test_declining_step_two_leaves_the_row_and_sends_nothing(business, fg):
    def decline_on(step):
        def confirm(message):
            if message.startswith(step):
                raise SystemExit("Aborted")
        return confirm
    with pytest.raises(SystemExit):
        _run(business, confirm=decline_on("STEP 2"))
    assert fg.sent == [] and len(_audit()) == 1


def test_declining_step_three_sends_the_approve_but_not_the_deposit(business, fg):
    def confirm(message):
        if message.startswith("STEP 3"):
            raise SystemExit("Aborted")
    with pytest.raises(SystemExit):
        _run(business, confirm=confirm)
    assert [s[0] for s in fg.sent] == ["approve"]
    assert _audit()[0][4] is None  # no transaction id recorded


def test_an_approve_that_does_not_take_effect_stops_before_the_deposit(business, fg):
    fg.approve_no_effect = True
    with pytest.raises(gd.DepositError, match="allowance"):
        _run(business)
    assert [s[0] for s in fg.sent] == ["approve"]


def test_a_failed_deposit_is_loud_and_records_no_transaction(business, fg):
    fg.fail_deposit = True
    with pytest.raises(RuntimeError, match="circle refused"):
        _run(business)
    row, = _audit()
    assert row[4] is None and row[5] is None


def test_a_reverted_deposit_transaction_is_loud(business, fg, monkeypatch):
    def wait(tx):
        if tx == "tx-deposit":
            raise RuntimeError("Transaction tx-deposit FAILED: execution reverted")
    monkeypatch.setattr(contract, "wait_for_transaction", wait)
    with pytest.raises(RuntimeError, match="reverted"):
        _run(business)
    assert _audit()[0][4] is None


# ---- it is not a payment and does not count as one

def test_a_deposit_row_is_not_counted_as_a_payment(business, fg):
    _run(business)
    stats = asyncio.run(decision_log.stats())
    assert stats["paid"] == 0 and stats["paid_usdc"] == 0 and stats["decisions"] == 0
    feed = asyncio.run(decision_log.recent())
    assert [d["action"] for d in feed] == ["GATEWAY_DEPOSIT"] and feed[0]["chain_tx_hash"] is None
