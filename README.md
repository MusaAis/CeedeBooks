# CeedeBooks

![Release](https://img.shields.io/github/v/release/MusaAis/CeedeBooks?label=release)
![CI](https://github.com/MusaAis/CeedeBooks/actions/workflows/ci.yml/badge.svg)
![License](https://img.shields.io/badge/license-MIT-blue)
![Foundry tests](https://img.shields.io/badge/forge%20tests-24%2F24%20passing-brightgreen)
![Python tests](https://img.shields.io/badge/pytest-109%2F109%20passing-brightgreen)
![Network](https://img.shields.io/badge/Arc-Testnet-informational)

*"Ceede" means money in Pulaar/Fulfulde.*

An autonomous financial-operations agent for African SMEs on Arc: pays invoices, runs contractor milestone escrow, and parks idle treasury into yield, with spending limits enforced **on-chain** (never in a prompt) and every decision hash-logged before money moves.

Built for the **Tameion Agents Hackathon** (Canteen × Circle × Arc), Sep 27 – Oct 10, 2026.

**Current: v1.2.6.2. The AP/AR engine is live on Arc Testnet behind a key-authenticated HTTPS API (`api.ceedebooks.xyz`), with a public proof page (`ceedebooks.xyz`) and a wallet-signed admin site (`admin.ceedebooks.xyz`). No outside business or paying user yet: every payment so far is self-owned.** See [Roadmap](#roadmap) and the [Changelog](#changelog).

---

## What is CeedeBooks

Most African SMEs run their finances the way most small businesses everywhere do: a spreadsheet, a WhatsApp thread with a bookkeeper, and someone manually checking a bank app before every payment.
CeedeBooks is an agent that takes over the repetitive parts of that job (validating and paying invoices, tracking contractor milestones, moving idle cash into yield when it isn't needed for a few days) while the business owner keeps hard, contract-level control over what the agent is allowed to spend, on whom, and how much.

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

**Planned**

- Vendor portal with signed vendor applications (v1.2.7)
- Hash-chained audit ledger, on-chain red-team log, key-custody check, re-evaluate action for stuck invoices (v1.2.8)
- Circle Gateway unified balance (v1.3.0)
- Idle-treasury auto-yield via a pooled USYC wrapper
- Agent-to-agent reference client for vendors (demo runs labelled `demo`)
- Contractor milestone escrow, only if time allows
- Laya fast-path decision model (kit built separately, not wired in)

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
| Test suites | 24 Foundry + 109 Python tests + browser-side JS tests, run by CI on every push |
| First self-owned vendor bill paid through the contract | `ceedebooks.xyz` registration, 2.20 USDC, category 1 |
| Outside businesses or paying users | None yet |

**Live snapshot** (read from the admin Overview on Oct 4, 2026; the proof page is the live source):

| Pool balance | Paid invoices | Escalations waiting | Vendors | Purchase orders |
|---|---|---|---|---|
| 108.79 USDC (testnet) | 2 | 3 | 2 | 2 |

The escalations are self-owned test invoices that the agent parked for review, as designed. Testnet USDC has no market value.

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
- **Tested, not just described.** 24 Foundry tests plus 109 Python tests, both passing against the deployed contract and the live decision pipeline.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      CeedeBooks Agent                            │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Treasury Brain   │  AP/AR Engine    │  Contractor Manager       │
│  (Phase 3)        │  (Phase 2: BUILT)│  (Phase 4)                │
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
| K2. Vendor portal | Signed vendor applications, submission metrics | v1.2.7 | ⏳ Next |
| E. Audit hardening | Hash-chained ledger, on-chain red-team log, key-custody check, re-evaluate | v1.2.8 | ⏳ Planned |
| F. Gateway | `POST /v1/balances` unified balance on the dashboard | v1.3.0 | ⏳ Planned |
| G. Treasury brain | `CeedeBooksYield.sol` pooled USYC wrapper, runway alerts | v1.3.x | ⏳ Planned |
| N. Agent-to-agent | Reference vendor client; traction counts an outside agent only | v1.3.x | ⏳ Planned |
| H. Milestone escrow | `MilestoneEscrow.sol` with a `requirementsHash` fixed at funding | v1.4.0 | 💡 Only if time allows |
| Submit | Demo video, final README, submission (due Oct 10, 11:59 PM ET) | | ⏳ Planned |
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
| ~~Paymaster~~ | Dropped | Not supported on Arc |

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

One instance per business. This deployment is not multi-tenant: onboarding a second business means deploying a second `BudgetEnforcer`.

### Contracts (Arc Testnet)

| Contract | Address |
|----------|---------|
| BudgetEnforcer | [`0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB`](https://explorer.testnet.arc.io/address/0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB) |
| CeedeBooksYield | not built yet |
| MilestoneEscrow | not built yet |

Live budget: 100 USDC/day, 20 USDC/tx, 50 USDC/day in category 0 (data oracle), 20 USDC/day in category 1 (infrastructure).

---

## API

Run with a **single worker**: `uvicorn backend.main:app` (the rate limiter is per-process).

**Access model.** Send the key in an `X-API-Key` header. Keys are created on the server with `python3 scripts/create_api_key.py create --role buyer --label <name>` (or `--role vendor --vendor-id N`), shown once, and stored only as SHA-256 hashes. `/decisions/*` needs no key.

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /vendors` | buyer | Register a vendor (wallet address validated) |
| `GET /vendors/{id}` | buyer; a vendor for its own record | Read a vendor |
| `POST /purchase-orders` | buyer | Create a PO (number, vendor, amount, category). Duplicate PO numbers return 409 |
| `POST /receipts` | buyer | Confirm delivery of a PO. The role is set by the server from the key; `confirmed_by_role` in the body is rejected (422). One receipt per PO (409) |
| `POST /invoices` | buyer, or a vendor for its own `vendor_id` | Full pipeline: retry guard, three-way match, decision, commit-then-pay or escalate. Always pays the wallet on file; the invoice category must match the PO's; the runway is computed server-side from the pool balance and trailing spend (`treasury_runway_days` in the body is rejected). Duplicate invoice numbers return 409 |
| `GET /invoices/{id}` | buyer; a vendor for its own invoices | Status and `reasoning_hash`. Another vendor's invoice returns 404 |
| `POST /invoices/preflight` | buyer, or a vendor for its own `vendor_id` | Dry run of `POST /invoices`: returns `would_pay`, `would_hold`, `would_escalate`, `would_be_refused_by_contract` or `already_paid`, with every check and the reasons. Writes nothing (no invoice row, no audit row, no chain transaction) and returns only booleans, never balances or limits |
| `GET /admin/auth/state`, `POST /admin/auth/challenge`, `POST /admin/auth/verify` | public | Wallet sign-in for the admin site: a one-time challenge is signed with the wallet and accepted only if it recovers to the contract's current `approver()`. Challenge requests are rate-limited; failures count toward the failed-auth throttle |
| `GET /admin/overview`, `/admin/vendors`, `/admin/purchase-orders`, `/admin/invoices`, `/admin/actions`, `/admin/invoices/{id}/escalation` | admin session | Admin dashboard data. A buyer API key is refused (403); no session is a 401 |
| `POST /admin/actions` | admin session | Records an on-chain admin transaction after the wallet sent it. The server checks the chain itself (to the contract, from the admin, successful); settling an escalation also needs the on-chain event to carry that invoice's own reasoning hash |
| `GET /.well-known/agent.json` | public | Machine-readable manifest: the vendor flow step by step, endpoints with their roles, and the guarantees, so another agent can discover how to invoice CeedeBooks |
| `GET /decisions/{hash}` | public | The stored audit record, including the full reasoning text |
| `GET /decisions/{hash}/verify` | public | Recomputes the SHA-256 of the stored `hash_input`, and (`onchain`) checks that the same hash is in a BudgetEnforcer event of the recorded transaction |
| `GET /decisions` | public | Latest audit entries, newest first (no reasoning text) |
| `GET /stats` | public | Counts of paid, held and escalated agent decisions; manual entries are reported separately and never counted as agent decisions |

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
│       └── test_admin.py
├── scripts/
│   ├── create_api_key.py   # create / revoke API keys (printed once, stored hashed)
│   └── pay_domain.py       # one-off: manual commit-then-pay of the ceedebooks.xyz domain bill
├── .github/workflows/ci.yml  # forge test + pytest on every push
├── backend/
│   ├── main.py             # FastAPI: vendors, POs, receipts, invoices, audit verify
│   ├── auth.py             # API-key auth, roles, failed-key throttling
│   ├── ratelimit.py        # in-memory sliding-window limiter
│   ├── admin_auth.py       # wallet sign-in for the admin site (session bound to the on-chain approver)
│   └── models.py           # SQLite schema + queries (incl. api_keys)
├── admin/                  # admin site: static app, wallet call encoding, Node tests
├── site/                   # public proof page and its in-browser hash check
├── deploy/                 # nginx configs, systemd unit, DEPLOY.md
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
git clone https://github.com/MusaAis/CeedeBooks
cd CeedeBooks
cp .env.example .env   # fill in real values

pip install -r agent/requirements.txt
forge install foundry-rs/forge-std   # if not already present

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

**24 Foundry tests**, one per revert path or acceptance scenario: unregistered and revoked vendor, over per-tx / daily / category limits, unset category (fail-closed), same invoice resubmitted as a different file, missing / reused / mismatched commit, crash-and-retry, simulated prompt injection (contract refuses even if the agent were fooled), the escalate → approve / reject flow, escalated invoices blocked from direct payment, pause, withdraw, agent rotation, two-step approver rotation, and the `reasoningHash` round-trip via the `PaymentMade` event.

**109 Python tests** covering the three-way match, rules-baseline decisions (pay / hold / escalate), retry safety (reprocessing a paid invoice is a no-op), API access control (every denied path), server-side runway, input validation, the public audit endpoints and on-chain verification, the invoice pre-flight dry run (it writes nothing and reveals no balances or limits), and the admin wallet sign-in and routes. Browser-side tests check the proof page's hash recomputation (`site/verify.test.js`) and the admin app's call encoding and rendering (`admin/*.test.js`).

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

---

## Changelog

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
