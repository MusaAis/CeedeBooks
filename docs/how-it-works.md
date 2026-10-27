# How it works

One invoice from submission to a confirmed payment, then shadow mode.

## How a payment actually happens

1. An invoice is submitted (`POST /invoices`) by the buyer or by the vendor itself. Whoever submits it enters the amount; nothing is defaulted.
2. **Retry guard:** the pipeline derives the invoice key (`keccak256(vendor, invoiceNumber)`) and asks the contract if it is already paid. If yes, nothing is resent.
3. **Three-way match**, before any model is involved: does the invoice carry a PO number? Does the PO exist and match the amount (within 0.01 USDC, a rounding allowance)? Is there a receipt, confirmed by someone other than the agent? Does the invoice wallet match the vendor on file? Any failure escalates, with the rule that failed as the logged explanation.
4. **Decision (rules baseline):** is the vendor registered on-chain? Is runway at least 7 days? Result: pay, hold, or escalate. If the rules escalate an unregistered vendor, `openai/gpt-oss-120b` (Groq) writes the narrative with placeholders for any figure; code fills them in (or replaces the text if it carried a digit of its own), and that text is logged.
5. **Second opinion:** the model is also asked for its own verdict on the same facts, with a 3-second limit. It is recorded and hashed beside the rules verdict (hash format 3) and never changes the decision. No answer is stored as null, never as agreement.
6. The decision is written to the audit log and hashed (`reasoningHash`), and chained into the business's signed ledger in the same database transaction, *before* anything touches the contract. With no ledger key, nothing is logged and nothing is paid.
7. For a payment, the agent first reads the contract's pause state (if payments are paused the invoice is held with `held_because: paused`, and if the state cannot be read nothing is paid), then calls `commitDecision()` bound to the exact payment parameters, **waits for it to confirm on-chain**, then calls `pay()` and waits for that to confirm too. A shadow-mode invoice waits for the owner's approval first (see [Shadow mode](#shadow-mode)).
8. The contract independently re-checks everything: vendor registered, invoice not already paid, amount inside per-tx / daily / category-daily limits, valid unconsumed commitment for these exact parameters. Only if all hold does USDC move.
9. The Circle transaction ID is stored against the decision. Anyone can pull the record, recompute the hash from the stored serialization, and compare it to the `PaymentMade` event for that transaction. The ledger head is anchored on-chain every 25 entries or 6 hours when the anchor job is on.

If the agent gets any of this wrong (a compromised prompt, a hallucinated vendor, a miscalibrated model), the contract doesn't know or care why the request was wrong. It just refuses.

## Shadow mode

A business that does not want to move real money can still be judged on real decisions. In shadow mode it keeps paying its vendors the way it does today, and gives CeedeBooks the same bills: the agent reads each one, runs the three-way match and the rules, and decides.

1. **Submit.** In the admin site's Invoices tab the owner picks a purchase order whose receipt is confirmed, enters the invoice number, the **real amount and currency** of the bill (exact decimals, three-letter currency) and the invoice file (hashed in the browser, never uploaded). The USDC amount is the purchase order's amount: the owner chooses the mirrored amount, there is no exchange-rate lookup and none is claimed.
2. **Decide.** A failed match is escalated and a rules hold is held, exactly as for a live invoice. If the rules say pay, the decision is hashed as `INVOICE_SHADOW_PENDING` and parked as `awaiting_owner`. Nothing is committed, escalated or sent on-chain at this point.
3. **Approve or reject.** **Approve and pay** runs the normal commit-then-pay and records that the owner agreed. The contract still enforces the vendor registry, the limits, the pause and once-only payment, so an approval can never exceed what the owner configured. While payments are paused the approval is refused and the invoice keeps waiting for the owner. **Reject** pays nothing and records that the owner disagreed. Approval is a wallet-signed admin session, not an on-chain signature.
4. **Verdicts on the rest.** A held or escalated shadow decision takes one "I would do the same" or "I would not" verdict. A verdict is final once recorded, so the rate cannot be tuned afterwards.
5. **The record.** The real amount and currency are written into the reasoning text the code produces, so the hashed record covers them with no change to the hash format. Payments approved from shadow mode are logged as `INVOICE_SHADOW_PAID`.

**Honest labels.** Shadow decisions are real decisions on real bills, but the payment is a testnet mirror: no real money moved. The real amount and currency are shown publicly on the business's proof card. Shadow figures are never added to live figures: `/stats` and `GET /businesses/{slug}/traction` report them apart, the agreement rate always shows how many verdicts it rests on and is null with none, and a shadow-approved payment is not counted as an agent payment in the live numbers. A business is labelled outside only when the operator confirms it.

**Not in shadow mode:** no bank or ERP connection, no PDF or email intake, no exchange-rate oracle.
