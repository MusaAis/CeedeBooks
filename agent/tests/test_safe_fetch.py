"""Phase H (v1.3.2.1): the fetch rules for evidence. Nothing here touches a network: the resolver, the transport and the
connected-peer lookup are injected, and a handler that records requests proves a refusal sent nothing."""
import asyncio

import httpx
import pytest

from agent import evidence, safe_fetch
from agent.safe_fetch import FetchFailed, FetchRefused

PUBLIC = "140.82.112.5"
HOST = "api.github.com"
URL = f"https://{HOST}/repos/acme/app/pulls/7"


class Net:
    """One scripted network: what the name resolves to, who answers, and which address the connection reached."""

    def __init__(self, monkeypatch, resolves=(PUBLIC,), peer=PUBLIC, handler=None):
        self.requests = []
        self.resolves, self.peer = (resolves if isinstance(resolves, Exception) else list(resolves)), peer
        self.handler = handler or (lambda request: httpx.Response(200, json={"ok": True}))

        async def resolver(host):
            if isinstance(self.resolves, Exception):
                raise self.resolves
            return self.resolves

        def serve(request):
            self.requests.append(str(request.url))
            return self.handler(request)

        monkeypatch.setattr(safe_fetch, "_resolver", resolver)
        monkeypatch.setattr(safe_fetch, "_transport", httpx.MockTransport(serve))
        monkeypatch.setattr(safe_fetch, "_peer_ip", lambda response: self.peer)


def fetch(url=URL, hosts=(HOST,)):
    return asyncio.run(safe_fetch.fetch_json(url, hosts, {}))


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                                "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::7f00:1",
                                "2002:7f00:1::", "192.0.2.1", "not-an-ip", ""])
def test_non_public_addresses_are_refused(ip):
    assert safe_fetch.is_public_ip(ip) is False


@pytest.mark.parametrize("ip", ["140.82.112.5", "8.8.8.8", "2606:4700::1"])
def test_public_addresses_pass(ip):
    assert safe_fetch.is_public_ip(ip) is True


@pytest.mark.parametrize("url", [
    "http://api.github.com/repos/a/b/pulls/1",              # not https
    "ftp://api.github.com/x", "file:///etc/passwd", "//api.github.com/x", "api.github.com/x",
    "https://api.github.com:8443/repos/a/b/pulls/1",         # other port
    "https://user:pw@api.github.com/repos/a/b/pulls/1",      # credentials
    "https://api.github.com@evil.example/repos/a/b/pulls/1",  # userinfo trick: the real host is evil.example
    "https://api.github.com.evil.example/repos/a/b/pulls/1",  # look-alike suffix
    "https://evil-api.github.com/repos/a/b/pulls/1",
    "https://github.com/a/b/pull/1",                          # a host that is not on the list
    "https://127.0.0.1/", "https://127.0.0.1:8004/", "https://169.254.169.254/latest/meta-data/", "https://[::1]/",
    "https://localhost/", "https://2130706433/",              # decimal form of 127.0.0.1
    "https://api.github.com\\@evil.example/", "https://api.github.com/a b", "https://api.github.com/a\r\nHost: x",
    "https://api.github.com/" + "a" * 2100, "", None, 42,
])
def test_bad_urls_are_refused_and_nothing_is_sent(monkeypatch, url):
    net = Net(monkeypatch)
    with pytest.raises(FetchRefused):
        fetch(url)
    assert net.requests == []


def test_the_host_check_is_case_insensitive_and_port_443_is_fine(monkeypatch):
    net = Net(monkeypatch)
    assert fetch("https://API.GitHub.com:443/repos/a/b/pulls/1") == {"ok": True}
    assert len(net.requests) == 1


def test_a_name_that_resolves_to_a_private_address_is_refused_before_connecting(monkeypatch):
    for bad in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "::1"):
        net = Net(monkeypatch, resolves=(bad,))
        with pytest.raises(FetchRefused):
            fetch()
        assert net.requests == []


def test_one_private_address_among_public_ones_refuses(monkeypatch):
    net = Net(monkeypatch, resolves=(PUBLIC, "10.0.0.9"))
    with pytest.raises(FetchRefused):
        fetch()
    assert net.requests == []


