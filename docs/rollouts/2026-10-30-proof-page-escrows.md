# The proof-page view of escrows, first live check (Oct 30, 2026)

**Feature:** the **Milestone escrows** section of the public proof page and the route `GET /escrows/{address}/decisions` (`site/`, `backend/main.py`, v1.3.2.5, Phase H piece 5). **Where:** the production API and `ceedebooks.xyz`, checked from a phone. Read-only: nothing was sent on-chain and no wallet was used.

## What was run

1. The new files were extracted on the server, the API restarted and the proof-page files copied. The first two `curl` calls ran inside the 15 seconds the API needs to start and answered 502 from nginx; the service log shows it started cleanly (active at 23:55:44 UTC on Oct 29).
2. `curl https://api.ceedebooks.xyz/escrows/0x9752F995739C8A171AbD24944925Eaf4d2971dd5/decisions` returned three decisions, newest first: `ESCROW_RELEASED` milestone 0 (0.1 USDC, transaction `0xc49c13dd...b83f1f`, hash `e720c5eb...23a895`), `ESCROW_ESCALATED` milestone 2 (0.5 USDC, no transaction) and `ESCROW_HELD` milestone 0 (0.1 USDC, no transaction). An unregistered address answered 404.
3. The full Python suite ran on the server with the CI placeholder variables: **699 passed** in 4 minutes 59 seconds.
4. The page showed the section: Active, "ceedebooks (builder's own)", balance 4.80 USDC, reserved 0.00 USDC, the agent releasing up to 0.25 USDC, milestone 0 released, milestone 1 rejected, milestone 2 released, and the agent, approver and contractor addresses as explorer links, then the three decisions and the note that escrow decisions are not counted in the totals.

## What failed

Pressing **Verify** on the release (milestone 0) ended in **FAIL**: "That transaction does not contain an event with this hash" and "Failed. Do not trust this record." The first three steps passed (record found, fingerprint matches, nothing edited). The page's on-chain step only looked for the business contract's events, and a release is a `MilestoneReleased` event on the escrow contract, so it could never match. The server's own lookup handled escrows from v1.3.2.3; only the page did not, and the same failure would have shown on any escrow release in the feed since then.

## What changed afterwards

The page's check now reads the escrow named in the record's subject, looks for `MilestoneReleased` emitted by that address with the record's hash in the second indexed topic, and trusts the escrow only if the API lists it as registered to the record's business. New checks in `site/verify.test.js` cover the subject parsing, the address, the event type, the hash, the success flag and the old lookup never matching a release. The wiring was also run in a headless browser against stubbed answers: a registered escrow passes; an unregistered one, another business's escrow and an event from another contract fail.

## Re-check after the fix (Oct 30, about 09:33 to 09:34 UTC+1)

With the corrected proof-page files copied to the server, Verify was pressed again on the real page, one decision at a time:

- **Release, milestone 0** (hash `e720c5eb...23a895`): all four steps PASS, including "Found on-chain. A MilestoneReleased event in block 69485148 carries the same hash. Read directly from rpc.testnet.arc.network", linked to the transaction `0xc49c13dd...b83f1f`. Verdict: Verified.
- **Escalation, milestone 2** (hash `dc3a8a57...46ffc83c`) and **hold, milestone 0** (hash `ccbaf7ac...e3e11b4`): the three record checks PASS and the on-chain line reads N/A, "No blockchain transaction is linked to this decision". Verdict: "Recorded off-chain only", which is right: the agent sent nothing for either.

One thing seen and not explained: at 09:31, in the wallet app's built-in browser, the page's own "Live check" at the top (the 2.20 USDC domain payment, an older record on the original business) showed "This browser could not reach a public Arc node, so the on-chain match did not run here" and the server's own lookup as a note, with the verdict "Partly verified". That is the page's existing fallback when no public RPC answers, and the same page reached an RPC a minute later in the other browser. It was not investigated and was not caused by anything changed in v1.3.2.5 as far as the code shows.

## Not done

The approver's own actions (pause, reject, override, withdraw) are not in this list by design: they are transactions on the explorer.
