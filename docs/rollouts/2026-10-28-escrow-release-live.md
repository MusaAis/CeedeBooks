# The agent's escrow release path, first live run (Oct 28, 2026)

**Feature:** `agent/escrow_release.py`, the escrow routes and the admin site's Escrows tab (v1.3.2.3, Phase H piece 3). **Where:** the production server (`api.ceedebooks.xyz`) and `admin.ceedebooks.xyz`, driven from the Escrows tab by the escrow's approver wallet `0xaE16D350db806A7785113C7C3690C337B753eAaa`. **Whose money:** the builder's own, testnet USDC. The agent wallet `0x32B59A6988DCA955c641D529Ee80484200DE37b0` is the escrow's agent; the contractor is `0x1e89Fc19Bfa9015C72cAd70D27CC77E684d59b38`. This is not outside-business traction and the escrow decisions are not counted in any traction figure.

## What was run

All times UTC, Oct 28. Each on-chain step was confirmed one at a time before it was taken.

1. 09:53: the reference escrow [`0x9752F995...2971dd5`](https://explorer.testnet.arc.io/address/0x9752F995739C8A171AbD24944925Eaf4d2971dd5) (deployed Oct 24, never funded until now) was registered from the Escrows tab (an off-chain action; the Activity tab shows `register_escrow`).
2. Funding, two USDC transfers from the approver's own wallet through the tab: 0.5 USDC in [`0xe391d45d...5d02250b`](https://explorer.testnet.arc.io/tx/0xe391d45de1e18203fce33fef71bd37bfba400fd7966b9f20304d158c5d02250b) and, after the first screen read 0.5, a further 5 USDC in [`0x8bf14a34...592e9b31`](https://explorer.testnet.arc.io/tx/0x8bf14a3454d6611231f68786b24d4f9f053d1fb9dd6b779dd4935c33592e9b31). The tab then read balance 5.5, reserved 0.8, agent limit 0.25, Active. (The 5 USDC was more than the run needed.)
3. 10:04: a hold. Milestone 0 with pull request 934 of `foundry-rs/forge-std`, which is closed and was never merged. The dry run said hold ("the pull request offered as evidence is not merged") and wrote nothing; Release then recorded `ESCROW_HELD`, decision hash `ccbaf7ac53bff4f5282abff3cb78d1e9f36b7e18364730fcf8405fe67e3e11b4`, and sent nothing.
4. 10:12: an escalation. Milestone 2 (0.5 USDC, above the 0.25 limit) with merged pull request 937. The verdict was escalate ("above the agent's release limit, so only the approver can release it"); `ESCROW_ESCALATED`, decision hash `dc3a8a57f478fdb781317dc0419acc09731a4002d69c120898751b5546ffc83c`, nothing sent.
5. 10:19: a release. Milestone 0 (0.1 USDC) with merged pull request 937 of `foundry-rs/forge-std` (merged Oct 26 by `dependabot[bot]`; the same pull request as in [the Oct 27 record](2026-10-27-escrow-reads-and-evidence.md)). The dry run said release. The decision was recorded first as `ESCROW_RELEASED`, decision hash `e720c5eb420bc48df8e6bc6cc18e1ae78fc3f920d5c477f4612064038723a895`, evidence hash `0xe614b119fd0bde382e3c12ac5246ec46dd301c3c3850a61442731c46f3c5978e`. Then the agent wallet sent two transactions:
   - the commit (`commitDecision(bytes32)`, method id `0xcff92ef9`): [`0x73ac7434...a22421f0`](https://explorer.testnet.arc.io/tx/0x73ac7434b489d13da2678cba5552b2f5339bf6c7c7f980eda905e1eea22421f0), 10:19:06, success;
   - the release (`releaseMilestone(uint256,bytes32,bytes32)`, method id `0x68dce7d8`): [`0xc49c13dd...b83f1f`](https://explorer.testnet.arc.io/tx/0xc49c13ddf6e89980df395a88db87bdb4445d7d1997385988f18b6ab14fb83f1f), block 69485148, 10:19:08, success, fee 0.003358785370405611 USDC. The explorer shows 0.1 USDC moving from the escrow to the contractor.

## Result

- After the release the tab read balance 5.4, reserved 0.7, milestone 0 `released`, milestones 1 and 2 `pending`.
- `GET /decisions/e720c5eb.../verify` returned `verified: true` and `onchain: {checked: true, match: true, chain_tx_hash: 0xc49c13dd...b83f1f, event: MilestoneReleased, block: 69485148}`. In the database, `audit_log` rows 26, 27 and 28 are the hold, the escalation and the release (hold and escalation carry no transaction hash; row 28 does and `contract_refused` is empty), and `escrow_releases` has one row for milestone 0, `released`, with the evidence hash above and Circle transaction id `4c39fe61-614a-522d-9027-e1a03281a94f`.

## What this does and does not show

- The merged pull request is a stand-in input. The milestone's requirements hash is a reference value and has no connection to the forge-std pull request, so this run shows the agent's decision, record and two-transaction release working, not that any work was delivered. A merged pull request is not proof that a requirement is met.
- Every wallet is the builder's: the approver, the agent and (as far as is known) the contractor address. No outside business or contractor took part.

## What was not run, and what was not captured

- Not run live: a reject, an approver override, a withdraw, a pause, a release the contract refuses, a double click, a failed or timed-out confirmation, a retry after failure, and the release path with a second business. Those are covered by the tests and by a local copy of the contract only.
- Milestones 1 (0.2 USDC) and 2 (0.5 USDC) are still pending; the escrow holds 5.4 USDC.
- The hold's and the escalation's evidence hashes and the commit transaction's fee were not captured. The funding was a plain USDC transfer; the second hash was taken from the wallet, not read back by us.

## What changed afterwards

Nothing in the code.

## Check it yourself

```bash
curl -s https://api.ceedebooks.xyz/decisions/e720c5eb420bc48df8e6bc6cc18e1ae78fc3f920d5c477f4612064038723a895/verify
curl -s "https://explorer.testnet.arc.io/api/v2/addresses/0x9752F995739C8A171AbD24944925Eaf4d2971dd5/transactions"
```

The evidence hash is the SHA-256 of the canonical JSON of the facts for `GET https://api.github.com/repos/foundry-rs/forge-std/pulls/937`.
