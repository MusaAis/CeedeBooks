# CeedeBooks

![Release](https://img.shields.io/github/v/release/MusaAis/CeedeBooks?label=release)
![CI](https://github.com/MusaAis/CeedeBooks/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue)
![Foundry tests](https://img.shields.io/badge/forge%20tests-111%2F111%20passing-brightgreen)
![Python tests](https://img.shields.io/badge/pytest-699%2F699%20passing-brightgreen)
![Network](https://img.shields.io/badge/Arc-Testnet-informational)

*"Ceede" means money in Pulaar/Fulfulde.*

An autonomous accounts-payable agent for African SMEs on Arc: it validates and pays invoices, with spending limits enforced **on-chain** (never in a prompt) and every decision hash-logged before money moves.

Built for the **Tameion Agents Hackathon** (Canteen × Circle × Arc), Sep 27 – Oct 17, 2026.

**Status: v1.3.2.5**, Phase H piece 5, which completes Phase H: an **Escrows** section on the public proof page, read-only. For each registered escrow it shows the state read from the chain (balance, reserved, the agent's limit, paused or active, each milestone and its status, the agent, approver and contractor addresses with explorer links), whether the business is the builder's own or an outside one, and the agent's recorded decisions on it, each with a Verify button; it sends nothing and asks for no wallet. A new public route, `GET /escrows/{address}/decisions`, lists those decisions. Checked live on Oct 30: the route returned the three recorded decisions and the page showed the escrow's state. Pressing Verify on the release failed, because the page's in-browser check only knew the business contract's events and not the escrow's; v1.3.2.5 fixes that, and after the fix the release verified on the real page (all four steps passed, block 69485148) while the held and escalated decisions correctly read "recorded off-chain only" ([record](docs/rollouts/2026-10-30-proof-page-escrows.md)). Before it, v1.3.2.4 (Phase H piece 4): the approver's own actions on a milestone escrow in the admin site (pause and resume, release a milestone yourself, reject, withdraw), sent from the approver's wallet and never by the agent. Run live once on Oct 29 from the admin site: a pause, a 0.1 USDC withdraw, a rejection, a release of the 0.5 USDC milestone that is above the agent's limit, and a resume ([record](docs/rollouts/2026-10-29-escrow-approver-actions-live.md)). Before it, v1.3.2.3 (Phase H piece 3): the agent's release path for a milestone escrow. On an admin's request the agent decides with code only, records the decision (hashed, chained and signed) and, if the verdict is release, commits and then releases from its own wallet; anything above the escrow's agent limit is left to the approver. Run live once on Oct 28: one hold, one escalation and one release of 0.1 USDC from the agent wallet ([record](docs/rollouts/2026-10-28-escrow-release-live.md)). Earlier: v1.3.2.2 (docs split), v1.3.2.1 (read-only escrow routes, GitHub evidence dry run), v1.3.2 (the contract). The admin site has an Escrows tab for both. Phase H (milestone escrow) is complete with this release; what comes next is on the [roadmap](docs/roadmap.md).

> **Read this first.** Everything runs on Arc Testnet with test USDC. Two businesses exist and both are ours: no outside business or paying user yet, and every payment so far is self-owned. The `MilestoneEscrow` contract is a reference deployment: no outside engagement, and it holds only our own testnet funds; the agent's release path has been run live once, on milestone 0 with a stand-in merged pull request, and the approver's own pause, withdraw, reject, release and resume were each run live once on Oct 29 (all our own wallets; the milestones' work is a stand-in, not delivered work); its source is not verified on the explorer. Idle-cash yield is planned, not built. Limits and guarantees: [trust model](docs/trust-model.md).

## Evidence you can check

Every row links to the explorer or to a public API route, so none of it has to be taken on trust. Testnet USDC, on the builder's own business: no outside business or paying user exists yet. The records, with what failed and what was not captured, are in [`docs/rollouts/`](docs/rollouts/README.md).

| What | Evidence |
|---|---|
| A payment | 2.20 USDC for the `ceedebooks.xyz` domain, [`0x8c176242...ce5d0d`](https://explorer.testnet.arc.io/tx/0x8c176242a84235a884d5790edf4a9aed403d87619359373e11f2e27141ce5d0d), a `PaymentMade` event in block 65102616. `GET /decisions/46f65d77.../verify` returns `verified: true` and an on-chain match. It was run by hand and is labelled `manual`: [record](docs/rollouts/2026-10-02-domain-payment.md) |
| A commit-then-pay pair | The same domain payment, in the order the contract requires: the decision was committed in [`0xd6ea5d93...45b813c`](https://explorer.testnet.arc.io/tx/0xd6ea5d9363d4a815aff5fdd492df5acc22622cdd128d36eb3a99c097845b813c) (block 65102581, 10:36:36 UTC), then paid in block 65102616 by the transaction above. Found by reading the contract's `DecisionCommitted` events in the 40 blocks before the payment: there is exactly one, and it falls 10 seconds after the audit row. The demo run has a pair too: commit [`0xd1ec6c62...e0ed97e`](https://explorer.testnet.arc.io/tx/0xd1ec6c6229277f7625bf75854d52d8e93b6ec1133327c2ffa5e5f409ae0ed97e) (block 68512236), then the 0.01 USDC payment in block 68512245 |
| A contract refusal | The agent wallet tried to pay an address the owner never approved and the contract refused it (`Vendor not approved`, caught at Circle's gas estimation, so no pay transaction exists to link). The refusal row is in the signed ledger and the head that includes it is anchored on-chain: [`0x2f75bf9f...0509af`](https://explorer.testnet.arc.io/tx/0x2f75bf9f9aa5702e68c5c031f58f79d56e1c0b68e22567c44d46c3ca4b0509af): [record](docs/rollouts/2026-10-15-red-team.md) |
| The ledger, anchored | Newest anchor at the last read (Oct 23): sequence 22, [`0x2e41acbc...7d00dee6`](https://explorer.testnet.arc.io/tx/0x2e41acbc14a8d9e3d8de6c2d74d4446dd2f77dd6b2fd33955c6f74037d00dee6); `GET /ledger/verify?business=ceedebooks` returned `ok`, 0 problems, anchor `match`: [record](docs/rollouts/2026-10-12-first-ledger-anchor.md) |
| Shadow-mode payments | Five approved decisions mirrored as testnet payments, 8.47 USDC, with the owner's real amounts and verdicts: [transactions and hashes](docs/rollouts/2026-10-shadow-mode-payments.md) |
| The demo path, run live | One paid (0.01 USDC, [`0xc791305f...cbfebf`](https://explorer.testnet.arc.io/tx/0xc791305f9c3f698408c0785eef8fb0673662f67ee1b9a3191eeedf10b9cbfebf)) and two escalated, all labelled `demo` and never traction: [record](docs/rollouts/2026-10-22-demo-run.md) |
| A Gateway deposit | 3 USDC from the agent wallet, [`0xa022b539...caba3f`](https://explorer.testnet.arc.io/tx/0xa022b539a506cfb58f1b2fe1f509a00cce6b5b3ccc23c4ebe40a410bebcaba3f); `manual`, not a payment and not traction: [record](docs/rollouts/2026-10-15-gateway-deposit.md) |
| A milestone escrow contract | [`0x9752F995...2971dd5`](https://explorer.testnet.arc.io/address/0x9752F995739C8A171AbD24944925EaF4d2971dd5) (not verified on the explorer), deployed in [`0x906d8cc3...57a6b5`](https://explorer.testnet.arc.io/tx/0x906d8cc3d1d4d474dfc418b626b197341b1dc026427a8c2788588b2a6257a6b5), read back with `cast call` and compared with a local build of the same source. A reference deployment: no outside engagement has run; it was first funded with our own testnet USDC on Oct 28 (see the release row below): [record](docs/rollouts/2026-10-24-milestone-escrow-deploy.md) |
| Escrow reads and a GitHub evidence check, on the server | The reference escrow read back through the API's own code (roles, limit, three Pending milestones), and a real merged pull request turned into facts and an evidence hash from GitHub's API; a pull request that does not exist is a failure: [record](docs/rollouts/2026-10-27-escrow-reads-and-evidence.md) |
| An escrow release, run live | Oct 28: the agent wallet committed [`0x73ac7434...a22421f0`](https://explorer.testnet.arc.io/tx/0x73ac7434b489d13da2678cba5552b2f5339bf6c7c7f980eda905e1eea22421f0) and then released milestone 0 in [`0xc49c13dd...b83f1f`](https://explorer.testnet.arc.io/tx/0xc49c13ddf6e89980df395a88db87bdb4445d7d1997385988f18b6ab14fb83f1f) (0.1 USDC to the contractor; block 69485148). `GET /decisions/e720c5eb.../verify` returns `verified: true` and an on-chain `MilestoneReleased` match. A hold and an escalation were recorded the same day and sent nothing. All our own wallets; the pull request was a stand-in input, not proof of work: [record](docs/rollouts/2026-10-28-escrow-release-live.md) |
| The approver's escrow actions, run live | Oct 29, from the admin site with the approver wallet, all `ok`: pause [`0x722515b4...56d0e8d7`](https://explorer.testnet.arc.io/tx/0x722515b498af9ba18b31974e81d670b7b17b618454ce6741728e633856d0e8d7), withdraw 0.1 USDC [`0x058fd250...45ec1bfd`](https://explorer.testnet.arc.io/tx/0x058fd2500aa95077bb651fe60f214dd93d18beaf6d16b9cf18a61ad345ec1bfd), reject milestone 1 [`0xe078db71...664537d1`](https://explorer.testnet.arc.io/tx/0xe078db719ac44a4e7082b989a83ea49aa50081067b0b84d39872a572664537d1), release milestone 2 (0.5 USDC, above the agent limit) as approver [`0x904a13bd...8adfaee6`](https://explorer.testnet.arc.io/tx/0x904a13bdee5135609085dbf1bbd719e2877cef36d16b8c8a846655f8adfaee6), resume [`0xe6fa3e83...31d58917`](https://explorer.testnet.arc.io/tx/0xe6fa3e83df336154c75bb56fc4ed0f31cae0df68461f06edae4587ad31d58917). All our own wallets; the evidence hash used was a stand-in: [record](docs/rollouts/2026-10-29-escrow-approver-actions-live.md) |

## Links

| What | Link |
|---|---|
| Proof page (public audit, decision checker, "Verify the whole chain") | [ceedebooks.xyz](https://ceedebooks.xyz) |
| API (key-authenticated; public audit routes need no key) | [api.ceedebooks.xyz](https://api.ceedebooks.xyz) |
| Admin site (wallet sign-in; only a business's on-chain approver gets in) | [admin.ceedebooks.xyz](https://admin.ceedebooks.xyz) |
| Vendor portal (vendors sign in with their wallet) | [portal.ceedebooks.xyz](https://portal.ceedebooks.xyz) |
| Business registration | [portal.ceedebooks.xyz/business.html](https://portal.ceedebooks.xyz/business.html) |

## 60-second tour for reviewers

1. **See it:** open [ceedebooks.xyz](https://ceedebooks.xyz) (no login). Live paid, held and escalated counts, an Outcomes block that states how many outside businesses exist, and a feed of decisions.
2. **Check it yourself:** press **Verify** on any decision. Your browser recomputes the hash and compares it to the event on Arc. Or from a terminal:
   ```bash
   curl -s https://api.ceedebooks.xyz/decisions/46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a/verify
   ```
   (the real domain bill; expect `verified: true` and `onchain.match: true`).
3. **Read the contract:** [`contracts/BudgetEnforcer.sol`](contracts/BudgetEnforcer.sol) and its 24 tests in [`test/`](test/BudgetEnforcer.t.sol); the multi-business contracts are deployed: factory [`0x97b9A3802bA6B258cBeF6070532a265656bb391C`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C), anchor [`0x008217BeC86462E76126F94e50eBe68fb4a41444`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444).
4. **Run the tests:** `forge test -vv` and `pytest agent/tests/` (setup below).
5. **See how an agent would use it:** [`/.well-known/agent.json`](https://api.ceedebooks.xyz/.well-known/agent.json).

---

## Since Sep 27

The hackathon is judged on what changed from its start, so here is the starting point and the end, both taken from the git history (`git log`), not from memory.

| | Sep 27, 2026 | Now (v1.3.2.5) |
|---|---|---|
| Repository | One commit, `dcf1f2a` "Initial commit", containing only the MIT `LICENSE` | 46 commits and 36 tagged releases, `v1.2.0` to `v1.3.2.5` |
| Code and contracts | None | `BudgetEnforcer` (Sep 29), then the multi-business contracts (`BudgetEnforcerV2`, `BudgetFactory`, `LedgerAnchor`, Oct 4) and the `MilestoneEscrow` contract (Oct 24); the Python engine, API, proof page, admin site and vendor portal described above |
| Tests | None | 111 Foundry, 699 Python, 6 Node runs |
| Live system | None | An API, a public proof page, a wallet-signed admin site and a vendor portal on Arc Testnet |
| Users | None | None outside the project: no outside business or paying user. The business that uses it is our own |
| Real payments | None | One real vendor bill of ours (the domain, 2.20 USDC, paid by card and reimbursed through the contract, labelled `manual`); everything else is self-owned test or shadow activity, shown apart on the proof page |

The first build commit after the initial one is `15d1b0f` (Sep 29): `BudgetEnforcer` deployed and verified, with 24 Foundry tests. Nothing in this repository predates Sep 27. The live counts (decisions, payments, businesses) change constantly, so the [proof page](https://ceedebooks.xyz) is the only source for them.

---

## What is CeedeBooks

Most African SMEs run their finances with a spreadsheet, a WhatsApp thread with a bookkeeper and someone checking a bank app before every payment. CeedeBooks is an agent that takes over the repetitive part: it checks each invoice against a purchase order and an independently confirmed receipt, then pays, holds or escalates it, while the owner keeps contract-level control over what the agent may spend, on whom and how much.

It is not "an LLM with a wallet". The agent proposes; a smart contract, not a prompt, decides whether a payment goes through. If the agent is wrong (a compromised prompt, a hallucinated vendor), the contract refuses.

## What it does

- **On-chain limits:** per-transaction, daily and per-category caps, a vendor registry, once-only payment and escalation to a person, all enforced by the contract
- **Commit-then-pay:** the decision hash is committed in an earlier block than the payment, so "reasoned before paid" is provable
- **Three-way match:** invoice, purchase order and a receipt confirmed by someone other than the agent; the rules decide, the model only writes narrative and a recorded second opinion
- **Audit you can check:** every decision is hashed, chained and signed per business, and the ledger head is anchored on-chain; the public proof page recomputes it in your browser
- **Oversight:** maker-checker on releases, a pause with a public reason, figures written by code
- **Sites:** a public proof page, a wallet-signed admin site, a vendor portal and a business registration page
- **Multi-business and shadow mode:** one contract per business from a factory; real bills mirrored as testnet payments after the owner approves
- **Escrow:** a milestone escrow contract, read-only routes, a GitHub evidence dry run and the agent's commit-then-release path, an Escrows tab in the admin site with the approver's own pause, release, reject and withdraw, one live agent release of 0.1 USDC ([record](docs/rollouts/2026-10-28-escrow-release-live.md)) and one live run of the approver's actions ([record](docs/rollouts/2026-10-29-escrow-approver-actions-live.md))

The full list, with limits for each item, is in [features](docs/features.md); the payment flow is in [how it works](docs/how-it-works.md).

## Contracts (Arc Testnet)

| Contract | Address |
|----------|---------|
| BudgetEnforcer | [`0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB`](https://explorer.testnet.arc.io/address/0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB) |
| BudgetFactory (v1.2.8) | [`0x97b9A3802bA6B258cBeF6070532a265656bb391C`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C) |
| LedgerAnchor (v1.2.8) | [`0x008217BeC86462E76126F94e50eBe68fb4a41444`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444) |
| BudgetEnforcerV2, test business `ceedebooks-test-v1282` (created through the factory on Oct 8) | [`0x030512Cd974F9A282415B0e6C81465f31502e32C`](https://explorer.testnet.arc.io/address/0x030512Cd974F9A282415B0e6C81465f31502e32C) |
| BudgetEnforcerV2, any other business | created per business by the factory |
| CeedeBooksYield | not built yet |
| MilestoneEscrow (v1.3.2, reference deployment, **not verified**) | [`0x9752F995739C8A171AbD24944925EaF4d2971dd5`](https://explorer.testnet.arc.io/address/0x9752F995739C8A171AbD24944925EaF4d2971dd5) |

What each contract does, and the businesses and their agent wallets: [contracts](docs/contracts.md).

## Setup

```bash
git clone https://github.com/MusaAis/CeedeBooks
cd CeedeBooks
cp .env.example .env   # fill in real values

pip install -r agent/requirements.txt
git clone --depth 1 https://github.com/foundry-rs/forge-std lib/forge-std   # test library, git-ignored
python3 scripts/ledger_key.py new --env .env   # the API refuses to start without a ledger signing key; the seed is never printed

set -a; source .env; set +a
uvicorn backend.main:app   # one worker only
```

Use the Canteen RPC (`arc-canteen rpc-url`) for `ARC_TESTNET_RPC_URL`, since testnet traction is counted through it. The Circle wallet and contract deployment steps are in [setup and deploy](docs/setup-and-deploy.md). CI runs 111 Foundry tests, 699 Python tests and 6 Node runs on every push; how to run each is in [testing](docs/testing.md).

## Documentation

| | |
|---|---|
| [Changelog](CHANGELOG.md) | Every release, newest first |
| [Features](docs/features.md), [How it works](docs/how-it-works.md) | What is built and how a payment happens |
| [Architecture](docs/architecture.md), [Trust model](docs/trust-model.md) | Design, guarantees and limits, custody trade-off |
| [Contracts](docs/contracts.md), [API](docs/api.md) | Contract behaviour and every route |
| [Traction](docs/traction.md), [Rollout records](docs/rollouts/README.md) | Evidence and what ran live, failures included |
| [Roadmap](docs/roadmap.md), [Known issues](docs/known-issues.md) | Phase status and documented bugs |
| [Testing](docs/testing.md), [Setup and deploy](docs/setup-and-deploy.md), [Project structure](docs/project-structure.md), [Server upgrades](deploy/DEPLOY.md) | For contributors and operators |

---

## Builder

**Musa Ali**, CS student at Federal University Dutse (FUD). Builder, PenTester & Dev. Appointed
**Lepton Peer Mentor** by Canteen.

- **3rd** place at Lepton, **Standout** at Agora with [@AgoraFX](https://github.com/MusaAis/AgoraFX)
- X: [@Musa_Ais](https://x.com/Musa_Ais)
- GitHub: [@MusaAis](https://github.com/MusaAis)

---

---

## License

MIT
