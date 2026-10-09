Contents

1. [Problem](#problem)
2. [Goals and non-goals](#goals)
3. [How attribution works](#attribution)
4. [Data sources](#sources)
5. [Data model](#model)
6. [Cost model](#cost)
7. [Edge cases](#edge)
8. [Deliverables](#deliverables)
9. [Build plan](#plan)
10. [Demo script](#demo)
11. [Risks and privacy](#risks)
12. [People and open questions](#people)
13. [After hackweek](#next)

Hackweek project plan · Oct 2026

# PR Token Ledger

Show the AI tokens and cost behind every pull request and Linear ticket, broken down by model.

Owner: Yifan ZhangStatus: planningDemo: hackweek judging (Fri)

**The pitch.** Today nobody can answer “what did this PR cost in AI, and on which model?” The data already exists. Claude Code logs the model, token usage and git branch for every response. This project connects that data to PRs and tickets, then shows it as a comment on each PR and on a personal dashboard.

## Problem

- **Cost is visible only to admins and only as company totals.** The July Fable restriction came from admin data (about 25% of Claude.ai tokens, but over 60% of cost). Engineers can't see their own numbers, let alone the cost of a given piece of work.
- **The current per-person view is shrinking.** The Bedrock telemetry in Grafana only covers people still on Bedrock. Many have moved to the Enterprise subscription and dropped off the dashboard.
- **People are asking.** “Is there any way for engineers to monitor their AI token usage?” (#enterprise-eng, Sep). The answer today is to check each tool's own usage page.
- **Totals don't help anyone choose a model.** Knowing you spent $X this month says nothing about which model is worth it for which kind of work. Cost per PR and per ticket does.

## Goals and non-goals

### Goals

- For each PR: tokens and estimated cost by model, cache hit rate, sessions involved.
- For each Linear ticket: the total across its PRs.
- A PR comment that posts the breakdown.
- A personal web view: your PRs and tickets, cost by model, and usage not linked to any PR.
- Private by default. Your data is visible only to you unless you choose to share it.

### Non-goals (this week)

- Per-person leaderboards or team rankings.
- Exact billed dollars. We show API-equivalent cost.
- Linking Cursor, Claude.ai or ChatGPT usage to PRs. Their usage data has no branch.
- Judging PR quality or productivity.

## How attribution works

Every assistant response in a Claude Code session log (`~/.claude/projects/**/*.jsonl`) records `cwd`, `gitBranch`, `timestamp`, `message.model` and a full `message.usage` block (input, output, cache write and cache read tokens). I checked this against a real session log. Branch is recorded on each response, so a session that switches branches still splits correctly.

**Response**model, usage, `cwd`, `gitBranch`, timestamp

→

**Repo + branch**`git -C cwd remote get-url origin`

→

**Pull request**`gh pr list --head <branch> --state all`, filtered by time range

→

**Linear ticket**ID from branch name (`yifan/eng-123-…`), else PR title or body

→

**Cost**tokens × per-model price, cache priced separately

Each response gets a label showing how sure the link is: direct (branch matches a PR), inferred (matched by time window, see edge cases) or unattributed.

## Data sources

| Source | Per-PR link | Access | Priority |
| --- | --- | --- | --- |
| Claude Code (local logs) | yes branch on every response | None | Must |
| GitHub (PRs, commits, reviews) | Join target | Your own `gh` token | Must |
| Linear (tickets) | Through branch or PR | Personal API key | Should |
| Codex CLI (local session logs) | probably records working directory; branch to confirm | None | Should |
| Commit trailers (`Co-Authored-By: Claude`, Cursor and similar) | yes which tools touched a PR | None | Should |
| Cursor, Claude.ai and Cowork, ChatGPT | no no branch in the data | Admin APIs | Stretch, shown as unattributed |
| Bedrock telemetry (Grafana) | no by default | Grafana access | Skip |

## Data model

Use SQLite on the sandbox. Four tables are enough.

```
usage_event   id, user, source, session_id, ts, repo, branch, cwd, model,
              input_tok, output_tok, cache_write_tok, cache_read_tok, cost_usd
pr            repo, number, title, author, head_branch, created_at,
              merged_at, state, additions, deletions, ai_trailers[]
ticket        id (ENG-123), title, state, estimate, completed_at
attribution   usage_event_id, repo, pr_number, ticket_id, confidence
              (direct | inferred | unattributed)
```

Keep raw responses only as `usage_event` rows. **The collector never uploads prompts or response text**, only metadata and token counts.

## Cost model

- Cost = tokens × per-model price for input, output, cache write and cache read. Store prices in a small table you can edit (`prices.json`), taken from vendor pricing pages.
- Label it **API-equivalent cost**. Kikoff's Enterprise pricing probably differs from list prices.
- Check that newer models (Fable 5.x, Opus 5.x, GPT-6 Astra) have prices. If ccusage is used, check that it knows them too, and add any missing ones yourself.
- Price 5-minute and 1-hour cache writes separately. The usage block already splits them.

## Edge cases

| Case | Handling |
| --- | --- |
| Work started on `main` before the branch existed | Link `main` usage in the same repo to the PR if it falls in the hour before the branch's first commit. Mark it inferred. |
| Several PRs from one branch, or a reused branch name | Split by each PR's time range (created to merged or closed). |
| Git worktrees | The working directory differs but the branch still matches. Get the repo from the worktree's remote. |
| Exploration that never became a PR | An “unshipped” bucket per repo. Worth showing on its own. |
| Subagents and background tasks | Their logs include the same fields. Add them to the parent session. |
| Cloud sessions (remote Claude Code, Cowork) | No local logs. Show as unattributed for v1. |
| Detached HEAD, rebases, squash merges | Join on PR head branch name, not commit SHAs. |

## Deliverables

### 1. Collector (local CLI)

One command, e.g. `ledger sync`. It reads the local Claude Code logs (and Codex logs if supported), looks up the PRs it needs with `gh`, adds Linear tickets, links usage to PRs, and sends the token data to the app with a per-user ingest token. Running it twice must not double-count anything.

### 2. PR comment

Run with `ledger comment <pr>`, or automatically on merge. Each run updates one comment, marked by an HTML marker, instead of posting a new one.

Example layout, illustrative numbers

```
🧾 AI usage for this PR · ENG-123
model              tokens     cache hit   est. cost
claude-opus-5-5    1.24M      82%         $4.10
gpt-6-astra        0.31M      40%         $1.20
total              1.55M                  $5.30   (6 sessions · 1 inferred)
API-equivalent cost · generated by PR Token Ledger
```

### 3. Web app (hackweek sandbox)

- **My PRs:** a table of PRs with cost by model, cache hit rate, sessions, ticket and merge status.
- **My tickets:** totals per Linear ticket, with estimate next to cost.
- **Models:** cost per merged PR by model, and median cost by PR size.
- **Unattributed:** unshipped exploration plus usage from tools that can't be linked.

## Build plan

Thu afternoon

- DM Sophia Kharal about the AI PR Awards tracker. Reuse its PR detection if it fits.
- Submit the pitch on hackweek.kikoff.dev.
- Collector part 1: parse Claude Code logs into `usage_event`, and check totals against `/usage` or ccusage.
- Link usage to PRs with `gh`. Check by hand on 3 of your own PRs.

Thu evening

- Price table and cost calculation.
- PR comment command. Post it on one real PR.
- Web app skeleton: ingest endpoint, SQLite, the My PRs table. Deploy to the sandbox.

Fri morning

- Linear ticket linking and the My tickets view.
- Models view, plus the inferred and unattributed buckets.
- Get 2–3 teammates to run the collector so the demo has real data from more than one person.

Before judging

- Stop adding features. Fix bugs, write the demo script, rehearse once.

**Cut first if you're running late:** Codex support, then the Models view, then Linear. The collector, the PR links and the PR comment alone still make a full demo.

## Demo script (about 3 minutes)

1. **The question:** “What did this PR cost, and was Fable worth it?” Nobody at Kikoff can answer that today.
2. **The insight:** every Claude Code response already logs its model, tokens and branch.
3. **Live:** run `ledger sync`, then open a real PR with the comment on it.
4. **Dashboard:** your PRs ranked by cost, plus one surprising finding (a cache setting, one expensive session, or the cost of unshipped exploration).
5. **What's next:** run it in CI for every PR, an opt-in team view, and admin sources for Cursor and Claude.ai.

## Risks and privacy

- **Surveillance worries.** Private by default, nothing shared without opt-in, and no prompt or response text ever leaves the laptop. Say this early in the demo.
- **Misreading cost per PR.** A big migration should cost more than a typo fix. Show cost next to PR size, and present trends, not scores.
- **Cost accuracy.** List prices aren't what Kikoff pays, and new models may be missing from the price table. Label figures “API-equivalent.”
- **Sandbox and auth.** The sandbox is behind Twingate. Use per-user ingest tokens on top. Confirm deployment details with Tim Zaitsev early.
- **Coverage gaps.** Cursor, Claude.ai and cloud sessions can't be linked to PRs. Show their volume honestly instead of hiding it.

## People and open questions

| Who | Why |
| --- | --- |
| Sophia Kharal, Libby Kusuma, Emily Hills | Built the AI PR Awards tracker. Possible overlap or reuse. |
| Joshua Choi | Wrote a script that parses Claude Code session logs (cache TTL analysis). |
| Paul Yang | Grant's “100% AI authorship” milestone and the PR tagging plan. |
| Alex Dejeu | Admin access for Cursor, Claude Enterprise and OpenAI (stretch goal). |
| Tim Zaitsev | Hackweek sandbox deployment. |
| Dmitrii Evstiukhin, Valentin Nikolaev | Context on the existing Bedrock and Claude Code telemetry. |

### Open questions

- Do Codex CLI session logs record the git branch, or only the working directory?
- Can a PR comment run in CI? That would need each author's usage to reach CI, so the collector would push first and CI would read from the app.
- What should the opt-in team view look like without turning into a leaderboard?

## After hackweek

- A GitHub Action that comments automatically on every merged PR.
- An opt-in team or repo view: median cost per PR by model and by PR type (using Paul's tags).
- Admin sources (Cursor, Claude Enterprise, OpenAI) as unattributed totals for each person.
- A Claude Code status line or hook that shows the running cost of the current branch while you work.

Plan drafted Oct 8, 2026, from Slack and Linear context. Example numbers in the PR comment are illustrative.