def test_no_addresses_and_unresolvable_hosts(monkeypatch):
    Net(monkeypatch, resolves=())
    with pytest.raises(FetchRefused):
        fetch()
    Net(monkeypatch, resolves=OSError("dns"))
    with pytest.raises(FetchFailed):
        fetch()


def test_dns_rebinding_the_connected_address_is_checked_too(monkeypatch):
    # the name looked public when resolved, but the connection went to a private address: the answer is thrown away
    net = Net(monkeypatch, peer="127.0.0.1")
    with pytest.raises(FetchRefused):
        fetch()
    assert len(net.requests) == 1  # it was sent, so only the discarding is guaranteed, and the body is never read


def test_an_unknown_peer_is_refused_not_assumed_fine(monkeypatch):
    Net(monkeypatch, peer=None)
    with pytest.raises(FetchRefused):
        fetch()


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_refused_and_not_followed(monkeypatch, status):
    net = Net(monkeypatch, handler=lambda r: httpx.Response(status, headers={"location": "http://169.254.169.254/"}))
    with pytest.raises(FetchRefused):
        fetch()
    assert net.requests == [URL]  # the second request was never made


def test_a_large_declared_answer_is_refused(monkeypatch):
    Net(monkeypatch, handler=lambda r: httpx.Response(200, headers={"content-length": str(safe_fetch.MAX_BYTES + 1)}, content=b"{}"))
    with pytest.raises(FetchRefused):
        fetch()


def test_a_large_streamed_answer_is_refused_without_a_length_header(monkeypatch):
    async def chunks():
        yield b'{"x": "'
        for _ in range(40):
            yield b"a" * 10_000  # 400 KB in pieces, no content-length

    def handler(request):
        response = httpx.Response(200, content=chunks())
        assert "content-length" not in response.headers
        return response

    Net(monkeypatch, handler=handler)
    with pytest.raises(FetchRefused):
        fetch()


def test_an_answer_at_the_cap_is_accepted(monkeypatch):
    body = b'"' + b"a" * (safe_fetch.MAX_BYTES - 2) + b'"'
    assert len(body) == safe_fetch.MAX_BYTES
    Net(monkeypatch, handler=lambda r: httpx.Response(200, content=body))
    assert len(fetch()) == safe_fetch.MAX_BYTES - 2


def test_bad_statuses_and_bad_bodies_fail_cleanly(monkeypatch):
    for status in (403, 404, 500):
        Net(monkeypatch, handler=lambda r, s=status: httpx.Response(s, json={"message": "no"}))
        with pytest.raises(FetchFailed):
            fetch()
    Net(monkeypatch, handler=lambda r: httpx.Response(200, content=b"<html>not json</html>"))
    with pytest.raises(FetchFailed):
        fetch()


def test_a_slow_host_times_out(monkeypatch):
    monkeypatch.setattr(safe_fetch, "TIMEOUT_SECONDS", 0.2)

    async def slow_resolver(host):
        await asyncio.sleep(1)
        return [PUBLIC]

    Net(monkeypatch)
    monkeypatch.setattr(safe_fetch, "_resolver", slow_resolver)
    with pytest.raises(FetchFailed):
        fetch()


