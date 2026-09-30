# CeedeBooks
*"Ceede" means money in Pulaar/Fulfulde.*

An autonomous financial-operations agent for African SMEs on Arc: pays invoices, runs contractor
milestone escrow, and parks idle treasury into yield — with spending limits enforced **on-chain**(never in a prompt) and every decision hash-logged before money moves.

Built for the **Tameion Agents Hackathon** (Canteen × Circle × Arc), Sep 27 – Oct 10, 2026.

---

## What is CeedeBooks

Most African SMEs run their finances the way most small businesses everywhere do: a spreadsheet, a
WhatsApp thread with a bookkeeper, and someone manually checking a bank app before every payment.
CeedeBooks is an agent that takes over the repetitive parts of that job — validating and paying
invoices, tracking contractor milestones, moving idle cash into yield when it isn't needed for a few
days — while the business owner keeps hard, contract-level control over what the agent is allowed to
spend, on whom, and how much.

It isn't "an LLM with a wallet." The agent proposes; a smart contract, not a prompt, decides whether
a payment is actually allowed to go through.

---

## ✨ Features

- **On-chain budget enforcement** — per-transaction, per-day, and cumulative per-category-per-day
  spending caps, enforced by the contract itself, not by the agent's instructions
- **Vendor registry with wallet-change control** — only approver-registered addresses can be paid;
  a vendor's wallet change requires explicit human sign-off
- **Commit-then-pay** — the agent's reasoning is hashed and committed to the chain in an earlier
  block than the payment it justifies, so "reasoned before paid" is provable from block order alone
- **Escalation workflow** — the agent can park a payment for human review instead of guessing; only
  the approver can release it, exactly once
- **Full audit trail** — every decision (paid, held, escalated) is hash-logged off-chain and
  on-chain, with a judge-reproducible round-trip: recompute the hash from the record, match it to
  the on-chain event
- **Contractor milestone escrow** *(planned)* — USDC held per milestone, released on evidence of
  completed work, contract-enforced regardless of the model's confidence
- **Idle-treasury auto-yield** *(planned)* — USYC via a pooled wrapper contract, working around
  Arc's $100k/institution-only eligibility gate at the raw Teller level
- **Multi-business ready** — vendor registry, budgets, and categories are all business-scoped
  concepts, not hardcoded to one deployment

---

## How a payment actually happens

1. AgoraFX's agent owes a data provider $0.001 for an FX-rate fetch. The invoice lands in the ledger.
2. Deterministic checks run first, before any model is involved: is there a matching PO? Has the
   receipt been confirmed by someone other than the agent? Has the vendor's wallet changed since the
   last payment? Any failure here sends the invoice straight to escalation.
3. If those pass, the decision layer — rules today, Laya once it's fine-tuned and calibrated on
   CeedeBooks' own schema, an LLM only for genuinely uncertain cases — decides: pay now, pay early
   for a discount, hold, or escalate.
4. Whatever the decision, it's written to the audit log and hashed (`reasoningHash`) *before*
   anything touches the contract.
5. The agent calls `commitDecision()` with that hash, bound to the exact payment parameters. The
   commitment has to sit in an earlier block than the payment itself.
6. The agent calls `pay()`. The contract independently re-checks all of it: is this vendor
   registered? Is this invoice already paid? Is the amount inside the per-tx, daily, and
   category-daily limits? Does a valid, unconsumed commitment exist for these exact parameters?
   Only if every one of those holds does USDC actually move.
7. Anyone can later pull the off-chain record for that decision, recompute the hash from the
   documented serialization, and check it against the on-chain event — "the agent reasoned before it
   paid" isn't a claim in a deck, it's checkable in one command.

If the agent gets any of this wrong — a compromised prompt, a hallucinated vendor, a miscalibrated
model — the contract doesn't know or care why the request was wrong. It just refuses.

---

## Why CeedeBooks

- **Verified, not assumed.** Every external dependency in this build — Laya's real accuracy, Groq's
  live model lineup, Circle's actual USYC/Gateway/CCTP integration surfaces, Arc's real contract
  addresses — was checked against a primary source before a line of code depended on it. Two of the
  bigger surprises that turned up doing that are in the table below.
- **Skin in the game, literally.** The agent cannot exceed its budget even if it's prompted,
  jailbroken, or fed a malicious invoice — the enforcement lives in Solidity, not in a system prompt.
  A simulated prompt-injection attempt is one of the 24 tests in this repo, and it fails the way it's
  supposed to: the contract refuses regardless of what the agent was convinced of.
