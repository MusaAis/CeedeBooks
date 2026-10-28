"""The agent's release path for a MilestoneEscrow (Phase H3, v1.3.2.3).

The agent decides with code only (no model): a milestone is released when the escrow, its roles and its balance allow it,
the amount is inside the escrow's own agent limit, and the evidence shows a merged GitHub pull request. A merged pull
request is an input to that decision, not proof that the requirements are met; the contract still enforces the limit,
the order of commit and release, and the approver's override. Anything above the limit goes to the approver, who can
release it from their own wallet: the agent never sends that transaction.

This is the only escrow module that sends transactions, and it sends exactly two, both from the business's own agent
wallet: commitDecision, then (after it is confirmed) releaseMilestone. `agent/escrow_chain.py` stays read-only.
"""
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

from eth_abi import encode
from web3 import Web3

from agent import contract, decision_log

ACTION_RELEASED = "ESCROW_RELEASED"
ACTION_HELD = "ESCROW_HELD"
ACTION_ESCALATED = "ESCROW_ESCALATED"
ESCROW_ACTIONS = (ACTION_RELEASED, ACTION_HELD, ACTION_ESCALATED)
_SUBJECT = re.compile(r"^escrow:(0x[0-9a-f]{40}):milestone:([0-9]{1,3})$")
_HASH = re.compile(r"^0x[0-9a-f]{64}$")


class Verdict(str, Enum):
    RELEASE = "release"
    HOLD = "hold"
    ESCALATE = "escalate"


class Refused(Exception):
    """The request is not a decision at all (wrong role, nothing to release): nothing is recorded. `status` is the HTTP answer."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class Assessment:
    verdict: Verdict
    reason: str
    checks: dict


def subject_for(escrow_address: str, index: int) -> str:
    return f"escrow:{escrow_address.lower()}:milestone:{int(index)}"


def parse_subject(subject: str) -> Optional[tuple]:
    """(escrow address, milestone index) of an escrow decision's subject, or None."""
    m = _SUBJECT.match(subject or "")
    return (m.group(1), int(m.group(2))) if m else None


def assess(snapshot: dict, index: int, facts: dict, business: dict) -> Assessment:
    """The agent's verdict for one milestone, from facts only. Pure: no I/O. Raises Refused for requests that are not
    decisions (the escrow is not this business's, the milestone does not exist or is no longer pending)."""
    same = lambda a, b: str(a).lower() == str(b).lower()
    if not business.get("agent_address") or not same(snapshot["agent"], business["agent_address"]):
        raise Refused("The escrow's agent is not this business's agent wallet, so the agent cannot release from it")
    if not (isinstance(index, int) and 0 <= index < len(snapshot["milestones"])):
        raise Refused("That milestone does not exist", 404)
    milestone = snapshot["milestones"][index]
    if milestone["status"] != "pending":
        raise Refused(f"That milestone is already {milestone['status']}", 409)

    amount, limit, balance = (Decimal(milestone["amount_usdc"]), Decimal(snapshot["agent_release_limit_usdc"]),
                              Decimal(snapshot["balance_usdc"]))
    checks = {
        "escrow_not_paused": not snapshot["paused"],
        "within_agent_limit": amount <= limit,
        "escrow_funded_for_milestone": balance >= amount,
        "evidence_pr_merged": facts.get("merged") is True,
    }
    if not checks["escrow_not_paused"]:
        return Assessment(Verdict.HOLD, "The escrow is paused by its approver; nothing is released until it resumes.", checks)
    if not checks["within_agent_limit"]:
        return Assessment(Verdict.ESCALATE, f"The milestone ({amount:f} USDC) is above the agent's release limit "
                                            f"({limit:f} USDC), so only the approver can release it.", checks)
    if not checks["escrow_funded_for_milestone"]:
        return Assessment(Verdict.HOLD, f"The escrow holds {balance:f} USDC, less than the milestone ({amount:f} USDC); "
                                        "the contract would refuse the release.", checks)
    if not checks["evidence_pr_merged"]:
        return Assessment(Verdict.HOLD, "The pull request offered as evidence is not merged.", checks)
    return Assessment(Verdict.RELEASE, "The escrow allows it, the amount is inside the agent's limit, the escrow is funded "
                                       "and the evidence pull request is merged.", checks)


