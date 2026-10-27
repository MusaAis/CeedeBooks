# Documentation

The [README](../README.md) is the short front page. The detail lives here, one subject per file, and each fact lives in one place.

| File | What it holds |
|---|---|
| [`../CHANGELOG.md`](../CHANGELOG.md) | Every release, newest first: what was built, tested and not run |
| [`features.md`](features.md) | Everything built, and what is planned |
| [`how-it-works.md`](how-it-works.md) | One invoice from submission to a confirmed payment; shadow mode |
| [`architecture.md`](architecture.md) | Diagram, Circle tools used, why the design is what it is |
| [`trust-model.md`](trust-model.md) | What is and is not guaranteed, custody trade-off, design notes |
| [`contracts.md`](contracts.md) | What each contract does, the businesses and the approver |
| [`api.md`](api.md) | Every route, who may call it, hardening |
| [`traction.md`](traction.md) | Evidence behind the counts, live runs in detail, spend categories |
| [`roadmap.md`](roadmap.md) | Phase by phase status |
| [`testing.md`](testing.md) | How to run each suite and what each covers |
| [`setup-and-deploy.md`](setup-and-deploy.md) | Circle wallet and contract deployment |
| [`project-structure.md`](project-structure.md) | The repository tree |
| [`known-issues.md`](known-issues.md) | Documented bugs and gotchas |
| [`rollouts/`](rollouts/README.md) | One record per thing run live, failures included |
| [`../deploy/DEPLOY.md`](../deploy/DEPLOY.md) | Server steps, one upgrade section per release |

## Keeping them current

Every release updates, in the same commit:

1. `CHANGELOG.md`: a new entry at the top.
2. The README: the Status line, the test counts in the badges and the "Since Sep 27" numbers.
3. Only the docs files the release touched: a new route goes in `api.md`, a new file in `project-structure.md`, a new phase row in `roadmap.md`, a new bug in `known-issues.md`, a new contract in `contracts.md`.
4. `deploy/DEPLOY.md`: an upgrade section.
5. `rollouts/`: a record for anything run live.

Rules: a fact is written once and linked from elsewhere; numbers come from code or git, not memory; failures stay in; nothing is described as built unless it runs.
