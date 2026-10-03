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
