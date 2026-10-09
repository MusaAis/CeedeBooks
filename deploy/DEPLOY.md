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

Notes: `TRUST_PROXY=1` is set in the unit so rate limits apply per real client (nginx overwrites `X-Forwarded-For`). The SQLite file (`CEEDEBOOKS_DB_PATH`) holds the audit log and keys: back it up and keep it out of git.

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

1. Back up: `cp "$CEEDEBOOKS_DB_PATH" "$CEEDEBOOKS_DB_PATH.pre-1.2.8.1"` (use the path the systemd unit uses).
2. Rehearse: `CEEDEBOOKS_DB_PATH=/tmp/rehearsal.db` with a copy of the file, then `PYTHONPATH=. python3 -c "import asyncio; from backend import models; from agent import decision_log; asyncio.run(models.init_db()); asyncio.run(decision_log.init_db()); print('migrated')"` and check `sqlite3 /tmp/rehearsal.db "select id,slug,enforcer_address,contract_version,status from businesses; select count(*), min(business_id), max(business_id) from invoices;"` (one business, version 1, every invoice in business 1).
3. Pull, then `sudo systemctl restart ceedebooks-api`.
4. Re-copy the proof page: `sudo cp site/index.html site/styles.css site/app.js site/verify.js /var/www/ceedebooks/` (`verify.js` and `app.js` changed).
5. Existing API keys keep working (they become business 1's keys). Check `curl -s https://api.ceedebooks.xyz/stats` and the verify button on one old decision.

To go back: stop the service, restore the `.pre-1.2.8.1` copy and check out the previous tag. Do not run the old code on a migrated database.

## Upgrading to v1.2.8.2 (business onboarding)

No database migration to run by hand: the first start adds the `business_applications` table and two indexes, and keeps every row. Back up first anyway.

1. Back up: `cp "$CEEDEBOOKS_DB_PATH" "$CEEDEBOOKS_DB_PATH.pre-1.2.8.2"` (the path the systemd unit uses).
2. Pull, then `sudo systemctl restart ceedebooks-api`. `BUDGET_FACTORY_ADDRESS` is optional: the deployed Arc Testnet factory is the default.
3. Re-copy the three sites (the proof page, the admin site and the portal all changed; the portal now also has `business.html` and `business.js`):
   - `sudo cp site/index.html site/styles.css site/app.js site/verify.js /var/www/ceedebooks/`
   - the admin site files, as in its section above
   - the portal files, as in the portal section above (the list now includes `business.html` and `business.js`)
4. Check from outside: `curl -s https://api.ceedebooks.xyz/businesses` lists one business (the home business); `curl -s -o /dev/null -w '%{http_code}\n' https://api.ceedebooks.xyz/operator/business-applications` prints 401; open `https://portal.ceedebooks.xyz/business.html` and confirm the Connect wallet button appears; sign in to the admin site and confirm the **Businesses** tab is there (only for the home business's admin).
5. The portal's nginx Content-Security-Policy needs no change (the page only calls the API and your wallet).

Accepting a business is one click in the Businesses tab; it creates that business's agent wallet. If Circle cannot create it, the tab shows "The agent wallet could not be created right now; nothing was accepted": try again in a minute, nothing was changed. Then tell the owner to open `business.html` with the same wallet. They fund their pool from their admin page with **Add funds** (never the wallet's normal Send: the contract only accepts a USDC token transfer) and send a little USDC to the agent wallet for fees. They create their contract, then fund it and the agent wallet's fee balance themselves. Mark a business as outside (admin site, Businesses tab) only after you have confirmed a real outside party owns it and real money is on the other side of its payments.

To go back: stop the service, restore the `.pre-1.2.8.2` copy and check out v1.2.8.1. The new table is ignored by the old code.

## Upgrading to v1.2.8.3 (shadow mode)

The first start adds `mode`, `real_amount` and `real_currency` to `invoices` (every existing invoice stays `live`) and creates the `decision_verdicts` table. No contract changes and no new environment variables.

1. Back up: `cp "$CEEDEBOOKS_DB_PATH" "$CEEDEBOOKS_DB_PATH.pre-1.2.8.3"` (the path the systemd unit uses).
2. Pull, then `sudo systemctl restart ceedebooks-api`.
3. Re-copy the three sites (each changed): the proof page (`index.html`, `styles.css`, `app.js`, `verify.js`), the admin site (`admin.js`) and the portal (`business.js`), with the commands in their sections above.
4. Check from outside: `curl -s https://api.ceedebooks.xyz/businesses/ceedebooks/traction` returns the live and shadow blocks (shadow all zero, agreement `rate` null); `curl -s https://api.ceedebooks.xyz/stats` now has a `shadow` block.
5. Try it on testnet before an outside business does: in the admin site's Invoices tab submit a shadow invoice against a received purchase order, approve it, and open the transaction link on the proof card.

To go back: stop the service, restore the `.pre-1.2.8.3` copy and check out v1.2.8.2. The new columns and table are ignored by the old code.
