"""Wallet signatures for the vendor portal: applying to become a vendor, and signing in as one.

Same idea as the admin sign-in (backend/admin_auth.py): a one-time challenge is signed with personal_sign (EIP-191).
Nothing is sent on-chain and no key or password is ever issued. Any wallet that can sign a plain message works, which
includes a program holding a key. Each challenge is bound to one purpose, so a signature made to sign in cannot be
replayed as an application, and the other way round. A session is bound to one vendor and ends if that vendor's wallet
on file changes.

State is in memory: run one uvicorn worker. A restart simply signs everyone out.
"""
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from eth_account import Account
from eth_account.messages import encode_defunct

from agent.config import config
from backend import models

CHALLENGE_TTL = 300
SESSION_IDLE = 900       # 15 minutes without use
SESSION_MAX = 7200       # and 2 hours no matter what
MAX_CHALLENGES = 2000
CHAIN_ID = 5042002       # Arc Testnet
APPLY, SIGN_IN = "apply", "signin"
BUSINESS_APPLY, BUSINESS_STATUS = "business_apply", "business_status"  # a new business is not a business yet: no business in the challenge

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SIG_RE = re.compile(r"^0x[a-fA-F0-9]{130}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
_STATEMENT = {
    APPLY: "Apply to become a CeedeBooks vendor with this wallet. This signs a message only: it sends no transaction and costs nothing.",
    SIGN_IN: "Sign in to the CeedeBooks vendor portal. This signs a message only: it sends no transaction and costs nothing.",
    BUSINESS_APPLY: "Apply to register a business on CeedeBooks with this wallet, which would become its owner. This signs a message only: it sends no transaction and costs nothing.",
    BUSINESS_STATUS: "Show the status of the business applications made with this wallet. This signs a message only: it sends no transaction and costs nothing.",
}

_challenges: dict = {}  # nonce -> {address, purpose, business_id, expires}
_sessions: dict = {}    # sha256(token) -> {business_id, vendor_id, address, issued, seen}


def reset() -> None:
    _challenges.clear()
    _sessions.clear()


def domain() -> str:
    return urlparse(config.portal_origin).netloc


def _purge(now: float) -> None:
    for nonce in [n for n, c in _challenges.items() if c["expires"] <= now]:
        del _challenges[nonce]
    for key in [k for k, s in _sessions.items() if now - s["seen"] > SESSION_IDLE or now - s["issued"] > SESSION_MAX]:
        del _sessions[key]


def make_challenge(address: str, purpose: str, business: Optional[dict]) -> dict:
    """Returns {nonce, message}. When the store is full the oldest challenge is dropped: a flood can slow sign-in, never block it."""
    if purpose not in _STATEMENT or not _ADDRESS_RE.match(address):
        raise ValueError("bad request")
    now = time.time()
    _purge(now)
    while len(_challenges) >= MAX_CHALLENGES:
        del _challenges[next(iter(_challenges))]
    nonce = secrets.token_hex(16)
    issued = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    expires = datetime.fromtimestamp(now + CHALLENGE_TTL, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = (
        f"{domain()} wants you to sign in with your Ethereum account:\n{address}\n\n{_STATEMENT[purpose]}\n{'Business: ' + business['slug'] if business else 'For a new business'}\n\n"
        f"URI: {config.portal_origin}\nVersion: 1\nChain ID: {CHAIN_ID}\nNonce: {nonce}\n"
        f"Issued At: {issued}\nExpiration Time: {expires}"
    )
    _challenges[nonce] = {"address": address.lower(), "purpose": purpose, "business_id": business["id"] if business else 0, "message": message,
                          "expires": now + CHALLENGE_TTL}
    return {"nonce": nonce, "message": message}


def consume(nonce: str, signature: str, purpose: str) -> Optional[tuple]:
    """Single-use: the challenge is spent whether or not the signature is good. Returns (lower-case signer, business id)
    for the business the challenge was issued for, or None."""
    if not _NONCE_RE.match(nonce or "") or not _SIG_RE.match(signature or ""):
        return None
    challenge = _challenges.pop(nonce, None)
    if challenge is None or challenge["expires"] <= time.time() or challenge["purpose"] != purpose:
        return None
    try:
        recovered = Account.recover_message(encode_defunct(text=challenge["message"]), signature=signature).lower()
    except Exception:
        return None
    return (recovered, challenge["business_id"]) if recovered == challenge["address"] else None


def start_session(business_id: int, vendor_id: int, address: str) -> dict:
    now = time.time()
    for key in [k for k, s in _sessions.items() if s["vendor_id"] == vendor_id and s["business_id"] == business_id]:
        del _sessions[key]  # one live session per vendor
    token = "cdb_vendor_" + secrets.token_urlsafe(32)
    _sessions[hashlib.sha256(token.encode()).hexdigest()] = {
        "business_id": business_id, "vendor_id": vendor_id, "address": address, "issued": now, "seen": now}
    return {"token": token, "vendor_id": vendor_id, "address": address, "expires_in": SESSION_IDLE}


async def validate_token(token: str) -> Optional[dict]:
    key = hashlib.sha256(token.encode()).hexdigest()
    session = _sessions.get(key)
    if session is None:
        return None
    now = time.time()
    if now - session["seen"] > SESSION_IDLE or now - session["issued"] > SESSION_MAX:
        _sessions.pop(key, None)
        return None
    vendor = await models.get_vendor(session["business_id"], session["vendor_id"])
    if vendor is None or vendor["wallet_address"].lower() != session["address"]:  # wallet changed: sign out
        _sessions.pop(key, None)
        return None
    session["seen"] = now
    return {"business_id": session["business_id"], "vendor_id": session["vendor_id"], "address": session["address"]}


def logout(token: str) -> None:
    _sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
