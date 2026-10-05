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
def chains(monkeypatch):
    registry = Chains()
    monkeypatch.setattr(contract, "chain_for", registry.for_business)
    monkeypatch.setattr(contract, "wait_for_transaction", lambda *a, **k: None)
    monkeypatch.setattr(contract, "chain_tx_hash", lambda tx: None)
    return registry


@pytest.fixture
def chain(chains):
    """The home business's contract (business 1, the v1 enforcer from the environment)."""
    return chains.get(config.budget_enforcer_address, 1)
