# Architecture and design

How the parts fit, which Circle tools are used and why the design is what it is.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      CeedeBooks Agent                            │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Treasury Brain   │  AP/AR Engine    │  Contractor Manager       │
│  (planned)        │  (BUILT)         │  (contract only)          │
├──────────────────┼──────────────────┼───────────────────────────┤
│ Circle Gateway    │ Three-way match  │ MilestoneEscrow.sol       │
│ POST /v1/balances │ rules baseline   │ holds USDC per milestone  │
│                   │ LLM: narrative + │                           │
│ USYC via own      │ recorded 2nd     │ Evidence dry run (built)  │
│ pooled wrapper    │ opinion (never   │ informs a decision; the   │
│ (CeedeBooksYield  │ acts)            │ contract's own require    │
│  .sol)            │ FastAPI intake + │ checks always apply       │
│                   │ SQLite ledger    │                           │
└──────────────────┴──────────────────┴───────────────────────────┘
                    │
          ┌─────────▼──────────┐        ┌──────────────────────┐
          │   BudgetEnforcer   │◄───────│ Audit log (SQLite)   │
          │   .sol             │ hash   │ hash_input + hash,   │
          │ vendor registry    │ commit │ written BEFORE the   │
          │ commit-then-pay    │        │ contract is touched, │
          │ per-tx/daily/      │        │ chained and signed   │
          │  category caps     │        │ per business         │
          │ escalation flow    │        └──────────┬───────────┘
          │ pause, 2-step      │                   │ head
          │  approver rotation │        ┌──────────▼───────────┐
          └────────────────────┘        │ LedgerAnchor.sol     │
                                        │ agent wallet anchors │
                                        │ the newest head      │
                                        └──────────────────────┘
```

The engine, the audit log and the ledger are built; the Treasury Brain column is planned and in the Contractor Manager column only the contract exists: it is deployed (reference deployment), the API can register and read it, run a GitHub pull-request dry run and, on an admin's request, make the agent's release decision and send the commit and the release from the agent wallet (run live once on Oct 28: [record](rollouts/2026-10-28-escrow-release-live.md)), the admin site has an Escrows tab for all of it and, from v1.3.2.4, the approver's own pause, release, reject and withdraw as wallet-signed transactions (run live once on Oct 29: [record](rollouts/2026-10-29-escrow-approver-actions-live.md)), and the proof page does not show escrows (the only pieces of the Treasury Brain that exist are the read-only Gateway balance on the admin Overview and a manual script that deposits into Gateway). There is one `BudgetEnforcer` per business: the original business runs on the v1 contract drawn above, and every other business gets a `BudgetEnforcerV2` from `BudgetFactory` (same shape, plus a weekly limit and return-to-agent). Each business has its own audit log, ledger chain and anchors.

## Circle tools

| Tool | Status | Notes |
|---|---|---|
| Agent Wallet | ✅ live | Developer-controlled wallet, Arc Testnet; signs every agent contract call (commit, pay, escalate, anchor) |
| USDC | ✅ live | Native ERC-20 at `0x3600000000000000000000000000000000000000` |
| Gateway | ✅ live: balance read and a manual deposit | `POST /v1/balances` for the agent wallet on domain 26, shown on the admin Overview; `scripts/gateway_deposit.py` approves and deposits USDC into Circle's Gateway Wallet (run once, 3 USDC). No spend path yet |
| USYC | Planned (Phase G) | Via own pooled wrapper (Arc's Teller has a $100k / allowlist gate) |
| CCTP | Stretch | Domain 26, `minFinalityThreshold: 2000`, V2 7-param `depositForBurn` only |
| EURC | Stretch | European vendor payments |
| ~~Paymaster~~ | Dropped | Not used |

Honest count: 3 live (Agent Wallet, USDC, and Gateway for a balance read and a manual deposit, with no spend), 3 planned (USYC, CCTP, EURC).

## Why CeedeBooks

- **Verified, not assumed.** Every external dependency in this build (Laya's real accuracy, Groq's live model lineup, Circle's actual USYC / Gateway / CCTP surfaces, Arc's contract addresses) was checked against a primary source before code depended on it.
- **Skin in the game, literally.** The agent cannot exceed its budget even if it is prompted, jailbroken, or fed a malicious invoice. Enforcement lives in Solidity. A simulated prompt-injection attempt is one of the 24 Foundry tests, and the contract refuses regardless of what the agent was convinced of.
- **Model output is an input, never a release condition.** In Circle's `arc-escrow` sample, the app releases funds when a GPT-4o answer returns `valid: true` with `HIGH` confidence (`app/api/contracts/validate-work/route.ts`), and the escrow contract itself does not check the work. In CeedeBooks the model's answer is a recorded input: the rules verdict acts, and the contract enforces the limits whatever the model says.
- **Independent receipt witness.** A three-way match proves nothing if the agent can confirm its own receipts. It can't.
- **Settlement is confirmed, not hoped for.** Circle's transaction API is asynchronous — accepting a request isn't the same as it succeeding on-chain. CeedeBooks waits for a terminal state before trusting any result, closing a real phantom-payment risk most demos never test for.
- **Tested, not just described.** 111 Foundry tests (24 cover the v1 contract, 45 cover the multi-business contracts, 42 cover the escrow contract) plus 693 Python tests covering the decision pipeline, the isolation between businesses, onboarding, shadow mode, per-business categories, the signed ledger and its on-chain anchor, the admin routes, the oversight controls, and the outcome figures, and the escrow reads and the evidence fetch rules.
