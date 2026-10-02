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