- **Model output is an input, never a release condition.** Circle's own `arc-escrow` sample app
  releases contractor funds on a bare JSON response from GPT-4o with no structural check behind it —
  CeedeBooks was built specifically not to repeat that pattern.
- **An audit trail that's actually checkable.** Not "we hash things" as a slide bullet — a reviewer
  can pull the off-chain record, recompute the hash from the documented serialization, and confirm it
  against the on-chain event, live.
- **Tested, not just described.** 24 Foundry tests plus 11 Python tests, all passing against the
  deployed contract's exact source and the live decision pipeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      CeedeBooks Agent                            │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Treasury Brain   │  AP/AR Engine    │  Contractor Manager       │
├──────────────────┼──────────────────┼───────────────────────────┤
│ Circle Gateway    │ rules baseline   │ MilestoneEscrow.sol       │
│ POST /v1/balances │  + Laya          │ holds USDC per milestone  │
│                   │  + LLM escalation│                           │
│ USYC via own      │                  │ Model validates evidence: │
│ pooled wrapper    │ Three-way match  │ gates the CALL, never     │
│ (CeedeBooksYield  │ (invoice+PO+     │ the CONTRACT's own        │
│  .sol)            │  receipt)        │ require() checks          │
└──────────────────┴──────────────────┴───────────────────────────┘
                    │
          ┌─────────▼──────────┐
          │   BudgetEnforcer   │
          │   .sol             │
          │                    │
          │ vendor registry    │
          │ commit-then-pay    │
          │ per-tx/daily/      │
          │  category caps     │
          │ escalation flow    │
          │ pause, 2-step      │
          │  approver rotation │
          └────────────────────┘
```

---

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1. Foundation | Repo, Circle treasury wallet, `BudgetEnforcer.sol` deployed + verified + tested | ✅ Done |
| 2. AP/AR Engine | Rules baseline, three-way match, Laya/LLM escalation, first real payments | 🔧 In progress |
| 3. Treasury Brain | Gateway unified balance, `CeedeBooksYield.sol` pooled USYC wrapper, runway forecasting | ⏳ Planned |
| 4. Contractor Milestones | `MilestoneEscrow.sol`, at least 1 real contractor paid | ⏳ Planned |
| 5. Audit Trail + Traction | 5+ businesses onboarded, real USDC volume, live hash round-trip demo | ⏳ Planned |
| 6. Submit | Demo video, final README, submission | ⏳ Planned |

---

## Circle tools — 6

| Tool | Status | Notes |
|---|---|---|
| Agent Wallet | ✅ live | Developer-controlled wallet, Arc Testnet |
| USDC | ✅ live | Native ERC-20 at `0x3600000000000000000000000000000000000000` |
| Gateway | Planned Phase 3 | `POST /v1/balances`, confirmed permissionless |
| USYC | Planned Phase 3 | Via own pooled wrapper — Arc's Teller has a $100k/allowlist gate at the raw contract level |
| CCTP | Stretch | Domain 26, `minFinalityThreshold: 2000`, V2 7-param `depositForBurn` only |
| EURC | Stretch | European vendor payments |
| ~~Paymaster~~ | Dropped |

---

## Contracts

### `BudgetEnforcer.sol`

The on-chain spending authority. Funds are **held by the contract**, not the agent wallet — the agent
can only move them through `pay()`, inside these limits:

- **Vendor registry** — only approver-registered addresses can be paid. A vendor wallet change means
  registering a new address; the old one stops working the moment it's revoked.
- **Commit-then-pay** — `commitDecision(hash)` must land in an earlier block than the `pay()` it
  justifies, bound to the exact payment parameters (vendor, amount, invoice, doc, category, reasoning).
  This makes "reasoned before paid" provable from block order, not just code discipline.
- **Canonical idempotency key** — `invoiceKey = keccak256(vendor, invoiceNumber)`, not something the
  agent can shift by resubmitting a different file. `docHash` is a separate pointer, emitted in the
  event, so every payment still points at a real document.
- **Limits** — per-tx, per-day, and cumulative per-category-per-day. An unregistered category defaults
  to a 0 limit — fail-closed, not fail-open.
- **Escalation** — the agent can park a payment instead of making it; only the approver can pay it,
  exactly once, and it's provably distinct from a direct payment.
- **Admin** — pause (circuit breaker), approver-only withdraw, agent rotation, and a two-step approver
  rotation (so a typo in the new address can't lock the contract).

`reasoningHash` (proves *this decision* was logged before money moved) and the invoice-level dedupe
key are deliberately different guarantees, kept in separate fields — conflating them would let a judge
mistake "this invoice wasn't double-paid" for "this reasoning was verified," which are not the same
claim.

One instance per business — this deployment is not multi-tenant. Onboarding a second business means
deploying a second `BudgetEnforcer`, not reusing this address.

**BudgetEnforcer Deployed** on **Arc Testnet** & verified. **Budget live:** 100 USDC/day, 20 USDC/tx,
50 USDC/day in the data-oracle category.

## 📋 Contracts (Arc Testnet)

| Contract | Address |
|----------|---------|
| BudgetEnforcer | [`0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB`](https://explorer.testnet.arc.io/address/0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB) |
| CeedeBooksYield | not built yet |
| MilestoneEscrow | not built yet |

---

## Project Structure

```
ceedebooks/
├── agent/
│   ├── config.py           # env loader
│   ├── categories.py       # spend category enum (off-chain names for on-chain uint8 keys)
│   ├── llm.py              # openai/gpt-oss-120b escalation reasoning (Groq)
│   ├── decision_log.py     # SHA256 audit log, hash-before-action sequencing
│   ├── contract.py         # reads + Circle-signed writes against BudgetEnforcer
│   ├── payables.py         # three-way match, rules-baseline decisions, commit-then-pay
│   ├── wallet_setup.py     # one-off Circle developer-controlled wallet creation
│   └── tests/
│       └── test_payables.py
├── backend/
│   ├── main.py              # FastAPI: vendors, purchase orders, receipts, invoices, audit verify
│   └── models.py             # SQLite schema + queries
├── contracts/
│   └── BudgetEnforcer.sol
├── test/
│   ├── BudgetEnforcer.t.sol
│   └── mocks/MockUSDC.sol
├── foundry.toml
├── README.md
└── .env.example
```

---

## Setup

```bash
git clone https://github.com/MusaAis/ceedebooks
cd ceedebooks
cp .env.example .env   # fill in real values

