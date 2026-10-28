# Project structure

```
ceedebooks/
├── agent/
│   ├── config.py           # env loader
│   ├── categories.py       # per-business category rules (names are off-chain; the on-chain uint8 key and its limit are the owner's)
│   ├── llm.py              # openai/gpt-oss-120b (Groq): escalation narrative and short completions
│   ├── second_opinion.py   # the model's recorded second opinion beside the rules verdict (v1.2.9.3)
│   ├── ledger.py           # signed hash-chained ledger per business (v1.2.9)
│   ├── anchor.py           # on-chain anchoring of each ledger head (v1.2.9.2)
│   ├── gateway.py          # read-only Circle Gateway balance of an agent wallet (v1.2.10)
│   ├── figures.py          # code-written figures for model text: placeholders, no digits of its own (v1.3.0)
│   ├── safe_fetch.py       # fetches one allowlisted https JSON document: public addresses only, no redirects, size and time caps (v1.3.2.1)
│   ├── evidence.py         # a GitHub pull request turned into facts and an evidence hash (v1.3.2.1)
│   ├── escrow_chain.py     # read-only view of a MilestoneEscrow contract and the registration checks (v1.3.2.1)
│   ├── escrow_release.py   # the agent's release verdict, decision record and the two transactions (commit, release) for an escrow (v1.3.2.3)
│   ├── decision_log.py     # canonical-JSON SHA-256 audit log, hash-before-action
│   ├── contract.py         # reads + Circle-signed writes (incl. the Gateway approve and deposit), wait_for_transaction
│   ├── payables.py         # three-way match, rules baseline, commit-then-pay, retry guard
│   ├── treasury.py         # server-side runway from the pool balance and trailing spend
│   ├── wallet_setup.py     # one-off Circle developer-controlled wallet creation (original business)
│   ├── requirements.txt    # pinned Python dependencies
│   └── tests/
│       ├── conftest.py         # FakeChain and a throwaway ledger key: the chain is never real in tests
│       ├── test_payables.py
│       ├── test_preflight.py
│       ├── test_api_auth.py
│       ├── test_audit_public.py
│       ├── test_admin.py
│       ├── test_portal.py
│       ├── test_multibusiness.py
│       ├── test_onboarding.py
│       ├── test_shadow.py
│       ├── test_traction_origin.py
│       ├── test_categories.py
│       ├── test_ledger.py
│       ├── test_anchor.py
│       ├── test_gateway.py
│       ├── test_gateway_deposit.py
│       ├── test_model_compare.py
│       ├── test_oversight.py
│       ├── test_demo.py
│       ├── test_outcomes.py
│       ├── test_safe_fetch.py
│       ├── test_escrow.py
│       ├── test_escrow_release.py
│       ├── test_relabel.py
│       └── test_red_team.py
├── backend/
│   ├── main.py             # FastAPI: every route in the API table (docs/api.md)
│   ├── models.py           # SQLite schema, migrations and tenant-scoped queries
│   ├── outcomes.py         # public outcome figures: one documented SQL statement per figure (v1.3.1)
│   ├── auth.py             # API-key auth, roles, failed-key throttling
│   ├── ratelimit.py        # in-memory sliding-window limiter
│   ├── admin_auth.py       # wallet sign-in for the admin site (session bound to the on-chain approver)
│   └── vendor_auth.py      # wallet signatures for vendor, business and application sign-in
├── scripts/
│   ├── create_api_key.py   # create / revoke API keys (printed once, stored hashed)
│   ├── ledger_key.py       # create the ledger signing key without printing it
│   ├── anchor_ledger.py    # status, and the confirmed first anchor per business
│   ├── relabel_origin.py   # list, then (after confirmation) relabel test invoices agent -> demo
│   ├── gateway_deposit.py  # asks for yes at each step: USDC approve and deposit into Circle's Gateway Wallet (max 5 USDC)
│   ├── red_team.py         # asks for yes at each step: a payment the contract must refuse, then an anchor
│   └── pay_domain.py       # one-off: manual commit-then-pay of the ceedebooks.xyz domain bill
├── admin/                  # admin site (admin.ceedebooks.xyz)
│   ├── index.html, admin.css, admin.js, wallet.js
│   ├── admin.test.js, wallet.test.js
│   └── logo.png, favicon.png, apple-touch-icon.png
├── portal/                 # vendor portal and business registration (portal.ceedebooks.xyz)
│   ├── index.html, business.html, portal.css, lib.js, portal.js, business.js
│   ├── portal.test.js, business.test.js
│   └── logo.png, favicon.png, apple-touch-icon.png
├── site/                   # public proof page (ceedebooks.xyz) and its in-browser checks
│   ├── index.html, styles.css, app.js, verify.js
│   ├── verify.test.js
│   └── logo.png, favicon.png, apple-touch-icon.png
├── docs/
│   ├── README.md           # index of these documents and the rule for keeping them current
│   ├── features.md, how-it-works.md, traction.md, architecture.md, trust-model.md
│   ├── roadmap.md, contracts.md, api.md, project-structure.md, testing.md
│   ├── setup-and-deploy.md, known-issues.md
│   └── rollouts/           # one record per thing run live, and an index
├── deploy/
│   ├── DEPLOY.md           # server steps, one upgrade section per release
│   ├── ceedebooks-api.service
│   └── nginx-api.conf, nginx-site.conf, nginx-admin.conf, nginx-portal.conf
├── contracts/
│   ├── BudgetEnforcer.sol      # v1, deployed (original business)
│   ├── BudgetEnforcerV2.sol    # multi-business version, created by the factory
│   ├── BudgetFactory.sol       # one enforcer per business, deployed
│   ├── LedgerAnchor.sol        # public ledger-head anchor, deployed
│   └── MilestoneEscrow.sol     # contractor milestone escrow, deployed (reference deployment)
├── test/
│   ├── BudgetEnforcer.t.sol
│   ├── BudgetEnforcerV2.t.sol
│   ├── BudgetFactory.t.sol
│   ├── LedgerAnchor.t.sol
│   ├── MilestoneEscrow.t.sol
│   └── mocks/MockUSDC.sol
├── .github/workflows/ci.yml  # forge build and test, pytest and the Node suites on every push
├── foundry.toml
├── lib/                    # forge-std, cloned in Setup, git-ignored (not in the repo)
├── .env.example, .gitignore, LICENSE
├── CHANGELOG.md            # every release, newest first
└── README.md               # the short front page
```
