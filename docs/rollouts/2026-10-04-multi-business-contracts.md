# Multi-business contracts deployed (v1.2.8, Oct 4 to 5, 2026)

**Feature:** `BudgetEnforcerV2`, `BudgetFactory`, `LedgerAnchor` (Phase L1). 69 Foundry tests (24 on the original contract, 45 on these three), built with solc 0.8.20 and `via_ir`.

## What was deployed

- `BudgetFactory` [`0x97b9A3802bA6B258cBeF6070532a265656bb391C`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C)
- `LedgerAnchor` [`0x008217BeC86462E76126F94e50eBe68fb4a41444`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444)
- `BudgetEnforcerV2` has no standalone deployment: the factory creates one per business.

Checked on-chain after deployment: the factory's USDC address is the Arc USDC, `businessCount` was 0, the anchor's sequence started at 0. Both were verified on the explorer.

## What failed on the way

- `forge create` swallows every token after `--constructor-args`, so a later flag caused `Constructor argument count mismatch`; the arguments must come last.
- The explorer's default verification endpoint returned `Params 'module' and 'action' are required parameters` (an open upstream bug, `circlefin/arc-node#210`); verification used the Blockscout verifier with `https://explorer.testnet.arc.io/api/`.
- Both are in the [known issues](../known-issues.md) table.

## What was not captured

The deployment transaction hashes are not recorded in this repository; the contract addresses above are, and the explorer shows each address's creation transaction.

## What changed afterwards

The running API and the original contract were unchanged by this release, and no business had been created through the factory yet. The only business made through it since is a test business of ours, now archived.

## Check it yourself

Open each address on the explorer; read `businessCount` on the factory and the anchor's sequence for the sender.
