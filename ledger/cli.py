"""ledger: sync local AI usage to PRs, comment on PRs, print reports."""
import argparse
import os
import sys
from datetime import datetime, timezone

from . import auth, config, github, linear, render
from .attribution import DEFAULT_BRANCHES, attribute
from .client import push
from .gitinfo import repo_for_cwd
from .pricing import PriceTable
from .sources import claude_code, codex
from .store import Store


def log(msg):
    print(msg, file=sys.stderr)


def _collect(prices, since=None, sources=("claude-code", "codex")):
    events = []
    for name, mod in (("claude-code", claude_code), ("codex", codex)):
        if name not in sources:
            continue
        n = 0
        for e in mod.iter_events():
            if not e.get("ts") or prices.is_ignored(e.get("model")):
                continue
            if since and e["ts"] < since:
                continue
            e["repo"] = repo_for_cwd(e["cwd"]) if e.get("cwd") else None
            e["cost_usd"] = prices.cost_usd(e["model"], e["input_tok"], e["output_tok"],
                                            e["cache_write_5m_tok"], e["cache_write_1h_tok"], e["cache_read_tok"])
            events.append(e)
            n += 1
        log(f"  {name}: {n} responses")
    return events


def _fetch_prs(store, events, cfg, max_branch_lookups=40):
    repos = sorted({e["repo"] for e in events if e.get("repo")})
    prs_by_repo = {}
    for repo in repos:
        try:
            prs = store.cache_get(f"prs:{repo}", max_age_hours=1) or github.list_my_prs(repo)
            store.cache_put(f"prs:{repo}", prs)
        except RuntimeError as e:
            log(f"  warning: {e}")
            prs = []
        prs_by_repo[repo] = prs
    # Branches with usage but no PR by me: maybe someone else's PR, or a worktree branch.
    known = {(r, p["head_branch"]) for r, ps in prs_by_repo.items() for p in ps}
    pending = sorted({(e["repo"], e["branch"]) for e in events
                      if e.get("repo") and e.get("branch") and e["branch"] not in DEFAULT_BRANCHES
                      and (e["repo"], e["branch"]) not in known})
    for repo, branch in pending[:max_branch_lookups]:
        key = f"branch:{repo}:{branch}"
        cached = store.cache_get(key)
        if cached is None:
            try:
                cached = github.list_prs_for_branch(repo, branch)
            except RuntimeError as e:
                log(f"  warning: {e}")
                cached = []
            store.cache_put(key, cached)
        prs_by_repo.setdefault(repo, []).extend(cached)
    # Team keys we trust: configured, from Linear (if a key is set), or seen in branch names.
    known = set(cfg.get("linear_team_keys") or [])
    if cfg.get("linear_api_key"):
        cached = store.cache_get("linear:team_keys", max_age_hours=24)
        if cached is None:
            cached = sorted(linear.fetch_team_keys(cfg["linear_api_key"]))
            store.cache_put("linear:team_keys", cached)
        known |= set(cached)
    all_prs = [pr for prs in prs_by_repo.values() for pr in prs]
    for pr in all_prs:
        pr["ticket_id"] = linear.ticket_from_branch(pr["head_branch"])
        if pr["ticket_id"]:
            known.add(pr["ticket_id"].split("-")[0])
    for pr in all_prs:
        if not pr["ticket_id"]:
            pr["ticket_id"] = linear.ticket_from_text(known, pr["title"], pr["body"])
    return prs_by_repo


def _enrich(store, prs_by_repo, attributions, cfg):
    used = {(a["repo"], a["pr_number"]) for a in attributions if a["pr_number"]}
    prs_out, tickets = [], {}
    for repo, prs in prs_by_repo.items():
        for pr in prs:
            if (repo, pr["number"]) not in used:
                continue
            key = f"trailers:{repo}:{pr['number']}"
            trailers = store.cache_get(key, max_age_hours=24)
            if trailers is None:
                try:
                    trailers = github.ai_trailers(repo, pr["number"])
                except RuntimeError:
                    trailers = []
                store.cache_put(key, trailers)
            row = {k: v for k, v in pr.items() if k != "body"}
            row["ai_trailers"] = trailers
            prs_out.append(row)
            if pr.get("ticket_id"):
                tickets.setdefault(pr["ticket_id"], {"id": pr["ticket_id"]})
    api_key = cfg.get("linear_api_key")
    if api_key:
        for tid in list(tickets):
            cached = store.cache_get(f"ticket:{tid}", max_age_hours=12)
            if cached is None:
                cached = linear.fetch_issue(api_key, tid)
                store.cache_put(f"ticket:{tid}", cached)
            if "error" not in cached:
                tickets[tid] = cached
    return prs_out, list(tickets.values())


def cmd_login(args):
    cfg = config.load()
    server = args.server or cfg.get("server_url")
    client_id = auth.client_id_from_server(server)
    if not client_id:
        log(f"{server} does not have GitHub sign-in configured yet. Use `ledger setup --token` with a token from the site instead.")
        return 1
    token = auth.device_login(client_id, log)
    login = auth.whoami(token)
    cfg.update(server_url=server, token=token, email=login)
    config.save(cfg)
    log(f"Signed in as {login}. Saved to {config.CONFIG_FILE}. Next: ./ledger.sh sync")
    return 0


