"""Shared test doubles. The chain is never real: `contract.chain_for` is patched to hand out a FakeChain per business."""
import pytest

from agent import contract
from agent.config import config


class FakeChain:
    """Drop-in for agent.contract.Chain. Set attributes to change what the 'contract' says; `calls` records writes."""

    def __init__(self, address: str, version: int = 1):
        self.address, self.version = address, version
        self.vendor_ok, self.paid, self.paused = True, False, False
        self.balance = 100_000_000
        self.remaining = (1_000_000_000, 1_000_000_000)
        self.approver_addr = "0x" + "00" * 20
        self.pending_addr = "0x" + "00" * 20
        self.limits = (100_000_000, 20_000_000)
        self.weekly = 500_000_000 if version >= 2 else None
        self.category_cap = 50_000_000
        self.events = []          # reasoning_events result
        self.admin_tx = None      # inspect_admin_tx result
        self.esc_key = None
        self.broken = False       # every read raises, like an unreachable RPC
        self.agent_addr = "0x" + "a9" * 20
        self.calls = []

    def _read(self):
        if self.broken:
            raise RuntimeError("chain unreachable")

    def is_vendor_approved(self, vendor):
        self._read(); return self.vendor_ok

    def is_paid(self, key):
        self._read(); return self.paid

    def usdc_balance(self):
        self._read(); return self.balance

    def remaining_today(self, category):
        self._read(); return self.remaining

    def approver(self):
        self._read(); return self.approver_addr

    def agent(self):
        self._read(); return self.agent_addr

    def pending_approver(self):
        self._read(); return self.pending_addr

    def is_paused(self):
        self._read(); return self.paused

    def budget_limits(self):
        self._read(); return self.limits

    def weekly_limit(self):
        self._read(); return self.weekly

    def remaining_this_week(self):
        self._read(); return self.weekly

    def category_limit(self, category):
        self._read(); return self.category_cap

    def commitment_for(self, *a):
        return b"\x02" * 32

    def commit_decision(self, commitment):
        self.calls.append(("commit", commitment)); return "tx-commit"

    def log_decision(self, reasoning_hash):
        self.calls.append(("log", reasoning_hash)); return "tx-log"

    def pay(self, *a):
        self.calls.append(("pay", a)); return "tx-pay"

    def escalate(self, *a):
        self.calls.append(("escalate", a)); return "tx-esc"

    def reasoning_events(self, tx_hash):
        self._read(); return self.events

    def escalation_key(self, tx_hash):
        self._read(); return self.esc_key

    def inspect_admin_tx(self, tx_hash):
        self._read()
        if self.admin_tx is None:
            raise RuntimeError("not found")
        return self.admin_tx


class Chains:
    """One FakeChain per business contract address, created on first use."""

    def __init__(self):
        self.by_address = {}

    def get(self, address: str, version: int = 1) -> FakeChain:
        key = address.lower()
        if key not in self.by_address:
            self.by_address[key] = FakeChain(address, version)
        return self.by_address[key]

    def for_business(self, business: dict) -> FakeChain:
        return self.get(business["enforcer_address"], int(business["contract_version"]))


@pytest.fixture(autouse=True)
def ledger_key(monkeypatch):
    """A throwaway Ed25519 seed for the decision ledger (a test value, not a secret)."""
    monkeypatch.setattr(config, "ledger_signing_key", "11" * 32)


@pytest.fixture(autouse=True)
def chains(monkeypatch):
    registry = Chains()
    monkeypatch.setattr(contract, "chain_for", registry.for_business)
    monkeypatch.setattr(contract, "wait_for_transaction", lambda *a, **k: None)
    monkeypatch.setattr(contract, "chain_tx_hash", lambda tx: None)
    return registry


class FakeAnchor:
    """The LedgerAnchor contract and the gas of agent wallets. `sent` records every anchor transaction."""

    def __init__(self):
        self.state = {}      # agent address (lower) -> (sequence, head hex)
        self.gas = {}        # agent address (lower) -> native balance; default plenty
        self.sent = []
        self.broken = False
        self.fail_send = False
        self.swallow = False  # the transaction "succeeds" but the contract shows nothing (wrong sender)
        self.sender_of = {}   # Circle wallet id -> the address that wallet sends from

    def anchor_state(self, agent):
        if self.broken:
            raise RuntimeError("anchor unreadable")
        return self.state.get(agent.lower(), (0, "00" * 32))

    def native_balance(self, address):
        return self.gas.get(address.lower(), 10 ** 18)

    def anchor_ledger(self, wallet_id, head_hex, sequence):
        if self.fail_send:
            raise RuntimeError("circle refused")
        self.sent.append((wallet_id, head_hex, sequence))
        self._last = (head_hex, sequence)
        return f"anchor-tx-{len(self.sent)}"

    def settle(self, sender):
        """The confirmed transaction lands: the contract now holds the head for the sending address."""
        if sender and not self.swallow:
            head_hex, sequence = self._last
            self.state[sender.lower()] = (sequence, head_hex)


@pytest.fixture(autouse=True)
def anchors(monkeypatch, chains):
    fake = FakeAnchor()
    monkeypatch.setattr(contract, "anchor_state", fake.anchor_state)
    monkeypatch.setattr(contract, "native_balance", fake.native_balance)

    def send(wallet_id, head_hex, sequence):
        tx = fake.anchor_ledger(wallet_id, head_hex, sequence)
        fake.settle(fake.sender_of.get(wallet_id))
        return tx

    monkeypatch.setattr(contract, "anchor_ledger", send)
    return fake


@pytest.fixture
def chain(chains):
    """The home business's contract (business 1, the v1 enforcer from the environment)."""
    return chains.get(config.budget_enforcer_address, 1)
