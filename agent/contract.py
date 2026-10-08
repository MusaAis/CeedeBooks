"""Reads and Circle-signed writes against one business's BudgetEnforcer (v1 or v2).

There is no module-level contract any more: every call goes through `chain_for(business)`, so a request can only ever
touch the contract and Circle wallet of the business it belongs to.
"""
import time
import uuid

_TERMINAL_SUCCESS = "COMPLETE"
_TERMINAL_FAILURE = {"FAILED", "CANCELLED"}

from circle.web3 import developer_controlled_wallets, utils
from eth_abi import encode
from web3 import Web3

from agent.config import config

CHAIN_ID = 5042002  # Arc Testnet; v2 commitments are bound to it

_wallet_client = utils.init_developer_controlled_wallets_client(
    api_key=config.circle_api_key, entity_secret=config.circle_entity_secret
)
_transactions_api = developer_controlled_wallets.TransactionsApi(_wallet_client)

_w3 = Web3(Web3.HTTPProvider(config.arc_rpc_url))


def _fn(name: str, inputs: list, outputs: list) -> dict:
    return {"type": "function", "name": name, "stateMutability": "view",
            "inputs": [{"name": "", "type": t} for t in inputs], "outputs": [{"name": "", "type": t} for t in outputs]}


_V1_ABI = [
    _fn("vendorApproved", ["address"], ["bool"]),
    _fn("remainingToday", ["uint8"], ["uint256", "uint256"]),
    _fn("paid", ["bytes32"], ["bool"]),
    _fn("approver", [], ["address"]),
    _fn("agent", [], ["address"]),
    _fn("pendingApprover", [], ["address"]),
    _fn("paused", [], ["bool"]),
    _fn("dailyLimit", [], ["uint256"]),
    _fn("perTxLimit", [], ["uint256"]),
    _fn("categoryDailyLimit", ["uint8"], ["uint256"]),
]
_V2_ABI = _V1_ABI + [_fn("weeklyLimit", [], ["uint256"]), _fn("remainingThisWeek", [], ["uint256"])]
_ABI = {1: _V1_ABI, 2: _V2_ABI}

_FACTORY_ABI = [_fn("isBusiness", ["address"], ["bool"]), _fn("businessCount", [], ["uint256"])]
_USDC_ABI = [_fn("balanceOf", ["address"], ["uint256"])]
_usdc = _w3.eth.contract(address=Web3.to_checksum_address(config.usdc_address), abi=_USDC_ABI)


def to_bytes32(text: str) -> bytes:
    return Web3.keccak(text=text)


def invoice_key(vendor: str, invoice_number: bytes) -> bytes:
    return Web3.keccak(encode(["address", "bytes32"], [Web3.to_checksum_address(vendor), invoice_number]))


