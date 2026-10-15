"""Gateway balance read: the request matches Circle's documented shape, and every failure is None, never a zero."""
import asyncio
import json

import httpx
import pytest

from agent import gateway
from agent.config import config

ADDR = "0x32B59A6988DCA955c641D529Ee80484200DE37b0"


def _serve(handler):
    gateway._transport = httpx.MockTransport(handler)


def _ok(balances):
    return lambda request: httpx.Response(200, json={"token": "USDC", "balances": balances})


def _get(addr=ADDR):
    return asyncio.run(gateway.get_balance(addr))


def test_request_matches_the_documented_shape():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": ADDR, "balance": "2.500000"}]})

    _serve(handler)
    assert _get() == 2.5
    assert seen["url"] == config.gateway_api_url.rstrip("/") + "/v1/balances"
    assert seen["body"] == {"token": "USDC", "sources": [{"domain": 26, "depositor": ADDR}]}
    assert "authorization" not in {k.lower() for k in httpx.Request("POST", seen["url"]).headers}  # no key is sent


def test_depositor_match_ignores_case():
    _serve(_ok([{"domain": 26, "depositor": ADDR.lower(), "balance": "1.25"}]))
    assert _get() == 1.25


def test_no_row_for_our_domain_after_a_good_answer_is_a_real_zero():
    _serve(_ok([]))
    assert _get() == 0.0


def test_rows_for_other_domains_or_depositors_are_not_counted():
    _serve(_ok([{"domain": 0, "depositor": ADDR, "balance": "9"}, {"domain": 26, "depositor": "0x" + "11" * 20, "balance": "7"}]))
    assert _get() == 0.0


@pytest.mark.parametrize("answer", [
    httpx.Response(503), httpx.Response(400, json={"message": "Bad request."}), httpx.Response(200, text="not json"),
    httpx.Response(200, json=[]), httpx.Response(200, json={"token": "EURC", "balances": []}), httpx.Response(200, json={"token": "USDC"}),
    httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": ADDR, "balance": "abc"}]}),
    httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": ADDR, "balance": "-1"}]}),
    httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": ADDR, "balance": "NaN"}]}),
    httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": ADDR}]}),
])
def test_anything_unreadable_is_none_not_zero(answer):
    _serve(lambda request: answer)
    assert _get() is None


def test_network_error_is_none():
    def boom(request):
        raise httpx.ConnectTimeout("timed out")

    _serve(boom)
    assert _get() is None


@pytest.mark.parametrize("bad", ["", "0x123", "not an address", None, 5, "0x" + "zz" * 20])
def test_a_bad_address_is_none_and_nothing_is_sent(bad):
    def never(request):
        raise AssertionError("must not call the API")

    _serve(never)
    assert asyncio.run(gateway.get_balance(bad)) is None


def test_a_failure_is_not_cached_and_a_success_is():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"token": "USDC", "balances": [{"domain": 26, "depositor": ADDR, "balance": "3"}]})

    _serve(handler)
    assert _get() is None and _get() == 3.0 and _get() == 3.0
    assert len(calls) == 2  # the failure was retried, the success was served from the cache
