"""Optional Linear enrichment with a personal API key (LINEAR_API_KEY)."""
import json
import re
import urllib.request

_TICKET_STRICT = re.compile(r"\b([A-Z]{2,10}-\d{1,6})\b")
# Linear-style branch names put the identifier at the start of a path segment: user/eng-123-slug
_TICKET_LOOSE = re.compile(r"(?:^|/)([A-Za-z]{2,10}-\d{1,6})(?=-|/|$)")
_DENY = {"utf", "sha", "base", "gpt", "iso", "md", "rfc", "http", "ipv", "x", "v", "es", "py",
         "node", "ruby", "step", "phase", "part", "v1", "v2", "tab", "day", "week", "ec", "s3"}


def ticket_from_branch(branch: str):
    if not branch:
        return None
    for m in _TICKET_LOOSE.finditer(branch):
        key = m.group(1)
        if key.split("-")[0].lower() in _DENY:
            continue
        return key.upper()
    return None


def ticket_from_text(known_keys, *texts: str):
    """Only accept identifiers whose team key is known (from branches or Linear), to avoid
    matching things like RETURN-500 or UTF-8 in prose."""
    for t in texts:
        if not t:
            continue
        for m in _TICKET_STRICT.finditer(t):
            key = m.group(1)
            if key.split("-")[0] in known_keys:
                return key
    return None


def fetch_team_keys(api_key: str):
    q = {"query": "{ teams { nodes { key } } }"}
    req = urllib.request.Request("https://api.linear.app/graphql", data=json.dumps(q).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        return {t["key"] for t in data["data"]["teams"]["nodes"]}
    except Exception:
        return set()


def fetch_issue(api_key: str, identifier: str):
    q = {"query": "query($id:String!){ issue(id:$id){ identifier title url estimate completedAt state{ name } } }",
         "variables": {"id": identifier}}
    req = urllib.request.Request("https://api.linear.app/graphql", data=json.dumps(q).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": api_key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception as e:  # network or auth problem: treat as unknown ticket
        return {"id": identifier, "error": str(e)[:200]}
    issue = (data.get("data") or {}).get("issue")
    if not issue:
        return {"id": identifier, "error": "not found"}
    return {"id": issue["identifier"], "title": issue.get("title"), "url": issue.get("url"),
            "estimate": issue.get("estimate"), "completed_at": issue.get("completedAt"),
            "state": (issue.get("state") or {}).get("name")}
