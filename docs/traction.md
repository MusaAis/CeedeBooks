# Traction

Everything below is checkable on-chain; nothing is estimated. The short table with links is in the [README](../README.md#evidence-you-can-check) and each live run has a record in [`rollouts/`](rollouts/README.md).

Everything below is checkable on-chain. Nothing here is estimated.

| What | Evidence |
|---|---|
| Contract deployed and verified on Arc Testnet | [`BudgetEnforcer`](https://explorer.testnet.arc.io/address/0x47D8a05a0d31aFA492A9F4A37A8991ED4aa683fB) |
| Test suites | 111 Foundry (24 on the v1 contract, 45 on the multi-business contracts, 42 on the escrow contract) + 699 Python tests + 6 Node runs (proof page, admin site, vendor portal), run by CI on every push |
| First self-owned vendor bill paid through the contract | `ceedebooks.xyz` registration, 2.20 USDC, category 1 |
| Multi-business contracts deployed and verified (v1.2.8) | [`BudgetFactory`](https://explorer.testnet.arc.io/address/0x97b9A3802bA6B258cBeF6070532a265656bb391C), [`LedgerAnchor`](https://explorer.testnet.arc.io/address/0x008217BeC86462E76126F94e50eBe68fb4a41444) |
| First ledger anchor of the original business (sequence 9) | [`0x24045426...a36639fb`](https://explorer.testnet.arc.io/tx/0x24045426ab28d346ce6e48998dfef1964e5890d64ee04f385cae1011a36639fb); `GET /ledger/verify?business=ceedebooks` compares it with the chain |
| Red team run live on the original business (Oct 15) | The agent wallet tried to pay a fresh, non-approved address for 1 unit and the contract refused it (at Circle's gas estimation, so no pay transaction reached the chain); the refusal was anchored in ledger sequence 16, [`0x2f75bf9f...0509af`](https://explorer.testnet.arc.io/tx/0x2f75bf9f9aa5702e68c5c031f58f79d56e1c0b68e22567c44d46c3ca4b0509af) (details below) |
| Gateway deposit from the agent wallet (Oct 15, `manual`) | 3 USDC from the agent wallet into Circle's Gateway Wallet, [`0xa022b539...caba3f`](https://explorer.testnet.arc.io/tx/0xa022b539a506cfb58f1b2fe1f509a00cce6b5b3ccc23c4ebe40a410bebcaba3f); Circle's Gateway API then returned 3 USDC for that wallet (details below) |
| Escrow release, run live (Oct 28, our own wallets) | The agent wallet committed [`0x73ac7434...a22421f0`](https://explorer.testnet.arc.io/tx/0x73ac7434b489d13da2678cba5552b2f5339bf6c7c7f980eda905e1eea22421f0) and released milestone 0 (0.1 USDC) in [`0xc49c13dd...b83f1f`](https://explorer.testnet.arc.io/tx/0xc49c13ddf6e89980df395a88db87bdb4445d7d1997385988f18b6ab14fb83f1f), block 69485148; `GET /decisions/e720c5eb.../verify` returned an on-chain match. A hold and an escalation were recorded and sent nothing. Escrow decisions are not in the paid, held or escalated counts and are not traction: [record](rollouts/2026-10-28-escrow-release-live.md) |
| Approver's escrow actions, run live (Oct 29, our own wallets) | Five transactions from the approver wallet to the escrow, all `ok`: pause [`0x722515b4...56d0e8d7`](https://explorer.testnet.arc.io/tx/0x722515b498af9ba18b31974e81d670b7b17b618454ce6741728e633856d0e8d7), withdraw 0.1 USDC [`0x058fd250...45ec1bfd`](https://explorer.testnet.arc.io/tx/0x058fd2500aa95077bb651fe60f214dd93d18beaf6d16b9cf18a61ad345ec1bfd), reject milestone 1 [`0xe078db71...664537d1`](https://explorer.testnet.arc.io/tx/0xe078db719ac44a4e7082b989a83ea49aa50081067b0b84d39872a572664537d1), release milestone 2 (0.5 USDC) as approver [`0x904a13bd...8adfaee6`](https://explorer.testnet.arc.io/tx/0x904a13bdee5135609085dbf1bbd719e2877cef36d16b8c8a846655f8adfaee6), resume [`0xe6fa3e83...31d58917`](https://explorer.testnet.arc.io/tx/0xe6fa3e83df336154c75bb56fc4ed0f31cae0df68461f06edae4587ad31d58917). Not traction: [record](rollouts/2026-10-29-escrow-approver-actions-live.md) |
| Businesses | Two, both ours: the original and a test business made through the factory (see [Contracts](contracts.md)) |
| Outside businesses or paying users | None yet |

**Red team, run live on Oct 15 (original business, v1 contract).** `scripts/red_team.py` ran from the server with a typed `yes` at each step. Target: `0x645165125d198C360f96D6a13807907159d83741`, a fresh address that is not an approved vendor, for 1 unit. Step 1 wrote the `RED_TEAM_REFUSED` audit row (`reasoning_hash` `d7ea640ae468f2e2dec1dc35ac05c7f1f2ca006b47b0f0cc00c001204fddb2a0`). Step 2, `commitDecision`, confirmed. Step 3, `pay`, was refused: `ESTIMATION_ERROR - execution reverted: Vendor not approved`. The refusal came from Circle's gas estimation, so no pay transaction reached the chain and there is no pay transaction hash to link; the revert reason is the contract's own check. Step 4 anchored the ledger head: sequence 16, head `7526aeb8f6b3c583460399253ca240dc049a4e4096a02596416f1d7f7383c16b`, transaction [`0x2f75bf9f9aa5702e68c5c031f58f79d56e1c0b68e22567c44d46c3ca4b0509af`](https://explorer.testnet.arc.io/tx/0x2f75bf9f9aa5702e68c5c031f58f79d56e1c0b68e22567c44d46c3ca4b0509af). Afterwards `GET /ledger/verify?business=ceedebooks` returned `ok` true, 17 entries, 16 decisions, no problems, anchor `match`, no entries after the anchor and `fully_verified` true. The row shows on the proof page as `manual entry, off-chain only`: the record itself is not an on-chain event, the anchored head that includes it is.

**Gateway deposit, run live on Oct 15 (original business, `manual`).** `scripts/gateway_deposit.py --amount 3` ran from the server with a typed `yes` at each of its three steps. The agent wallet `0x32B59A6988DCA955c641D529Ee80484200DE37b0` held 39.925090 USDC. Step 1 wrote the `GATEWAY_DEPOSIT` audit row (`reasoning_hash` `537ee6b951883914c4772e982b667bae38f97eeb42c54f1ee8d35c36fac01902`), step 2 approved exactly 3 USDC to Circle's Gateway Wallet, step 3 called `deposit`: transaction [`0xa022b539a506cfb58f1b2fe1f509a00cce6b5b3ccc23c4ebe40a410bebcaba3f`](https://explorer.testnet.arc.io/tx/0xa022b539a506cfb58f1b2fe1f509a00cce6b5b3ccc23c4ebe40a410bebcaba3f). Circle's Gateway API then returned 3.000000 USDC for the wallet, and the admin Overview card shows 3 USDC. The approve transaction's hash was not printed. This is the agent wallet's own USDC and not the business pool, it is not a payment to a vendor, and it is not traction. The Gateway Wallet address (`0x0077777d7EBA4688BDeF3E311b846F25870A19B9`) is the one on Circle's contract-addresses page.

**Outside recognition.** According to an email from XERJ's founder on Oct 15, CeedeBooks' v1.2.9.4 work (return to agent, re-run, red team) was selected for the XERJ Corpus Hub, an open index of code solutions that coding agents consult, under the repository's MIT licence with attribution to MusaAis/CeedeBooks. It is not a user, a customer or a payment, and it is not counted anywhere above.

**Live numbers** (pool balance, paid, held and escalated counts, vendors, purchase orders) change with every payment, so the [proof page](https://ceedebooks.xyz) and `GET /stats` are the only source. The escalations on it are self-owned test invoices that the agent parked for review, as designed. Testnet USDC has no market value.

**The domain payment.** CeedeBooks' own domain was bought by card (Namecheap order 215679394, $2.00 + $0.20 ICANN fee), and the registrar cannot take USDC, so the founder was reimbursed through the contract: vendor registered with `setVendor`, category 1 given a 20 USDC/day limit, the receipt hashed into `docHash`, the reasoning hash logged off-chain first, then `commitDecision` and `pay` from the Circle agent wallet in separate blocks.

- Payment transaction: [`0x8c176242...ce5d0d`](https://explorer.testnet.arc.io/tx/0x8c176242a84235a884d5790edf4a9aed403d87619359373e11f2e27141ce5d0d)
- `reasoning_hash`: `46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a`
- Script: [`scripts/pay_domain.py`](../scripts/pay_domain.py)

This was run by hand through the same two-call sequence the agent uses, labelled `manual` in the audit log. It is not an autonomous agent decision. The cost itself was real: the domain was paid by card and reimbursed through the contract.

## Spend categories

Each business has its own categories. The original business was seeded with two (keys 0 and 1 on its v1 contract; the owner can change a limit at any time, so read the contract for the current value):

| Key | Name | Daily limit when created |
|---|---|---|
| 0 | Data oracle | 50 USDC |
| 1 | Infrastructure (domains, hosting, SaaS) | 20 USDC |

Every other business starts with none and creates its own in the admin site's Limits tab (v1.2.9.1): it types a name, the server gives it the next free id, and the owner signs `setCategoryDailyLimit(id, limit)` from their own wallet (the server never holds that key). A category can take purchase orders and invoices only while its on-chain limit is above zero and readable. An unregistered category has a limit of 0, so the contract refuses it. Names live off-chain in the `categories` table and can be renamed; ids never change. Names may be shown publicly, so they should hold no private data.
