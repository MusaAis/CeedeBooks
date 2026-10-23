# Gateway deposit from the agent wallet (Oct 15, 2026)

**Feature:** `scripts/gateway_deposit.py` (v1.2.10.1, Phase F2). **Where:** the original business's agent wallet `0x32B59A6988DCA955c641D529Ee80484200DE37b0`, run from the server, `manual`. A dry run with 1 USDC came first, then the real run with `--amount 3`, a typed `yes` at each of three steps.

## What was run

The wallet held 39.925090 USDC.

1. `GATEWAY_DEPOSIT` audit row written, ledger-chained and hashed before any on-chain call. `reasoning_hash` `537ee6b951883914c4772e982b667bae38f97eeb42c54f1ee8d35c36fac01902`. Circle transaction id `4805533c-a98e-51aa-b628-2c4b783f60c5`.
2. `approve` of exactly 3 USDC to Circle's Gateway Wallet `0x0077777d7EBA4688BDeF3E311b846F25870A19B9` (the address on Circle's contract-addresses page).
3. `GatewayWallet.deposit(USDC, 3)`: transaction [`0xa022b539a506cfb58f1b2fe1f509a00cce6b5b3ccc23c4ebe40a410bebcaba3f`](https://explorer.testnet.arc.io/tx/0xa022b539a506cfb58f1b2fe1f509a00cce6b5b3ccc23c4ebe40a410bebcaba3f).

## Result

Circle's Gateway API returned 3.000000 USDC for the wallet and the admin Overview card shows 3 USDC.

## What was not captured, and what this is not

- The approve transaction's hash was not printed.
- The audit row has no `chain_tx_hash` on purpose: the proof page's Verify looks for a payment event and a deposit has none.
- This is the agent wallet's own USDC, not the business pool. It is not a payment to a vendor and not traction, and it is not counted in `/stats`.
- Deposit only. No gasless spend is built and no decision uses the Gateway balance.

## What changed afterwards

Nothing in the script after the run. The script refuses anything unreadable and never sends a plain transfer to the Gateway Wallet, which Circle's docs say loses the funds (a test scans for it).

## Check it yourself

Open the deposit transaction above on the explorer; the Gateway balance is read by `POST /v1/balances` and shown on the admin Overview.
