# Contracts

What each contract does. The deployed addresses are in the [README](../README.md#contracts-arc-testnet).

## `BudgetEnforcer.sol`

The on-chain spending authority. Funds are **held by the contract**, not the agent wallet. The agent can only move them through `pay()`, inside these limits:

- **Vendor registry**: only approver-registered addresses can be paid. A wallet change means registering a new address; the old one stops working once revoked.
- **Commit-then-pay**: `commitDecision(hash)` must land in an earlier block than the `pay()` it justifies, bound to the exact payment parameters (vendor, amount, invoice, doc, category, reasoning).
- **Canonical idempotency key**: `invoiceKey = keccak256(vendor, invoiceNumber)`, so resubmitting a different file doesn't create a new key. `docHash` is a separate pointer emitted in the event.
- **Limits**: per-tx, per-day, and cumulative per-category-per-day. An unregistered category has a 0 limit (fail-closed).
- **Escalation**: the agent can park a payment; only the approver can pay it, exactly once.
- **Admin**: pause, approver-only withdraw, agent rotation, two-step approver rotation.

`reasoningHash` (this decision was logged before money moved) and the invoice key (this invoice wasn't paid twice) are deliberately separate guarantees in separate fields.

One instance per business. This is the original business's contract (v1): it is not multi-tenant and no other business is onboarded on it. `BudgetFactory` creates one enforcer per business from the owner's own wallet, each with its own pool, approver, agent and limits. This contract stays the original business's live pool; moving it onto a factory contract is optional (roadmap L5), and if that ever happens this address stays listed here, marked superseded.

## `BudgetEnforcerV2.sol`, `BudgetFactory.sol`, `LedgerAnchor.sol` (v1.2.8)

- **`BudgetEnforcerV2`** keeps everything above and adds a rolling 7-day limit (`setBudget(daily, perTx, weekly)` requires `0 < perTx <= daily <= weekly`), `reopenEscalation` (approver only, only for a Pending escalation, so the agent can re-evaluate and then pay through every normal limit or escalate again), commitments bound to the contract address and chain id, and distinct agent and approver roles.
- **`BudgetFactory`**: `createBusiness(agent, daily, perTx, weekly)` deploys one enforcer per business, and the caller becomes its approver. The factory has no admin function, holds no funds and cannot touch any enforcer.
- **`LedgerAnchor`**: `anchor(head, sequence)` records a ledger head per sender, and the sequence must increase. A verifier trusts only events from a business's agent address.

## `MilestoneEscrow.sol` (v1.3.2)

Holds USDC for one contractor job and releases it per milestone. One instance per job, deployed with `forge create` (the server never deploys). The agent proposes, the contract enforces, the approver can always override.

- **Fixed at deployment:** the agent, the approver and the contractor (three different non-zero addresses), the USDC address, an `agentReleaseLimit`, and 1 to 50 milestones, each with an amount and a non-zero `requirementsHash`. There is no function that changes a milestone. Funding is a plain USDC transfer to the contract.
- **Agent release:** `commitDecision(commitment)`, then in a later block `releaseMilestone(index, evidenceHash, reasoningHash)`. The commitment is `keccak256(abi.encode(contract, chainId, index, evidenceHash, reasoningHash))` (`commitmentFor`), single use, so it cannot be replayed on another escrow, chain or milestone. Only a Pending milestone at or below `agentReleaseLimit` can be released this way; the amount goes to the contractor.
- **Approver:** `overrideRelease` (a Pending or Rejected milestone, the only way to release one above the limit), `rejectMilestone` (the agent can reject too), `setPaused`, `setAgent`, a two-step approver change, and `withdraw`, which can take only the balance not reserved for Pending milestones (`withdrawable()`). Pause blocks the agent's release and reject; the approver's override, reject and withdraw still work.
- **What it does not do:** it cannot check that work was done. The `evidenceHash` is a record emitted in `MilestoneReleased`, not proof, and the approver threshold is what keeps the agent from releasing a large milestone alone. A milestone that is not funded cannot be released: the transfer reverts and the milestone stays Pending.

The one deployment (its address is in the [README](../README.md#contracts-arc-testnet)) is a reference: no engagement has run and it holds no funds. Its source could not be verified on the explorer (see [Known issues, #12](known-issues.md)); the deployed bytecode matches a local build of this source.

## Businesses and the original approver

| Business | Contract | Agent wallet (Circle, hosted by us) | What it is |
|---|---|---|---|
| `ceedebooks` | v1, `0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB` | [`0x32B59A6988DCA955c641D529Ee80484200DE37b0`](https://explorer.testnet.arc.io/address/0x32B59A6988DCA955c641D529Ee80484200DE37b0) | The original business, ours |
| `ceedebooks-test-v1282` | v2, `0x030512Cd974F9A282415B0e6C81465f31502e32C` | [`0xFaC937E443D213fd6E5F56F6420Feab3390a9f2F`](https://explorer.testnet.arc.io/address/0xFaC937E443D213fd6E5F56F6420Feab3390a9f2F) | A test business we made to check onboarding. Not an outside business. An archived business leaves the public lists but keeps its row here |

**Approver of the original contract.** Since the Oct 4 rotation it is `0xaE16D350db806A7785113C7C3690C337B753eAaa` ([`proposeApprover` transaction](https://explorer.testnet.arc.io/tx/0x8cf974608db5b18c37821109e88291ad70cd9f1aa0e4919632ee0d7d73587323), then accepted from that wallet in the admin site). The former deployer key `0x56cd960F4EbC926e973d90833Faea799fA924c39` is an ordinary wallet with no power over it. Check: `cast call 0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB "approver()(address)" --rpc-url $ARC_TESTNET_RPC_URL`, or `GET /businesses`, which reads it from the contract. Limits are set by the approver and can be read from the contract (`dailyLimit()`, `perTxLimit()`, `categoryDailyLimit(uint8)`) or on the admin Overview.