pip install -r agent/requirements.txt
forge install foundry-rs/forge-std   # if not already present

set -a; source .env; set +a
```

---

### Circle treasury wallet

```bash
python agent/wallet_setup.py
```

Reuses an already-registered Circle entity secret (don't generate a new one for an account that
already has one — that's a rotation flow, not a fresh setup). Creates a separate wallet set + one
Arc Testnet wallet, appends its ID/address to `.env`. Fund it at
[faucet.circle.com](https://faucet.circle.com) (Arc Testnet).

---

### Tests

```bash
forge test -vv
```

24 tests, one per revert path / acceptance scenario: unregistered vendor, revoked vendor, over
per-tx/daily/category limits, unset category (fail-closed), invoice resubmitted as a different file,
missing/reused/mismatched commit, crash-and-retry (no double payment), a simulated prompt-injection
attempt (contract refuses even if the agent were fooled), full escalate → approve/reject flow,
escalated invoices blocked from direct payment, pause, withdraw, agent rotation, two-step approver
rotation, and the `reasoningHash` round-trip via the `PaymentMade` event.

```bash
pytest agent/tests/
```

11 tests covering the three-way match, rules-baseline decisions (pay/hold/escalate), and retry
safety — reprocessing an already-paid invoice is a no-op instead of a second payment attempt.

---

### Running the API

```bash
uvicorn backend.main:app --reload
```

- `POST /vendors`, `POST /purchase-orders`, `POST /receipts` — set up the records a real invoice
  gets matched against. A receipt with `confirmed_by_role: "agent"` is rejected outright; it has to
  come from an independent party.
- `POST /invoices` — runs the full pipeline: three-way match → rules-baseline decision →
  commit-then-pay or escalate.
- `GET /invoices/{id}` — current status and `reasoning_hash` for that invoice.
- `GET /decisions/{reasoning_hash}/verify` — the hash round-trip check as a real endpoint, not a
  manual script: recomputes the hash from the stored record and confirms it matches.

---

### Deploy

```bash
forge create contracts/BudgetEnforcer.sol:BudgetEnforcer \
  --rpc-url $ARC_TESTNET_RPC_URL \
  --private-key $DEPLOYER_PRIVATE_KEY \
  --broadcast \
  --verify \
  --verifier blockscout \
  --verifier-url https://explorer.testnet.arc.io/api/ \
  --constructor-args $AGENT_ADDRESS $APPROVER_ADDRESS 0x3600000000000000000000000000000000000000
```

Then, from the approver key:

```bash
cast send $BUDGET_ENFORCER_ADDRESS "setBudget(uint256,uint256)" 100000000 20000000 \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY

cast send $BUDGET_ENFORCER_ADDRESS "setCategoryDailyLimit(uint8,uint256)" 0 50000000 \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY

