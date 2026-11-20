#!/usr/bin/env python3
"""Health watch (v1.3.3.4): checks CeedeBooks' public routes and sites from outside the API process and posts to a Discord webhook
when one of them stops answering. Run it from a systemd timer every 10 minutes (deploy/ceedebooks-health.timer). It reads and posts only:
it calls public GET routes, holds no wallet or signing key, and never touches the database.

  python3 scripts/health_watch.py                  run the checks, post if something changed
  python3 scripts/health_watch.py --dry-run        run the checks and print them; post nothing, write no state
  python3 scripts/health_watch.py --test-webhook   post one test message

A check must fail twice in a row (HEALTH_FAIL_AFTER, default 2) before a DOWN message is posted, so one slow answer does not page
anyone. A failing check posts again at most every 6 hours, and one RECOVERED message follows when it passes again. The webhook URL
is DISCORD_WEBHOOK_URL in .env; it is never printed or logged. Exit code 0: every check passed; 1: a check failed or a post failed.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 10.0
FAIL_AFTER = 2
REMIND_EVERY = 6 * 3600
BASES = {"api": "https://api.ceedebooks.xyz", "site": "https://ceedebooks.xyz",
         "admin": "https://admin.ceedebooks.xyz", "portal": "https://portal.ceedebooks.xyz"}


def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def check_stats(resp):
    data = _json(resp)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    return (True, "") if isinstance(data, dict) and "submissions" in data else (False, "the answer is not the expected JSON")


def check_businesses(resp):
    data = _json(resp)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    return (True, "") if isinstance(data, (list, dict)) and data else (False, "the answer is empty or not JSON")


def check_ledger(resp):
    data = _json(resp)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    if not isinstance(data, dict) or "ok" not in data:
        return False, "the answer is not the expected JSON"
    return (True, "") if data["ok"] is True else (False, f"the ledger reports {data.get('problem_count', '?')} problem(s)")


def check_page(resp):
    return (True, "") if resp.status_code == 200 else (False, f"HTTP {resp.status_code}")


def plan(bases=None):
    b = {**BASES, **(bases or {})}
    return [("api /stats", b["api"] + "/stats", check_stats),
            ("api /businesses", b["api"] + "/businesses", check_businesses),
            ("ledger chain", b["api"] + "/ledger/verify?business=ceedebooks", check_ledger),
            ("proof page", b["site"] + "/", check_page),
            ("admin site", b["admin"] + "/", check_page),
            ("vendor portal", b["portal"] + "/", check_page)]


def run_checks(client, bases=None) -> dict:
    """name -> (ok, detail). A network error is a failure whose detail is only the error's type, never the URL."""
    out = {}
    for name, url, check in plan(bases):
        try:
            out[name] = check(client.get(url, timeout=TIMEOUT))
        except Exception as e:  # noqa: BLE001 - any failure to answer is a failed check
            out[name] = (False, type(e).__name__)
    return out


def decide(state: dict, results: dict, now: float, fail_after: int = FAIL_AFTER, remind: float = REMIND_EVERY):
    """Returns (new state, [(check name, message)]). Pure: it posts and writes nothing."""
    new, msgs = {}, []
    for name, (ok, detail) in results.items():
        old = state.get(name) or {}
        fails, alerted, last = int(old.get("fails", 0)), bool(old.get("alerted", False)), float(old.get("last_alert", 0))
        if ok:
            if alerted:
                msgs.append((name, f"RECOVERED: {name} answers again"))
            new[name] = {"fails": 0, "alerted": False, "last_alert": 0}
            continue
        fails += 1
        if fails >= fail_after and (not alerted or now - last >= remind):
            msgs.append((name, f"DOWN: {name}: {detail} ({fails} checks in a row)"))
            alerted, last = True, now
        new[name] = {"fails": fails, "alerted": alerted, "last_alert": last}
    return new, msgs


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def post(client, webhook: str, text: str) -> bool:
    """One Discord message that mentions no one. False on any failure; the URL is never printed."""
    try:
        r = client.post(webhook, json={"content": text[:1900], "allowed_mentions": {"parse": []}}, timeout=TIMEOUT)
        return r.status_code in (200, 204)
    except Exception:  # noqa: BLE001
        return False


def main(argv=None, *, client=None, env=None, now=None, state_path=None) -> int:
    ap = argparse.ArgumentParser(description="CeedeBooks health watch")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-webhook", action="store_true")
    args = ap.parse_args(argv)
    if env is None:
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        env = os.environ
    own = client is None
    client = client or httpx.Client()
    try:
        webhook = (env.get("DISCORD_WEBHOOK_URL") or "").strip()
        if args.test_webhook:
            if not webhook:
                print("DISCORD_WEBHOOK_URL is not set in .env", file=sys.stderr)
                return 1
            ok = post(client, webhook, "CeedeBooks health watch: test message")
            print("test message posted" if ok else "the test message was not accepted", file=None if ok else sys.stderr)
            return 0 if ok else 1
        results = run_checks(client, {k: env[f"HEALTH_{k.upper()}"] for k in BASES if env.get(f"HEALTH_{k.upper()}")})
        for name, (ok, detail) in results.items():
            print(("ok    " + name) if ok else f"FAIL  {name}: {detail}")
        failing = any(not ok for ok, _ in results.values())
        if args.dry_run:
            return 1 if failing else 0
        path = Path(state_path) if state_path else Path(env.get("HEALTH_STATE") or Path.home() / ".ceedebooks-health.json")
        old = load_state(path)
        new, msgs = decide(old, results, (now or time.time)(), int(env.get("HEALTH_FAIL_AFTER") or FAIL_AFTER))
        sent = True
        if msgs:
            if not webhook:
                print("alert not sent: DISCORD_WEBHOOK_URL is not set in .env", file=sys.stderr)
                sent = False
            else:
                sent = post(client, webhook, "CeedeBooks health\n" + "\n".join(m for _, m in msgs))
                if not sent:
                    print("alert not sent: Discord did not accept the post", file=sys.stderr)
        if not sent:  # keep the old alert flags so the same alert is tried again on the next run
            for name, _ in msgs:
                prev = old.get(name) or {}
                new[name] = {**new[name], "alerted": bool(prev.get("alerted", False)), "last_alert": float(prev.get("last_alert", 0))}
        try:
            save_state(path, new)
        except OSError:
            print("could not write the state file", file=sys.stderr)
            sent = False
        return 0 if (not failing and sent) else 1
    finally:
        if own:
            client.close()


if __name__ == "__main__":
    sys.exit(main())
