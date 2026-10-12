"""Anchor a business's ledger head on-chain (LedgerAnchor), from that business's own agent wallet.

  python3 scripts/anchor_ledger.py status
  python3 scripts/anchor_ledger.py anchor --business ceedebooks

status only reads: per business, the agent address, its gas, the ledger head and what is anchored on-chain.
anchor shows exactly what it will send, asks for "yes", sends once, waits, reads the chain back, then records it.
The first anchor of each business is made this way; the background job (LEDGER_ANCHOR_AUTO=1) takes over after it.

Run from the repo/folder root with .env loaded.
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import anchor, contract  # noqa: E402
from agent.config import config  # noqa: E402
from backend import models  # noqa: E402


async def status() -> None:
    await models.init_db()
    await anchor.ensure_schema()
    print(f"anchor contract {config.ledger_anchor_address}")
    for b in await models.list_businesses("active"):
        head = await anchor.head_of(b["id"])
        found = await anchor.check(b)
        agent = found.get("agent") or b.get("agent_address")
        try:
            gas = f"{contract.native_balance(agent) / 10 ** 18:.4f}"
        except Exception as e:
            gas = f"unreadable ({e})"
        print(f"\n{b['slug']}\n  agent wallet   {agent}\n  gas            {gas}\n  ledger head    "
              f"{f'{head[0]} {head[1]}' if head else 'none'}\n  on-chain       {found['status']}"
              + (f", sequence {found['anchored_sequence']}" if found.get("checked") else ""))


async def send(slug: str) -> None:
    await models.init_db()
    await anchor.ensure_schema()
    b = await models.get_business_by_slug(slug)
    if b is None:
        sys.exit(f"No business '{slug}'")
    head = await anchor.head_of(b["id"])
    if head is None or head[0] < 1:
        sys.exit("Nothing to anchor: the ledger has no entries yet")
    agent = contract.chain_for(b).agent()
    print(f"business        {slug}\nfrom (agent)    {agent}\nwallet id       {b.get('circle_wallet_id')}\n"
          f"contract        {config.ledger_anchor_address}\ncall            anchor({head[1]}, {head[0]})")
    if input('Send this transaction? Type "yes": ').strip() != "yes":
        sys.exit("Cancelled, nothing sent")
    try:
        r = await anchor.anchor_now(b, manual=True)
    except anchor.AnchorError as e:
        sys.exit(f"Not anchored: {e}")
    print(r)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    a = sub.add_parser("anchor")
    a.add_argument("--business", required=True, help="business slug")
    args = p.parse_args()
    asyncio.run(status() if args.cmd == "status" else send(args.business))


if __name__ == "__main__":
    main()
