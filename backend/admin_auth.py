"""Wallet sign-in for the admin site (no passwords, no keys).

The admin of a business is whoever that business's contract approver() returns right now. A visitor asks for a one-time
challenge for one business, signs it with their wallet (personal_sign, nothing is sent on-chain), and gets a short
session only if the signature recovers to that business's current approver. A session is bound to (address, business)
and dies the moment that approver changes; it never works on another business.

State is in memory: run one uvicorn worker (see README). A restart simply signs everyone out.
"""
import asyncio
import hashlib
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from eth_account import Account
from eth_account.messages import encode_defunct

from agent import contract
from agent.config import config
from backend import models

log = logging.getLogger("ceedebooks.admin")

CHALLENGE_TTL = 300      # seconds a challenge can be signed
SESSION_IDLE = 900       # a session dies after 15 minutes without use
SESSION_MAX = 7200       # and after 2 hours no matter what
MAX_CHALLENGES = 2000
APPROVER_CACHE = 15      # seconds the on-chain approver is cached
CHAIN_ID = 5042002       # Arc Testnet

_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SIG_RE = re.compile(r"^0x[a-fA-F0-9]{130}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

_challenges: dict = {}  # nonce -> {address, business_id, message, expires}
_sessions: dict = {}    # sha256(token) -> {address, business_id, issued, seen}
_approver_cache: dict = {}  # business id -> (approver, read at)


def reset() -> None:
    _challenges.clear()
    _sessions.clear()
    _approver_cache.clear()


def domain() -> str:
    return urlparse(config.admin_origin).netloc


async def current_approver(business: dict) -> Optional[str]:
    """Lower-case approver address of this business's contract, or None if it cannot be read (callers fail closed)."""
    now = time.time()
    cached = _approver_cache.get(business["id"])
    if cached and cached[0] and now - cached[1] < APPROVER_CACHE:
        return cached[0]
    try:
        value = (await asyncio.to_thread(contract.chain_for(business).approver)).lower()
    except Exception:
        log.exception("Could not read approver() for business %s", business["id"])
        return None
    _approver_cache[business["id"]] = (value, now)
    return value


def _purge(now: float) -> None:
    for nonce in [n for n, c in _challenges.items() if c["expires"] <= now]:
        del _challenges[nonce]
    for key in [k for k, s in _sessions.items() if now - s["seen"] > SESSION_IDLE or now - s["issued"] > SESSION_MAX]:
        del _sessions[key]


def make_challenge(address: str, business: dict) -> dict:
    """Returns {nonce, message}. When the store is full the oldest challenge is dropped, so a flood can slow sign-in but
    never lock the admin out."""
    if not _ADDRESS_RE.match(address):
        raise ValueError("bad address")
    now = time.time()
    _purge(now)
    while len(_challenges) >= MAX_CHALLENGES:
        del _challenges[next(iter(_challenges))]
    nonce = secrets.token_hex(16)
    issued = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    expires = datetime.fromtimestamp(now + CHALLENGE_TTL, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    message = (
        f"{domain()} wants you to sign in with your Ethereum account:\n{address}\n\n"
        f"Sign in to the CeedeBooks admin site for the business \"{business['slug']}\". This signs a message only: it sends no transaction and costs nothing.\n\n"
        f"URI: {config.admin_origin}\nVersion: 1\nChain ID: {CHAIN_ID}\nNonce: {nonce}\n"
        f"Issued At: {issued}\nExpiration Time: {expires}"
    )
    _challenges[nonce] = {"address": address.lower(), "business_id": business["id"], "message": message, "expires": now + CHALLENGE_TTL}
    return {"nonce": nonce, "message": message}


async def verify(nonce: str, signature: str) -> Optional[dict]:
    """Single-use: the challenge is consumed whether or not the signature is good. Returns session info or None."""
    if not _NONCE_RE.match(nonce or "") or not _SIG_RE.match(signature or ""):
        return None
    now = time.time()
    challenge = _challenges.pop(nonce, None)
    if challenge is None or challenge["expires"] <= now:
        return None
    try:
        recovered = Account.recover_message(encode_defunct(text=challenge["message"]), signature=signature).lower()
    except Exception:
        return None
    if recovered != challenge["address"]:
        return None
    business = await models.get_business(challenge["business_id"])
    if business is None:
        return None
    approver = await current_approver(business)
    if approver is None or recovered != approver:
        return None
    for key in [k for k, s in _sessions.items() if s["address"] == recovered and s["business_id"] == business["id"]]:
        del _sessions[key]  # one live session per admin and business
    token = "cdb_admin_" + secrets.token_urlsafe(32)
    _sessions[hashlib.sha256(token.encode()).hexdigest()] = {
        "address": recovered, "business_id": business["id"], "issued": now, "seen": now}
    return {"token": token, "address": recovered, "business": business["slug"], "business_id": business["id"], "expires_in": SESSION_IDLE}


async def validate_token(token: str) -> Optional[dict]:
    key = hashlib.sha256(token.encode()).hexdigest()
    session = _sessions.get(key)
    if session is None:
        return None
    now = time.time()
    if now - session["seen"] > SESSION_IDLE or now - session["issued"] > SESSION_MAX:
        _sessions.pop(key, None)
        return None
    business = await models.get_business(session["business_id"])
    approver = await current_approver(business) if business is not None else None
    if approver is None or approver != session["address"]:  # rotated, or chain unreadable: sign out
        _sessions.pop(key, None)
        return None
    session["seen"] = now
    return {"address": session["address"], "business_id": session["business_id"]}


def logout(token: str) -> None:
    _sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
