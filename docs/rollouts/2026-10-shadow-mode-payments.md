# Shadow mode: five approved decisions mirrored as testnet payments (Oct 9 to 16, 2026)

**Feature:** shadow mode (v1.2.8.3, Phase L4). A bill is read, decided, and only after the owner approves it is a payment sent that mirrors its real amount. **Where:** the original business (the builder's own), admin site.

## What ran

Five shadow invoices were approved by the owner and paid on Arc Testnet. Each row is the invoice number, the real amount and currency the owner entered, the USDC mirrored, the payment transaction and the `reasoning_hash` it can be checked against.

| Invoice | Real amount entered | USDC mirrored | Time (UTC) | Transaction | `reasoning_hash` |
|---|---|---|---|---|---|
| inv-009 | 3800.00 NGN | 0.01 | 2026-10-09 21:52 | [`0xa1cbdbe1...a2a5ff`](https://explorer.testnet.arc.io/tx/0xa1cbdbe193bda1130860bf16e44f55adb36d8e3555391c1ec344a8d1cea2a5ff) | `4c22825fa128f2f2868f2d47457629de2aed4f966c03bbd6f8dc8fa2c96d58e2` |
| 0688 | 4300 NGN | 3.24 | 2026-10-13 11:29 | [`0xde9be30b...29fb78`](https://explorer.testnet.arc.io/tx/0xde9be30b3b9b6914230361fac9a04e8fab916048aca4565238da3a94e329fb78) | `1ad281737630068fa84c47ba6545816aa2528698a872020688acd4cd61d3a3b7` |
| 243 | 3 USD | 3.00 | 2026-10-13 11:30 | [`0xe933bd22...f0786427`](https://explorer.testnet.arc.io/tx/0xe933bd22378b9bbeb051c69c16caaf4517303209a516c64440df47e4f0786427) | `df8fccd8bb5a731c0553812106ad517c2e1d003e817e7e79fedad3cb4b6ad8b2` |
| 321 | 1360 NGN | 1.02 | 2026-10-13 11:40 | [`0x09dc64a8...f3e164`](https://explorer.testnet.arc.io/tx/0x09dc64a802e136f6480a2039f7184248539222b6f309edc7aaf842e676f3e164) | `83a6bc423caf77a7da9a8b081d0d7dd2ce6e79e58e65eede2dd16f0c0fb30390` |
| 33 | 1.2 USD | 1.20 | 2026-10-16 08:40 | [`0x86b6898f...c6cd3c`](https://explorer.testnet.arc.io/tx/0x86b6898f9d91772159398a43d85fb38c424e2062f27a06dcb5798ad495c6cd3c) | `43f25d98c2e2f8494fbd242eaba5a434d5bb32401b81f51edbddb435e0d3a003` |

Totals read from `GET /businesses/ceedebooks/traction` on Oct 23: shadow 5 paid, 0 held, 0 escalated, 8.47 USDC mirrored, real amounts NGN 9460.00 and USD 4.2, owner agreement 5 of 5 (n=5). The USDC amounts add up to 8.47 and the real amounts to those totals.

## What this is, and what it is not

- The USDC amount is the purchase order's amount, chosen by the owner. There is no exchange-rate lookup and none is claimed.
- The payments are testnet mirrors; no real money moved. The real amount and currency are what the owner entered; nothing on-chain checks that a bill existed.
- The business is the builder's own (kind `own`), so this is own-company activity, not an outside business and not an outside user.
- The 5 of 5 agreement is the owner's own verdicts. It measures the owner agreeing with the agent on these five, which is a small number, and it is not the model-versus-rules figure in `/metrics/decisions`.

## What changed afterwards

The per-business traction card and the figures below it were made agent-only in v1.3.1.2: manual and demo invoices are shown apart (see the changelog).

## Check it yourself

Open any transaction above, or paste its `reasoning_hash` into the verifier on https://ceedebooks.xyz.