def wait_for_transaction(tx_id: str, timeout: float = 60, interval: float = 2) -> None:
    """Blocks until Circle reports a terminal state; raises if it failed or reverted."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        txn = _transactions_api.get_transaction(id=tx_id).data.transaction
        state = getattr(txn.state, "value", txn.state)
        if state == _TERMINAL_SUCCESS:
            return
        if state in _TERMINAL_FAILURE:
            raise RuntimeError(f"Transaction {tx_id} {state}: {txn.error_reason} - {txn.error_details}")
        time.sleep(interval)
    raise TimeoutError(f"Transaction {tx_id} did not reach a terminal state within {timeout}s")


def chain_tx_hash(circle_tx_id: str):
    """The on-chain transaction hash for a Circle transaction id, or None if it is not available yet."""
    try:
        txn = _transactions_api.get_transaction(id=circle_tx_id).data.transaction
        value = getattr(txn, "tx_hash", None)
        return str(value) if value else None
    except Exception:
        return None


# ---- audit events. topic0 = keccak256 of the event signature (identical in v1 and v2).
_REASONING_EVENTS = {
    "1c9d044335a4a11ba5017f5152049ff125e54489e1f73345177810d658c624d0": ("PaymentMade", 2),
    "8b958ddaf670e221a55a2f21042994d5613123b0230429b5763e2178979ed045": ("PaymentEscalated", 2),
    "15eb229f5b2ce7ced26c2ceefe969a9c01e6e867545204be4053b0195c4fc4ec": ("DecisionLogged", 1),
}
EVENT_SIGNATURES = {
    "PaymentMade": "PaymentMade(bytes32,bytes32,address,uint256,uint8,bytes32,uint256)",
    "PaymentEscalated": "PaymentEscalated(bytes32,bytes32,address,uint256,uint8,string)",
    "DecisionLogged": "DecisionLogged(bytes32,uint256)",
}
_ESCALATION_RESULTS = {
    "f10f5169dd83b5b624e0a34647ebb3e1e73ce20e3bc59a6d5a836e4fd4a375ee": "EscalationApproved",
    "6b90159687711ccd3b2677caad269e5e6b993e68ae1a267d357209e1b6268a28": "EscalationRejected",
}
_ESCALATED_TOPIC = "8b958ddaf670e221a55a2f21042994d5613123b0230429b5763e2178979ed045"


def _hex(value) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    return (text[2:] if text.startswith("0x") else text).lower()


class Chain:
    """One business's contract and Circle wallet."""

    def __init__(self, address: str, version: int, circle_wallet_id: str):
        if version not in _ABI:
            raise ValueError(f"unknown contract version {version}")
        self.address = Web3.to_checksum_address(address)
        self.version = version
        self.circle_wallet_id = circle_wallet_id
        self._read = _w3.eth.contract(address=self.address, abi=_ABI[version])

    # ---- reads
    def is_vendor_approved(self, vendor: str) -> bool:
        return self._read.functions.vendorApproved(Web3.to_checksum_address(vendor)).call()

    def is_paid(self, invoice_key_bytes: bytes) -> bool:
        return self._read.functions.paid(invoice_key_bytes).call()

    def usdc_balance(self) -> int:
        """USDC held by this business's pool, in raw 6-decimal units."""
        return _usdc.functions.balanceOf(self.address).call()

    def remaining_today(self, category: int) -> tuple[int, int]:
        return self._read.functions.remainingToday(category).call()

    def approver(self) -> str:
        return self._read.functions.approver().call()

    def agent(self) -> str:
        return self._read.functions.agent().call()

    def pending_approver(self) -> str:
        return self._read.functions.pendingApprover().call()

    def is_paused(self) -> bool:
        return self._read.functions.paused().call()

    def budget_limits(self) -> tuple:
        return self._read.functions.dailyLimit().call(), self._read.functions.perTxLimit().call()

    def weekly_limit(self):
        """The weekly cap (v2 only; None for the v1 contract, which has none)."""
        return self._read.functions.weeklyLimit().call() if self.version >= 2 else None

    def remaining_this_week(self):
        return self._read.functions.remainingThisWeek().call() if self.version >= 2 else None

    def category_limit(self, category: int) -> int:
        return self._read.functions.categoryDailyLimit(category).call()

    def commitment_for(
        self, vendor: str, amount: int, invoice_number: bytes, doc_hash: bytes, category: int, reasoning_hash: bytes
    ) -> bytes:
        """v1 hashes the payment fields; v2 also binds this contract's address and the chain id."""
        types = ["address", "uint256", "bytes32", "bytes32", "uint8", "bytes32"]
        values = [Web3.to_checksum_address(vendor), amount, invoice_number, doc_hash, category, reasoning_hash]
        if self.version >= 2:
            types, values = ["address", "uint256"] + types, [self.address, CHAIN_ID] + values
        return Web3.keccak(encode(types, values))

    # ---- Circle-signed writes (from this business's own wallet)
    def _execute(self, abi_function_signature: str, abi_parameters: list) -> str:
        if not self.circle_wallet_id:
            raise RuntimeError("This business has no Circle wallet configured")
        request = developer_controlled_wallets.CreateContractExecutionTransactionForDeveloperRequest.from_dict({
            "walletId": self.circle_wallet_id,
            "contractAddress": self.address,
            "abiFunctionSignature": abi_function_signature,
            "abiParameters": abi_parameters,
            "feeLevel": "MEDIUM",
            "idempotencyKey": str(uuid.uuid4()),
        })
        response = _transactions_api.create_developer_transaction_contract_execution(
            create_contract_execution_transaction_for_developer_request=request
        )
        return response.data.id

    def commit_decision(self, commitment: bytes) -> str:
        return self._execute("commitDecision(bytes32)", [Web3.to_hex(commitment)])

    def log_decision(self, reasoning_hash: bytes) -> str:
        return self._execute("logDecision(bytes32)", [Web3.to_hex(reasoning_hash)])

    def pay(self, vendor: str, amount: int, invoice_number: bytes, doc_hash: bytes, category: int, reasoning_hash: bytes) -> str:
        return self._execute(
            "pay(address,uint256,bytes32,bytes32,uint8,bytes32)",
            [vendor, str(amount), Web3.to_hex(invoice_number), Web3.to_hex(doc_hash), category, Web3.to_hex(reasoning_hash)],
        )

    def escalate(
        self, vendor: str, amount: int, invoice_number: bytes, doc_hash: bytes, category: int, reasoning_hash: bytes, reason: str
    ) -> str:
        return self._execute(
            "escalate(address,uint256,bytes32,bytes32,uint8,bytes32,string)",
            [vendor, str(amount), Web3.to_hex(invoice_number), Web3.to_hex(doc_hash), category, Web3.to_hex(reasoning_hash), reason],
        )

    # ---- reading this contract's events back
    def _logs(self, receipt) -> list:
        mine = self.address.lower()
        return [[_hex(t) for t in log["topics"]] for log in receipt["logs"] if str(log["address"]).lower() == mine and log["topics"]]

    def reasoning_events(self, tx_hash: str) -> list:
        """This contract's audit events in one transaction: [{event, reasoning_hash, block, success}]. Events from any
        other contract are ignored, so a record can only be confirmed by its own business's contract."""
        receipt = _w3.eth.get_transaction_receipt(tx_hash)
        found = []
        for topics in self._logs(receipt):
            if topics[0] not in _REASONING_EVENTS:
                continue
            name, index = _REASONING_EVENTS[topics[0]]
            if len(topics) > index:
                found.append({"event": name, "reasoning_hash": topics[index], "block": receipt["blockNumber"],
                              "success": receipt["status"] == 1})
        return found

    def escalation_key(self, tx_hash: str):
        """The invoice key of the escalation created in this transaction (needed to approve or reject it), or None."""
        for topics in self._logs(_w3.eth.get_transaction_receipt(tx_hash)):
            if topics[0] == _ESCALATED_TOPIC and len(topics) > 1:
                return "0x" + topics[1]
        return None

    def inspect_admin_tx(self, tx_hash: str) -> dict:
        """What an admin transaction did: {sender, to, success, block, escalation: (event, invoice_key, reasoning_hash) | None}."""
        receipt = _w3.eth.get_transaction_receipt(tx_hash)
        escalation = None
        for topics in self._logs(receipt):
            name = _ESCALATION_RESULTS.get(topics[0])
            if name and len(topics) > 2:
                escalation = (name, topics[1], topics[2])
        return {
            "sender": str(receipt["from"]).lower(),
            "to": str(receipt["to"]).lower() if receipt["to"] else None,
            "success": receipt["status"] == 1,
            "block": receipt["blockNumber"],
            "escalation": escalation,
        }


