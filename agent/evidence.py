"""Evidence that a contractor milestone was met: one fact that can be checked from outside, a merged GitHub pull request.

The facts are read from GitHub's API through agent.safe_fetch (one allowlisted host, no redirects). The evidence hash is
the SHA-256 of those facts in canonical JSON, so the same pull request in the same state always gives the same hash.
A merged pull request shows that code was merged, not that it meets the requirements: it is an input to a person's or the
contract's decision, never a release condition on its own.
"""
import hashlib
import json
import re

from agent import safe_fetch

GITHUB_API_HOST = "api.github.com"
OWNER_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$"
REPO_PATTERN = r"^[A-Za-z0-9._-]{1,100}$"
MAX_PR_NUMBER = 10**9
_SHA = re.compile(r"^[0-9a-f]{40}$")
_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "CeedeBooks-evidence", "X-GitHub-Api-Version": "2022-11-28"}


def valid_target(owner: str, repo: str, number: int) -> bool:
    return (isinstance(owner, str) and re.fullmatch(OWNER_PATTERN, owner) is not None
            and isinstance(repo, str) and re.fullmatch(REPO_PATTERN, repo) is not None and repo not in (".", "..")
            and isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= MAX_PR_NUMBER)


def evidence_hash(facts: dict) -> str:
    canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
    return "0x" + hashlib.sha256(canonical.encode()).hexdigest()


async def github_pr(owner: str, repo: str, number: int) -> dict:
    """The state of one pull request: {facts, evidence_hash}. Raises FetchRefused or FetchFailed; a shape that is not
    exactly what the API documents is a failure, never a guess."""
    if not valid_target(owner, repo, number):
        raise safe_fetch.FetchRefused("That repository or pull request number is not valid")
    data = await safe_fetch.fetch_json(f"https://{GITHUB_API_HOST}/repos/{owner}/{repo}/pulls/{number}", {GITHUB_API_HOST}, _HEADERS)
    try:
        base_repo = data["base"]["repo"]["full_name"]
        merged = data["merged"]
        if data["number"] != number or not isinstance(merged, bool) or str(base_repo).lower() != f"{owner}/{repo}".lower():
            raise ValueError("not the pull request asked for")
        merged_at, sha = data.get("merged_at"), data.get("merge_commit_sha")
        if merged and (not isinstance(merged_at, str) or not isinstance(sha, str) or not _SHA.match(sha)):
            raise ValueError("a merged pull request must carry its merge time and commit")
        if not merged:
            merged_at = sha = None
        facts = {"kind": "github_pr_merged", "repo": str(base_repo), "pr": number, "merged": merged, "merged_at": merged_at,
                 "merge_commit_sha": sha, "base_ref": str(data["base"]["ref"]), "author": str(data["user"]["login"])}
    except (KeyError, TypeError, ValueError):
        raise safe_fetch.FetchFailed("GitHub's answer did not have the expected shape")
    return {"facts": facts, "evidence_hash": evidence_hash(facts)}
