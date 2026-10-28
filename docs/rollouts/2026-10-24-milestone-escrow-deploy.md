# MilestoneEscrow, reference deployment (Oct 24, 2026)

**Feature:** `contracts/MilestoneEscrow.sol` (v1.3.2, Phase H piece 1). **Where:** Arc Testnet, deployed from the server with `forge create` by a fresh key that holds no role in the contract. **This is a reference deployment: no engagement has run, no funds are in it and nothing in the agent, the API or the sites calls it yet.**

## What was run

1. `forge test` on the server: 111 passed, 0 failed (the 42 new tests are in `test/MilestoneEscrow.t.sol`; the other 69 are the earlier contracts').
2. `forge create` with the constructor arguments below. Contract: [`0x9752F995739C8A171AbD24944925EaF4d2971dd5`](https://explorer.testnet.arc.io/address/0x9752F995739C8A171AbD24944925EaF4d2971dd5), deployment transaction [`0x906d8cc3d1d4d474dfc418b626b197341b1dc026427a8c2788588b2a6257a6b5`](https://explorer.testnet.arc.io/tx/0x906d8cc3d1d4d474dfc418b626b197341b1dc026427a8c2788588b2a6257a6b5).

| Constructor argument | Value |
|---|---|
| agent | `0x32b59a6988dca955c641d529ee80484200de37b0` (the original business's agent wallet) |
| approver | `0xaE16D350db806A7785113C7C3690C337B753eAaa` (the original business's approver) |
| contractor | `0x1e89Fc19Bfa9015C72cAd70D27CC77E684d59b38` |
| USDC | `0x3600000000000000000000000000000000000000` |
| agent release limit | 250000 (0.25 USDC) |
| milestones | 100000, 200000, 500000 (0.10, 0.20 and 0.50 USDC); only the third is above the agent's limit |
| requirements hashes | keccak256 of the texts "reference deployment: milestone 1", "... 2" and "... 3": `0xaf66697c...86b1bab50`, `0x84c664ab...772df83c`, `0x562e9b23...7f616ee2` |

## Result

Read back from the chain with `cast call` right after the deployment: `agent()`, `approver()`, `contractor()` and `usdc()` returned the addresses above; `agentReleaseLimit()` 250000; `milestoneCount()` 3; `pendingTotal()` 800000; `paused()` false; `withdrawable()` 0; the three milestones carry the amounts and hashes above, each with status 0 (Pending).

Bytecode check: `forge inspect ... deployedBytecode` from the tagged source and `cast code` on the chain have the same length (9060 hex characters) and an identical metadata tail (the part that encodes the source hash and compiler settings). 110 bytes differ, which fits the three immutable values (contractor, USDC, agent limit) that the constructor writes into the code.

## What failed, and what was not captured

- **Explorer source verification failed and the contract is marked not verified.** The `--verify` step at deployment ended with `Fail - Unable to verify`; a second `forge verify-contract` run ended the same way; a third was answered `Too many requests` by the explorer's API; a manual submission through the explorer's own page also failed. The cause was not found. The bytecode comparison above is the check that does not depend on the explorer.
- At the time of this record the contract was never funded and no commit, release, reject, override, withdraw or pause had been sent. It was funded and one milestone was released on Oct 28 ([record](2026-10-28-escrow-release-live.md)); a reject, override, withdraw and pause still have no live run and are covered by the Foundry tests only.
- The block number of the deployment was not captured.

## What changed afterwards

Nothing in the contract: a deployed contract is frozen, and a change would be a new deployment with this address kept and marked superseded.

## Check it yourself

```bash
cast call 0x9752F995739C8A171AbD24944925EaF4d2971dd5 "milestones(uint256)(uint256,bytes32,uint8)" 2 --rpc-url $ARC_TESTNET_RPC_URL
cast call 0x9752F995739C8A171AbD24944925EaF4d2971dd5 "agentReleaseLimit()(uint256)" --rpc-url $ARC_TESTNET_RPC_URL
```

The first prints 500000, the third requirements hash and 0; the second prints 250000.
