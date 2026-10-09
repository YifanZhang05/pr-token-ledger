"""Local SQLite mirror of what gets uploaded. Lets `ledger comment` and `ledger report` work offline."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_event (
  id TEXT PRIMARY KEY, user TEXT, source TEXT, session_id TEXT, ts TEXT,
  repo TEXT, branch TEXT, cwd TEXT, model TEXT,
  input_tok INTEGER, output_tok INTEGER, cache_write_5m_tok INTEGER, cache_write_1h_tok INTEGER,
  cache_read_tok INTEGER, cost_usd REAL
);
CREATE INDEX IF NOT EXISTS usage_event_repo_branch ON usage_event(repo, branch);
CREATE TABLE IF NOT EXISTS pr (
  repo TEXT, number INTEGER, title TEXT, author TEXT, head_branch TEXT, state TEXT,
  created_at TEXT, merged_at TEXT, closed_at TEXT, additions INTEGER, deletions INTEGER,
  ai_trailers TEXT, url TEXT, ticket_id TEXT, PRIMARY KEY (repo, number)
);
CREATE TABLE IF NOT EXISTS ticket (
  id TEXT PRIMARY KEY, title TEXT, state TEXT, estimate REAL, completed_at TEXT, url TEXT
);
CREATE TABLE IF NOT EXISTS attribution (
  usage_event_id TEXT PRIMARY KEY, repo TEXT, pr_number INTEGER, ticket_id TEXT,
  confidence TEXT, bucket TEXT
);
CREATE TABLE IF NOT EXISTS lookup_cache (key TEXT PRIMARY KEY, value TEXT, checked_at TEXT);
"""

EVENT_COLS = ["id", "user", "source", "session_id", "ts", "repo", "branch", "cwd", "model",
              "input_tok", "output_tok", "cache_write_5m_tok", "cache_write_1h_tok", "cache_read_tok", "cost_usd"]
PR_COLS = ["repo", "number", "title", "author", "head_branch", "state", "created_at", "merged_at",
           "closed_at", "additions", "deletions", "ai_trailers", "url", "ticket_id"]
TICKET_COLS = ["id", "title", "state", "estimate", "completed_at", "url"]
ATTR_COLS = ["usage_event_id", "repo", "pr_number", "ticket_id", "confidence", "bucket"]


def _upsert(conn, table, cols, rows):
    if not rows:
        return
    sql = (f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})")
    conn.executemany(sql, [[_v(r.get(c)) for c in cols] for r in rows])


def _v(x):
    return json.dumps(x) if isinstance(x, (list, dict)) else x


class Store:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def save(self, events, prs, tickets, attributions):
        with self.conn:
            _upsert(self.conn, "usage_event", EVENT_COLS, events)
            _upsert(self.conn, "pr", PR_COLS, prs)
            _upsert(self.conn, "ticket", TICKET_COLS, tickets)
            _upsert(self.conn, "attribution", ATTR_COLS, attributions)

    def cache_get(self, key, max_age_hours=6):
        row = self.conn.execute(
            "SELECT value FROM lookup_cache WHERE key=? AND checked_at > datetime('now', ?)",
            (key, f"-{max_age_hours} hours")).fetchone()
        return json.loads(row["value"]) if row else None

    def cache_put(self, key, value):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO lookup_cache VALUES (?,?,datetime('now'))",
                              (key, json.dumps(value)))

    def pr_summary(self, repo, number):
        """Per-model totals for one PR plus session/confidence counts."""
        rows = self.conn.execute("""
            SELECT e.model, SUM(e.input_tok) input_tok, SUM(e.output_tok) output_tok,
                   SUM(e.cache_write_5m_tok + e.cache_write_1h_tok) cache_write_tok,
                   SUM(e.cache_read_tok) cache_read_tok, SUM(e.cost_usd) cost_usd,
                   COUNT(DISTINCT e.session_id) sessions, COUNT(*) responses
            FROM attribution a JOIN usage_event e ON e.id = a.usage_event_id
            WHERE a.repo=? AND a.pr_number=? GROUP BY e.model ORDER BY cost_usd DESC""",
            (repo, number)).fetchall()
        meta = self.conn.execute("""
            SELECT COUNT(DISTINCT e.session_id) sessions,
                   SUM(CASE WHEN a.confidence='inferred' THEN 1 ELSE 0 END) inferred,
                   MIN(e.ts) first_ts, MAX(e.ts) last_ts
            FROM attribution a JOIN usage_event e ON e.id = a.usage_event_id
            WHERE a.repo=? AND a.pr_number=?""", (repo, number)).fetchone()
        pr = self.conn.execute("SELECT * FROM pr WHERE repo=? AND number=?", (repo, number)).fetchone()
        return [dict(r) for r in rows], dict(meta) if meta else {}, dict(pr) if pr else None

    def report(self):
        prs = self.conn.execute("""
            SELECT p.repo, p.number, p.title, p.state, p.ticket_id, p.additions, p.deletions,
                   SUM(e.cost_usd) cost_usd, COUNT(DISTINCT e.session_id) sessions,
                   SUM(e.input_tok + e.output_tok + e.cache_write_5m_tok + e.cache_write_1h_tok + e.cache_read_tok) tokens
            FROM pr p JOIN attribution a ON a.repo=p.repo AND a.pr_number=p.number
            JOIN usage_event e ON e.id=a.usage_event_id
            GROUP BY p.repo, p.number ORDER BY cost_usd DESC""").fetchall()
        buckets = self.conn.execute("""
            SELECT a.confidence, a.bucket, COUNT(*) n, SUM(e.cost_usd) cost_usd
            FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id
            GROUP BY a.confidence, a.bucket ORDER BY cost_usd DESC""").fetchall()
        models = self.conn.execute("""
            SELECT model, COUNT(*) n, SUM(cost_usd) cost_usd FROM usage_event GROUP BY model ORDER BY cost_usd DESC""").fetchall()
        return [dict(r) for r in prs], [dict(r) for r in buckets], [dict(r) for r in models]
