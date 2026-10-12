# Deploying the API at api.ceedebooks.xyz

Requirements: Ubuntu server, repo at `~/ceedebooks` with `.env` filled in (never committed), nginx, certbot (`python3-certbot-nginx`), ports 80 and 443 open (on Oracle Cloud: the VCN security list and the instance firewall).

1. **DNS** (Namecheap, Advanced DNS): `A` record, host `api`, value = the server's public IPv4, TTL automatic.
2. **Service:** `sudo cp deploy/ceedebooks-api.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now ceedebooks-api`
3. **Proxy:** `sudo cp deploy/nginx-api.conf /etc/nginx/sites-available/ceedebooks-api && sudo ln -s /etc/nginx/sites-available/ceedebooks-api /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
4. **HTTPS:** `sudo certbot --nginx -d api.ceedebooks.xyz` (certbot adds the 443 block, the redirect and auto-renewal).
5. **Check from outside the server:**
   - `curl -i https://api.ceedebooks.xyz/decisions/<hash>/verify` returns 200 JSON
   - `curl -i -X POST https://api.ceedebooks.xyz/vendors` returns 401 (no key)
6. **API keys:** `python3 scripts/create_api_key.py create --role vendor --vendor-id N --label <name>`. Never hand out the buyer key.

Notes: `TRUST_PROXY=1` is set in the unit so rate limits apply per real client (nginx overwrites `X-Forwarded-For`). The SQLite file (`ceedebooks.db` in the repo folder unless `CEEDEBOOKS_DB_PATH` is set) holds the audit log and keys: back it up and keep it out of git.

## Public proof page at ceedebooks.xyz

1. DNS: the `@` `A` record points at the server (already set).
2. Files: `sudo mkdir -p /var/www/ceedebooks && sudo cp site/index.html site/styles.css site/app.js site/verify.js /var/www/ceedebooks/` (re-run after any change to `site/`).
3. nginx: `sudo cp deploy/nginx-site.conf /etc/nginx/sites-available/ceedebooks-site && sudo ln -s /etc/nginx/sites-available/ceedebooks-site /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
4. HTTPS: `sudo certbot --nginx -d ceedebooks.xyz`
5. The page calls `api.ceedebooks.xyz` (allowed by the API's CORS default) and public Arc RPC endpoints (allowed by the page's Content-Security-Policy; the page also loads two Google Fonts families, allowed in the same policy). If you add another RPC to `site/verify.js`, add it to `connect-src` in the nginx file too.
6. After the API restarts on v1.3.0 the audit table gains a `chain_tx_hash` column automatically. Backfill the domain payment once:
   `python3 -c "import sqlite3,os; c=sqlite3.connect(os.environ.get('CEEDEBOOKS_DB_PATH','./ceedebooks.db')); c.execute(\"UPDATE audit_log SET chain_tx_hash='0x8c176242a84235a884d5790edf4a9aed403d87619359373e11f2e27141ce5d0d' WHERE reasoning_hash='46f65d7786a368e6e0814c938b8af9a7f6bed20bb1196478ac0a32708fa6f14a'\"); c.commit(); print(c.total_changes)"`
   It should print `1`.

## Admin site at admin.ceedebooks.xyz

Sign-in is a wallet signature from whoever `approver()` returns on the contract. There are no passwords or API keys to hand out.

1. DNS: add an `A` record, host `admin`, value = the server's public IPv4.
2. Files: `sudo mkdir -p /var/www/ceedebooks-admin && sudo cp admin/index.html admin/admin.css admin/wallet.js admin/admin.js /var/www/ceedebooks-admin/` (re-run after any change to `admin/`).
3. nginx: `sudo cp deploy/nginx-admin.conf /etc/nginx/sites-available/ceedebooks-admin && sudo ln -s /etc/nginx/sites-available/ceedebooks-admin /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
4. HTTPS: `sudo certbot --nginx -d admin.ceedebooks.xyz`
5. Restart the API once so CORS includes the admin origin (it is the default; set `ADMIN_ORIGIN` only to change it): `sudo systemctl restart ceedebooks-api`.
6. Open the site in a wallet app's browser (MetaMask mobile) or a browser with the MetaMask or Rabby extension.

### Moving the approver to a wallet that lives only in your wallet app

