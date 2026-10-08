# CeedeBooks

![Release](https://img.shields.io/github/v/release/MusaAis/CeedeBooks?label=release)
![CI](https://github.com/MusaAis/CeedeBooks/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue)
![Foundry tests](https://img.shields.io/badge/forge%20tests-69%2F69%20passing-brightgreen)
![Python tests](https://img.shields.io/badge/pytest-208%2F208%20passing-brightgreen)
![Network](https://img.shields.io/badge/Arc-Testnet-informational)

*"Ceede" means money in Pulaar/Fulfulde.*

An autonomous accounts-payable agent for African SMEs on Arc: it validates and pays invoices, with spending limits enforced **on-chain** (never in a prompt) and every decision hash-logged before money moves. Contractor escrow and idle-cash yield are planned, not built.

Built for the **Tameion Agents Hackathon** (Canteen × Circle × Arc), Sep 27 – Oct 17, 2026.

**Current: v1.2.8.2 (Phase L3, business onboarding).** A business can now register itself: it applies with a wallet signature, the operator accepts, the server creates its hosted agent wallet, and the owner creates the contract from their own wallet. The server records the business only after the chain proves our factory made it, with the applicant's wallet as approver and the issued agent wallet as agent. Every key, session, record and query belongs to one business. Only the original business exists so far (the live v1 `BudgetEnforcer`); no outside business has registered yet. `BudgetFactory` and `LedgerAnchor` are deployed and verified on Arc Testnet. The AP/AR engine is live on Arc Testnet behind a key-authenticated HTTPS API (`api.ceedebooks.xyz`), with a public proof page (`ceedebooks.xyz`), a wallet-signed admin site (`admin.ceedebooks.xyz`), a vendor portal (`portal.ceedebooks.xyz`) and a business registration page (`portal.ceedebooks.xyz/business.html`). No outside business or paying user yet: every payment so far is self-owned. See [Custody trade-off](#custody-trade-off), the [Roadmap](#roadmap) and the [Changelog](#changelog).

## 60-second tour for reviewers

1. **See it:** open [ceedebooks.xyz](https://ceedebooks.xyz) (no login). Live paid, held and escalated counts, and a feed of decisions.
2. **Check it yourself:** press **Verify** on any decision. Your browser recomputes the hash and compares it to the event on Arc. Or from a terminal:
   ```bash
   curl -s https://api.ceedebooks.xyz/decisions/46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a/verify
   ```
   (the real domain bill; expect `verified: true` and `onchain.match: true`).
3. **Read the contract:** [`contracts/BudgetEnforcer.sol`](contracts/BudgetEnforcer.sol) and its 24 tests in [`test/`](test/BudgetEnforcer.t.sol); the multi-business contracts are deployed: factory [`0x97b9A3802bA6B258cBeF6070532a265656bb391C`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C), anchor [`0x008217BeC86462E76126F94e50eBe68fb4a41444`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444).
4. **Run the tests:** `forge test -vv` and `pytest agent/tests/` (setup below).
5. **See how an agent would use it:** [`/.well-known/agent.json`](https://api.ceedebooks.xyz/.well-known/agent.json).

---

## What is CeedeBooks

Most African SMEs run their finances the way most small businesses everywhere do: a spreadsheet, a WhatsApp thread with a bookkeeper, and someone manually checking a bank app before every payment.
CeedeBooks is an agent that takes over the repetitive parts of that job: it checks each invoice against a purchase order and a confirmed receipt, then pays, holds or escalates it, while the business owner keeps hard, contract-level control over what the agent is allowed to spend, on whom, and how much. Contractor milestone escrow and moving idle cash into yield are planned, not built.

It isn't "an LLM with a wallet." The agent proposes; a smart contract, not a prompt, decides whether a payment is actually allowed to go through.

---

## Features

**Built and running**

- **On-chain budget enforcement**: per-transaction, per-day, and cumulative per-category-per-day spending caps, enforced by the contract itself, not by the agent's instructions
- **Vendor registry**: only approver-registered addresses can be paid; changing a vendor wallet requires explicit approver action
- **Commit-then-pay**: the agent's reasoning hash is committed on-chain in an earlier block than the payment it justifies, confirmed before the payment fires, so "reasoned before paid" is provable from block order
- **Escalation workflow**: the agent can park a payment for human review instead of guessing; only the approver can release it, exactly once
- **Three-way match**: an invoice must match a purchase order (number and amount) and an independently confirmed receipt, and the invoice wallet must match the vendor on file
- **Independent receipt witness**: a receipt confirmed by the `agent` role is rejected at the API and again inside the match
- **Rules-baseline decision engine**: deterministic pay / hold / escalate decisions, with an LLM used only to write the narrative on escalations
- **Retry-safe payments**: before doing anything, the pipeline asks the contract whether the invoice key is already paid, so a crash-and-retry cannot pay twice
- **Confirmed, not assumed, settlement**: every commit, payment, and escalation is polled to a terminal on-chain state before it's recorded as succeeded — a reverted transaction is never logged as paid
- **Audit trail with round-trip check**: every decision (paid, held, escalated) is stored with its exact serialized hash input; `GET /decisions/{hash}/verify` recomputes the hash
- **Public proof page** (`ceedebooks.xyz`, no login): live paid / held / escalated counts, a feed of recent decisions, and a Verify button that recomputes the hash in the browser and checks it against the on-chain event
- **Refusals shown first-class**: held decisions are anchored on-chain with `logDecision`, and the page counts holds and escalations next to payments
- **Invoice pre-flight**: `POST /invoices/preflight` is a dry run that says whether an invoice would be paid, held or escalated, and why, without writing anything
- **Agent manifest**: `GET /.well-known/agent.json` describes the vendor flow, roles and guarantees for other agents
- **Wallet-signed admin site** (`admin.ceedebooks.xyz`): a session is accepted only if its signature recovers to the contract's current `approver()`; vendor approval, POs, receipts, escalations, limits and pause are signed in the admin's own wallet
- **Vendor portal** (`portal.ceedebooks.xyz`): a vendor applies with a wallet signature that proves it controls the payee address, the admin reviews the application, and once approved the vendor signs in with the same wallet. It sees only its own purchase orders and invoices, runs a dry run first, submits, and links to the proof page for the decision. No key or password is issued, and the portal never asks for a transaction. Spam controls: one pending application per wallet, at most 3 pending per IP, 5 submissions per hour per IP, small body cap
- **Invoice metrics on the proof page**: invoices processed, USDC paid and duplicates caught, split by origin (`agent`, `manual`, `demo`) so hand-run payments and demo runs never count as agent traffic

**Planned** (see the [Roadmap](#roadmap) for order and status; nothing below is live)

- **Multi-business onboarding (built, no outside business yet):** a business applies with a wallet signature at `portal.ceedebooks.xyz/business.html`, the operator accepts it (the server creates the business's hosted agent wallet), and the owner creates the contract with one transaction from their own wallet and picks their own limits. The server activates the business only after verifying the creation on-chain. The owner gets a setup checklist and an Add funds button in the admin site, and only the operator can mark a business as an outside party. The home business is not yet moved onto a factory contract (optional, last)
- **Audit hardening:** hash-chained ledger per business anchored on-chain, a log comparing the model's verdict with the rules' verdict, on-chain red-team log, key-custody check, re-evaluate for stuck invoices
- **Oversight:** maker-checker approvals, pause with a stated reason, every figure in a narrative written by code
- Circle Gateway unified balance
- Treasury: economics-gated yield sweep via a pooled USYC wrapper, continuous vendor screening, other-currency invoices
- Agent-to-agent reference client for vendors (demo runs labelled `demo`)
- Invoice intake from PDF or email, a Telegram status bot, receivables
- Contractor milestone escrow
- Laya fast-path decision model (built separately, not wired in)

---

## How a payment actually happens

1. A business's agent owes a vendor $0.001 for a data fetch. The invoice lands in the ledger (`POST /invoices`).
2. **Retry guard:** the pipeline derives the invoice key (`keccak256(vendor, invoiceNumber)`) and asks the contract if it is already paid. If yes, nothing is resent.
3. **Three-way match**, before any model is involved: does the invoice carry a PO number? Does the PO exist and match the amount (within $0.01)? Is there a receipt, confirmed by someone other than the agent? Does the invoice wallet match the vendor on file? Any failure escalates, with the rules reason as the logged explanation.
4. **Decision (rules baseline):** is the vendor registered on-chain? Is runway at least 7 days? Result: pay, hold, or escalate. If the decision is escalate, `openai/gpt-oss-120b` (Groq) writes the narrative, and that text is logged. The model never changes the decision.
5. The decision is written to the audit log and hashed (`reasoningHash`) *before* anything touches the contract.
6. For a payment, the agent calls `commitDecision()` bound to the exact payment parameters, **waits for it to confirm on-chain**, then calls `pay()` and waits for that to confirm too.
7. The contract independently re-checks everything: vendor registered, invoice not already paid, amount inside per-tx / daily / category-daily limits, valid unconsumed commitment for these exact parameters. Only if all hold does USDC move.
8. The Circle transaction ID is stored against the decision. Anyone can pull the record, recompute the hash from the stored serialization, and compare it to the `PaymentMade` event for that transaction.

If the agent gets any of this wrong (a compromised prompt, a hallucinated vendor, a miscalibrated model), the contract doesn't know or care why the request was wrong. It just refuses.

---

## Traction

Everything below is checkable on-chain. Nothing here is estimated.

| What | Evidence |
|---|---|
| Contract deployed and verified on Arc Testnet | [`BudgetEnforcer`](https://explorer.testnet.arc.io/address/0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB) |
| Test suites | 69 Foundry (24 on the v1 contract, 45 on the multi-business contracts) + 208 Python tests + browser-side JS tests, run by CI on every push |
| First self-owned vendor bill paid through the contract | `ceedebooks.xyz` registration, 2.20 USDC, category 1 |
| Multi-business contracts deployed and verified (v1.2.8) | [`BudgetFactory`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C), [`LedgerAnchor`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444) |
| Outside businesses or paying users | None yet |

**Live numbers** (pool balance, paid, held and escalated counts, vendors, purchase orders) change with every payment, so the [proof page](https://ceedebooks.xyz) and `GET /stats` are the only source. The escalations on it are self-owned test invoices that the agent parked for review, as designed. Testnet USDC has no market value.

**The domain payment.** CeedeBooks' own domain was bought by card (Namecheap order 215679394, $2.00 + $0.20 ICANN fee), and the registrar cannot take USDC, so the founder was reimbursed through the contract: vendor registered with `setVendor`, category 1 given a 20 USDC/day limit, the receipt hashed into `docHash`, the reasoning hash logged off-chain first, then `commitDecision` and `pay` from the Circle agent wallet in separate blocks.

- Payment transaction: [`0x8c176242...ce5d0d`](https://explorer.testnet.arc.io/tx/0x8c176242a84235a884d5790edf4a9aed403d87619359373e11f2e27141ce5d0d)
- `reasoning_hash`: `46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a`
- Script: [`scripts/pay_domain.py`](scripts/pay_domain.py)

This was run by hand through the same two-call sequence the agent uses, labelled `manual` in the audit log. It is not an autonomous agent decision. The cost itself was real: the domain was paid by card and reimbursed through the contract.

### Spend categories

| Key | Name | Daily limit |
|---|---|---|
| 0 | Data oracle | 50 USDC |
| 1 | Infrastructure (domains, hosting, SaaS) | 20 USDC |

An unregistered category has a limit of 0, so the contract refuses it. Names live off-chain in `agent/categories.py`.

---

## Why CeedeBooks

- **Verified, not assumed.** Every external dependency in this build (Laya's real accuracy, Groq's live model lineup, Circle's actual USYC / Gateway / CCTP surfaces, Arc's contract addresses) was checked against a primary source before code depended on it.
- **Skin in the game, literally.** The agent cannot exceed its budget even if it is prompted, jailbroken, or fed a malicious invoice. Enforcement lives in Solidity. A simulated prompt-injection attempt is one of the 24 Foundry tests, and the contract refuses regardless of what the agent was convinced of.
- **Model output is an input, never a release condition.** Circle's own `arc-escrow` sample releases contractor funds on a bare JSON response from GPT-4o with no structural check behind it. CeedeBooks is built specifically not to repeat that pattern.
- **Independent receipt witness.** A three-way match proves nothing if the agent can confirm its own receipts. It can't.
- **Settlement is confirmed, not hoped for.** Circle's transaction API is asynchronous — accepting a request isn't the same as it succeeding on-chain. CeedeBooks waits for a terminal state before trusting any result, closing a real phantom-payment risk most demos never test for.
- **Tested, not just described.** 69 Foundry tests (24 cover the v1 contract, 45 cover the multi-business contracts) plus 208 Python tests covering the decision pipeline, the isolation between businesses and the onboarding flow.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      CeedeBooks Agent                            │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Treasury Brain   │  AP/AR Engine    │  Contractor Manager       │
│  (planned)        │  (BUILT)         │  (planned)                │
├──────────────────┼──────────────────┼───────────────────────────┤
│ Circle Gateway    │ Three-way match  │ MilestoneEscrow.sol       │
│ POST /v1/balances │ rules baseline   │ holds USDC per milestone  │
│                   │ LLM on escalation│                           │
│ USYC via own      │ (Laya: planned)  │ Model validates evidence: │
│ pooled wrapper    │                  │ gates the CALL, never     │
│ (CeedeBooksYield  │ FastAPI intake + │ the CONTRACT's own        │
│  .sol)            │ SQLite ledger    │ require() checks          │
└──────────────────┴──────────────────┴───────────────────────────┘
                    │
          ┌─────────▼──────────┐        ┌──────────────────────┐
          │   BudgetEnforcer   │◄───────│ Audit log (SQLite)   │
          │   .sol             │ hash   │ hash_input + hash,   │
          │ vendor registry    │ commit │ written BEFORE the   │
          │ commit-then-pay    │        │ contract is touched  │
          │ per-tx/daily/      │        └──────────────────────┘
          │  category caps     │
          │ escalation flow    │
          │ pause, 2-step      │
          │  approver rotation │
          └────────────────────┘
```

---

## Trust model and limits (read this before relying on it)

- **What the contract guarantees:** only registered vendors are paid, within per-transaction, daily and per-category limits, once per invoice, and only after a commitment from an earlier block. The agent cannot change any of that, whatever it is told.
- **What it does not guarantee:** that an invoice is genuine. A human approves vendors and confirms receipts; the three-way match is only as honest as those people.
- **One operator.** The agent wallet is a Circle developer-controlled wallet under one account. "A second wallet" is not an independent agent.
- **The audit log is a database.** Each record's hash is checked against an on-chain event, but the log is not yet hash-chained, so a deleted row would not be detected. This is planned (Audit hardening).
- **The admin key.** The approver is a wallet held off the server. If it is lost, nobody can approve vendors or change limits.
- **Testnet.** Everything runs on Arc Testnet with test USDC. No outside business or paying user yet; every payment so far is self-owned.
- **One business so far.** The backend is multi-tenant and onboarding is built, but only the original business (the live v1 contract) exists. Isolation between businesses and the registration checks are enforced in code and tested against mocked businesses and, for the factory event, a real factory on a local node; no real second business has used them yet.
- **Hosted agent wallets.** See [Custody trade-off](#custody-trade-off): for businesses we onboard, we run the agent wallet.
- **Public reasoning.** `GET /decisions/{hash}` returns the full reasoning text, so no private data belongs in it.

---

## Custody trade-off

For the businesses we onboard first, **we run your agent's wallet.** It can only pay your approved vendors within your limits, and you can revoke it any time. You keep the owner wallet that controls the money and the limits. This was a deliberate choice: it lets a business with no technical staff get started in minutes, with no keys or servers to run.

What the agent wallet can and cannot do, all enforced by your contract and not by us:
- It can pay a vendor your wallet has approved, up to your per-payment, daily, weekly and per-category limits, once per invoice, and only after a decision was committed in an earlier block. It can also escalate a payment for you to decide and log a refusal.
- It cannot withdraw funds, add or approve vendors, change a limit, pause, or take over the contract. Those belong to your owner wallet, which we never hold. You can replace the agent wallet at any time.
- It holds only the small amount of USDC you send it for network fees. Your money sits in your contract, not in the agent wallet.

What it costs you in trust: the agent wallets are controlled by CeedeBooks through one Circle account, so they are **a separate wallet per business, but the same operator**. If that account were compromised, an attacker could act as every business's agent at once, limited by each contract's rules. A self-held agent (you run the signer, we hold nothing) is a possible later option for technical users and is not built.

Each business's contract and agent wallet addresses are public on the proof page and at `GET /businesses`, so anyone can check them on the explorer.

## Roadmap

| Phase | Scope | Version | Status |
|---|---|---|---|
| 1. Foundation | Repo, Circle treasury wallet, `BudgetEnforcer.sol` deployed, verified, tested | v1.1 | ✅ Built |
| 2. AP/AR engine | Three-way match, independent receipt witness, rules baseline, escalation, retry safety, confirmed settlement | v1.2 | ✅ Built, confirmed with a real payment on Arc Testnet |
| A. API auth | `X-API-Key` roles, server-side runway, body cap, rate limits | v1.2.2 | ✅ Built |
| B. Deployment | nginx + certbot + systemd at `api.ceedebooks.xyz` | v1.2.3 | ✅ Built |
| C. Proof page | Public verify button, refusal counts, on-chain check in `verify` | v1.2.4 | ✅ Built |
| Pre-flight and manifest | Invoice dry run, `agent.json` | v1.2.5 | ✅ Built |
| K1. Admin site | Wallet-signed admin at `admin.ceedebooks.xyz`, approver hand-over | v1.2.6 | ✅ Built |
| K2. Vendor portal | Signed vendor applications, wallet sign-in, vendor view, invoice metrics | v1.2.7 | ✅ Built |
| L1. Multi-business contracts | `BudgetEnforcerV2` (weekly limit, return-to-agent, commitments bound to the contract), `BudgetFactory` (one enforcer per business), `LedgerAnchor` | v1.2.8 | ✅ Built; factory and anchor deployed and verified |
| L2. Multi-tenant backend | `businesses` table, `business_id` on every table, per-business contract and agent wallet, business-bound keys and sessions, hash format 2, cross-business denial tests | v1.2.8.1 | ✅ Built |
| L3. Business onboarding | Application with wallet signature, operator accepts, hosted agent wallet per business, owner creates the contract from their own wallet, registration verified on-chain, owner checklist, operator-only outside flag, businesses on the proof page | v1.2.8.2 | ✅ Built (no outside business has used it yet) |
| L4. Shadow mode and per-business proof | A business keeps paying as today while its real invoices are mirrored as testnet USDC payments; the owner records whether they agree with each decision; a public traction summary per business (transaction links, reasoning, agreement rate) | v1.2.8.3 | ⏳ Next |
| L5. Home business on the factory | Create the home business through the factory, move the pool, archive the v1 contract as business #1. Optional and last: it moves a live pool | v1.2.8.4 | ⏳ Optional |
| E. Audit hardening | Per-business hash-chained ledger anchored on-chain, model-vs-rules log, on-chain red-team log, key-custody check, re-evaluate | v1.2.9 | ⏳ Planned |
| O. Oversight | Maker-checker, pause with reason, figures written by code | v1.2.10 | ⏳ Planned |
| F. Gateway | `POST /v1/balances` unified balance on the dashboard | v1.3.0 | ⏳ Planned |
| P. Proof and evidence | Outcomes numbers (decisions made vs escalated, human agreement rate, duplicates caught), "try it in 5 minutes" demo path, rollout records | | ⏳ Planned |
| G. Treasury brain | Economics-gated sweep, vendor screening, `CeedeBooksYield.sol` pooled USYC wrapper, EURC, CCTP | v1.3.x | ⏳ Planned |
| N. Agent-to-agent | Reference vendor client; traction counts an outside agent only | v1.3.x | ⏳ Planned |
| Q. Reach | PDF and email intake, Telegram bot, receivables, webhooks | | ⏳ Planned |
| H. Milestone escrow | `MilestoneEscrow.sol` with a `requirementsHash` fixed at funding | | ⏳ Planned, after Proof and evidence |
| Later | Agent-posted decision bond | | 💡 Idea |

**Still open in Phase 2:** Laya isn't wired into `payables.py` yet (built and tested separately; live decisions run on the rules baseline until it is). So far the pipeline has only processed self-owned flows; an outside business or agent is the next thing to land.

---

## Circle tools

| Tool | Status | Notes |
|---|---|---|
| Agent Wallet | ✅ live | Developer-controlled wallet, Arc Testnet; signs every contract call |
| USDC | ✅ live | Native ERC-20 at `0x3600000000000000000000000000000000000000` |
| Gateway | Planned (Phase F, v1.3.0) | `POST /v1/balances` |
| USYC | Planned (Phase G) | Via own pooled wrapper (Arc's Teller has a $100k / allowlist gate) |
| CCTP | Stretch | Domain 26, `minFinalityThreshold: 2000`, V2 7-param `depositForBurn` only |
| EURC | Stretch | European vendor payments |
| ~~Paymaster~~ | Dropped | Not used |

Honest count: 2 live (Agent Wallet, USDC), 4 planned (Gateway, USYC, CCTP, EURC).

---

## Contracts

### `BudgetEnforcer.sol`

The on-chain spending authority. Funds are **held by the contract**, not the agent wallet. The agent can only move them through `pay()`, inside these limits:

- **Vendor registry**: only approver-registered addresses can be paid. A wallet change means registering a new address; the old one stops working once revoked.
- **Commit-then-pay**: `commitDecision(hash)` must land in an earlier block than the `pay()` it justifies, bound to the exact payment parameters (vendor, amount, invoice, doc, category, reasoning).
- **Canonical idempotency key**: `invoiceKey = keccak256(vendor, invoiceNumber)`, so resubmitting a different file doesn't create a new key. `docHash` is a separate pointer emitted in the event.
- **Limits**: per-tx, per-day, and cumulative per-category-per-day. An unregistered category has a 0 limit (fail-closed).
- **Escalation**: the agent can park a payment; only the approver can pay it, exactly once.
- **Admin**: pause, approver-only withdraw, agent rotation, two-step approver rotation.

`reasoningHash` (this decision was logged before money moved) and the invoice key (this invoice wasn't paid twice) are deliberately separate guarantees in separate fields.

One instance per business. This deployment is not multi-tenant, and a second business is not onboarded on it. The `BudgetFactory` below creates one enforcer per business from the owner's own wallet, each with its own pool, approver, agent and limits; no business has been created through it yet, and this contract stays as the live pool and, later, an archived, still-verifiable business #1.

### `BudgetEnforcerV2.sol`, `BudgetFactory.sol`, `LedgerAnchor.sol` (v1.2.8)

- **`BudgetEnforcerV2`** keeps everything above and adds a rolling 7-day limit (`setBudget(daily, perTx, weekly)` requires `0 < perTx <= daily <= weekly`), `reopenEscalation` (approver only, only for a Pending escalation, so the agent can re-evaluate and then pay through every normal limit or escalate again), commitments bound to the contract address and chain id, and distinct agent and approver roles.
- **`BudgetFactory`**: `createBusiness(agent, daily, perTx, weekly)` deploys one enforcer per business, and the caller becomes its approver. The factory has no admin function, holds no funds and cannot touch any enforcer.
- **`LedgerAnchor`**: `anchor(head, sequence)` records a ledger head per sender, and the sequence must increase. A verifier trusts only events from a business's agent address.

### Contracts (Arc Testnet)

| Contract | Address |
|----------|---------|
| BudgetEnforcer | [`0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB`](https://explorer.testnet.arc.io/address/0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB) |
| BudgetFactory (v1.2.8) | [`0x97b9A3802bA6B258cBeF6070532a265656bb391C`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C) |
| LedgerAnchor (v1.2.8) | [`0x008217BeC86462E76126F94e50eBe68fb4a41444`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444) |
| BudgetEnforcerV2 | created per business by the factory; none created yet |
| CeedeBooksYield | not built yet |
| MilestoneEscrow | not built yet |

Live budget: 100 USDC/day, 20 USDC/tx, 50 USDC/day in category 0 (data oracle), 20 USDC/day in category 1 (infrastructure).

---

## API

Run with a **single worker**: `uvicorn backend.main:app` (the rate limiter is per-process).

**Access model.** Send the key in an `X-API-Key` header. Keys are created on the server with `python3 scripts/create_api_key.py create --role buyer --label <name>` (or `--role vendor --vendor-id N`), shown once, and stored only as SHA-256 hashes. `/decisions/*` needs no key.

**Businesses (v1.2.8.1).** A key, a vendor session and an admin session each belong to exactly one business, and every query is scoped to it: another business's ids answer 404, and its contract is never called. Create a key for a business with `--business <slug>` (default `ceedebooks`). The sign-in challenges (`/admin/auth/challenge`, `/vendor/auth/challenge`, `/apply/challenge`) take an optional `business` slug (default: the first business); the signed message names it, and an admin signature is accepted only if it recovers to that business's own `approver()`. Public reads (`/stats`, `/decisions`) cover every business by default and take `?business=<slug>` to narrow them. A business that is not `active` accepts no writes. New decisions use hash format 2, which also binds the business id and its contract address; older records keep format 1 and still verify.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /vendors` | buyer | Register a vendor (wallet address validated) |
| `GET /vendors/{id}` | buyer; a vendor for its own record | Read a vendor |
| `POST /purchase-orders` | buyer | Create a PO (number, vendor, amount, category). Duplicate PO numbers return 409 |
| `POST /receipts` | buyer | Confirm delivery of a PO. The role is set by the server from the key; `confirmed_by_role` in the body is rejected (422). One receipt per PO (409) |
| `POST /invoices` | buyer, or a vendor (key or wallet session) for its own `vendor_id` | Full pipeline: retry guard, three-way match, decision, commit-then-pay or escalate. Always pays the wallet on file; the invoice category must match the PO's; the runway is computed server-side from the pool balance and trailing spend (`treasury_runway_days` in the body is rejected). Duplicate invoice numbers return 409 |
| `GET /invoices/{id}` | buyer; a vendor for its own invoices | Status and `reasoning_hash`. Another vendor's invoice returns 404 |
| `POST /invoices/preflight` | buyer, or a vendor for its own `vendor_id` | Dry run of `POST /invoices`: returns `would_pay`, `would_hold`, `would_escalate`, `would_be_refused_by_contract` or `already_paid`, with every check and the reasons. Writes nothing (no invoice row, no audit row, no chain transaction) and returns only booleans, never balances or limits |
| `GET /admin/auth/state`, `POST /admin/auth/challenge`, `POST /admin/auth/verify`, `POST /admin/auth/logout` | public / admin session | Wallet sign-in for the admin site: a one-time challenge is signed with the wallet and accepted only if it recovers to the contract's current `approver()`. Challenge requests are rate-limited; failures count toward the failed-auth throttle |
| `GET /admin/overview`, `/admin/vendors`, `/admin/purchase-orders`, `/admin/invoices`, `/admin/actions`, `/admin/invoices/{id}/escalation` | admin session | Admin dashboard data. A buyer API key is refused (403); no session is a 401 |
| `POST /admin/actions` | admin session | Records an on-chain admin transaction after the wallet sent it. The server checks the chain itself (to the contract, from the admin, successful); settling an escalation also needs the on-chain event to carry that invoice's own reasoning hash |
| `POST /apply/challenge`, `POST /apply` | public | Apply to become a vendor. A one-time challenge is signed with the payee wallet (EIP-191 `personal_sign`, nothing sent on-chain); the signature must recover to the wallet being applied for. A sign-in signature cannot be replayed here. Applications are private, 1 pending per wallet, 3 pending per IP, 5 per hour per IP |
| `POST /vendor/auth/challenge`, `POST /vendor/auth/verify`, `POST /vendor/auth/logout` | public / vendor | Vendor wallet sign-in. Only a wallet on file as a vendor gets a session (15 minutes idle, 2 hours at most, one live session per vendor); the challenge answers the same for every address. Send the token as `Authorization: Bearer ...`. A session is a vendor principal: it can use the vendor routes above, never `/vendors`, `/receipts`, `/purchase-orders` or `/admin/*` |
| `GET /vendor/me` | vendor | The vendor's own record, on-chain approval, purchase orders (with receipt and invoiced flags) and invoices. Nobody else's |
| `GET /admin/applications`, `POST /admin/applications/{id}/accept`, `.../reject` | admin session | Review applications. Accepting only creates the vendor record: it still cannot be paid until the admin approves its wallet on-chain |
| `GET /.well-known/agent.json` | public | Machine-readable manifest: the vendor flow step by step, endpoints with their roles, and the guarantees, so another agent can discover how to invoice CeedeBooks |
| `GET /decisions/{hash}` | public | The stored audit record, including the full reasoning text |
| `GET /decisions/{hash}/verify` | public | Recomputes the SHA-256 of the stored `hash_input`, and (`onchain`) checks that the same hash is in a BudgetEnforcer event of the recorded transaction |
| `GET /decisions` | public | Latest audit entries, newest first (no reasoning text) |
| `POST /business/apply/challenge`, `POST /business/apply` | public | Apply to register a business. The challenge is signed with the wallet that will own it (EIP-191, nothing sent on-chain); it cannot be replayed from a vendor sign-in or application. 1 pending per wallet, 2 pending per client, 3 per hour per client |
| `POST /business/status/challenge`, `POST /business/status` | public (signed) | An owner reads only the applications made with their own wallet, and for an accepted one the agent wallet and the factory to call |
| `POST /business/register` | public | `{application_id, tx_hash}` after the owner created the contract. Goes live only if the chain shows our factory made it, the applicant's wallet is its approver and the issued agent wallet is its agent |
| `GET /businesses` | public | Active businesses with contract and agent wallet addresses, so anyone can check them on the explorer |
| `GET /operator/business-applications`, `POST .../{id}/accept`, `POST .../{id}/reject`, `POST /operator/businesses/{id}/external` | operator session | The home business's admin only. Accepting creates the hosted agent wallet. `external` (an outside party) is set only here and never for the home business |
| `GET /admin/onboarding` | admin session | The owner's own setup checklist: agent wallet gas, pool funded, a category limit set, a vendor, a purchase order. Unreadable steps show as unknown, never as done |
| `GET /stats` | public | Counts of paid, held and escalated agent decisions (manual entries are reported separately and never counted as agent decisions), plus `submissions`: invoices processed, USDC paid and duplicates caught, split by origin |

Hardening: amounts are exact decimals (positive, at most 6 places); request bodies over `MAX_BODY_BYTES` return 413; per-IP rate limit (`RATE_LIMIT_PER_MIN`) and a stricter limit on failed keys (`FAILED_AUTH_PER_MIN`) return 429; CORS allows only `CORS_ORIGINS` (default `https://ceedebooks.xyz`). Behind a reverse proxy set `TRUST_PROXY=1` so limits apply per real client.

Deploy steps (nginx + certbot + systemd, and the admin site): [deploy/DEPLOY.md](deploy/DEPLOY.md).

Limits: runway uses ledger history only (paid invoices in the last 30 days), so a fresh ledger gets the 365-day cap.

`verify` checks the stored record and, where a transaction is recorded, the matching on-chain event. A hash-chain link between entries is planned (Phase E).

---

## Project Structure

```
ceedebooks/
├── agent/
│   ├── config.py           # env loader
│   ├── categories.py       # spend category enum (off-chain names for on-chain uint8 keys)
│   ├── llm.py              # openai/gpt-oss-120b escalation narrative (Groq)
│   ├── decision_log.py     # canonical-JSON SHA-256 audit log, hash-before-action
│   ├── contract.py         # reads + Circle-signed writes, wait_for_transaction
│   ├── payables.py         # three-way match, rules baseline, commit-then-pay, retry guard
│   ├── treasury.py         # server-side runway from the pool balance and trailing spend
│   ├── wallet_setup.py     # one-off Circle developer-controlled wallet creation
│   └── tests/
│       ├── test_payables.py
│       ├── test_api_auth.py
│       ├── test_audit_public.py
│       ├── test_preflight.py
│       ├── test_admin.py
│       ├── test_portal.py
│       ├── test_multibusiness.py
│       ├── test_onboarding.py
│       └── conftest.py        # FakeChain: the chain is never real in tests
├── scripts/
│   ├── create_api_key.py   # create / revoke API keys (printed once, stored hashed)
│   └── pay_domain.py       # one-off: manual commit-then-pay of the ceedebooks.xyz domain bill
├── .github/workflows/ci.yml  # forge test + pytest on every push
├── backend/
│   ├── main.py             # FastAPI: vendors, POs, receipts, invoices, audit verify
│   ├── auth.py             # API-key auth, roles, failed-key throttling
│   ├── ratelimit.py        # in-memory sliding-window limiter
│   ├── admin_auth.py       # wallet sign-in for the admin site (session bound to the on-chain approver)
│   ├── vendor_auth.py      # wallet signatures for vendor applications and vendor sign-in
│   └── models.py           # SQLite schema + queries (incl. api_keys)
├── admin/                  # admin site: static app, wallet call encoding, Node tests
├── portal/                 # vendor portal and business registration page (business.html), Node tests
├── site/                   # public proof page and its in-browser hash check
├── deploy/                 # nginx configs, systemd unit, DEPLOY.md
├── contracts/
│   ├── BudgetEnforcer.sol      # v1, deployed
│   ├── BudgetEnforcerV2.sol    # multi-business version, created by the factory
│   ├── BudgetFactory.sol       # one enforcer per business, deployed
│   └── LedgerAnchor.sol        # public ledger-head anchor, deployed
├── test/
│   ├── BudgetEnforcer.t.sol
│   ├── BudgetEnforcerV2.t.sol
│   ├── BudgetFactory.t.sol
│   ├── LedgerAnchor.t.sol
│   └── mocks/MockUSDC.sol
├── foundry.toml
├── lib/                    # forge-std, cloned in Setup, git-ignored
├── README.md
└── .env.example
```

---

## Setup

```bash
git clone https://github.com/MusaAis/CeedeBooks
cd CeedeBooks
cp .env.example .env   # fill in real values

pip install -r agent/requirements.txt
git clone --depth 1 https://github.com/foundry-rs/forge-std lib/forge-std   # test library, git-ignored

set -a; source .env; set +a
```

Use the Canteen RPC (`arc-canteen rpc-url`) for `ARC_TESTNET_RPC_URL`, since testnet traction is counted through it.

### Circle treasury wallet

```bash
python agent/wallet_setup.py
```

Reuses an already-registered Circle entity secret (don't generate a new one for an account that already has one; that's a rotation flow). Creates a wallet set and one Arc Testnet wallet and appends its ID and address to `.env`. Fund it at [faucet.circle.com](https://faucet.circle.com).

### Tests

```bash
forge test -vv
pytest agent/tests/
```

**69 Foundry tests** (24 for the v1 contract, 35 for `BudgetEnforcerV2`, 6 for `BudgetFactory`, 4 for `LedgerAnchor`), one per revert path or acceptance scenario: unregistered and revoked vendor, over per-tx / daily / category limits, unset category (fail-closed), same invoice resubmitted as a different file, missing / reused / mismatched commit, crash-and-retry, simulated prompt injection (contract refuses even if the agent were fooled), the escalate → approve / reject flow, escalated invoices blocked from direct payment, pause, withdraw, agent rotation, two-step approver rotation, and the `reasoningHash` round-trip via the `PaymentMade` event.

**208 Python tests** covering the three-way match, rules-baseline decisions (pay / hold / escalate), retry safety (reprocessing a paid invoice is a no-op), API access control (every denied path), server-side runway, input validation, the public audit endpoints and on-chain verification, the invoice pre-flight dry run (it writes nothing and reveals no balances or limits), and the admin wallet sign-in and routes. Browser-side tests check the proof page's hash recomputation (`site/verify.test.js`) and the admin app's call encoding and rendering (`admin/*.test.js`). The vendor portal suite covers: a signature from another wallet is refused, a sign-in signature cannot be replayed as an application, challenges are single use, the pending and per-IP limits, accepting once only, vendor sessions seeing only their own data, sessions ending on wallet change, idle time and logout, a vendor never labelling its own origin, and the metrics split by origin.

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

cast send $BUDGET_ENFORCER_ADDRESS "setVendor(address,bool)" <VENDOR_ADDRESS> true \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY
```

Amounts are USDC in its 6-decimal ERC-20 form (`100000000` = $100.00).

**Multi-business contracts (v1.2.8).** Neither has an owner, so the deployer key has no power over them. Deployed Oct 4, 2026:

```bash
forge create contracts/LedgerAnchor.sol:LedgerAnchor \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY --broadcast \
  --verify --verifier blockscout --verifier-url https://explorer.testnet.arc.io/api/

forge create contracts/BudgetFactory.sol:BudgetFactory \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $DEPLOYER_PRIVATE_KEY --broadcast \
  --verify --verifier blockscout --verifier-url https://explorer.testnet.arc.io/api/ \
  --constructor-args 0x3600000000000000000000000000000000000000
```

Deployment transactions: [LedgerAnchor `0x45c031cd...9f9f4d`](https://explorer.testnet.arc.io/tx/0x45c031cda95d0e688b967e8f2a52abd4d00b801bb1240f4a314477497a9f9f4d), [BudgetFactory `0x4e69b06e...b61fbb`](https://explorer.testnet.arc.io/tx/0x4e69b06eac8b349a320ca29276e3739177409db018af46a84659f32237b61fbb); both verified on the explorer. A business owner then calls `createBusiness(agent, daily, perTx, weekly)` from their own wallet (Phase L3); each business's enforcer address will be listed in the Contracts table.

---

## Changelog

**v1.2.8.2: Phase L3, business onboarding (built; no outside business has used it yet)**
- A business applies with a wallet signature (`/business/apply`), the operator accepts it in the admin site's new Businesses tab, and the server creates the business's hosted agent wallet (a Circle wallet in its own wallet set). The owner then creates the contract with one transaction to `BudgetFactory.createBusiness` from their own wallet, choosing their own daily, per-payment and weekly limits at `portal.ceedebooks.xyz/business.html`
- The server activates a business only after `verify_business_creation` proves it from the chain: the transaction succeeded and went to our factory, exactly one `BusinessCreated` event is in it, the factory lists the new contract as a business, and the contract's own `approver()` and `agent()` match the applicant's wallet and the issued agent wallet. Checked against a real factory on a local node, and against crafted receipts for every refusal
- Custody is stated plainly at application, at acceptance, in the README and in `agent.json` (see Custody trade-off). Each business funds its own pool and its agent wallet's fee balance; the operator funds no business
- Operator-only controls: accept or reject, and the `external` flag (never settable on the home business). `GET /businesses`, the proof page's new Businesses section (contract and agent wallet links, per-business counts and feed) and `/stats` counts of outside businesses
- Funding: the admin site's **Add funds** button sends a USDC token transfer from the owner's wallet into the business's contract. A plain wallet Send to the contract fails, because the contracts accept no native value
- Admin site: any business's owner signs in at `/?business=<slug>`; transactions go to that business's contract, not a fixed one; a setup checklist for the owner. Vendor portal: `/?business=<slug>`
- Hardening: one pending application per wallet and 2 per client, an hourly cap, single-use challenges bound to their purpose, a crash after the wallet exists never creates a second wallet, a Circle failure accepts nothing
- 36 new Python tests (208 in total) and new Node tests for the owner page, the operator tab and the business parameter; CI runs them
- Not in this release: moving the home business onto a factory contract (optional, last), a self-held agent mode. Hosted wallet creation was tested with a mock only; if Circle refuses it, accepting a business shows a clear error and changes nothing

**v1.2.8.1: Phase L2, multi-tenant backend (no new business yet)**
- `businesses` table; business 1 is the live v1 contract, recorded in the database (not only in `.env`). `business_id` on vendors, purchase orders, receipts, invoices, applications, rejected submissions, admin actions, API keys and the audit log. Databases from v1.2.7 migrate on first start (every existing row becomes business 1; PO and invoice numbers become unique per business, not globally)
- `agent/contract.py` has no module-level contract any more: `chain_for(business)` returns that business's v1 or v2 handle, and every read and Circle-signed write goes through it. The v2 commitment (bound to the contract address and chain id) was checked against the real v2 contract on a local node
- Every model query takes `business_id` as a required argument. Keys, vendor sessions and admin sessions are bound to one business; admin sign-in accepts only that business's on-chain `approver()`, and a session ends if that approver changes or its chain cannot be read
- Hash format 2 binds the business id and contract address into every new decision; format 1 records still verify. The proof page checks both, and trusts a v2 record's contract only if the factory created it
- `create_api_key.py --business`, `?business=` on public reads, `agent.json` lists active businesses
- 32 new Python tests (172 in total; the existing ones now run against a fake chain), including a migration from a real v1.2.7 schema, cross-business denial on every route family and per-business session and chain-failure tests; new Node tests for format-2 records. The isolation tests were checked by breaking the scoping on purpose
- Not in this release: creating a business, deploying anything, moving the pool (Phase L3). The live pool, API behaviour and the admin and portal sites are unchanged for business 1

**v1.2.8: Phase L1, multi-business contracts (factory and anchor deployed and verified on Arc Testnet)**
- Deployed and verified: `BudgetFactory` [`0x97b9A3802bA6B258cBeF6070532a265656bb391C`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C) and `LedgerAnchor` [`0x008217BeC86462E76126F94e50eBe68fb4a41444`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444). Checked on-chain after deployment: the factory's USDC address is the Arc USDC, `businessCount` is 0, the anchor's sequence starts at 0. `BudgetEnforcerV2` has no standalone deployment: the factory creates one per business
- `BudgetEnforcerV2.sol`: weekly rolling limit via `setBudget(daily, perTx, weekly)`, `reopenEscalation` (approver only, Pending only), commitments bound to the contract and chain, distinct agent and approver roles
- `BudgetFactory.sol`: one enforcer per business, the caller becomes its approver; no admin and no funds
- `LedgerAnchor.sol`: public anchor for a ledger head, sequence must increase per sender
- 45 new Foundry tests (69 in total); the deployed v1 contract and its 24 tests are unchanged
- README audited against the repo: current-version line, "what is CeedeBooks" no longer implies escrow and yield are built, architecture labels, roadmap rows and versions, Circle tools table, project tree, API table (admin logout), Setup (forge-std), live snapshot replaced by a pointer to the proof page
- `lib/` is git-ignored so a local forge-std clone is never committed
- The running API and the live v1 contract are unchanged; no business has been created through the factory yet (Phase L2 and L3)

**v1.2.7.1: README polish (docs only, no code change)**
- A 60-second reviewer tour with a copy-paste verification command
- A "Trust model and limits" section: what the contract guarantees, what it does not, one operator, database audit log, testnet only
- The tagline no longer implies escrow and yield are built; the Planned list and Roadmap now show the multi-business redeploy, audit hardening, oversight, treasury, proof and reach phases with their order
- Contracts section describes the planned factory design; Circle tools show the honest live and planned count
- Project tree lists `test_portal.py`

**v1.2.7: Phase K2, vendor portal**
- Vendors apply with a wallet signature (proof of control of the payee address) at `portal.ceedebooks.xyz`; the admin site has a new Applications tab. Accepting creates the vendor record only; approving the wallet on-chain stays a separate admin signature. `/vendors` stays buyer-only
- Vendors sign in with the same wallet (15-minute sessions, no keys issued) and see only their own purchase orders and invoices; dry run first, then submit; each decision links to the proof page (`#check=<hash>` opens the check directly)
- Spam controls on applications: 1 pending per wallet, 3 pending per IP, 5 per hour per IP, body cap; applications and contact details are private
- Invoices now carry an `origin` (`agent`, `manual`, `demo`); the hand-run domain payment is relabelled `manual` on first start. Only the buyer can label a demo run. Rejected duplicate submissions are counted (counts only, no vendor data)
- `/stats` and the proof page show invoices processed, USDC paid and duplicates caught, split by origin
- Agent manifest updated: vendors apply with a signature; wallet sign-in described
- One look across the proof page, the vendor portal and the admin site: the CeedeBooks logo (favicon and touch icon included), the same typeface and palette, the receipt as the action card. The portal is mobile first: the headline, then the Connect button, then the reassurance points
- 31 new Python tests (140 in total) and a Node test for the portal; CI runs it

**v1.2.6.2: admin site on phones, README brought up to date**
- Admin header stays on one row (the wallet address shortens instead of pushing Sign out down)
- Tables become labelled cards under 40rem, so the reasoning hash and action buttons are no longer clipped; action buttons are larger to tap
- README: built features moved out of Planned, roadmap now lists every phase with its version and status, live snapshot added to Traction, stale `verify` note fixed, test count corrected to 109
- Test: table cells carry their column label (the phone layout reads it)

**v1.2.6.1: admin overview fix**
- Fixed the admin Overview and Limits pages, which showed no spending categories because a name in `backend/main.py` hid the category list. Added a regression test that runs the real chain snapshot

**v1.2.6: Phase K1, admin site**
- `admin.ceedebooks.xyz`: a private admin app. Before a wallet connects it shows only a Connect wallet button; every action after sign-in is a wallet signature, and the admin never types an API key
- Admin identity is the contract itself: the `/admin` API accepts a session only if its signature recovers to the current on-chain `approver()`, and a session ends the moment the approver changes (or the chain cannot be read). One-time challenges, 15-minute idle and 2-hour absolute session limits, tokens held in browser memory only
- Admin actions: add and approve or revoke vendors on-chain, create purchase orders, confirm receipts, approve or reject escalated payments, set category limits, pause and resume payments, view the activity log. On-chain steps are signed in the admin's own wallet; the server then verifies each transaction on-chain before recording it
- A wallet proposed with `proposeApprover` can take over with one **Accept admin role** button, so the approver key can leave the server
- `agent/contract.py` reads `approver`, `pendingApprover`, `paused` and the limits; new `admin_actions` table; CORS allows the admin origin and the `Authorization` header
- Tests: wrong signer, non-approver, replayed and expired challenges, approver rotated mid-session, unreadable chain, every admin route without a session, a buyer API key on admin routes, transaction verification and escalation settling (Python); call encoding and the sign-in and approval flows with a mocked wallet (Node, run by CI)

**v1.2.5.1: proof page wording and README**
- The ceedebooks.xyz domain payment is now presented as what it is: a real bill, paid by card and reimbursed through `BudgetEnforcer`, run by hand and shown apart from the agent's decisions
- Fewer repeated network caveats on the proof page; fixed a typo in its "does not prove" list
- README test counts, project tree and status line brought up to date (79 Python tests)

**v1.2.5: invoice pre-flight and agent manifest**
- `POST /invoices/preflight`: a dry run of the payment pipeline that tells a vendor (or the buyer) whether an invoice would be paid, held or escalated, and why, before anything is saved or sent on-chain. It also flags an invoice that would exceed today's budget, which the contract would otherwise refuse at payment time
- `GET /.well-known/agent.json`: public manifest of the vendor flow and guarantees for agent-to-agent use
- The invoice access checks are shared between `/invoices` and `/invoices/preflight`, with 17 new tests (79 in total)

**v1.2.4: Phase C, public proof page**
- Static proof page at `ceedebooks.xyz` (`site/`): live decision counts, a feed of recent decisions, and a Verify button that recomputes the SHA-256 in the browser, checks the displayed record is what was hashed, and reads the matching `PaymentMade`, `PaymentEscalated` or `DecisionLogged` event straight from a public Arc RPC
- New public endpoints `GET /stats` and `GET /decisions`; `GET /decisions/{hash}/verify` now also checks the recorded on-chain transaction (this closes the "verify is off-chain only" limitation)
- Audit rows now store the on-chain transaction hash (`chain_tx_hash`; existing databases are migrated on startup)
- Held decisions are now also written on-chain with `logDecision`, so refusals are provable like payments (best effort: a failed anchor never blocks or changes the hold)
- Page logic tested in Node (`site/verify.test.js`, run by CI) against records produced by the Python hashing code

**v1.2.3: Phase B, deployment**
- Added `deploy/` (nginx site config, systemd unit, DEPLOY.md): API behind nginx with a Let's Encrypt certificate at `api.ceedebooks.xyz`, one uvicorn worker under systemd, `TRUST_PROXY=1`
- README status line and test description brought up to date

**v1.2.2: Phase A, API auth and safe intake**
- `X-API-Key` authentication with `buyer` and `vendor` roles (keys stored as SHA-256 hashes), keyless read-only `/decisions/*`; `scripts/create_api_key.py` to create and revoke keys
- Receipt role is set by the server from the key, never from the body; vendors cannot create vendors, POs or receipts, and can submit and read only their own invoices
- Treasury runway computed server-side (`agent/treasury.py`) and removed from the request body; fails closed to 0 if the pool balance cannot be read
- Invoice pays the wallet on file; invoice category must match the PO's
- Exact-decimal amounts, 413 body cap, per-IP rate limit, failed-key throttle, CORS limited to the site origin, internal errors no longer returned to clients
- New tests in `agent/tests/test_api_auth.py` covering every denied path

**v1.2.1: Phase 2 follow-up**
- Added `agent/categories.py` (the README already listed it) and registered category 1 (infrastructure) on-chain
- Added `.github/workflows/ci.yml`: `forge test` and `pytest` on every push, plus a CI badge
- Pinned `agent/requirements.txt` to exact versions
- Tests no longer poll Circle: `wait_for_transaction` is mocked, so the suite runs offline and with newer Circle SDK versions
- Added a Traction section and `scripts/pay_domain.py`; first self-owned vendor bill (the `ceedebooks.xyz` domain) paid through `BudgetEnforcer`

**Phase 2: AP/AR engine**
- Added FastAPI intake (vendors, POs, receipts, invoices, decisions, verify)
- Added three-way match with PO number, amount tolerance, independent receipt witness, vendor wallet match
- Added rules-baseline pay / hold / escalate with LLM narrative on escalations only
- Added retry guard (`paid` check on-chain before any action) and confirmed-commit-before-pay
- Audit log now hashes canonical JSON, stores the exact `hash_input`, and records `model_used` explicitly
- Added `wait_for_transaction`: every commit, payment, and escalation is polled to a terminal on-chain state before being trusted or recorded — closes a phantom-payment gap where a reverted transaction could have been logged as paid
- Added input validation on wallet addresses (0x + 40 hex), a clean 409 on duplicate invoice numbers, and `GET /vendors/{id}` / `GET /decisions/{hash}` for inspecting stored records directly
- Confirmed live on Arc Testnet: a real invoice processed end-to-end through the deployed contract, vendor balance verified on-chain

**Phase 1: Foundation**
- `BudgetEnforcer.sol` deployed and verified on Arc Testnet; 24 Foundry tests
- Circle treasury wallet, Groq health check, Canteen RPC in `.env.example`

---

## Documented bugs & gotchas

| # | Area | Symptom | Cause | Fix |
|---|---|---|---|---|
| 1 | `forge create` | `Constructor argument count mismatch: expected N but got N+k` | `--constructor-args` swallows every token after it, including later flags | Put `--constructor-args ...` **last** |
| 2 | verification | `Params 'module' and 'action' are required parameters` from `testnet.arcscan.app` | Open upstream bug (`circlefin/arc-node#210`) | Verify via `--verifier blockscout --verifier-url https://explorer.testnet.arc.io/api/` |
| 3 | Laya | Model card looks production-ready; the base checkpoint isn't | Base `laya` scores **0.362 zero-shot** on typed-decisions, below majority-class guessing (0.461) | Never gate a real decision on the base checkpoint; fine-tune on your own schema |
| 4 | Groq | LLM calls fail silently while the process looks healthy | `groq/compound-mini` decommissioned Sep 21, 2026; `llama-3.3-70b-versatile` moved to Enterprise-only on developer keys; a try/except hid the failure | Health-check the exact model string; target `openai/gpt-oss-120b` |
| 5 | USYC | Deposits revert / can't clear eligibility | Arc's USYC Teller has a $100k minimum + non-US-institution allowlist | Route through one pooled wrapper allowlisted once |
| 6 | CCTP on Arc | Attestation stuck at "pending" | Arc needs `minFinalityThreshold: 2000`, not `1000` | Hardcode `2000` |
| 7 | CCTP on Arc | Silent revert with no error data | Arc only supports the V2 7-parameter `depositForBurn` | Confirm the V2 selector explicitly |
| 8 | Circle wallets | A payment looked successful but reverted on-chain | Circle's `contractExecution` API returns a transaction ID on *acceptance*, not on-chain confirmation — calling `pay()` right after `commitDecision()` can fire before the commit is actually mined | Poll `get_transaction` to a terminal state (`COMPLETE`/`FAILED`/`CANCELLED`) before trusting any result |

---

## Design notes for reviewers

- **`invoiceKey` excludes amount on purpose.** It's derived from `(vendor, invoiceNumber)` only, so altering the amount on a resubmission can't get a second payment through.
- **`categoryDailyLimit` defaults to 0.** An unregistered category is fail-closed.
- **Escalated invoices are locked out of `pay()`.** Once `escalate()` runs for a key, `pay()` for the same key reverts with `"Invoice escalated"`.
- **Gas and off-chain costs (LLM inference, hosting) are not `pay()` categories.** Only transactions that move USDC through the contract belong there; the rest are off-chain ledger entries, never a phantom "paid".
- **A retry can't pay twice.** `process_invoice()` checks the contract's own `paid` mapping first.
- **A transaction ID isn't a result.** Every commit, payment, and escalation is confirmed to a terminal state before anything is logged as succeeded — see bug #8 above.
- **Escalation reasons come from the rules, not the model.** A failed match is escalated with the rule that failed; the LLM is only asked to narrate vendor-registration escalations.

---

## Builder

**Musa Ali**, CS student at Federal University Dutse (FUD). Builder, PenTester & Dev. Appointed
**Lepton Peer Mentor** by Canteen.

- **3rd** place at Lepton, **Standout** at Agora with [@AgoraFX](https://github.com/MusaAis/AgoraFX)
- X: [@Musa_Ais](https://x.com/Musa_Ais)
- GitHub: [@MusaAis](https://github.com/MusaAis)

---

## License

MIT