_chains: dict = {}


def chain_for(business: dict) -> Chain:
    """The contract handle for one business row. Raises if the row cannot be used (callers fail closed)."""
    key = (str(business["enforcer_address"]).lower(), int(business["contract_version"]), business.get("circle_wallet_id") or "")
    chain = _chains.get(key)
    if chain is None:
        chain = _chains[key] = Chain(business["enforcer_address"], int(business["contract_version"]), key[2])
    return chain


# ---- onboarding (Phase L3): a hosted agent wallet per business, and proof that a business contract is real

_BUSINESS_CREATED = Web3.keccak(text="BusinessCreated(uint256,address,address,address)").hex().lower().removeprefix("0x")
GAS_DUST_UNITS = 5 * 10**17  # 0.5 USDC of native gas (18 decimals) keeps a hosted agent wallet working for a long time


def native_balance(address: str) -> int:
    """Native (gas) balance in 18-decimal units. Arc gas is USDC."""
    return _w3.eth.get_balance(Web3.to_checksum_address(address))


def create_agent_wallet(label: str) -> dict:
    """A new Circle developer-controlled wallet on Arc Testnet in its own wallet set, same call shape as agent/wallet_setup.py.
    Returns {wallet_id, address}. Hosted custody: the server can sign for it, so it must only ever be a business's AGENT."""
    import json

    sets_api = developer_controlled_wallets.WalletSetsApi(_wallet_client)
    wallets_api = developer_controlled_wallets.WalletsApi(_wallet_client)
    wallet_set = sets_api.create_wallet_set(developer_controlled_wallets.CreateWalletSetRequest.from_dict({"name": f"CeedeBooks business: {label}"[:50]}))
    wallet_set_id = wallet_set.data.wallet_set.actual_instance.id
    wallet = wallets_api.create_wallet(developer_controlled_wallets.CreateWalletRequest.from_dict(
        {"walletSetId": wallet_set_id, "blockchains": ["ARC-TESTNET"], "count": 1, "accountType": "EOA"}))
    data = json.loads(wallet.model_dump_json())["data"]["wallets"][0]
    return {"wallet_id": data["id"], "address": Web3.to_checksum_address(data["address"])}


