# First on-chain ledger anchor (Oct 12, 2026)

**Feature:** `agent/anchor.py` and `scripts/anchor_ledger.py` (v1.2.9.2, Phase E2). **Where:** the original business, anchored by its own agent wallet `0x32B59A6988DCA955c641D529Ee80484200DE37b0` through `LedgerAnchor` `0x008217BeC86462E76126F94e50eBe68fb4a41444`. The owner typed `yes`.

## What was run

Gas before anchoring: the original business's agent wallet held 39.95 USDC (native gas). The first anchor was sent with ledger sequence 9, head `21b693a4c70d0a7daef6e9cfce7ff07e512a4d65a17ebbc32be0d9c5aff72b08`, transaction [`0x24045426ab28d346ce6e48998dfef1964e5890d64ee04f385cae1011a36639fb`](https://explorer.testnet.arc.io/tx/0x24045426ab28d346ce6e48998dfef1964e5890d64ee04f385cae1011a36639fb).

## Result

`GET /ledger/verify?business=ceedebooks` returned `ok` true, `fully_verified` true, anchor `match`, no entries after the anchor. The proof page's "Verify the whole chain" button showed three passing rows and "Intact, and its head matches the on-chain anchor". `LEDGER_ANCHOR_AUTO=1` was then set on the server and the service restarted clean.

## What was not verified

- The automatic job (every 25 new entries or 6 hours after a new entry, only once a first anchor exists) had not been run live at the time of this run.
- The second business made through the factory was never anchored on purpose: the job skips a business with no first anchor, and that business was archived before the demo video.
- The key and the database share one server, and the agent wallet and the approver share one operator. The anchor makes the ledger tamper-evident against partial edits and against rewriting history after the anchor; it is not proof that the operator is independent.

## What changed afterwards

`GET /ledger/verify?business=ceedebooks` on Oct 23 lists these newest recorded anchors: sequence 15 ([`0xe705a84c...a5254f92`](https://explorer.testnet.arc.io/tx/0xe705a84ca3c5e8f3435396aee862db8d4cbe5c6e99542328c2a679dea5254f92), Oct 13 17:39 UTC), sequence 16 (the red team, Oct 15 09:47 UTC; see `2026-10-15-red-team.md`), sequence 17 ([`0xdeb79d88...1551697d`](https://explorer.testnet.arc.io/tx/0xdeb79d88453fffcc8db625f42d158c03713633a5267c7f9994c703fa1551697d), Oct 15 15:56 UTC), sequence 19 ([`0xb67cddaf...fbdd194`](https://explorer.testnet.arc.io/tx/0xb67cddafd8b31d4f2e284070c5c5742ee53321cc17b761967fea45bddfbdd194), Oct 16 08:45 UTC) and sequence 22 ([`0x2e41acbc...7d00dee6`](https://explorer.testnet.arc.io/tx/0x2e41acbc14a8d9e3d8de6c2d74d4446dd2f77dd6b2fd33955c6f74037d00dee6), Oct 22 11:29 UTC). The chain then held 23 entries (22 decisions, 8 backfilled at the start), `ok` true, 0 problems, anchor `match`. This record does not say which of these were sent by the automatic job and which by hand.

## Check it yourself

Open the transaction above, or run "Verify the whole chain" on https://ceedebooks.xyz, which re-checks every hash, link and Ed25519 signature in the browser and reads the anchor by `eth_call` on public RPCs.
