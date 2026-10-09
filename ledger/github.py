"""GitHub access through the user's own `gh` CLI (no extra tokens)."""
import json
import re
import subprocess

PR_FIELDS = "number,title,body,headRefName,state,createdAt,mergedAt,closedAt,additions,deletions,url,author"
AI_TRAILER = re.compile(r"Co-Authored-By:.*?\b(Claude|Codex|Cursor|Copilot|ChatGPT|Gemini|Devin|Aider)\b", re.I)
_PR_URL = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")


def _gh(args, timeout=60):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {out.stderr.strip()[:300]}")
    return out.stdout


def whoami() -> str:
    return _gh(["api", "user", "--jq", ".login"]).strip()


def _norm(pr: dict, repo: str) -> dict:
    return {
        "repo": repo,
        "number": pr["number"],
        "title": pr.get("title") or "",
        "body": pr.get("body") or "",
        "author": (pr.get("author") or {}).get("login"),
        "head_branch": pr.get("headRefName"),
        "state": pr.get("state"),
        "created_at": pr.get("createdAt"),
        "merged_at": pr.get("mergedAt"),
        "closed_at": pr.get("closedAt"),
        "additions": pr.get("additions") or 0,
        "deletions": pr.get("deletions") or 0,
        "url": pr.get("url"),
    }


def list_my_prs(repo: str, limit: int = 300):
    out = _gh(["pr", "list", "--repo", repo, "--author", "@me", "--state", "all",
               "--limit", str(limit), "--json", PR_FIELDS])
    return [_norm(p, repo) for p in json.loads(out or "[]")]


def list_prs_for_branch(repo: str, branch: str):
    out = _gh(["pr", "list", "--repo", repo, "--head", branch, "--state", "all",
               "--limit", "20", "--json", PR_FIELDS])
    return [_norm(p, repo) for p in json.loads(out or "[]")]


def ai_trailers(repo: str, number: int):
    """Which AI tools appear as Co-Authored-By trailers in the PR's commits."""
    out = _gh(["pr", "view", str(number), "--repo", repo, "--json", "commits",
               "--jq", "[.commits[].messageBody] | join(\"\\n\")"])
    return sorted({m.group(1).title() for m in AI_TRAILER.finditer(out)})


def parse_pr_ref(ref: str, default_repo=None):
    """Accept '123', 'owner/name#123' or a PR URL."""
    m = _PR_URL.search(ref)
    if m:
        return m.group(1), int(m.group(2))
    if "#" in ref:
        repo, num = ref.split("#", 1)
        return repo, int(num)
    if not default_repo:
        raise ValueError("Give a PR URL or owner/name#number, or run inside the repo.")
    return default_repo, int(ref)


def upsert_comment(repo: str, number: int, body: str, marker: str) -> str:
    """Create or update the single comment carrying `marker`. Returns the comment URL."""
    existing = _gh(["api", f"repos/{repo}/issues/{number}/comments", "--paginate",
                    "--jq", f'[.[] | select(.body | contains("{marker}"))] | first | .id // empty'])
    cid = existing.strip()
    if cid:
        res = _gh(["api", "-X", "PATCH", f"repos/{repo}/issues/comments/{cid}",
                   "-f", f"body={body}", "--jq", ".html_url"])
    else:
        res = _gh(["api", "-X", "POST", f"repos/{repo}/issues/{number}/comments",
                   "-f", f"body={body}", "--jq", ".html_url"])
    return res.strip()
