# Since Sep 27

What changed during the Tameion Agents Hackathon (Canteen × Circle × Arc, Sep 27 – Oct 17, 2026). The starting point and the end are both taken from the git history (`git log`), not from memory.

| | Sep 27, 2026 | Now (v1.3.3.3) |
|---|---|---|
| Repository | One commit, `dcf1f2a` "Initial commit", containing only the MIT `LICENSE` | 50 commits and 40 tagged releases, `v1.2.0` to `v1.3.3.3` |
| Code and contracts | None | `BudgetEnforcer` (Sep 29), then the multi-business contracts (`BudgetEnforcerV2`, `BudgetFactory`, `LedgerAnchor`, Oct 4) and the `MilestoneEscrow` contract (Oct 24); the Python engine, API, proof page, admin site and vendor portal described above |
| Tests | None | 111 Foundry, 766 Python, 6 Node runs |
| Live system | None | An API, a public proof page, a wallet-signed admin site and a vendor portal on Arc Testnet |
| Users | None | None outside the project: no outside business or paying user. The business that uses it is our own |
| Real payments | None | One real vendor bill of ours (the domain, 2.20 USDC, paid by card and reimbursed through the contract, labelled `manual`); everything else is self-owned test or shadow activity, shown apart on the proof page |

The first build commit after the initial one is `15d1b0f` (Sep 29): `BudgetEnforcer` deployed and verified, with 24 Foundry tests. Nothing in this repository predates Sep 27. The live counts (decisions, payments, businesses) change constantly, so the [proof page](https://ceedebooks.xyz) is the only source for them.

Next: the [roadmap](roadmap.md).
