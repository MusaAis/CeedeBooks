"""Reads and Circle-signed writes against BudgetEnforcer."""
import time
import uuid

_TERMINAL_SUCCESS = "COMPLETE"
_TERMINAL_FAILURE = {"FAILED", "CANCELLED"}

from circle.web3 import developer_controlled_wallets, utils
from eth_abi import encode
from web3 import Web3

from agent.config import config

_wallet_client = utils.init_developer_controlled_wallets_client(
    api_key=config.circle_api_key, entity_secret=config.circle_entity_secret
)
_transactions_api = developer_controlled_wallets.TransactionsApi(_wallet_client)

_w3 = Web3(Web3.HTTPProvider(config.arc_rpc_url))
_READ_ABI = [
    {"type": "function", "name": "vendorApproved", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "bool"}]},
    {"type": "function", "name": "remainingToday", "stateMutability": "view",
     "inputs": [{"name": "category", "type": "uint8"}],
     "outputs": [{"name": "overall", "type": "uint256"}, {"name": "inCategory", "type": "uint256"}]},
    {"type": "function", "name": "paid", "stateMutability": "view",
     "inputs": [{"name": "", "type": "bytes32"}], "outputs": [{"name": "", "type": "bool"}]},
]
_read_contract = _w3.eth.contract(address=Web3.to_checksum_address(config.budget_enforcer_address), abi=_READ_ABI)
_USDC_ABI = [
    {"type": "function", "name": "balanceOf", "stateMutability": "view",
     "inputs": [{"name": "", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]
_usdc = _w3.eth.contract(address=Web3.to_checksum_address(config.usdc_address), abi=_USDC_ABI)


def to_bytes32(text: str) -> bytes:
    return Web3.keccak(text=text)


def invoice_key(vendor: str, invoice_number: bytes) -> bytes:
    return Web3.keccak(encode(["address", "bytes32"], [Web3.to_checksum_address(vendor), invoice_number]))


def commitment_for(
    vendor: str, amount: int, invoice_number: bytes, doc_hash: bytes, category: int, reasoning_hash: bytes
) -> bytes:
    return Web3.keccak(encode(
        ["address", "uint256", "bytes32", "bytes32", "uint8", "bytes32"],
        [Web3.to_checksum_address(vendor), amount, invoice_number, doc_hash, category, reasoning_hash],
    ))


def is_vendor_approved(vendor: str) -> bool:
    return _read_contract.functions.vendorApproved(Web3.to_checksum_address(vendor)).call()


def is_paid(invoice_key_bytes: bytes) -> bool:
    return _read_contract.functions.paid(invoice_key_bytes).call()


def usdc_balance() -> int:
    """USDC held by the BudgetEnforcer pool, in raw 6-decimal units."""
    return _usdc.functions.balanceOf(Web3.to_checksum_address(config.budget_enforcer_address)).call()


def remaining_today(category: int) -> tuple[int, int]:
    return _read_contract.functions.remainingToday(category).call()


def _execute(abi_function_signature: str, abi_parameters: list) -> str:
    request = developer_controlled_wallets.CreateContractExecutionTransactionForDeveloperRequest.from_dict({
        "walletId": config.circle_treasury_wallet_id,
        "contractAddress": config.budget_enforcer_address,
        "abiFunctionSignature": abi_function_signature,
        "abiParameters": abi_parameters,
        "feeLevel": "MEDIUM",
        "idempotencyKey": str(uuid.uuid4()),
    })
    response = _transactions_api.create_developer_transaction_contract_execution(
        create_contract_execution_transaction_for_developer_request=request
    )
    return response.data.id


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


def commit_decision(commitment: bytes) -> str:
    return _execute("commitDecision(bytes32)", [Web3.to_hex(commitment)])


def log_decision(reasoning_hash: bytes) -> str:
    return _execute("logDecision(bytes32)", [Web3.to_hex(reasoning_hash)])


def pay(vendor: str, amount: int, invoice_number: bytes, doc_hash: bytes, category: int, reasoning_hash: bytes) -> str:
    return _execute(
        "pay(address,uint256,bytes32,bytes32,uint8,bytes32)",
        [vendor, str(amount), Web3.to_hex(invoice_number), Web3.to_hex(doc_hash), category, Web3.to_hex(reasoning_hash)],
    )


def escalate(
    vendor: str, amount: int, invoice_number: bytes, doc_hash: bytes, category: int, reasoning_hash: bytes, reason: str
) -> str:
    return _execute(
        "escalate(address,uint256,bytes32,bytes32,uint8,bytes32,string)",
        [vendor, str(amount), Web3.to_hex(invoice_number), Web3.to_hex(doc_hash), category, Web3.to_hex(reasoning_hash), reason],
    )



# ---- reading the audit events back (Phase C). topic0 = keccak256 of the event signature.
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


def _hex(value) -> str:
    text = value.hex() if hasattr(value, "hex") else str(value)
    return (text[2:] if text.startswith("0x") else text).lower()


def chain_tx_hash(circle_tx_id: str):
    """The on-chain transaction hash for a Circle transaction id, or None if it is not available yet."""
    try:
        txn = _transactions_api.get_transaction(id=circle_tx_id).data.transaction
        value = getattr(txn, "tx_hash", None)
        return str(value) if value else None
    except Exception:
        return None


def reasoning_events(tx_hash: str) -> list:
    """BudgetEnforcer audit events in one transaction: [{event, reasoning_hash, block, success}]."""
    receipt = _w3.eth.get_transaction_receipt(tx_hash)
    enforcer = config.budget_enforcer_address.lower()
    found = []
    for log in receipt["logs"]:
        if str(log["address"]).lower() != enforcer:
            continue
        topics = [_hex(t) for t in log["topics"]]
        if not topics or topics[0] not in _REASONING_EVENTS:
            continue
        name, index = _REASONING_EVENTS[topics[0]]
        if len(topics) > index:
            found.append(
                {"event": name, "reasoning_hash": topics[index], "block": receipt["blockNumber"], "success": receipt["status"] == 1}
            )
    return found