# Real vendor (data-provider) address:
cast send $BUDGET_ENFORCER_ADDRESS "setVendor(address,bool)" <VENDOR_ADDRESS> true \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY
```

Amounts are USDC in its native 6-decimal ERC-20 representation (`100000000` = $100.00).

---

## Documented bugs & gotchas

Only the ones big enough to actually cost someone real time or silently break something real.

| # | Area | Symptom | Cause | Fix |
|---|---|---|---|---|
| 1 | `forge create` | `Constructor argument count mismatch: expected N but got N+k` | `--constructor-args` is greedy — it swallows every token after it, including later flags like `--broadcast` or `--verify` | Put `--constructor-args ...` **last** in the command |
| 2 | verification | `Params 'module' and 'action' are required parameters` from `testnet.arcscan.app`, even with correct Etherscan-style fields | Open, unresolved upstream bug (`circlefin/arc-node#210`) — Arcscan's verify endpoint is inconsistent for fresh submissions despite being documented as Etherscan-V1-compatible | Verify against Arc's other explorer instance instead: `--verifier blockscout --verifier-url https://explorer.testnet.arc.io/api/`. Confirmed working, "exact match." |
| 3 | Laya | Model-card numbers look production-ready; the checkpoint most people would actually reach for isn't | The base `laya` checkpoint scores **0.362 zero-shot** on typed-decisions — below even majority-class guessing (0.461). It's explicitly a base to fine-tune, not a decision engine, but that caveat is easy to miss under deadline pressure | Never gate a real decision on the base checkpoint. Fine-tune on your own labeled schema — and don't assume a workflow-specific fine-tuned checkpoint transfers to a *different* schema either; per its own model card, it doesn't |
| 4 | Groq | A live agent's LLM calls fail silently — process stays up, only a log warning, looks fine from the outside | `groq/compound-mini` was decommissioned Sep 21, 2026, and `llama-3.3-70b-versatile` was quietly moved to Enterprise-tier-only pricing on developer keys around the same time. A wrapped try/except turned a hard failure into a silent one | Don't trust "the process is running" as a proxy for "the model calls are succeeding" — add a real health check against the exact model string. Target `openai/gpt-oss-120b` directly: live, developer-tier accessible, no orchestration wrapper needed |
| 5 | USYC | Deposits revert / can't clear eligibility | Arc's USYC Teller has a **$100k minimum + non-US-institution allowlist** at the contract level | Route through one pooled wrapper contract (allowlisted once), track per-business shares internally — never call the Teller directly per business |
| 6 | CCTP on Arc | Attestation stuck at "pending" forever | Arc requires `minFinalityThreshold: 2000` (finalized) — the usual `1000` (safe) value other CCTP testnets accept doesn't work here | Hardcode `2000` for any Arc CCTP call |
| 7 | CCTP on Arc | Silent revert with no error data | Arc only supports the CCTP **V2 7-parameter** `depositForBurn` selector; V1's 4-param version is accepted by the node but reverts | Confirm your library/call uses the V2 selector explicitly |

---

## Design notes for reviewers

- **`invoiceKey` excludes amount on purpose.** It's derived from `(vendor, invoiceNumber)` only, so an
  attacker can't get a second payment through by altering the amount on a resubmission.
- **`categoryDailyLimit` defaults to 0.** An unregistered category is fail-closed, not fail-open —
  categories must be explicitly enabled by the approver before the agent can spend in them.
- **Escalated invoices are locked out of `pay()`.** Once `escalate()` runs for an invoice key, `pay()`
  for that same key reverts with `"Invoice escalated"` — there's no path where the agent's escalation
  gets silently bypassed by a later direct payment attempt.
- **Gas and off-chain costs (LLM inference, cloud hosting) are not `pay()` categories.** Only
  transactions that actually move USDC through this contract belong there; everything else is a
  recorded expense in the off-chain ledger, never a phantom "paid" entry (see `decision_log.py`).
- **A retry can't pay twice.** Before doing anything else, `payables.process_invoice()` checks the
  contract's own `paid` mapping for that invoice's key. If a crash happened after a payment landed
  on-chain but before the local record updated, reprocessing that invoice is a no-op, not a resend.

---

## Builder

**Musa Ali** — CS student at Federal University Dutse (FUD). Builder, PenTester & Dev. Appointed
**Lepton Peer Mentor** by Canteen.

- **3rd** place at Lepton, **Standout** at Agora with [@AgoraFX](https://github.com/MusaAis/AgoraFX)
- X: [@Musa_Ais](https://x.com/Musa_Ais)
- GitHub: [@MusaAis](https://github.com/MusaAis)

---

## License

MIT

⭐ **Star the repo if you find it useful**
