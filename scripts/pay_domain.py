"""One-off: reimburse the ceedebooks.xyz domain purchase through BudgetEnforcer.

Run manually (not an autonomous agent decision) from the repo root:
    PYTHONPATH=. python scripts/pay_domain.py
It pauses for confirmation before each on-chain step.
"""
import asyncio
import time

from web3 import Web3

from agent import contract, decision_log

VENDOR = "0xd7a8e903815F144FB5d120C893184DA036c8CaBe"
AMOUNT_USDC = 2.20
AMOUNT_UNITS = 2_200_000
INVOICE_NUMBER = "NAMECHEAP-215679394"
DOC_SHA256 = "7371e3b8e66ca9f510497dde40b3a6fae8d5b7e02878c5255c3ce11e481213ad"
CATEGORY = 1
REASONING = (
    "Manual reimbursement, not an autonomous agent decision: ceedebooks.xyz domain registration "
    "(Namecheap order 215679394, 1 year, $2.00 + $0.20 ICANN fee) paid by the founder by card on "
    "2026-10-02; reimbursed to the ops wallet. docHash is keccak256 of the receipt's SHA-256."
)


def confirm(message: str) -> None:
    if input(f"\n{message}\nType 'yes' to continue: ").strip().lower() != "yes":
        raise SystemExit("Aborted. Nothing further was sent.")


async def main() -> None:
    await decision_log.init_db()
    vendor = Web3.to_checksum_address(VENDOR)
    invoice_number = contract.to_bytes32(INVOICE_NUMBER)
    doc_hash = contract.to_bytes32(DOC_SHA256)

    assert contract.is_vendor_approved(vendor), "Vendor not approved on-chain"
    assert not contract.is_paid(contract.invoice_key(vendor, invoice_number)), "Invoice already paid"
    overall, in_category = contract.remaining_today(CATEGORY)
    assert overall >= AMOUNT_UNITS and in_category >= AMOUNT_UNITS, f"Limits too low: {overall}, {in_category}"

    print(f"Vendor:         {vendor}")
    print(f"Amount:         {AMOUNT_USDC} USDC ({AMOUNT_UNITS} units)")
    print(f"Invoice number: {INVOICE_NUMBER}")
    print(f"Category:       {CATEGORY}")
    print(f"Remaining:      overall={overall}, category={in_category}")

    confirm("STEP 7: write the audit row (hash is computed and stored before any on-chain call).")
    reasoning_hash_hex = await decision_log.log_decision(
        "INVOICE_PAID", INVOICE_NUMBER, REASONING, model_used="manual", amount=AMOUNT_USDC
    )
    reasoning_hash = bytes.fromhex(reasoning_hash_hex)
    print(f"reasoning_hash: {reasoning_hash_hex}")

    try:
        commitment = contract.commitment_for(vendor, AMOUNT_UNITS, invoice_number, doc_hash, CATEGORY, reasoning_hash)
        confirm("STEP 8: send commitDecision from the agent wallet.")
        commit_tx = contract.commit_decision(commitment)
        contract.wait_for_transaction(commit_tx)
        print(f"Commit confirmed (Circle tx id {commit_tx}).")

        start = contract._w3.eth.block_number
        while contract._w3.eth.block_number <= start:
            time.sleep(1)

        confirm("STEP 9: send pay from the agent wallet (moves 2.20 USDC).")
        pay_tx = contract.pay(vendor, AMOUNT_UNITS, invoice_number, doc_hash, CATEGORY, reasoning_hash)
        contract.wait_for_transaction(pay_tx)
    except BaseException:
        print(f"\nFAILED after the audit row was written. reasoning_hash={reasoning_hash_hex}")
        print("The audit row says PAID but no payment may have settled. Check the contract before retrying.")
        raise

    await decision_log.record_onchain_tx(reasoning_hash_hex, pay_tx)
    txn = contract._transactions_api.get_transaction(id=pay_tx).data.transaction
    print("\nDone.")
    print(f"Circle tx id:   {pay_tx}")
    print(f"On-chain hash:  {getattr(txn, 'tx_hash', None)}")
    print(f"On-chain hash:  {chain_hash}")


if __name__ == "__main__":
    asyncio.run(main())