The approver was the deployer key in the server `.env`. Rotate it so a server breach cannot be an admin breach. Each step is an on-chain transaction: run it only after reading it.
1. Fund the new wallet with a little USDC for gas (plain transfer from the old key).
2. `proposeApprover(<new wallet>)` from the old key (`cast send`).
3. Open the admin site with the new wallet: it shows **Accept admin role**. One transaction later it is the approver.
4. Check with `cast call <BudgetEnforcer> "approver()(address)"`.
Keep the new wallet's seed phrase offline. If the approver wallet is lost, nobody can approve vendors or change limits.

## Vendor portal at portal.ceedebooks.xyz

Vendors apply and sign in with a wallet signature. No keys or passwords are issued, and the portal never asks a wallet for a transaction.

1. DNS: add an `A` record, host `portal`, value = the server's public IPv4.
2. Files: `sudo mkdir -p /var/www/ceedebooks-portal && sudo cp portal/index.html portal/business.html portal/portal.css portal/lib.js portal/portal.js portal/business.js portal/logo.png portal/favicon.png portal/apple-touch-icon.png /var/www/ceedebooks-portal/` (re-run after any change to `portal/`).
3. nginx: `sudo cp deploy/nginx-portal.conf /etc/nginx/sites-available/ceedebooks-portal && sudo ln -s /etc/nginx/sites-available/ceedebooks-portal /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
4. HTTPS: `sudo certbot --nginx -d portal.ceedebooks.xyz`
5. Restart the API so CORS includes the portal origin (it is the default; set `PORTAL_ORIGIN` only to change it): `sudo systemctl restart ceedebooks-api`. The first start after v1.2.7 adds the `origin` column to `invoices`, creates the application tables, and relabels the hand-run domain payment as `manual`.
6. Check from outside: `curl -s -X POST https://api.ceedebooks.xyz/apply/challenge -H 'Content-Type: application/json' -d '{"address":"0x0000000000000000000000000000000000000001"}'` returns a challenge, and `curl -i https://api.ceedebooks.xyz/admin/applications` returns 401.

Reviewing applications: the admin site has an **Applications** tab. Accepting one only creates the vendor record. The vendor cannot be paid until you approve its wallet on-chain in the **Vendors** tab with your own signature.

The portal loads its typeface (Bricolage Grotesque, the same as the proof page) from Google Fonts, so its Content-Security-Policy allows `fonts.googleapis.com` and `fonts.gstatic.com`. If you set the portal up before that line existed, update the live config in place (do not copy the repo file over it: certbot has edited the live one):
`sudo sed -i "s#style-src 'self'; img-src#style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src#" /etc/nginx/sites-available/ceedebooks-portal && sudo nginx -t && sudo systemctl reload nginx`

## Upgrading to v1.2.8.1 (multi-tenant backend)

The first start migrates the database in place: it adds `business_id` to every table, rebuilds `purchase_orders` and `invoices` so their numbers are unique per business, and records the live contract as business 1. Take a copy first and rehearse it on the copy:

