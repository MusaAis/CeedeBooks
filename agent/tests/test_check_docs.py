"""scripts/check_docs.py: each rule has a failing and a passing fixture (a tiny tree built in a temp folder)."""
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("check_docs", Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py")
cd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cd)

GOOD = "0x9752F995739C8A171AbD24944925EaF4d2971dd5"
BAD = GOOD.replace("EaF4", "Eaf4")  # one letter's case is wrong: fails EIP-55
TESTING = "**111 Foundry tests** (24 for one contract, 87 for the rest) and **755 Python tests**.\n"


def tree(tmp_path, files: dict) -> Path:
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def run(check, root):
    return check(root)


# 1 counts
def test_counts_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"docs/testing.md": TESTING, "README.md": "![b](https://x/badge/pytest-755%2F755%20passing-green)\n755 Python tests, 111 Foundry tests, 24 Foundry tests.\n| 111 Foundry, 755 Python, 6 Node |\n"})
    assert run(cd.check_counts, ok) == []
    bad = tree(tmp_path / "bad", {"docs/testing.md": TESTING, "README.md": "717 Python tests\n110 Foundry tests\n| 111 Foundry, 741 Python |\n![b](https://x/pytest-741%2F741%20passing-green)\n"})
    out = run(cd.check_counts, bad)
    assert len(out) == 4 and any("717 Python tests" in x for x in out) and any("badge" in x for x in out)


# 2 addresses
def test_addresses_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"README.md": f"{GOOD} and the lowercase copy {GOOD.lower()}\n"})
    assert run(cd.check_addresses, ok) == []
    bad = tree(tmp_path / "bad", {"README.md": f"first\n{BAD}\n", "docs/rollouts/a.md": f"cast call {BAD} x\n"})
    out = run(cd.check_addresses, bad)
    assert len(out) == 2 and GOOD in out[0] and "README.md:2" in out[0]


def test_a_longer_hex_string_is_not_an_address(tmp_path):
    ok = tree(tmp_path, {"README.md": "hash 0x" + "aB" * 32 + "\n"})
    assert run(cd.check_addresses, ok) == []


# 3 links
def test_links_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"README.md": "[a](docs/x.md#the-title) [b](#top) [c](https://e.org)\n# Top\n", "docs/x.md": "# The title\n"})
    assert run(cd.check_links, ok) == []
    bad = tree(tmp_path / "bad", {"README.md": "[a](docs/missing.md) [b](docs/x.md#nope) [c](#nowhere)\n", "docs/x.md": "# The title\n"})
    out = run(cd.check_links, bad)
    assert len(out) == 3 and "missing.md" in out[0] and "#nope" in out[1] and "#nowhere" in out[2]


def test_links_inside_code_are_ignored(tmp_path):
    ok = tree(tmp_path, {"README.md": "`[a](nope.md)`\n```\n[b](nope.md)\n```\n"})
    assert run(cd.check_links, ok) == []


# 4 private words
def test_private_words_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"README.md": "A normal sentence with the rule of three and a 7 day rule.\n", "agent/a.py": "# fail closed\n"})
    assert run(cd.check_private_words, ok) == []
    words = ["hand" + "off", "build " + "chat", "(rule " + "17)", "Alj" + "osa", "Co-Auth" + "ored-By: x", "Cla" + "ude", "Anthro" + "pic", "generated " + "with"]
    bad = tree(tmp_path / "bad", {f"docs/f{i}.md": f"text {w} text\n" for i, w in enumerate(words)})
    assert len(run(cd.check_private_words, bad)) == len(words)


# 5 doubles
def test_doubles_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"README.md": "# T\n\nOne sentence that is long enough to count here.\n\nAnother, different sentence that is also long enough.\n\n---\n\n## S\n"})
    assert run(cd.check_doubles, ok) == []
    s = "Everything below is checkable on-chain; nothing is estimated."
    bad = tree(tmp_path / "bad", {"docs/a.md": f"# T\n\n{s}\n\nEverything below is checkable on-chain. Nothing here is estimated.\n",
                                  "README.md": "x\n\n---\n\n---\n\n## S\n",
                                  "docs/b.md": "One sentence that is long enough to count. One sentence that is long enough to count.\n"})
    out = run(cd.check_doubles, bad)
    assert len(out) == 3 and any("two horizontal rules" in x for x in out) and any("next paragraph" in x for x in out) and any("one paragraph" in x for x in out)


# 6 deploy
CHANGELOG = "**v1.3.1: x**\n- a\n\n**v1.3.0: y**\n- b\n\n**v1.2.1: old, before the first section**\n"


def test_deploy_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"CHANGELOG.md": CHANGELOG, "deploy/DEPLOY.md": "## Upgrading to v1.3.1 (x)\n\n## Upgrading to v1.3.0 (y)\n"})
    assert run(cd.check_deploy, ok) == []
    wrong_order = tree(tmp_path / "wrong", {"CHANGELOG.md": CHANGELOG, "deploy/DEPLOY.md": "## Upgrading to v1.3.0 (y)\n\n## Upgrading to v1.3.1 (x)\n"})
    assert any("newest first" in x for x in run(cd.check_deploy, wrong_order))
    missing = tree(tmp_path / "missing", {"CHANGELOG.md": CHANGELOG, "deploy/DEPLOY.md": "## Upgrading to v1.3.1 (x)\n"})
    assert run(cd.check_deploy, missing) == ["deploy/DEPLOY.md: no upgrade section for v1.3.0 (a docs-only release gets a one-line section)"]


# 7 api
MAIN = '@app.get("/a/{x}")\nasync def a(): pass\n@app.post("/b")\nasync def b(): pass\n'


def test_api_pass_and_fail(tmp_path):
    ok = tree(tmp_path / "ok", {"backend/main.py": MAIN, "docs/api.md": "| `GET /a/{id}` | x |\n| `POST /b` | y |\n"})
    assert run(cd.check_api, ok) == []
    bad = tree(tmp_path / "bad", {"backend/main.py": MAIN, "docs/api.md": "| `GET /a/{id}` | x |\n"})
    assert run(cd.check_api, bad) == ["docs/api.md: route /b in backend/main.py is not documented"]


def test_the_real_repository_is_clean():
    assert cd.run(cd.ROOT) == []


def test_the_command_line_exits_1_on_a_problem(tmp_path):
    bad = tree(tmp_path, {"docs/testing.md": TESTING, "README.md": "717 Python tests\n"})
    import subprocess, sys
    r = subprocess.run([sys.executable, str(cd.ROOT / "scripts" / "check_docs.py"), str(bad)], capture_output=True, text=True)
    assert r.returncode == 1 and "717 Python tests" in r.stdout
