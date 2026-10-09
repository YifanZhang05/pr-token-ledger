# PR Token Ledger

Show the AI tokens and estimated cost behind every pull request and Linear ticket,
broken down by model. Hackweek Oct 2026. Idea and plan: `PR Token Ledger.md`.

**Privacy:** the collector reads only metadata and token counts from local session
logs. No prompt or response text ever leaves the laptop. Each person sees only
their own data.

## Use it

```bash
# 1. Sign in with GitHub (shows a short code to type on github.com; read-only profile access)
./ledger.sh login
# 2. Sync (safe to re-run; nothing double-counts)
./ledger.sh sync
# optional: Linear enrichment (ticket titles, estimates) or just your team keys for title matching
./ledger.sh setup --linear-api-key lin_api_...      # or: --linear-team-keys ENG,EVRG
# 3. Open the dashboard (Twingate on) and click "Sign in with GitHub"
#    https://yifan-zhang-pr-token-ledger.a.hackweek.kikoff.dev
# 4. Post / refresh the comment on a PR (run inside the repo, or give a URL)
./ledger.sh comment 13916 --dry-run
./ledger.sh comment https://github.com/Kikoff/kikoff/pull/13916
# Local summary without the web app
./ledger.sh report
```

Requires `python3` (3.10+), `git` and a logged-in `gh`. No packages to install.

## Sign-in

Identity is your GitHub account, for both the site ("Sign in with GitHub") and the
collector (`ledger login`, GitHub's device flow with the `read:user` scope only). The
site is reachable only through Twingate, which is what limits it to Kikoff staff; GitHub
just says who you are. Sessions are signed cookies; the OAuth secret lives in SSM.

The GitHub OAuth App lives under Yifan's GitHub account (no org admin needed):
Settings → Developer settings → OAuth Apps → New OAuth App, callback URL
`https://yifan-zhang-pr-token-ledger.a.hackweek.kikoff.dev/auth/callback`, **Enable
Device Flow** ticked. Then:

```bash
bash deploy/set-github.sh <client_id> <client_secret>
```

Until that is done the site falls back to self-serve tokens at `/register`.

## How it works

Claude Code logs (`~/.claude/projects/**/*.jsonl`) record model, token usage,
working directory and git branch on every response; Codex logs (`~/.codex/sessions`)
record cwd, branch and per-turn token counts. The collector:

1. parses both into `usage_event` rows (one per API response; duplicate log lines collapsed),
2. maps cwd to a GitHub repo with `git remote get-url origin`,
3. lists your PRs with `gh` and links each event by head branch (**direct**), or, for
   default-branch work in the hour before a PR branch started, by time (**inferred**),
   otherwise **unattributed** (unshipped exploration, main/master, no repo),
4. finds Linear tickets from branch names (`yifan/eng-123-...`) or PR titles,
5. prices tokens with `prices.json` (input, output, cache read, 5-min and 1-hour cache writes),
6. stores a local copy in `~/.config/pr-token-ledger/ledger.db` and uploads to the web app.

Costs are **API-equivalent list prices**, not what Kikoff is billed. Edit `prices.json`
to add models; unknown models are reported with cost `n/a`.

## Layout

```
ledger/            collector CLI (stdlib only)
  sources/         claude_code.py, codex.py log parsers
  attribution.py   direct / inferred / unattributed rules
  github.py        gh wrapper: PR lookup, trailers, comment upsert
  linear.py        ticket ids + optional Linear API
app/handler.py     web app: Lambda behind internal ALB, SQLite in S3
deploy/            deploy.sh, cleanup.sh, state.env, DEPLOYMENT.md
prices.json        per-model prices (USD per 1M tokens)
```

## Accuracy check

Compared against `ccusage daily --timezone UTC` on Yifan's logs (2026-09-10 to 2026-10-07):
input and cache-write tokens match exactly, cache-read tokens match for Claude-only days
(ccusage folds Codex into its totals). ccusage reports 10-40% more output tokens; it
appears to add `thinking_tokens` on top of `output_tokens`, which Anthropic already
includes, so the ledger's figure is the billable one.
