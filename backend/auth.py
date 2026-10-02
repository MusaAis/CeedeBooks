"""API-key authentication and role checks.

Roles:
  buyer   the business owner; full write access. Issued only by scripts/create_api_key.py on the server.
  vendor  bound to exactly one vendor_id; may submit and read only its own invoices.
  public  no key at all; read-only access to /decisions/* (the audit trail).

Keys are 256-bit random tokens, stored only as SHA-256 hashes, and shown once at creation.
Failed key attempts are throttled per client IP.
"""
import hashlib
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request

from agent.config import config
from backend import models
from backend.ratelimit import RateLimiter

BUYER = "buyer"
VENDOR = "vendor"
ROLES = (BUYER, VENDOR)  # "public" is the absence of a key, not a stored role
_MAX_KEY_LEN = 200

request_limiter = RateLimiter()      # every request, per client IP
failed_auth_limiter = RateLimiter()  # failed key attempts, per client IP


def generate_key(role: str) -> str:
    return f"cdb_{role}_{secrets.token_urlsafe(32)}"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def client_ip(request: Request) -> str:
    """Client address. Behind Caddy (TRUST_PROXY=1) use the entry the proxy appended to X-Forwarded-For;
    otherwise every caller would share the proxy's own address and one rate-limit bucket."""
    if config.trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


@dataclass(frozen=True)
class Principal:
    key_id: int
    role: str
    vendor_id: Optional[int]
    label: str


async def authenticate(request: Request, x_api_key: Optional[str] = Header(default=None)) -> Principal:
    ip = client_ip(request)
    if failed_auth_limiter.blocked(ip, config.failed_auth_per_min, 60.0):
        raise HTTPException(429, "Too many failed attempts; try again later", headers={"Retry-After": "60"})

    row = None
    if x_api_key and len(x_api_key) <= _MAX_KEY_LEN:
        row = await models.get_api_key_by_hash(hash_key(x_api_key))
    if row is None or row["revoked"]:
        failed_auth_limiter.allow(ip, config.failed_auth_per_min, 60.0)  # records the failure
        raise HTTPException(401, "Missing or invalid X-API-Key")
    return Principal(key_id=row["id"], role=row["role"], vendor_id=row["vendor_id"], label=row["label"])


def require(*roles: str):
    """Dependency: the caller must present a valid key whose role is one of `roles`."""

    async def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        if principal.role not in roles:
            raise HTTPException(403, "This API key's role cannot perform this action")
        return principal

    return dependency
