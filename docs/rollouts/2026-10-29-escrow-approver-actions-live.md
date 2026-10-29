# The approver's actions on a milestone escrow, first live run (Oct 29, 2026)

**Feature:** the **Approver actions** section of the admin site's Escrows tab (`admin/admin.js`, `admin/wallet.js`, v1.3.2.4, Phase H piece 4). **Where:** the production admin site (`admin.ceedebooks.xyz`), driven from a phone by the escrow's approver wallet `0xaE16D350db806A7785113C7C3690C337B753eAaa`, against the reference escrow [`0x9752F995...2971dd5`](https://explorer.testnet.arc.io/address/0x9752F995739C8A171AbD24944925Eaf4d2971dd5). **Whose money:** the builder's own, testnet USDC. Every wallet involved (the approver, the agent `0x32B5...37b0`, the contractor `0x1e89Fc19Bfa9015C72cAd70D27CC77E684d59b38`) belongs to the builder, so none of this is outside-business traction. These actions send no request to the server and write no activity-log or ledger entry by design: each transaction on the escrow is the record.

## What was run

Five transactions, each sent from the approver's wallet to the escrow after the page's confirmation dialog, one at a time, all UTC, all `ok` on the explorer. The escrow started at balance 5.4 USDC and 0.7 USDC reserved, after the Oct 28 release.

1. 10:49:04, **pause** (`setPaused(true)`): [`0x722515b4...56d0e8d7`](https://explorer.testnet.arc.io/tx/0x722515b498af9ba18b31974e81d670b7b17b618454ce6741728e633856d0e8d7). The card then read PAUSED and offered Resume.
2. 10:51:52, **withdraw 0.1 USDC** to the approver's own wallet (the "To" field left empty), while paused: [`0x058fd250...45ec1bfd`](https://explorer.testnet.arc.io/tx/0x058fd2500aa95077bb651fe60f214dd93d18beaf6d16b9cf18a61ad345ec1bfd). The page offered at most the unreserved balance (4.7 USDC before). Balance 5.4 to 5.3, reserved unchanged at 0.7.
3. 10:54:49, **reject milestone 1** (0.2 USDC), while paused: [`0xe078db71...664537d1`](https://explorer.testnet.arc.io/tx/0xe078db719ac44a4e7082b989a83ea49aa50081067b0b84d39872a572664537d1). The typed reason was `H4 live test: reject milestone 1`; the page hashed it with SHA-256 and the dialog showed `0x6c56f53fc497ee4f81c3ea7777f2aeb93d9602a617d240720141e1abd1f8f668`, which is what `printf '%s' 'H4 live test: reject milestone 1' | sha256sum` gives. Milestone 1 read `rejected` and reserved fell from 0.7 to 0.5.
4. 10:57:58, **release milestone 2 as the approver** (0.5 USDC, above the agent's 0.25 limit, which the agent can never release), while paused: [`0x904a13bd...8adfaee6`](https://explorer.testnet.arc.io/tx/0x904a13bdee5135609085dbf1bbd719e2877cef36d16b8c8a846655f8adfaee6). The evidence hash was `0xe614b119fd0bde382e3c12ac5246ec46dd301c3c3850a61442731c46f3c5978e`, the hash of merged pull request #937 of `foundry-rs/forge-std` from [the Oct 27 record](2026-10-27-escrow-reads-and-evidence.md); it is a real hash used as a stand-in, not evidence that any work was delivered. Milestone 2 read `released`, reserved 0, balance 4.8 USDC.
5. 11:00:21, **resume** (`setPaused(false)`): [`0xe6fa3e83...31d58917`](https://explorer.testnet.arc.io/tx/0xe6fa3e83df336154c75bb56fc4ed0f31cae0df68461f06edae4587ad31d58917). The card read Active again and offered Pause.

## Result

Final state read from the Escrows tab: balance 4.8 USDC, reserved 0, State Active; milestone 0 `released` (by the agent, Oct 28), milestone 1 `rejected`, milestone 2 `released` (by the approver). The explorer lists the five transactions from the approver's wallet with the methods `setPaused`, `withdraw`, `rejectMilestone`, an undecoded `0xccf38f78` (`overrideRelease`) and `setPaused`.

## What was not captured, and what was not run

- The receipts were not opened separately: the withdraw's 0.1 USDC arriving at the approver's wallet and the release's 0.5 USDC arriving at the contractor were read from the escrow's balance falling by those amounts, not from each recipient's balance.
- The hold the contract applies to the agent while paused (the agent's release and reject refused) was not tried live; neither was a wallet that is not the approver (its buttons are hidden by the page and the contract would refuse), a withdraw to another address, a withdraw above the unreserved balance (the page refuses it before the wallet), an approver release of the rejected milestone 1, or changing the agent or the approver. They are covered by the page's checks and the Foundry tests.
- The escrow now has no Pending milestone: 1 is rejected and can still be released by the approver, 0 and 2 are released. A further live run of the agent's own release path needs a new escrow.
- No activity-log or ledger entry exists for these actions, by design; the page says so.

## What changed afterwards

Nothing in the code.

## Check it yourself

```bash
curl -s "https://explorer.testnet.arc.io/api/v2/addresses/0x9752F995739C8A171AbD24944925Eaf4d2971dd5/transactions" | python3 -c "import sys,json; [print(t['hash'], t.get('method'), t['timestamp'], t['status'], t['from']['hash']) for t in json.load(sys.stdin)['items']]"
cast call 0x9752F995739C8A171AbD24944925Eaf4d2971dd5 "paused()(bool)" --rpc-url $ARC_TESTNET_RPC_URL
cast call 0x9752F995739C8A171AbD24944925Eaf4d2971dd5 "milestones(uint256)(uint256,bytes32,uint8)" 1 --rpc-url $ARC_TESTNET_RPC_URL
```

The first lists the transactions above; `paused()` returns `false`; milestone 1 returns status `2` (Rejected).
