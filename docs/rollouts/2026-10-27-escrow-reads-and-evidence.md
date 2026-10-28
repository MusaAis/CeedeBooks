# Escrow reads and the GitHub evidence check, on the server (Oct 27, 2026)

**Feature:** `agent/escrow_chain.py`, `agent/safe_fetch.py`, `agent/evidence.py` and the escrow routes (v1.3.2.1, Phase H piece 2). **Where:** the production server after the upgrade, run by hand with a read-only helper script and `curl`. Nothing was registered and nothing was sent to the chain: the reference escrow from [the deployment record](2026-10-24-milestone-escrow-deploy.md) was only read.

## What was run

1. After the restart, `GET /escrows` answered `{"escrows":[]}`; `GET /escrows/0x9752F995...2971dd5` answered 404 (the escrow is not registered); an unauthenticated `POST /admin/escrows` answered 401.
2. The escrow was read through the same code the API uses (`escrow_chain.escrow_for(address).snapshot()`). It returned the agent `0x32B59A6988DCA955c641D529Ee80484200DE37b0`, the approver `0xaE16D350db806A7785113C7C3690C337B753eAaa`, the contractor and USDC addresses of the deployment, `agent_release_limit_usdc` 0.25, `balance_usdc` 0, `pending_usdc` 0.8, `withdrawable_usdc` 0, not paused, and three Pending milestones of 0.1, 0.2 and 0.5 USDC with the three requirements hashes of the deployment record; the agent can release the first two and not the third.
3. The evidence fetch ran against GitHub's real API from the server. Merged pull request 937 of `foundry-rs/forge-std` (chosen with `gh pr list --state merged`, not by us) gave `merged` true, merged at 2026-10-26T13:38:19Z, merge commit `bd8c87087369377df8b451de74fb3a4fa9a7f577`, base branch `master`, author `dependabot[bot]`, and the evidence hash `0xe614b119fd0bde382e3c12ac5246ec46dd301c3c3850a61442731c46f3c5978e`.
4. Two pull requests that do not exist (number 1 and number 999999999 of the same repository) both ended in `FetchFailed: The host answered 404`, so a missing pull request is a failure and never a result.

## What failed or was not captured

- The first target chosen, `foundry-rs/forge-std` pull request 1, does not exist; it is the 404 above, not a fault.
- The routes that need a wallet session (register, list, evidence check) were not run live at the time: the admin site had no screen for them yet. They were first run live on Oct 28 ([record](2026-10-28-escrow-release-live.md)).
- The server's own check that the connection reached a public address passed for GitHub; it was not tried against a hostile address on the server, only in the tests.

## What changed afterwards

Nothing.

## Check it yourself

`GET https://api.ceedebooks.xyz/escrows` lists registered escrows (none yet). The evidence hash is the SHA-256 of the canonical JSON of the facts above, so it can be recomputed from the public pull request: `GET https://api.github.com/repos/foundry-rs/forge-std/pulls/937`.
