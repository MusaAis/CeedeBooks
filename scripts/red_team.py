"""E4 red team: make the agent wallet try a payment the contract must refuse, and keep the refusal as a signed, anchored record.

  PYTHONPATH=. python3 scripts/red_team.py --business ceedebooks

The attempt is a pay() to a fresh address that is NOT an approved vendor, for the smallest amount. It pauses for "yes" before
each on-chain step. The expected outcome is a revert ("Vendor not approved"); no money can move. If the contract does NOT
refuse, the script stops and says so loudly: that would be a real finding. On a refusal it marks the audit row
contract_refused, and (after one more "yes") anchors the ledger head through LedgerAnchor so the record is on-chain too.
"""
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eth_account import Account  # noqa: E402

from agent import anchor, contract, decision_log  # noqa: E402
from backend import models  # noqa: E402

ACTION = "RED_TEAM_REFUSED"
AMOUNT_UNITS = 1  # the smallest USDC unit
CATEGORY = 1


class NotRefused(Exception):
    """The contract accepted a payment it should have refused."""


def _confirm(message: str) -> None:
    if input(f"\n{message}\nType 'yes' to continue: ").strip().lower() != "yes":
        raise SystemExit("Aborted. Nothing further was sent.")


async def run(business: dict, confirm=_confirm, wait_blocks=True) -> dict:
    chain = contract.chain_for(business)
    stranger = Account.create().address  # fresh, so it cannot be an approved vendor
    assert not chain.is_vendor_approved(stranger), "Unexpected: a fresh address is already approved"
    invoice_number = contract.to_bytes32("REDTEAM-" + str(int(time.time())))
    doc_hash = contract.to_bytes32("0" * 64)
    reasoning = (f"Red-team test, not a business payment: the agent wallet attempts to pay {stranger}, which is not an approved vendor, "
                 f"for {AMOUNT_UNITS} unit. The contract is expected to refuse it.")
    print(f"Contract:  {chain.address}\nTarget:    {stranger} (not an approved vendor)\nAmount:    {AMOUNT_UNITS} unit\nExpected:  revert")

    confirm("STEP 1: write the audit row (hash computed and stored before any on-chain call).")
    reasoning_hash_hex = await decision_log.log_decision(ACTION, "REDTEAM", reasoning, model_used="manual", amount=0, business=business)
    reasoning_hash = bytes.fromhex(reasoning_hash_hex)
    commitment = chain.commitment_for(stranger, AMOUNT_UNITS, invoice_number, doc_hash, CATEGORY, reasoning_hash)

    confirm("STEP 2: send commitDecision from the agent wallet (so the revert is about the vendor, not a missing commitment).")
    contract.wait_for_transaction(chain.commit_decision(commitment))
    if wait_blocks:
        start = contract._w3.eth.block_number
        while contract._w3.eth.block_number <= start:
            time.sleep(1)

    confirm("STEP 3: send pay from the agent wallet. It must revert; no USDC can move.")
    try:
        contract.wait_for_transaction(chain.pay(stranger, AMOUNT_UNITS, invoice_number, doc_hash, CATEGORY, reasoning_hash))
    except RuntimeError as e:
        await decision_log.mark_contract_refused(reasoning_hash_hex)
        print(f"\nRefused, as required: {e}")
        return {"refused": True, "reasoning_hash": reasoning_hash_hex}
    raise NotRefused(f"THE CONTRACT ACCEPTED THE PAYMENT (reasoning_hash {reasoning_hash_hex}). Pause the contract and investigate.")


async def main(slug: str) -> None:
    await models.init_db()
    await decision_log.init_db()
    await anchor.ensure_schema()
    business = await models.get_business_by_slug(slug)
    if business is None:
        sys.exit(f"No business '{slug}'")
    result = await run(business)
    _confirm("STEP 4: anchor the ledger head on-chain through LedgerAnchor, so this refusal is part of the anchored record.")
    print(await anchor.anchor_now(business, manual=True))
    print(f"\nDone. Look up reasoning_hash {result['reasoning_hash']} on the public audit page.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--business", required=True, help="business slug")
    asyncio.run(main(ap.parse_args().business))
