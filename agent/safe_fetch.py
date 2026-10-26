"""Fetch one public JSON document without letting a caller point the server at its own network.

Rules (Phase H): https only, port 443, a fixed host allowlist, every address the name resolves to must be public, the
address actually connected to must be public too, no redirects, a response size cap and a time cap. Anything else is
refused before a byte is sent, or discarded. There is no way to pass a user-supplied URL: callers build the URL from
validated parts and name the hosts they allow.
"""
import asyncio
import ipaddress
import json
import socket
from typing import Optional
from urllib.parse import urlsplit

import httpx

MAX_BYTES = 256 * 1024
TIMEOUT_SECONDS = 6.0

_NAT64 = ipaddress.ip_network("64:ff9b::/96")      # can embed a private IPv4 address behind a translator
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")   # same, for 6to4


class FetchRefused(Exception):
    """A rule was broken. Nothing was sent, or the answer was thrown away."""


class FetchFailed(Exception):
    """The remote answered, but not with a usable JSON document."""


_transport = None  # tests inject an httpx transport
_resolver = None   # tests inject a resolver: async (host) -> list of address strings


def is_public_ip(text: str) -> bool:
    """True only for a globally routable unicast address (never private, loopback, link-local, shared, reserved, multicast)."""
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(str(ip.ipv4_mapped))
        if ip in _NAT64 or ip in _SIX_TO_FOUR:
            return False
    return bool(ip.is_global) and not ip.is_multicast


def check_url(url: str, allowed_hosts) -> str:
    """The host of an acceptable URL; raises FetchRefused otherwise."""
    if not isinstance(url, str) or len(url) > 2048 or any(c in url for c in "\r\n\t \\"):
        raise FetchRefused("The address is not acceptable")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise FetchRefused("The address is not acceptable")
    if parts.scheme != "https":
        raise FetchRefused("Only https addresses are allowed")
    if parts.username is not None or parts.password is not None:
        raise FetchRefused("An address with credentials is not allowed")
    if port not in (None, 443):
        raise FetchRefused("Only port 443 is allowed")
    host = (parts.hostname or "").lower()
    if host not in {h.lower() for h in allowed_hosts}:
        raise FetchRefused("That host is not allowed")
    return host


async def _resolve(host: str) -> list:
    if _resolver is not None:
        return list(await _resolver(host))
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def _peer_ip(response) -> Optional[str]:
    """The address the connection actually went to, when the transport reports it."""
    stream = response.extensions.get("network_stream")
    if stream is None:
        return None
    addr = stream.get_extra_info("server_addr")
    return str(addr[0]) if addr else None


async def _get(url: str, headers: dict):
    # trust_env=False: a proxy set in the environment must not stand between us and the address we check
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=_transport, follow_redirects=False, trust_env=False) as client:
        async with client.stream("GET", url, headers=headers) as response:
            if 300 <= response.status_code < 400:
                raise FetchRefused("The host tried to redirect, which is not followed")
            peer = _peer_ip(response)
            if peer is None or not is_public_ip(peer):  # an unknown peer is refused, never assumed fine
                raise FetchRefused("The connection did not go to a public address")
            if response.status_code != 200:
                raise FetchFailed(f"The host answered {response.status_code}")
            declared = response.headers.get("content-length")
            if declared is not None and (not declared.isdigit() or int(declared) > MAX_BYTES):
                raise FetchRefused("The answer is too large")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise FetchRefused("The answer is too large")
    try:
        return json.loads(bytes(body))
    except ValueError:
        raise FetchFailed("The answer is not JSON")


async def fetch_json(url: str, allowed_hosts, headers: Optional[dict] = None):
    """GET one JSON document from an allowlisted https host. Raises FetchRefused (a rule) or FetchFailed (a bad answer)."""
    host = check_url(url, allowed_hosts)
    try:
        addresses = await asyncio.wait_for(_resolve(host), TIMEOUT_SECONDS)
    except (OSError, asyncio.TimeoutError):
        raise FetchFailed("The host could not be resolved")
    if not addresses or not all(is_public_ip(a) for a in addresses):
        raise FetchRefused("The host does not resolve to public addresses only")
    try:
        return await asyncio.wait_for(_get(url, headers or {}), TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise FetchFailed("The host took too long")
    except httpx.HTTPError:
        raise FetchFailed("The host could not be reached")
