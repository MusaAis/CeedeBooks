#!/usr/bin/env python3
"""Docs checker: catches the kind of mistake a reader finds before we do. Run from anywhere: `python3 scripts/check_docs.py`
(exit 0 when clean, 1 with one line per problem). CI and the pre-push check run it. The rules, each tested with a failing and a
passing fixture in agent/tests/test_check_docs.py:

1. every "N Python tests" or "N Foundry tests" figure in the README and docs equals the figure in docs/testing.md
2. every 0x address is all lowercase or has a valid EIP-55 checksum (so one address has one mixed-case spelling)
3. every relative link and #anchor resolves
4. no private process words in a public file
5. no sentence repeated in the same or the next paragraph, and no empty section made of two rules in a row
6. deploy/DEPLOY.md upgrade sections run newest first and cover every changelog version from v1.2.8.1
7. docs/api.md lists every route in backend/main.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIRST_DEPLOY_SECTION = (1, 2, 8, 1)
# Split so that this file never matches its own search.
PRIVATE = [r"hand" + r"off", r"build " + r"chat", r"\brule \d+\b", r"Alj" + r"osa", r"Co-Auth" + r"ored-By", r"Cla" + r"ude", r"Anthro" + r"pic",
           r"generated " + r"with"]
SKIP_PRIVATE = {"scripts/check_docs.py", "agent/tests/test_check_docs.py"}
TEXT_SUFFIXES = {".md", ".py", ".js", ".html", ".css", ".sol", ".yml", ".example", ".service", ".conf", ".toml", ".txt"}
SKIP_DIRS = {".git", "lib", "out", "cache", "node_modules", "__pycache__", ".pytest_cache"}


def _files(root: Path, suffixes=None):
    for p in sorted(root.rglob("*")):
        if p.is_file() and not (set(p.relative_to(root).parts) & SKIP_DIRS) and (suffixes is None or p.suffix in suffixes or p.name.endswith(".example")):
            yield p


def _rel(root: Path, p: Path) -> str:
    return str(p.relative_to(root))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _doc_files(root: Path):
    out = [root / "README.md"] + sorted((root / "docs").glob("*.md"))
    return [p for p in out if p.exists()]


# 1 ---------------------------------------------------------------------------------------------------------------------------
def check_counts(root: Path) -> list:
    testing = root / "docs" / "testing.md"
    if not testing.exists():
        return ["docs/testing.md is missing (the source of the test counts)"]
    text = _read(testing)
    py = re.search(r"(\d+) Python tests", text)
    fo = re.search(r"(\d+) Foundry tests", text)
    if not py or not fo:
        return ["docs/testing.md must state '<N> Python tests' and '<N> Foundry tests'"]
    want = {"Python": py.group(1), "Foundry": fo.group(1)}
    # Per-contract counts quoted next to the Foundry total in docs/testing.md are allowed too (24 on one contract, and so on).
    foundry_ok = {want["Foundry"]} | set(re.findall(r"\d+", next((l for l in text.splitlines() if "Foundry tests" in l), "")))
    problems = []
    for p in _doc_files(root):
        t = _read(p)
        for kind in want:
            for m in re.finditer(r"(\d+) " + kind + r" tests", t):
                if m.group(1) not in (foundry_ok if kind == "Foundry" else {want[kind]}):
                    problems.append(f"{_rel(root, p)}: '{m.group(0)}' but docs/testing.md says {want[kind]}")
        for m in re.finditer(r"(\d+) Foundry, (\d+) Python", t):
            if (m.group(1), m.group(2)) != (want["Foundry"], want["Python"]):
                problems.append(f"{_rel(root, p)}: '{m.group(0)}' but docs/testing.md says {want['Foundry']} Foundry, {want['Python']} Python")
        for m in re.finditer(r"pytest-(\d+)%2F(\d+)%20passing", t):
            if m.group(1) != want["Python"] or m.group(2) != want["Python"]:
                problems.append(f"{_rel(root, p)}: badge '{m.group(0)}' but docs/testing.md says {want['Python']} Python tests")
    return problems


# 2 ---------------------------------------------------------------------------------------------------------------------------
def check_addresses(root: Path) -> list:
    from eth_utils import to_checksum_address
    problems = []
    targets = _doc_files(root) + [root / "CHANGELOG.md", root / "deploy" / "DEPLOY.md", root / ".env.example"]
    targets += sorted((root / "docs" / "rollouts").glob("*.md")) if (root / "docs" / "rollouts").exists() else []
    for p in targets:
        if not p.exists():
            continue
        for n, line in enumerate(_read(p).splitlines(), 1):
            for m in re.finditer(r"(?<![0-9a-zA-Z])0x([0-9a-fA-F]{40})(?![0-9a-zA-Z])", line):
                a = "0x" + m.group(1)
                if a == a.lower():
                    continue
                if a != to_checksum_address(a.lower()):
                    problems.append(f"{_rel(root, p)}:{n}: {a} is not a valid EIP-55 checksum (should be {to_checksum_address(a.lower())})")
    return problems


# 3 ---------------------------------------------------------------------------------------------------------------------------
def _slug(heading: str) -> str:
    h = re.sub(r"`", "", heading.strip().lower())
    h = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", h)
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


def _anchors(text: str) -> set:
    seen, out, fence = {}, set(), False
    for line in text.splitlines():
        if line.startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            s = _slug(m.group(1))
            out.add(s if s not in seen else f"{s}-{seen[s]}")
            seen[s] = seen.get(s, 0) + 1
    return out


def check_links(root: Path) -> list:
    problems = []
    for p in _files(root, {".md"}):
        text = _read(p)
        fence = False
        for n, line in enumerate(text.splitlines(), 1):
            if line.startswith("```"):
                fence = not fence
            if fence:
                continue
            for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)", re.sub(r"`[^`]*`", "", line)):
                target = m.group(1)
                if re.match(r"[a-z][a-z0-9+.-]*:", target, re.I):
                    continue
                path_part, _, anchor = target.partition("#")
                dest = (p.parent / path_part) if path_part else p
                if not dest.exists():
                    problems.append(f"{_rel(root, p)}:{n}: link to '{target}' does not resolve")
                elif anchor and dest.suffix == ".md" and anchor not in _anchors(_read(dest)):
                    problems.append(f"{_rel(root, p)}:{n}: anchor '#{anchor}' not found in {_rel(root, dest)}")
    return problems


# 4 ---------------------------------------------------------------------------------------------------------------------------
def check_private_words(root: Path) -> list:
    problems = []
    rx = re.compile("|".join(PRIVATE), re.I)
    for p in _files(root, TEXT_SUFFIXES):
        rel = _rel(root, p)
        if rel in SKIP_PRIVATE:
            continue
        for n, line in enumerate(_read(p).splitlines(), 1):
            m = rx.search(line)
            if m:
                problems.append(f"{rel}:{n}: private or process wording '{m.group(0)}'")
    return problems


# 5 ---------------------------------------------------------------------------------------------------------------------------
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", s.lower())).strip()


def check_doubles(root: Path) -> list:
    problems = []
    for p in _doc_files(root):
        text = _read(p)
        if re.search(r"(?m)^---\s*\n\s*\n---\s*$", text):
            problems.append(f"{_rel(root, p)}: two horizontal rules in a row (an empty section)")
        blocks, fence = [], False
        cur = []
        for line in text.splitlines():
            if line.startswith("```"):
                fence = not fence
            if fence or line.startswith("```") or line.startswith("|") or not line.strip():
                if cur:
                    blocks.append(cur); cur = []
                continue
            cur.append(line)
        if cur:
            blocks.append(cur)
        sentences = []
        for b in blocks:
            parts = [_norm(s) for s in re.split(r"(?<=[.;!?])\s+", " ".join(b))]
            sentences.append([s for s in parts if len(s) >= 35])
        for i, group in enumerate(sentences):
            seen = set()
            nxt = set(sentences[i + 1]) if i + 1 < len(sentences) else set()
            for s in group:
                if s in seen:
                    problems.append(f"{_rel(root, p)}: the sentence '{s[:60]}...' is repeated in one paragraph")
                seen.add(s)
            for s in seen & nxt:
                problems.append(f"{_rel(root, p)}: the sentence '{s[:60]}...' is repeated in the next paragraph")
    return problems


# 6 ---------------------------------------------------------------------------------------------------------------------------
def _version(s: str):
    return tuple(int(x) for x in s.split("."))


def check_deploy(root: Path) -> list:
    deploy, changelog = root / "deploy" / "DEPLOY.md", root / "CHANGELOG.md"
    if not deploy.exists() or not changelog.exists():
        return ["deploy/DEPLOY.md or CHANGELOG.md is missing"]
    sections = [_version(v) for v in re.findall(r"(?m)^## Upgrading to v([\d.]+)", _read(deploy))]
    problems = []
    for a, b in zip(sections, sections[1:]):
        if a <= b:
            problems.append(f"deploy/DEPLOY.md: section v{'.'.join(map(str, b))} follows v{'.'.join(map(str, a))}; sections must run newest first")
    versions = {_version(v) for v in re.findall(r"(?m)^\*\*v([\d.]+)[:\s]", _read(changelog))}
    for v in sorted(versions - set(sections)):
        if v >= FIRST_DEPLOY_SECTION:
            problems.append(f"deploy/DEPLOY.md: no upgrade section for v{'.'.join(map(str, v))} (a docs-only release gets a one-line section)")
    return problems


# 7 ---------------------------------------------------------------------------------------------------------------------------
def _norm_path(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path.split("?")[0].rstrip("/")) or "/"


def check_api(root: Path) -> list:
    main, api = root / "backend" / "main.py", root / "docs" / "api.md"
    if not main.exists() or not api.exists():
        return ["backend/main.py or docs/api.md is missing"]
    routes = {_norm_path(m.group(2)) for m in re.finditer(r'@app\.(get|post|put|patch|delete)\(\s*"([^"]+)"', _read(main))}
    documented = set()
    for span in re.findall(r"`([^`]+)`", _read(api)):
        for m in re.finditer(r"(?<![\w{}.-])(/[\w\-./{}]*)", span):
            documented.add(_norm_path(m.group(1)))
    return [f"docs/api.md: route {r} in backend/main.py is not documented" for r in sorted(routes - documented)]


CHECKS = [check_counts, check_addresses, check_links, check_private_words, check_doubles, check_deploy, check_api]


def run(root: Path) -> list:
    out = []
    for check in CHECKS:
        out += [f"[{check.__name__[6:]}] {line}" for line in check(root)]
    return out


if __name__ == "__main__":
    base = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT
    found = run(base)
    for line in found:
        print(line)
    print(f"docs check: {len(found)} problem(s)" if found else "docs check: ok")
    sys.exit(1 if found else 0)