def verify_business_creation(tx_hash: str, factory_address: str) -> dict:
    """Proves from the chain that tx_hash made a business through OUR factory. Returns {id, enforcer, approver, agent, block}.
    Checks: the transaction succeeded and went to the factory; the factory's BusinessCreated event is in it; the factory
    itself says the new contract is a business; and the contract's own approver() and agent() match the event. Raises
    ValueError with a reason on any failure, and lets RPC errors propagate (callers fail closed)."""
    factory = Web3.to_checksum_address(factory_address)
    receipt = _w3.eth.get_transaction_receipt(tx_hash)
    if receipt["status"] != 1:
        raise ValueError("That transaction failed on-chain")
    if not receipt["to"] or str(receipt["to"]).lower() != factory.lower():
        raise ValueError("That transaction did not go to the CeedeBooks factory")
    events = []
    for log in receipt["logs"]:
        topics = [_hex(t) for t in log["topics"]]
        if str(log["address"]).lower() == factory.lower() and len(topics) == 4 and topics[0] == _BUSINESS_CREATED:
            events.append((topics, _hex(log["data"])))
    if len(events) != 1:
        raise ValueError("Expected exactly one business creation in that transaction")
    topics, data = events[0]
    enforcer = Web3.to_checksum_address("0x" + topics[2][-40:])
    approver = Web3.to_checksum_address("0x" + topics[3][-40:])
    agent = Web3.to_checksum_address("0x" + data[-40:])
    if not _w3.eth.contract(address=factory, abi=_FACTORY_ABI).functions.isBusiness(enforcer).call():
        raise ValueError("The factory does not list that contract as a business")
    live = _w3.eth.contract(address=enforcer, abi=_V2_ABI)
    if live.functions.approver().call().lower() != approver.lower() or live.functions.agent().call().lower() != agent.lower():
        raise ValueError("The contract's approver or agent does not match its creation record")
    return {"id": int(topics[1], 16), "enforcer": enforcer, "approver": approver, "agent": agent, "block": receipt["blockNumber"]}
