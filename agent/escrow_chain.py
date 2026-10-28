"""Read-only access to a MilestoneEscrow contract. Nothing here sends a transaction; every read that fails raises, so a
caller answers "unreadable" instead of guessing."""
from decimal import Decimal

from web3 import Web3

from agent import contract
from agent.config import config

_STATUS = {0: "pending", 1: "released", 2: "rejected"}
_ABI = [
    contract._fn("agent", [], ["address"]),
    contract._fn("approver", [], ["address"]),
    contract._fn("pendingApprover", [], ["address"]),
    contract._fn("contractor", [], ["address"]),
    contract._fn("usdc", [], ["address"]),
    contract._fn("agentReleaseLimit", [], ["uint256"]),
    contract._fn("paused", [], ["bool"]),
    contract._fn("pendingTotal", [], ["uint256"]),
    contract._fn("withdrawable", [], ["uint256"]),
    contract._fn("milestoneCount", [], ["uint256"]),
    contract._fn("milestones", ["uint256"], ["uint256", "bytes32", "uint8"]),
]


_RELEASED_TOPIC = contract._hex(Web3.keccak(text="MilestoneReleased(uint256,bytes32,bytes32,uint256)"))


def usdc_text(units: int) -> str:
    """Exact USDC text for raw 6-decimal units, computed by code."""
    return f"{Decimal(int(units)) / Decimal(10**6):f}"


class EscrowChain:
    def __init__(self, address: str):
        self.address = Web3.to_checksum_address(address)
        self._read = contract._w3.eth.contract(address=self.address, abi=_ABI)

    def has_code(self) -> bool:
        return contract.has_code(self.address)

    def snapshot(self) -> dict:
        """Everything a viewer needs, in one read. Raises if the chain cannot be read or nothing is deployed there."""
        if not self.has_code():
            raise RuntimeError("no contract at this address")
        f = self._read.functions
        limit = int(f.agentReleaseLimit().call())
        count = int(f.milestoneCount().call())
        milestones = []
        for i in range(count):
            amount, req_hash, status = f.milestones(i).call()
            milestones.append({
                "index": i, "amount_usdc": usdc_text(amount), "requirements_hash": Web3.to_hex(req_hash),
                "status": _STATUS[int(status)], "agent_can_release": int(status) == 0 and int(amount) <= limit,
            })
        return {
            "address": self.address, "agent": str(f.agent().call()), "approver": str(f.approver().call()),
            "contractor": str(f.contractor().call()), "usdc": str(f.usdc().call()), "paused": bool(f.paused().call()),
            "agent_release_limit_usdc": usdc_text(limit), "balance_usdc": usdc_text(contract.usdc_balance_of(self.address)),
            "pending_usdc": usdc_text(f.pendingTotal().call()), "withdrawable_usdc": usdc_text(f.withdrawable().call()),
            "milestones": milestones,
        }


    def release_events(self, tx_hash: str) -> list:
        """The MilestoneReleased events this escrow emitted in one transaction: [{event, reasoning_hash, block, success}].
        Events from any other contract are ignored. Raises if the chain cannot be read."""
        receipt = contract._w3.eth.get_transaction_receipt(tx_hash)
        found = []
        for log in receipt["logs"]:
            topics = [contract._hex(t) for t in log["topics"]]
            if str(log["address"]).lower() != self.address.lower() or len(topics) < 3 or topics[0] != _RELEASED_TOPIC:
                continue
            found.append({"event": "MilestoneReleased", "reasoning_hash": topics[2], "block": receipt["blockNumber"],
                          "success": receipt["status"] == 1})
        return found


def escrow_for(address: str) -> EscrowChain:
    return EscrowChain(address)


def registration_problems(snapshot: dict, business: dict, admin_address: str) -> list:
    """Why this escrow cannot be registered to this business (empty list: it can). Only facts read from the contract."""
    same = lambda a, b: str(a).lower() == str(b).lower()
    problems = []
    if not same(snapshot["usdc"], config.usdc_address):
        problems.append("The escrow does not hold the USDC this system uses")
    if not business.get("agent_address") or not same(snapshot["agent"], business["agent_address"]):
        problems.append("The escrow's agent is not this business's agent wallet")
    if not same(snapshot["approver"], admin_address):
        problems.append("The escrow's approver is not the wallet you are signed in with")
    if same(snapshot["address"], business.get("enforcer_address", "")):
        problems.append("That is the business's own budget contract, not an escrow")
    return problems
