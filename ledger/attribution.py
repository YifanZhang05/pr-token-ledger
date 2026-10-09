"""Link usage events to pull requests.

direct      the event's git branch is a PR's head branch
inferred    the event was on the default branch within the hour before work on the PR's branch began
unattributed  nothing matched (bucket says why: unshipped, default-branch, no-repo, no-branch)
"""
from datetime import datetime, timedelta, timezone

DEFAULT_BRANCHES = {"main", "master", "develop", "HEAD"}
INFER_WINDOW = timedelta(hours=1)


def _dt(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _pick_pr(prs, ts):
    """Several PRs share a branch: pick the one whose lifetime covers ts, else the latest."""
    if len(prs) == 1:
        return prs[0]
    t = _dt(ts)
    for pr in sorted(prs, key=lambda p: p["created_at"] or ""):
        end = _dt(pr.get("merged_at") or pr.get("closed_at"))
        if end is None or (t is not None and t <= end):
            return pr
    return prs[-1]


def attribute(events, prs_by_repo):
    """Return a list of attribution rows, one per event."""
    by_branch = {}
    for repo, prs in prs_by_repo.items():
        for pr in prs:
            by_branch.setdefault((repo, pr["head_branch"]), []).append(pr)

    # When did work on each PR's branch begin (earliest usage on that branch)?
    branch_start = {}
    for e in events:
        if e.get("repo") and e.get("branch") and e["branch"] not in DEFAULT_BRANCHES and e.get("ts"):
            key = (e["repo"], e["branch"])
            t = _dt(e["ts"])
            if key not in branch_start or t < branch_start[key]:
                branch_start[key] = t

    rows = []
    for e in events:
        repo, branch, ts = e.get("repo"), e.get("branch"), e.get("ts")
        row = {"usage_event_id": e["id"], "repo": repo, "pr_number": None, "ticket_id": None,
               "confidence": "unattributed", "bucket": None}
        if not repo:
            row["bucket"] = "no-repo"
        elif not branch:
            row["bucket"] = "no-branch"
        elif (repo, branch) in by_branch:
            pr = _pick_pr(by_branch[(repo, branch)], ts)
            row.update(pr_number=pr["number"], ticket_id=pr.get("ticket_id"), confidence="direct", bucket="pr")
        elif branch in DEFAULT_BRANCHES:
            t = _dt(ts)
            best = None
            for (r, b), start in branch_start.items():
                if r != repo or (r, b) not in by_branch or t is None:
                    continue
                if start - INFER_WINDOW <= t < start and (best is None or start < best[0]):
                    best = (start, _pick_pr(by_branch[(r, b)], ts))
            if best:
                pr = best[1]
                row.update(pr_number=pr["number"], ticket_id=pr.get("ticket_id"), confidence="inferred", bucket="pr")
            else:
                row["bucket"] = "default-branch"
        else:
            row["bucket"] = "unshipped"
        rows.append(row)
    return rows
