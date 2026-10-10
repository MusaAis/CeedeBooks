"""Create or show the ledger signing key (an Ed25519 key that signs decision-ledger entries).

  python3 scripts/ledger_key.py new --env .env    adds LEDGER_SIGNING_KEY to that file; prints only the public key
  python3 scripts/ledger_key.py show              prints the public key of the configured key (run from the repo root)

The private seed is never printed. It is not a wallet key and moves no money, but anyone who holds it can forge ledger
entries, so keep it only in the server's .env (never in git, a chat or a log).
"""
import argparse
import hashlib
import os
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NAME = "LEDGER_SIGNING_KEY"


def _public(seed_hex: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _report(seed_hex: str) -> None:
    public = _public(seed_hex)
    print(f"public key: {public}")
    print(f"key id:     {hashlib.sha256(bytes.fromhex(public)).hexdigest()[:16]}")


def new(env_path: str) -> None:
    lines = open(env_path).read().splitlines() if os.path.exists(env_path) else []
    for line in lines:
        if line.startswith(NAME + "=") and line.split("=", 1)[1].strip():
            sys.exit(f"{env_path} already has a {NAME}. Changing it would break verification of every existing entry; nothing was changed.")
    seed = os.urandom(32).hex()
    lines = [l for l in lines if not l.startswith(NAME + "=")] + [f"{NAME}={seed}"]
    with open(env_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(env_path, 0o600)
    print(f"{NAME} added to {env_path} (the seed is not shown).")
    _report(seed)


def show() -> None:
    from agent.config import config
    if not config.ledger_signing_key:
        sys.exit(f"{NAME} is not set")
    _report(config.ledger_signing_key.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("--env", default=".env")
    sub.add_parser("show")
    args = parser.parse_args()
    new(args.env) if args.cmd == "new" else show()
