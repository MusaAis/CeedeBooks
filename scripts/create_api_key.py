"""Create or revoke API keys. The key is printed once and only its SHA-256 hash is stored.

  python3 scripts/create_api_key.py create --role buyer --label musa
  python3 scripts/create_api_key.py create --role vendor --vendor-id 3 --label kudiarc
  python3 scripts/create_api_key.py create --role buyer --business acme --label acme-ops

A key belongs to one business (--business <slug>, default: ceedebooks) and can never act in another.
  python3 scripts/create_api_key.py revoke --id 2

Run from the repo/folder root with .env loaded (it needs CEEDEBOOKS_DB_PATH, same as the API).
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import auth, models  # noqa: E402


async def create(role: str, vendor_id, label: str, business_slug: str) -> None:
    await models.init_db()
    business = await models.get_business_by_slug(business_slug)
    if business is None:
        sys.exit(f"No business '{business_slug}'")
    if role == "vendor":
        if vendor_id is None:
            sys.exit("--vendor-id is required for a vendor key")
        if await models.get_vendor(business["id"], vendor_id) is None:
            sys.exit(f"No vendor with id {vendor_id} in business '{business_slug}'")
    elif vendor_id is not None:
        sys.exit("--vendor-id is only for vendor keys")
    key = auth.generate_key(role)
    key_id = await models.save_api_key(business["id"], auth.hash_key(key), role, vendor_id, label)
    print(f"key id {key_id} for business '{business_slug}' ({role}{f', vendor {vendor_id}' if vendor_id else ''}, label '{label}')")
    print(f"API key (shown once, store it now): {key}")


async def revoke(key_id: int) -> None:
    await models.init_db()
    print("revoked" if await models.revoke_api_key(key_id) else f"no key with id {key_id}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--role", choices=auth.ROLES, required=True)
    c.add_argument("--vendor-id", type=int)
    c.add_argument("--label", required=True)
    c.add_argument("--business", default="ceedebooks", help="business slug (default: ceedebooks)")
    r = sub.add_parser("revoke")
    r.add_argument("--id", type=int, required=True)
    args = parser.parse_args()
    if args.cmd == "create":
        asyncio.run(create(args.role, args.vendor_id, args.label, args.business))
    else:
        asyncio.run(revoke(args.id))


if __name__ == "__main__":
    main()