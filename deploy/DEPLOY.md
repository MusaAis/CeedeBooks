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
2. Files: `sudo mkdir -p /var/www/ceedebooks-portal && sudo cp portal/index.html portal/portal.css portal/lib.js portal/portal.js portal/logo.png portal/favicon.png portal/apple-touch-icon.png /var/www/ceedebooks-portal/` (re-run after any change to `portal/`).
3. nginx: `sudo cp deploy/nginx-portal.conf /etc/nginx/sites-available/ceedebooks-portal && sudo ln -s /etc/nginx/sites-available/ceedebooks-portal /etc/nginx/sites-enabled/ && sudo nginx -t && sudo systemctl reload nginx`
4. HTTPS: `sudo certbot --nginx -d portal.ceedebooks.xyz`
5. Restart the API so CORS includes the portal origin (it is the default; set `PORTAL_ORIGIN` only to change it): `sudo systemctl restart ceedebooks-api`. The first start after v1.2.7 adds the `origin` column to `invoices`, creates the application tables, and relabels the hand-run domain payment as `manual`.
6. Check from outside: `curl -s -X POST https://api.ceedebooks.xyz/apply/challenge -H 'Content-Type: application/json' -d '{"address":"0x0000000000000000000000000000000000000001"}'` returns a challenge, and `curl -i https://api.ceedebooks.xyz/admin/applications` returns 401.

Reviewing applications: the admin site has an **Applications** tab. Accepting one only creates the vendor record. The vendor cannot be paid until you approve its wallet on-chain in the **Vendors** tab with your own signature.

The portal loads its typeface (Bricolage Grotesque, the same as the proof page) from Google Fonts, so its Content-Security-Policy allows `fonts.googleapis.com` and `fonts.gstatic.com`. If you set the portal up before that line existed, update the live config in place (do not copy the repo file over it: certbot has edited the live one):
`sudo sed -i "s#style-src 'self'; img-src#style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src#" /etc/nginx/sites-available/ceedebooks-portal && sudo nginx -t && sudo systemctl reload nginx`