def reasoning_text(escrow_address: str, index: int, snapshot: dict, assessment: Assessment, facts: dict, evidence_hash: str) -> str:
    """The text that is hashed. Written by code from facts the server read; no model words and no figure from anywhere else.
    It carries the evidence facts in full, so the evidence hash can be recomputed from the record itself."""
    m = snapshot["milestones"][index]
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return (
        f"Escrow {escrow_address.lower()}, milestone {index}: {m['amount_usdc']} USDC to the contractor {snapshot['contractor'].lower()}. "
        f"Requirements hash {m['requirements_hash']}. Verdict: {assessment.verdict.value}. {assessment.reason} "
        f"Evidence hash {evidence_hash} over the facts {canonical}. "
        "A merged pull request shows that code was merged, not that the requirements are met; it is an input to this decision, "
        f"not a release condition. The agent release limit is {snapshot['agent_release_limit_usdc']} USDC and the escrow "
        "contract enforces it, the order of commit and release, and the approver's override."
    )


def commitment_for(escrow_address: str, index: int, evidence_hash: str, reasoning_hash: str, chain_id: int = contract.CHAIN_ID) -> bytes:
    """keccak256(abi.encode(escrow, chainid, index, evidenceHash, reasoningHash)), exactly as MilestoneEscrow.commitmentFor."""
    ev = evidence_hash if evidence_hash.startswith("0x") else "0x" + evidence_hash
    rs = reasoning_hash if reasoning_hash.startswith("0x") else "0x" + reasoning_hash
    if not _HASH.match(ev.lower()) or not _HASH.match(rs.lower()):
        raise ValueError("a hash must be 32 bytes of hex")
    return Web3.keccak(encode(["address", "uint256", "uint256", "bytes32", "bytes32"],
                              [Web3.to_checksum_address(escrow_address), chain_id, int(index),
                               bytes.fromhex(ev[2:]), bytes.fromhex(rs[2:])]))


# ---- the two transactions (Circle-signed, from the business's own agent wallet)

def send_commit(wallet_id: str, escrow_address: str, commitment: bytes) -> str:
    return contract.execute_from_wallet(wallet_id, Web3.to_checksum_address(escrow_address), "commitDecision(bytes32)",
                                        [Web3.to_hex(commitment)])


def send_release(wallet_id: str, escrow_address: str, index: int, evidence_hash: str, reasoning_hash: str) -> str:
    return contract.execute_from_wallet(
        wallet_id, Web3.to_checksum_address(escrow_address), "releaseMilestone(uint256,bytes32,bytes32)",
        [str(int(index)), "0x" + evidence_hash.removeprefix("0x"), "0x" + reasoning_hash.removeprefix("0x")])


def _chain_hash(tx_id: str):
    try:
        return contract.chain_tx_hash(tx_id)
    except Exception:
        return None


async def record_decision(business: dict, escrow_address: str, index: int, snapshot: dict, assessment: Assessment,
                          facts: dict, evidence_hash: str) -> str:
    """Writes the decision (hash format 2, chained and signed) before anything touches the chain. Returns the reasoning hash.
    Without a usable ledger key this raises and nothing is logged."""
    action = {Verdict.RELEASE: ACTION_RELEASED, Verdict.HOLD: ACTION_HELD, Verdict.ESCALATE: ACTION_ESCALATED}[assessment.verdict]
    return await decision_log.log_decision(
        action, subject_for(escrow_address, index), reasoning_text(escrow_address, index, snapshot, assessment, facts, evidence_hash),
        model_used="rules", amount=float(Decimal(snapshot["milestones"][index]["amount_usdc"])), business=business)


async def commit_then_release(business: dict, escrow_address: str, index: int, evidence_hash: str, reasoning_hash: str) -> str:
    """Commit, wait for it to confirm, release, wait. Returns the Circle id of the release. If either transaction fails or
    reverts the decision row is flagged `contract_refused` and the error propagates (the caller answers 502)."""
    commitment = commitment_for(escrow_address, index, evidence_hash, reasoning_hash)
    wallet = business.get("circle_wallet_id")
    try:
        commit_id = send_commit(wallet, escrow_address, commitment)
        contract.wait_for_transaction(commit_id)
        release_id = send_release(wallet, escrow_address, index, evidence_hash, reasoning_hash)
        contract.wait_for_transaction(release_id)
    except RuntimeError:
        await decision_log.mark_contract_refused(reasoning_hash)
        raise
    await decision_log.record_onchain_tx(reasoning_hash, release_id, _chain_hash(release_id))
    return release_id
