# Rollout records

One short record for each thing that was run live on Arc Testnet: what was run, the transaction and the decision hash, what failed or was not captured, and what changed afterwards. Failures stay in. Every link points to the explorer or to a public API route, so each can be checked without trusting these pages.

Testnet USDC. The business in every record is the builder's own, so none of this is outside-business traction; no outside business exists yet.

| Date (UTC) | Record | Version | Evidence |
|---|---|---|---|
| Oct 2 | [Domain payment](2026-10-02-domain-payment.md) | v1.2.1 | pay transaction `0x8c176242...ce5d0d`, 2.20 USDC, `manual` |
| Oct 4 | [Multi-business contracts](2026-10-04-multi-business-contracts.md) | v1.2.8 | `BudgetFactory` and `LedgerAnchor` addresses; two deploy bugs kept in |
| Oct 9 to 16 | [Shadow-mode payments](2026-10-shadow-mode-payments.md) | v1.2.8.3 | five approved decisions, five transactions, 8.47 USDC mirrored |
| Oct 12 | [First ledger anchor](2026-10-12-first-ledger-anchor.md) | v1.2.9.2 | sequence 9, transaction `0x24045426...a36639fb`; later anchors listed |
| Oct 15 | [Red team](2026-10-15-red-team.md) | v1.2.9.4 | the contract refused an unapproved payee; no pay hash exists, and why |
| Oct 15 | [Gateway deposit](2026-10-15-gateway-deposit.md) | v1.2.10.1 | 3 USDC, transaction `0xa022b539...caba3f` |
| Oct 21 | [Outcomes, first live reading](2026-10-outcomes-live.md) | v1.3.1 | `GET /outcomes` and its SQL |
| Oct 22 | [Demo path, first live run](2026-10-22-demo-run.md) | v1.3.1.1 | one paid (`0xc791305f...cbfebf`), two escalated; a counting bug it exposed |
| Oct 24 | [MilestoneEscrow, reference deployment](2026-10-24-milestone-escrow-deploy.md) | v1.3.2 | contract `0x9752F995...2971dd5`, read back and bytecode-compared; explorer source not verified; funded and used on Oct 28 (next row) |
| Oct 27 | [Escrow reads and the GitHub evidence check](2026-10-27-escrow-reads-and-evidence.md) | v1.3.2.1 | the reference escrow read through the API's code; merged pull request 937 of forge-std turned into facts and hash `0xe614b119...c5978e`; two missing pull requests failed |
| Oct 28 | [The agent's escrow release path, first live run](2026-10-28-escrow-release-live.md) | v1.3.2.3 | commit `0x73ac7434...a22421f0`, release `0xc49c13dd...b83f1f` (0.1 USDC, block 69485148); a hold and an escalation recorded, nothing sent; all our own wallets |

## Shipped but with no live run recorded here

These exist in the code and have not been run live. Treat none of them as evidence of live use: the pause with a stated reason (it writes permanent public ledger entries), the maker-checker release rule, Return to agent on a v2 contract, the milestone escrow's reject, override, withdraw and pause actions (the release of milestone 0 was run live on Oct 28, see the record above; these were not), the agent's release path when the contract refuses, when a confirmation fails or times out, or when it is retried, and a Gateway gasless spend (not built).

## Record format

What was run, where and by whom; the evidence (transaction, hash, route); the result; what was not captured; what changed afterwards; how to check it yourself.
