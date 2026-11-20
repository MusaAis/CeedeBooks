"""scripts/health_watch.py: the checks, the alert rules and the posting, with no network (httpx.MockTransport)."""
import importlib.util
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location("health_watch", ROOT / "scripts" / "health_watch.py")
hw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hw)

HOOK = "https://discord.com/api/webhooks/123/secret-token-value"
GOOD = {"api /stats": (True, ""), "ledger chain": (True, "")}
BAD = {"api /stats": (False, "HTTP 502"), "ledger chain": (True, "")}


def _site(overrides=None, posts=None, post_status=204, post_raises=False):
    """A fake world: every route answers well unless `overrides` (path substring -> httpx.Response or Exception) says otherwise."""
    overrides = overrides or {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            if posts is not None:
                posts.append(json.loads(req.content))
            if post_raises:
                raise httpx.ConnectError(f"cannot reach {req.url}")
            return httpx.Response(post_status)
        for key, val in overrides.items():
            if key in str(req.url):
                if isinstance(val, Exception):
                    raise val
                return val
        u = str(req.url)
        if "/stats" in u:
            return httpx.Response(200, json={"submissions": {}, "shadow": {}})
        if "/businesses" in u:
            return httpx.Response(200, json=[{"slug": "ceedebooks"}])
        if "/ledger/verify" in u:
            return httpx.Response(200, json={"ok": True, "problem_count": 0})
        return httpx.Response(200, text="<html>page</html>")
    return httpx.Client(transport=httpx.MockTransport(handler))


def _run(client, tmp_path, env=None, argv=None, now=1_000_000.0):
    env = {"DISCORD_WEBHOOK_URL": HOOK, **(env or {})}
    return hw.main(argv or [], client=client, env=env, now=lambda: now, state_path=tmp_path / "state.json")


# the checks ---------------------------------------------------------------------------------------------------------------
def test_every_check_passes_when_everything_answers():
    assert all(ok for ok, _ in hw.run_checks(_site()).values())


def test_a_server_error_is_a_failure_with_its_status():
    r = hw.run_checks(_site({"/stats": httpx.Response(502)}))
    assert r["api /stats"] == (False, "HTTP 502")


def test_stats_that_is_not_the_expected_json_fails():
    assert hw.run_checks(_site({"/stats": httpx.Response(200, text="<html>nginx</html>")}))["api /stats"][0] is False
    assert hw.run_checks(_site({"/stats": httpx.Response(200, json={"other": 1})}))["api /stats"][0] is False


def test_an_empty_business_list_fails():
    assert hw.run_checks(_site({"/businesses": httpx.Response(200, json=[])}))["api /businesses"][0] is False


def test_a_ledger_with_problems_fails_and_says_how_many():
    r = hw.run_checks(_site({"/ledger/verify": httpx.Response(200, json={"ok": False, "problem_count": 3})}))
    assert r["ledger chain"] == (False, "the ledger reports 3 problem(s)")


def test_the_ledger_503_when_the_key_is_missing_fails():
    assert hw.run_checks(_site({"/ledger/verify": httpx.Response(503)}))["ledger chain"] == (False, "HTTP 503")


def test_a_site_that_is_down_fails_without_leaking_its_url():
    r = hw.run_checks(_site({"https://admin.ceedebooks.xyz": httpx.ConnectError("boom https://admin.ceedebooks.xyz/")}))
    assert r["admin site"] == (False, "ConnectError")


# the alert rules ----------------------------------------------------------------------------------------------------------
def test_no_message_while_everything_passes():
    state, msgs = hw.decide({}, GOOD, 100)
    assert msgs == [] and state["api /stats"]["fails"] == 0


def test_one_failure_alone_posts_nothing():
    state, msgs = hw.decide({}, BAD, 100)
    assert msgs == [] and state["api /stats"] == {"fails": 1, "alerted": False, "last_alert": 0}


def test_the_second_failure_in_a_row_posts_one_down_message():
    state, _ = hw.decide({}, BAD, 100)
    state, msgs = hw.decide(state, BAD, 700)
    assert msgs == [("api /stats", "DOWN: api /stats: HTTP 502 (2 checks in a row)")]
    assert state["api /stats"]["alerted"] is True


def test_a_failure_that_continues_does_not_repeat_before_six_hours_and_then_reminds():
    state = {}
    for t in (0, 600):
        state, _ = hw.decide(state, BAD, t)
    state, msgs = hw.decide(state, BAD, 1200)
    assert msgs == []
    state, msgs = hw.decide(state, BAD, 600 + hw.REMIND_EVERY)
    assert len(msgs) == 1 and msgs[0][1].startswith("DOWN")


def test_a_failure_then_a_pass_then_a_failure_never_alerts():
    state, m1 = hw.decide({}, BAD, 0)
    state, m2 = hw.decide(state, GOOD, 600)
    state, m3 = hw.decide(state, BAD, 1200)
    assert m1 == m2 == m3 == []


def test_recovery_posts_once_and_clears_the_state():
    state = {}
    for t in (0, 600):
        state, _ = hw.decide(state, BAD, t)
    state, msgs = hw.decide(state, GOOD, 1200)
    assert msgs == [("api /stats", "RECOVERED: api /stats answers again")]
    state, msgs = hw.decide(state, GOOD, 1800)
    assert msgs == [] and state["api /stats"]["alerted"] is False


def test_each_check_has_its_own_state():
    both_bad = {"api /stats": (False, "HTTP 500"), "ledger chain": (False, "HTTP 500")}
    state, _ = hw.decide({}, both_bad, 0)
    state, msgs = hw.decide(state, {"api /stats": (False, "HTTP 500"), "ledger chain": (True, "")}, 600)
    assert [n for n, _ in msgs] == ["api /stats"]


# the run ------------------------------------------------------------------------------------------------------------------
def test_a_healthy_run_posts_nothing_and_exits_zero(tmp_path):
    posts = []
    assert _run(_site(posts=posts), tmp_path) == 0 and posts == []


def test_two_failing_runs_post_once_with_no_mentions_and_exit_one(tmp_path):
    posts = []
    c = _site({"/stats": httpx.Response(502)}, posts=posts)
    assert _run(c, tmp_path, now=0) == 1 and posts == []
    assert _run(c, tmp_path, now=600) == 1
    assert len(posts) == 1 and "DOWN: api /stats: HTTP 502" in posts[0]["content"]
    assert posts[0]["allowed_mentions"] == {"parse": []}
    assert HOOK not in posts[0]["content"]


def test_the_alert_is_tried_again_when_discord_refuses_the_post(tmp_path, capsys):
    bad_post = _site({"/stats": httpx.Response(502)}, post_status=500)
    for t in (0, 600):
        _run(bad_post, tmp_path, now=t)
    assert json.loads((tmp_path / "state.json").read_text())["api /stats"]["alerted"] is False
    posts = []
    _run(_site({"/stats": httpx.Response(502)}, posts=posts), tmp_path, now=1200)
    assert len(posts) == 1 and "DOWN" in posts[0]["content"]


def test_the_webhook_url_is_never_printed_even_when_the_post_raises(tmp_path, capsys):
    c = _site({"/stats": httpx.Response(502)}, post_raises=True)
    for t in (0, 600):
        _run(c, tmp_path, now=t)
    out = capsys.readouterr()
    assert "secret-token-value" not in out.out + out.err and "alert not sent" in out.err


def test_without_a_webhook_it_says_so_and_keeps_the_alert_for_later(tmp_path, capsys):
    c = _site({"/stats": httpx.Response(502)})
    for t in (0, 600):
        assert _run(c, tmp_path, env={"DISCORD_WEBHOOK_URL": ""}, now=t) == 1
    assert "DISCORD_WEBHOOK_URL is not set" in capsys.readouterr().err
    assert json.loads((tmp_path / "state.json").read_text())["api /stats"]["alerted"] is False


def test_dry_run_prints_the_checks_and_posts_and_writes_nothing(tmp_path, capsys):
    posts = []
    c = _site({"/stats": httpx.Response(502)}, posts=posts)
    for t in (0, 600):
        assert _run(c, tmp_path, argv=["--dry-run"], now=t) == 1
    assert posts == [] and not (tmp_path / "state.json").exists()
    assert "FAIL  api /stats: HTTP 502" in capsys.readouterr().out


def test_test_webhook_posts_one_message(tmp_path):
    posts = []
    assert _run(_site(posts=posts), tmp_path, argv=["--test-webhook"]) == 0
    assert posts[0]["content"] == "CeedeBooks health watch: test message"


def test_a_corrupt_state_file_is_treated_as_empty(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    assert _run(_site(), tmp_path) == 0


def test_fail_after_can_be_lowered_for_the_one_time_stop_test(tmp_path):
    posts = []
    c = _site({"/stats": httpx.Response(502)}, posts=posts)
    _run(c, tmp_path, env={"HEALTH_FAIL_AFTER": "1"})
    assert len(posts) == 1


def test_base_urls_can_be_pointed_elsewhere_for_a_staging_check():
    urls = {u for _, u, _ in hw.plan({"api": "http://127.0.0.1:8004"})}
    assert "http://127.0.0.1:8004/stats" in urls


# the files ----------------------------------------------------------------------------------------------------------------
def test_the_timer_runs_every_ten_minutes_and_the_service_is_one_shot_with_no_secret():
    timer = (ROOT / "deploy" / "ceedebooks-health.timer").read_text()
    service = (ROOT / "deploy" / "ceedebooks-health.service").read_text()
    assert "OnUnitActiveSec=10min" in timer and "OnBootSec=2min" in timer
    assert "Type=oneshot" in service and "scripts/health_watch.py" in service
    assert "discord.com" not in service and "discord.com" not in timer


def test_the_env_example_names_the_webhook_but_holds_no_value():
    lines = (ROOT / ".env.example").read_text().splitlines()
    assert "DISCORD_WEBHOOK_URL=" in lines
    assert not any(l.startswith("DISCORD_WEBHOOK_URL=") and len(l) > len("DISCORD_WEBHOOK_URL=") for l in lines)
