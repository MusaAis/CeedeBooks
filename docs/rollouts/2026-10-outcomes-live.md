# The Outcomes block, first live reading (Oct 21, 2026)

**Feature:** `GET /outcomes` and the Outcomes block on the proof page (v1.3.1, Phase P1). **Where:** read on the live site after deployment.

## What ran

After deployment `GET /outcomes?business=ceedebooks` answered with scope `ceedebooks` and 3 invoices processed in the builder's own agent traffic, and the proof page showed the block with the line "Outside businesses: 0 confirmed". Each figure comes from one SQL statement that the response returns beside it (`queries`), so a reader can reproduce any number.

## What is not in it

- The median seconds to payment started empty: only payments stamped at payment time (from this release) are timed, and earlier ones are counted as `without_timing`, never estimated.
- "Person agreement" is null until a person has settled an escalation, and it measures a person rejecting what the agent escalated, so an owner who releases everything gives 0%. A low number there says the owner overrode the agent's caution, not that the agent was wrong.
- No outside business exists, so the outside rows are empty.

## Check it yourself

`curl https://api.ceedebooks.xyz/outcomes` and read `queries` next to `by_ownership`.