def cmd_sync(args):
    cfg = config.load()
    email = cfg.get("email")
    if not email:
        log("Not signed in. Run: ./ledger.sh login")
        return 2
    prices = PriceTable(config.PRICES_FILE)
    store = Store(config.DB_FILE)
    log("Reading local session logs (metadata and token counts only)...")
    events = _collect(prices, since=args.since, sources=args.sources.split(","))
    for e in events:
        e["user"] = email
    log(f"Looking up pull requests with gh ({len({e['repo'] for e in events if e.get('repo')})} repos)...")
    prs_by_repo = _fetch_prs(store, events, cfg)
    attributions = attribute(events, prs_by_repo)
    prs, tickets = _enrich(store, prs_by_repo, attributions, cfg)
    store.save(events, prs, tickets, attributions)
    if prices.unknown:
        log(f"  warning: no price for {sorted(prices.unknown)} — add them to prices.json")
    counts = {}
    for a in attributions:
        counts[a["confidence"]] = counts.get(a["confidence"], 0) + 1
    cost = sum(e["cost_usd"] or 0 for e in events)
    log(f"  {len(events)} responses, {len(prs)} PRs, {len(tickets)} tickets; "
        f"direct {counts.get('direct', 0)}, inferred {counts.get('inferred', 0)}, "
        f"unattributed {counts.get('unattributed', 0)}; est. ${cost:,.2f} total")
    if args.no_push:
        log("Skipped upload (--no-push).")
        return 0
    if not cfg.get("server_url") or not cfg.get("token"):
        log("Not signed in; saved locally only. Run `./ledger.sh login` to enable upload.")
        return 0
    log(f"Uploading to {cfg['server_url']} ...")
    sent = push(cfg["server_url"], cfg["token"], email, events, prs, tickets, attributions)
    log(f"Uploaded {sent} usage rows. Dashboard: {cfg['server_url'].rstrip('/')}/")
    return 0


def cmd_comment(args):
    cfg = config.load()
    store = Store(config.DB_FILE)
    default_repo = repo_for_cwd(os.environ.get("LEDGER_CWD") or os.getcwd())
    repo, number = github.parse_pr_ref(args.pr, default_repo)
    rows, meta, pr = store.pr_summary(repo, number)
    if not rows:
        log(f"No usage linked to {repo}#{number}. Run `ledger sync` first, or check the branch name.")
        return 1
    dash = f"{cfg['server_url'].rstrip('/')}/pr?repo={repo}&number={number}" if cfg.get("server_url") else None
    body = render.pr_comment(rows, meta, pr, dash)
    if args.dry_run:
        print(body)
        return 0
    url = github.upsert_comment(repo, number, body, render.MARKER)
    log(f"Comment posted: {url}")
    return 0


def cmd_report(args):
    store = Store(config.DB_FILE)
    prs, buckets, models = store.report()
    print("PRs by estimated cost")
    print(render.table(["repo", "PR", "state", "ticket", "sessions", "tokens", "cost", "title"],
                       [[p["repo"], p["number"], p["state"], p["ticket_id"] or "", p["sessions"],
                         render.fmt_tokens(p["tokens"]), render.fmt_usd(p["cost_usd"]), (p["title"] or "")[:50]]
                        for p in prs[:args.limit]]))
    print("\nAttribution buckets")
    print(render.table(["confidence", "bucket", "responses", "cost"],
                       [[b["confidence"], b["bucket"], b["n"], render.fmt_usd(b["cost_usd"])] for b in buckets]))
    print("\nModels")
    print(render.table(["model", "responses", "cost"],
                       [[m["model"], m["n"], render.fmt_usd(m["cost_usd"])] for m in models]))
    return 0


def cmd_setup(args):
    cfg = config.load()
    for k in ("email", "server_url", "token", "linear_api_key"):
        v = getattr(args, k.replace("server_url", "server"), None)
        if v:
            cfg[k] = v
    if args.linear_team_keys:
        cfg["linear_team_keys"] = [k.strip().upper() for k in args.linear_team_keys.split(",") if k.strip()]
    if not cfg.get("email"):
        log("--email is required the first time.")
        return 2
    config.save(cfg)
    log(f"Saved {config.CONFIG_FILE}: email={cfg.get('email')} server={cfg.get('server_url', '-')} "
        f"token={'set' if cfg.get('token') else 'unset'} linear={'set' if cfg.get('linear_api_key') else 'unset'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="ledger", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="save email, server URL and ingest token")
    s.add_argument("--email"); s.add_argument("--server"); s.add_argument("--token"); s.add_argument("--linear-api-key", dest="linear_api_key")
    s.add_argument("--linear-team-keys", dest="linear_team_keys", help="comma-separated, e.g. ENG,EVRG")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("login", help="sign in with GitHub (device flow) and save the credentials")
    s.add_argument("--server", help=f"ledger server URL (default {config.DEFAULT_SERVER})")
    s.set_defaults(fn=cmd_login)
    s = sub.add_parser("sync", help="parse local logs, link to PRs, upload")
    s.add_argument("--since", help="ISO date, e.g. 2026-09-01")
    s.add_argument("--sources", default="claude-code,codex")
    s.add_argument("--no-push", action="store_true", help="save locally only")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("comment", help="post/update the usage comment on a PR")
    s.add_argument("pr", help="PR number (inside the repo), owner/name#123, or URL")
    s.add_argument("--dry-run", action="store_true", help="print instead of posting")
    s.set_defaults(fn=cmd_comment)
    s = sub.add_parser("report", help="print a local summary")
    s.add_argument("--limit", type=int, default=25)
    s.set_defaults(fn=cmd_report)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except RuntimeError as e:
        log(f"error: {e}")
        return 1
