"""Reads and Circle-signed writes against BudgetEnforcer."""
import uuid

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

