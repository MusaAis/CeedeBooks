"""Circle Gateway: a read-only look at the unified USDC balance of a business's agent wallet.

POST {GATEWAY_API_URL}/v1/balances with {"token": "USDC", "sources": [{"domain": 26, "depositor": <address>}]}; no API key
or allowlist is needed. Nothing here moves money and nothing authorises from it: the pool balance on the contract stays the
only figure a payment is checked against. Any problem (network, status, shape, number) returns None, never zero.
"""
import logging
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx

from agent.config import config

ARC_DOMAIN = 26  # Arc, as listed by Circle
CACHE_SECONDS = 15
TIMEOUT_SECONDS = 8

log = logging.getLogger(__name__)
_cache: dict = {}  # address -> (balance, read at)
_transport = None  # tests inject an httpx transport


def reset() -> None:
    _cache.clear()


async def get_balance(depositor: str) -> Optional[float]:
    """The depositor's Gateway USDC balance on Arc, or None if it cannot be read exactly."""
    if not isinstance(depositor, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", depositor):
        return None
    key = depositor.lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < CACHE_SECONDS:
        return hit[0]
    body = {"token": "USDC", "sources": [{"domain": ARC_DOMAIN, "depositor": depositor}]}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=_transport) as client:
            resp = await client.post(config.gateway_api_url.rstrip("/") + "/v1/balances", json=body)
        if resp.status_code != 200:
            log.warning("Gateway balance request answered %s", resp.status_code)
            return None
        data = resp.json()
        if not isinstance(data, dict) or data.get("token") != "USDC" or not isinstance(data.get("balances"), list):
            return None
        total = Decimal(0)
        for row in data["balances"]:
            if not isinstance(row, dict) or row.get("domain") != ARC_DOMAIN or str(row.get("depositor", "")).lower() != key:
                continue  # not the row we asked for
            value = Decimal(str(row["balance"]))
            if not value.is_finite() or value < 0:
                return None
            total += value
    except (httpx.HTTPError, ValueError, KeyError, InvalidOperation):
        log.exception("Could not read the Gateway balance")
        return None
    result = float(total)  # a row for our domain may be absent when nothing was deposited: that is a real zero
    _cache[key] = (result, time.time())
    return result
