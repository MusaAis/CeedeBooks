"""List test invoices still labelled `agent` and, only after you confirm, relabel the ones you choose `demo`.

A payment to a wallet you control is a test, not agent traction. Run from the repo root with .env loaded:

  PYTHONPATH=. python3 scripts/relabel_origin.py list [--business ceedebooks] [--prefix INV-TEST] [--wallets 0xAAA,0xBBB] [--ids 3,4]
  PYTHONPATH=. python3 scripts/relabel_origin.py apply --ids 3,4,5 --code <code printed by list> [--business ...] [--prefix ...]

`list` changes nothing. `apply` re-reads exactly those rows, refuses if they changed since `list` or if any is not an
`agent` invoice of that business, shows them again, and changes them only if you type yes.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import models  # noqa: E402


def _code(business_id: int, rows: list) -> str:
    """Binds the business and the exact rows shown, so `apply` can only act on what `list` displayed."""
    shown = [[r["id"], r["invoice_number"], r["status"], r["amount_usdc"], r["wallet_address"]] for r in rows]
    return hashlib.sha256(json.dumps([business_id, shown], separators=(",", ":")).encode()).hexdigest()[:10]


def _show(rows: list, out) -> None:
    for r in rows:
        previous = f" (previous wallet {r['previous_wallet_address']})" if r["previous_wallet_address"] else ""
        out(f"  id {r['id']:>4}  {r['invoice_number']:<22} {r['status']:<10} {r['amount_usdc']:>10} USDC  {r['vendor_name']}  {r['wallet_address']}{previous}")


def _ids(text: str) -> list:
    try:
        return sorted({int(x) for x in text.split(",") if x.strip()})
    except ValueError:
        sys.exit("--ids must be numbers separated by commas")


async def run(argv: list, ask=input, out=print) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cmd", choices=["list", "apply"])
    parser.add_argument("--business", default="ceedebooks")
    parser.add_argument("--prefix", default="INV-TEST")
    parser.add_argument("--wallets", default=None, help="comma-separated payee wallets you control (list only)")
    parser.add_argument("--ids", default=None)
    parser.add_argument("--code", default=None)
    args = parser.parse_args(argv)

    await models.init_db()
    business = await models.get_business_by_slug(args.business)
    if business is None:
        out(f"No business '{args.business}'.")
        return 1
    wallets = [w.strip() for w in args.wallets.split(",") if w.strip()] if args.wallets else None
    rows = await models.list_relabel_candidates(business["id"], args.prefix, wallets)
    wanted = _ids(args.ids) if args.ids else None
    if wanted is not None:
        rows = [r for r in rows if r["id"] in wanted]

    if args.cmd == "list":
        out(f"{len(rows)} invoice(s) in '{args.business}' labelled agent, numbered {args.prefix}*" + (", paid to the wallets you gave" if wallets else "") + ":")
        _show(rows, out)
        if rows:
            ids = ",".join(str(r["id"]) for r in rows)
            out("\nNothing was changed. Check every wallet above is one you control. To relabel exactly these as demo:")
            out(f"  PYTHONPATH=. python3 scripts/relabel_origin.py apply --business {args.business} --prefix {args.prefix} --ids {ids} --code {_code(business['id'], rows)}")
        return 0

    if wanted is None or not args.code:
        out("apply needs --ids and the --code that `list` printed. Nothing was changed.")
        return 1
    missing = sorted(set(wanted) - {r["id"] for r in rows})
    if missing:
        out(f"Not agent-labelled {args.prefix}* invoices of '{args.business}': {missing}. Nothing was changed.")
        return 1
    if args.code != _code(business["id"], rows):
        out("The code does not match these rows (they changed, or you changed the list). Run `list` again. Nothing was changed.")
        return 1
    out(f"About to relabel {len(rows)} invoice(s) in '{args.business}' from agent to demo:")
    _show(rows, out)
    if ask("Type 'yes' to relabel them: ").strip().lower() != "yes":
        out("Aborted. Nothing was changed.")
        return 1
    changed = await models.relabel_agent_to_demo(business["id"], wanted)
    if changed != len(wanted):
        out("Nothing was changed: the rows were no longer all agent invoices.")
        return 1
    await models.log_admin_action(business["id"], "relabel-script", "relabel_demo", "invoices " + ",".join(map(str, wanted)))
    out(f"Relabelled {changed} invoice(s) to demo. They no longer count in the agent totals.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