def test_a_connection_error_fails_cleanly(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("refused")

    Net(monkeypatch, handler=boom)
    with pytest.raises(FetchFailed):
        fetch()


# ---- the GitHub evidence built on it

def pr(**over):
    data = {"number": 7, "merged": True, "merged_at": "2026-10-20T10:00:00Z", "merge_commit_sha": "a" * 40,
            "base": {"ref": "main", "repo": {"full_name": "Acme/App"}}, "user": {"login": "dev"}, "title": "ignored text"}
    data.update(over)
    return data


def get(monkeypatch, data, owner="acme", repo="app", number=7):
    net = Net(monkeypatch, handler=lambda r: httpx.Response(200, json=data))
    return net, asyncio.run(evidence.github_pr(owner, repo, number))


def test_a_merged_pull_request_gives_facts_and_a_stable_hash(monkeypatch):
    net, result = get(monkeypatch, pr())
    assert net.requests == ["https://api.github.com/repos/acme/app/pulls/7"]
    assert result["facts"] == {"kind": "github_pr_merged", "repo": "Acme/App", "pr": 7, "merged": True, "merged_at": "2026-10-20T10:00:00Z",
                               "merge_commit_sha": "a" * 40, "base_ref": "main", "author": "dev"}
    assert "title" not in str(result["facts"])  # free text from the pull request never enters the hash
    _, again = get(monkeypatch, pr(title="another title"))
    assert again["evidence_hash"] == result["evidence_hash"]
    assert result["evidence_hash"].startswith("0x") and len(result["evidence_hash"]) == 66


def test_the_hash_changes_with_the_facts(monkeypatch):
    _, merged = get(monkeypatch, pr())
    _, other = get(monkeypatch, pr(merge_commit_sha="b" * 40))
    _, open_ = get(monkeypatch, pr(merged=False, merged_at=None, merge_commit_sha=None))
    assert len({merged["evidence_hash"], other["evidence_hash"], open_["evidence_hash"]}) == 3
    assert open_["facts"]["merged"] is False and open_["facts"]["merged_at"] is None


def test_the_hash_does_not_depend_on_key_order():
    a = {"kind": "x", "pr": 1}
    assert evidence.evidence_hash(a) == evidence.evidence_hash({"pr": 1, "kind": "x"})


@pytest.mark.parametrize("data", [
    pr(number=8), pr(base={"ref": "main", "repo": {"full_name": "other/app"}}), pr(merged="yes"), pr(merged_at=None),
    pr(merge_commit_sha="xyz"), pr(merge_commit_sha=None), {"unexpected": True}, [], "text", pr(user=None),
])
def test_a_wrong_or_unexpected_shape_is_a_failure_not_a_guess(monkeypatch, data):
    Net(monkeypatch, handler=lambda r: httpx.Response(200, json=data))
    with pytest.raises(FetchFailed):
        asyncio.run(evidence.github_pr("acme", "app", 7))


@pytest.mark.parametrize("owner,repo,number", [
    ("acme/../x", "app", 7), ("-acme", "app", 7), ("acme", "app/../../x", 7), ("acme", "..", 7), ("acme", ".", 7), ("acme", "", 7),
    ("acme", "app", 0), ("acme", "app", -1), ("acme", "app", 10**9 + 1), ("acme", "app", True), ("acme", "app", "7"),
    ("a" * 40, "app", 7), ("acme", "a" * 101, 7), ("ac me", "app", 7), ("acme?x=1", "app", 7), ("acme", "app#frag", 7),
])
def test_invalid_targets_are_refused_and_nothing_is_sent(monkeypatch, owner, repo, number):
    net = Net(monkeypatch)
    with pytest.raises(FetchRefused):
        asyncio.run(evidence.github_pr(owner, repo, number))
    assert net.requests == []


def test_only_the_github_api_host_is_ever_used():
    assert evidence.GITHUB_API_HOST == "api.github.com"
    import inspect
    assert "http://" not in inspect.getsource(evidence)


def test_the_connected_address_is_read_from_a_real_connection():
    """Not a mock: a local server, a real socket, and the helper that the rebinding check relies on."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    async def go():
        async with httpx.AsyncClient(trust_env=False) as client:
            async with client.stream("GET", f"http://127.0.0.1:{srv.server_port}/") as response:
                return safe_fetch._peer_ip(response)

    try:
        peer = asyncio.run(go())
    finally:
        srv.shutdown()
    assert peer == "127.0.0.1"
    assert safe_fetch.is_public_ip(peer) is False


def test_a_proxy_in_the_environment_is_never_used(monkeypatch):
    seen = {}
    real = httpx.AsyncClient

    def spy(*a, **k):
        seen.update(k)
        return real(*a, **k)

    Net(monkeypatch)
    monkeypatch.setattr(safe_fetch.httpx, "AsyncClient", spy)
    fetch()
    assert seen["trust_env"] is False and seen["follow_redirects"] is False
