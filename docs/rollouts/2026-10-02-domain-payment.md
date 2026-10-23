# The domain payment: first self-owned bill through the contract (Oct 2, 2026)

**Feature:** `scripts/pay_domain.py` (v1.2.1). **Where:** the original business, v1 `BudgetEnforcer` `0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB`, paid from the Circle agent wallet. **Label:** `manual`, not an autonomous agent decision.

## What was run

The `ceedebooks.xyz` domain was bought by card (Namecheap order 215679394, $2.00 + $0.20 ICANN fee). The registrar cannot take USDC, so the founder was reimbursed through the contract: vendor registered with `setVendor`, category 1 given a 20 USDC per day limit, the receipt hashed into `docHash`, the reasoning hash logged off-chain first, then `commitDecision` and `pay` in separate blocks.

- Payment transaction: [`0x8c176242a84235a884d5790edf4a9aed403d87619359373e11f2e27141ce5d0d`](https://explorer.testnet.arc.io/tx/0x8c176242a84235a884d5790edf4a9aed403d87619359373e11f2e27141ce5d0d), 2.20 USDC
- Commit transaction, sent first: [`0xd6ea5d93...45b813c`](https://explorer.testnet.arc.io/tx/0xd6ea5d9363d4a815aff5fdd492df5acc22622cdd128d36eb3a99c097845b813c), block 65102581 (10:36:36 UTC); the payment followed in block 65102616. The commit hash was not stored in the database. It was found on Oct 23 by reading the contract's `DecisionCommitted` events in the 40 blocks before the payment: there is one event in that window and it falls 10 seconds after the audit row. The match rests on that timing and uniqueness; the commitment value was not recomputed.
- `reasoning_hash`: `46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a`, written to the audit log on 2026-10-02 10:36 UTC as `INVOICE_PAID`, subject `NAMECHEAP-215679394`, `model_used` `manual`

## Result

`GET /decisions/46f65d77.../verify` (read on Oct 23) returned `verified` true and an on-chain check that matched: event `PaymentMade`, block 65102616, the transaction above. The stored reasoning says in plain words that this was a manual reimbursement and not an autonomous decision.

## What this is not

It is not an agent decision and not traction. The cost was real (paid by card), the settlement is testnet USDC.

## What failed on the way

The tests of the time used to poll Circle and broke on newer SDK versions; they now mock `wait_for_transaction`. Before this run, the engine's own handling of a reverted transaction was fixed: every commit, payment and escalation is polled to a terminal on-chain state before it is trusted, which closed a gap where a reverted transaction could have been logged as paid.

## Check it yourself

Open the transaction above, or paste the `reasoning_hash` into the verifier on https://ceedebooks.xyz.