1. Back up: `mkdir -p ~/backups && cp ceedebooks.db ~/backups/ceedebooks.db.pre-1.2.8.1` (from `~/ceedebooks`).
2. Rehearse: `CEEDEBOOKS_DB_PATH=/tmp/rehearsal.db` with a copy of the file, then `PYTHONPATH=. python3 -c "import asyncio; from backend import models; from agent import decision_log; asyncio.run(models.init_db()); asyncio.run(decision_log.init_db()); print('migrated')"` and check `sqlite3 /tmp/rehearsal.db "select id,slug,enforcer_address,contract_version,status from businesses; select count(*), min(business_id), max(business_id) from invoices;"` (one business, version 1, every invoice in business 1).
3. Pull, then `sudo systemctl restart ceedebooks-api`.
4. Re-copy the proof page: `sudo cp site/index.html site/styles.css site/app.js site/verify.js /var/www/ceedebooks/` (`verify.js` and `app.js` changed).
5. Existing API keys keep working (they become business 1's keys). Check `curl -s https://api.ceedebooks.xyz/stats` and the verify button on one old decision.

To go back: stop the service, restore `~/backups/ceedebooks.db.pre-1.2.8.1` and check out the previous tag. Do not run the old code on a migrated database.

## Upgrading to v1.2.8.2 (business onboarding)

No database migration to run by hand: the first start adds the `business_applications` table and two indexes, and keeps every row. Back up first anyway.

1. Back up: `mkdir -p ~/backups && cp ceedebooks.db ~/backups/ceedebooks.db.pre-1.2.8.2` (from `~/ceedebooks`).
2. Pull, then `sudo systemctl restart ceedebooks-api`. `BUDGET_FACTORY_ADDRESS` is optional: the deployed Arc Testnet factory is the default.
3. Re-copy the three sites (the proof page, the admin site and the portal all changed; the portal now also has `business.html` and `business.js`):
   - `sudo cp site/index.html site/styles.css site/app.js site/verify.js /var/www/ceedebooks/`
   - the admin site files, as in its section above
   - the portal files, as in the portal section above (the list now includes `business.html` and `business.js`)
4. Check from outside: `curl -s https://api.ceedebooks.xyz/businesses` lists one business (the home business); `curl -s -o /dev/null -w '%{http_code}\n' https://api.ceedebooks.xyz/operator/business-applications` prints 401; open `https://portal.ceedebooks.xyz/business.html` and confirm the Connect wallet button appears; sign in to the admin site and confirm the **Businesses** tab is there (only for the home business's admin).
5. The portal's nginx Content-Security-Policy needs no change (the page only calls the API and your wallet).

Accepting a business is one click in the Businesses tab; it creates that business's agent wallet. If Circle cannot create it, the tab shows "The agent wallet could not be created right now; nothing was accepted": try again in a minute, nothing was changed. Then tell the owner to open `business.html` with the same wallet. They fund their pool from their admin page with **Add funds** (never the wallet's normal Send: the contract only accepts a USDC token transfer) and send a little USDC to the agent wallet for fees. They create their contract, then fund it and the agent wallet's fee balance themselves. Mark a business as outside (admin site, Businesses tab) only after you have confirmed a real outside party owns it and real money is on the other side of its payments.

To go back: stop the service, restore `~/backups/ceedebooks.db.pre-1.2.8.2` and check out v1.2.8.1. The new table is ignored by the old code.

## Upgrading to v1.2.8.3 (shadow mode)

The first start adds `mode`, `real_amount` and `real_currency` to `invoices` (every existing invoice stays `live`) and creates the `decision_verdicts` table. No contract changes and no new environment variables.

1. Back up: `mkdir -p ~/backups && cp ceedebooks.db ~/backups/ceedebooks.db.pre-1.2.8.3` (from `~/ceedebooks`).
2. Pull, then `sudo systemctl restart ceedebooks-api`.
3. Re-copy the three sites (each changed): the proof page (`index.html`, `styles.css`, `app.js`, `verify.js`), the admin site (`admin.js`) and the portal (`business.js`), with the commands in their sections above.
4. Check from outside: `curl -s https://api.ceedebooks.xyz/businesses/ceedebooks/traction` returns the live and shadow blocks (shadow all zero, agreement `rate` null); `curl -s https://api.ceedebooks.xyz/stats` now has a `shadow` block.
5. Try it on testnet before an outside business does: in the admin site's Invoices tab submit a shadow invoice against a received purchase order, approve it, and open the transaction link on the proof card.

To go back: stop the service, restore `~/backups/ceedebooks.db.pre-1.2.8.3` and check out v1.2.8.2. The new columns and table are ignored by the old code.

## Upgrading to v1.2.9 (hash-chained ledger, archive, relabel tool)

The first start adds the `ledger` table and chains every existing decision once, in order, under a genesis entry per business. It needs a ledger signing key: **the API refuses to start without one**, because without it nothing could be logged and so nothing could be paid. The key is an Ed25519 seed, not a wallet key; it moves no money, but anyone who holds it can forge ledger entries.

1. Back up: `mkdir -p ~/backups && cp ceedebooks.db ~/backups/ceedebooks.db.pre-1.2.9` (from `~/ceedebooks`).
2. Install the new dependency: `pip install -r agent/requirements.txt` (adds `cryptography`).
3. Create the key without showing it: `python3 scripts/ledger_key.py new --env .env`. It prints only the public key and a key id. Keep a copy of `.env` in your backups: if the seed is lost, old entries can no longer be shown to be signed by the published key.
4. Pull or apply the patch, run the tests, then `sudo systemctl restart ceedebooks-api` and wait about 15 seconds. `sudo journalctl -u ceedebooks-api -n 30` should show no "Ledger ... FAILED" line.
5. Check from outside: `curl -s https://api.ceedebooks.xyz/.well-known/ledger-key` shows the public key (the same as step 3); `curl -s https://api.ceedebooks.xyz/ledger/verify` returns `"ok": true`, and so does `?business=<slug>` for each other business.
6. Re-copy the sites that changed: the proof page (`site/app.js`) and the admin site (`admin/admin.js`), with the commands in their sections above.

**Archiving a business** (operator, admin site, Businesses tab, **Archive**): it leaves the public list and totals, takes no new activity, and still verifies. The proof page says how many are archived, never which. **Unarchive** brings it back. The home business cannot be archived.

**Relabelling test invoices** (rule: a payment to a wallet you control is `demo`, not `agent`): `PYTHONPATH=. python3 scripts/relabel_origin.py list --wallets <your wallet>,<another>` shows the rows and changes nothing; it prints the exact `apply` command, which only works with the code `list` printed and a typed `yes`.

To go back: stop the service, restore `~/backups/ceedebooks.db.pre-1.2.9` and check out v1.2.8.3. Do not just run the old code on this database: decisions it logs would have no ledger entry and `/ledger/verify` would report them as unchained.

## Upgrading to v1.2.9.1 (owner-created categories, entered invoice amounts)

No contract change and no ledger or hash change. The first start adds the `categories` table and seeds the home business with categories 0 (Data Oracle) and 1 (Infrastructure), so its history reads the same. Every other business starts with no categories: **until its owner adds one and signs its limit, that business cannot create a purchase order or an invoice** (the admin Overview says so).

1. Back up: `mkdir -p ~/backups && cp ceedebooks.db ~/backups/ceedebooks.db.pre-1.2.9.1` (from `~/ceedebooks`).
2. Apply the patch, run `pytest agent/tests/` (set the CI placeholder variables first), then `sudo systemctl restart ceedebooks-api` and wait about 15 seconds. `sudo journalctl -u ceedebooks-api -n 30` should show no errors.
3. Re-copy the admin site and the vendor portal: `sudo cp admin/index.html admin/admin.css admin/wallet.js admin/admin.js /var/www/ceedebooks-admin/` and `sudo cp portal/index.html portal/business.html portal/portal.css portal/lib.js portal/portal.js portal/business.js /var/www/ceedebooks-portal/`.
4. Check: sign in to the admin site, open **Limits and safety**: the home business shows Data Oracle and Infrastructure with their limits and a Ready status. The **Purchase orders** form lists only Ready categories. The vendor portal's invoice form shows the order amount filled in and editable.
5. For each other business, its owner adds a category in **Limits and safety** and signs its daily limit (a wallet transaction); only then can it raise a purchase order.

To go back: stop the service, restore `~/backups/ceedebooks.db.pre-1.2.9.1` (or leave the database: the `categories` table is additive and the older code ignores it) and check out v1.2.9.

## Upgrading to v1.2.9.2 (on-chain ledger anchor)

No contract change, no hash change, no ledger change. The first start adds the `ledger_anchors` table. Nothing is sent on-chain until you run the anchor script yourself. Each business's own agent wallet pays the gas.

1. Back up (from `~/ceedebooks`): `mkdir -p ~/backups && cp ceedebooks.db ~/backups/ceedebooks.db.pre-1.2.9.2`.
2. Apply the patch, run `pytest agent/tests/` (set the CI placeholder variables first), then `sudo systemctl restart ceedebooks-api` and wait about 15 seconds. `sudo journalctl -u ceedebooks-api -n 30` should show no errors.
3. Re-copy the proof page: `sudo cp site/index.html site/styles.css site/app.js site/verify.js /var/www/ceedebooks/`.
4. Read-only check: `cd ~/ceedebooks && set -a && . ./.env && set +a && PYTHONPATH=. python3 scripts/anchor_ledger.py status` shows, per business, the agent wallet, its gas, the ledger head and the on-chain anchor.
5. First anchor per business (an on-chain transaction, sent from that business's agent wallet): `PYTHONPATH=. python3 scripts/anchor_ledger.py anchor --business ceedebooks`. It prints the call and asks for `yes`. Repeat with `--business <slug>` for each other business.
6. Check: `curl -s "https://api.ceedebooks.xyz/ledger/verify?business=ceedebooks"` shows `"fully_verified": true` and `anchor.status` `match`. On the proof page, press **Verify the whole chain**.
7. Only after every first anchor worked, add `LEDGER_ANCHOR_AUTO=1` to `.env` and restart the service. The job then anchors every 25 new entries or 6 hours, only when the head moved.

To go back: stop the service and check out v1.2.9.1; the `ledger_anchors` table is additive. An anchor already on-chain stays there. If the ledger is ever restored from an older backup, `/ledger/verify` will fail with `anchored_entries_missing`, which is the check working.
