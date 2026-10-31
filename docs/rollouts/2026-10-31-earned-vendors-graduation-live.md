# Earned vendors, first live run: one graduation (Oct 31, 2026)

**Feature:** the earned-vendor rule and record (`agent/earned.py`, the admin Vendors tab's Earned vendors panel and `POST /admin/earned-vendors/settings`, v1.3.3, Phase R piece 1). **Where:** the production API and the admin site, on the builder's own business (`ceedebooks`). Nothing was sent on-chain by this feature and no payment was made: a graduation is a ledger record only.

## What was run

1. The v1.3.3 files were extracted on the server (checksum checked, exactly the 20 expected files), the full Python suite ran there with the CI placeholder variables (**717 passed**, 5 minutes 14 seconds) and the Node suites passed (`JS_TESTS_OK`, `ADMIN_SMOKE_OK` twice). The database was backed up, the API restarted (200 on `/businesses`), and `businesses.earn_after` read back as 5 for both businesses. `GET /admin/earned-vendors` answered 401 without a session.
2. The admin and proof-page files were copied. The Vendors tab showed the Earned vendors panel with the number 5 and each vendor's streak from the existing shadow verdicts: Test Vendor 0, Test Vendor 2 1, ggs ent. 1, Ais Data ltd 1, ceedebooks test 2 **2**, Demo Supplies (demo) 0, all "Not yet" (a phone screenshot at 15:15 UTC).
3. With the owner's yes, the number was set to **2** from the panel. "ceedebooks test 2" (vendor 6, the owner's own wallet `0xae16d350...753eaaa`) already had two approved shadow pay decisions in a row (invoices `0688` and `243`), so it graduated at once, from existing verdicts, with no new invoice and no payment.

## What the server showed

- One new decision-log row, action `VENDOR_GRADUATED`, subject `vendor:6`, amount 0, model `rules`, hash **`4dc3a786e9af44c919ee7d7b1b00eebf84b84e5dbc38c5b807555265602728d1`**, written at 2026-10-31 15:21:51 UTC. Its text, written by code: "Vendor ceedebooks test 2 (id 6, wallet 0xae16...eaaa) graduated: the owner approved its last 2 shadow pay decisions in a row (0688, 243), which meets the threshold of 2 the owner set. This record changes no limit and pays nothing; the contract's limits and every check still apply to each payment." It has no transaction (`chain_tx_hash` null).
- `GET /decisions/4dc3a786.../verify`: `verified: true`; on-chain: "No on-chain transaction recorded for this decision", which is right.
- The ledger: the entry is sequence 26 (`4dc3a786e9af`), 27 entries in all; `GET /ledger/verify?business=ceedebooks` returned `ok` true, 0 problems and anchor `match`.
- `GET /decisions?limit=3` listed the record first, ahead of the escrow release and escalation of Oct 28 and 29.
- No public figure moved: `GET /stats` read paid 1, held 0, escalated 2, decisions 3, the same as the Oct 22 reading.

## Not run live, and not captured

- **Revocation was not run live.** It needs a rejected shadow invoice, which would add a permanent disagreement to the public agreement rate, so it is covered by tests only (a graduated vendor is revoked by a rejection, and by an "I would not" verdict on a held decision).
- The Save click itself was not captured on screen, and `/stats` was not read immediately before it (the comparison is with the Oct 22 reading). The proof-page feed showing the Graduated chip and the in-browser Verify on this record were not looked at on the real page yet; both were driven in a headless browser against a stubbed API before release.
- Nothing pays by itself in this release, so no autopay was tried.

## Afterwards

The number was set back to 5: the server read `earn_after` 2 for `ceedebooks` before, and 5 after the owner pressed Save with 5 (the admin site showed "Saved." at 15:55 UTC). The graduation record stays. The two business applications accepted the same afternoon (ProbeCo and CTF Biz) read `earn_after` 5 and have no contract yet.

## What changed afterwards

Nothing in the code. The first live read of the numbers matched the Oct 22 ones, so no counting change was needed.
