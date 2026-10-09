"""PR Token Ledger web app. AWS Lambda behind an internal ALB. Standard library + boto3 only.

Storage: one SQLite file in a private S3 bucket. The function runs with reserved
concurrency 1, so load -> modify -> save is safe. Reads are served from the warm
container's copy when the S3 ETag has not changed.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3

BUCKET = os.environ["LEDGER_BUCKET"]
DB_KEY = os.environ.get("LEDGER_DB_KEY", "ledger.db")
ADMIN_PARAM = os.environ.get("ADMIN_TOKEN_PARAM")
GITHUB_PARAM = os.environ.get("GITHUB_PARAM")              # SecureString JSON: {"client_id","client_secret"}
SESSION_SECRET_PARAM = os.environ.get("SESSION_SECRET_PARAM")
GITHUB_API = "https://api.github.com"
TOKEN_CACHE_TTL = 600
LOCAL_DB = "/tmp/ledger.db"
SESSION_TTL = 30 * 24 * 3600
s3 = boto3.client("s3", region_name="us-west-2", endpoint_url="https://s3.us-west-2.amazonaws.com")
_state = {"etag": None, "admin": None, "github": None, "session_secret": None, "token_cache": {}}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created_at TEXT, last_sync TEXT);
CREATE TABLE IF NOT EXISTS usage_event (
  id TEXT PRIMARY KEY, user TEXT, source TEXT, session_id TEXT, ts TEXT, repo TEXT, branch TEXT, cwd TEXT, model TEXT,
  input_tok INTEGER, output_tok INTEGER, cache_write_5m_tok INTEGER, cache_write_1h_tok INTEGER, cache_read_tok INTEGER, cost_usd REAL);
CREATE INDEX IF NOT EXISTS ue_user ON usage_event(user);
CREATE TABLE IF NOT EXISTS pr (repo TEXT, number INTEGER, title TEXT, author TEXT, head_branch TEXT, state TEXT, created_at TEXT,
  merged_at TEXT, closed_at TEXT, additions INTEGER, deletions INTEGER, ai_trailers TEXT, url TEXT, ticket_id TEXT, PRIMARY KEY (repo, number));
CREATE TABLE IF NOT EXISTS ticket (id TEXT PRIMARY KEY, title TEXT, state TEXT, estimate REAL, completed_at TEXT, url TEXT);
CREATE TABLE IF NOT EXISTS attribution (usage_event_id TEXT PRIMARY KEY, repo TEXT, pr_number INTEGER, ticket_id TEXT, confidence TEXT, bucket TEXT);
"""
EVENT_COLS = ["id", "user", "source", "session_id", "ts", "repo", "branch", "cwd", "model", "input_tok", "output_tok",
              "cache_write_5m_tok", "cache_write_1h_tok", "cache_read_tok", "cost_usd"]
PR_COLS = ["repo", "number", "title", "author", "head_branch", "state", "created_at", "merged_at", "closed_at",
           "additions", "deletions", "ai_trailers", "url", "ticket_id"]
TICKET_COLS = ["id", "title", "state", "estimate", "completed_at", "url"]
ATTR_COLS = ["usage_event_id", "repo", "pr_number", "ticket_id", "confidence", "bucket"]


# ---------- storage ----------
def load_db():
    try:
        head = s3.head_object(Bucket=BUCKET, Key=DB_KEY)
        etag = head["ETag"]
        if etag != _state["etag"] or not os.path.exists(LOCAL_DB):
            s3.download_file(BUCKET, DB_KEY, LOCAL_DB)
            _state["etag"] = etag
    except s3.exceptions.ClientError as e:
        if e.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            raise
        if os.path.exists(LOCAL_DB):
            os.remove(LOCAL_DB)
    conn = sqlite3.connect(LOCAL_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_db(conn):
    conn.commit()
    conn.close()
    s3.upload_file(LOCAL_DB, BUCKET, DB_KEY)
    _state["etag"] = s3.head_object(Bucket=BUCKET, Key=DB_KEY)["ETag"]


def admin_token():
    if _state["admin"] is None and ADMIN_PARAM:
        ssm = boto3.client("ssm", region_name="us-west-2")
        _state["admin"] = ssm.get_parameter(Name=ADMIN_PARAM, WithDecryption=True)["Parameter"]["Value"]
    return _state["admin"]


def ssm_get(name):
    ssm = boto3.client("ssm", region_name="us-west-2")
    try:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def github_config():
    """GitHub OAuth app settings, or None when not configured yet."""
    if _state["github"] is None and GITHUB_PARAM:
        raw = ssm_get(GITHUB_PARAM)
        cfg = json.loads(raw) if raw else {}
        _state["github"] = cfg if cfg.get("client_id") and cfg.get("client_secret") else {}
    return _state["github"] or None


def github_get(path, token):
    req = urllib.request.Request(f"{GITHUB_API}{path}", headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                                                "User-Agent": "pr-token-ledger"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def github_user_for_token(token):
    """GitHub login for an OAuth token (validated against GitHub, cached briefly). None if invalid."""
    key = thash(token)
    hit = _state["token_cache"].get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    try:
        login = (github_get("/user", token).get("login") or "").lower() or None
    except urllib.error.HTTPError:
        login = None
    _state["token_cache"][key] = (login, time.time() + TOKEN_CACHE_TTL)
    return login


def session_secret():
    if _state["session_secret"] is None:
        _state["session_secret"] = (ssm_get(SESSION_SECRET_PARAM) if SESSION_SECRET_PARAM else None) or ""
    return _state["session_secret"]


def sign_session(email):
    exp = int(time.time()) + SESSION_TTL
    msg = f"{email}|{exp}"
    sig = hmac.new(session_secret().encode(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}|{sig}".encode()).decode()


def verify_session(value):
    try:
        email, exp, sig = base64.urlsafe_b64decode(value.encode()).decode().split("|")
    except Exception:
        return None
    good = hmac.new(session_secret().encode(), f"{email}|{exp}".encode(), hashlib.sha256).hexdigest()
    if not session_secret() or not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return email


def thash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(conn, user):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT OR REPLACE INTO users (email, token_hash, created_at, last_sync) VALUES (?,?,?,(SELECT last_sync FROM users WHERE email=?))",
                 (user, thash(token), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), user))
    return token


def ensure_user(conn, user):
    """Create the users row on first sight. Returns True if the DB changed."""
    if conn.execute("SELECT 1 FROM users WHERE email=?", (user,)).fetchone():
        return False
    conn.execute("INSERT INTO users (email, token_hash, created_at) VALUES (?,?,?)",
                 (user, "github-only", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    return True


def auth_user(conn, token):
    """Who is calling the API: a ledger ingest token, or (when GitHub is configured) a GitHub OAuth token."""
    if not token:
        return None
    user = user_for_token(conn, token)
    if user or not github_config():
        return user
    return github_user_for_token(token)


def user_for_token(conn, token):
    if not token:
        return None
    row = conn.execute("SELECT email FROM users WHERE token_hash=?", (thash(token),)).fetchone()
    return row["email"] if row else None


# ---------- http plumbing ----------
REASONS = {200: "OK", 302: "Found", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}


def resp(status, body, ctype="text/html; charset=utf-8", headers=None):
    h = {"content-type": ctype, "cache-control": "no-store"}
    h.update(headers or {})
    return {"statusCode": status, "statusDescription": f"{status} {REASONS.get(status, '')}".strip(),
            "isBase64Encoded": False, "headers": h, "body": body}


def redirect(location, headers=None):
    h = {"location": location}
    h.update(headers or {})
    return resp(302, "", headers=h)


def json_resp(status, obj):
    return resp(status, json.dumps(obj), ctype="application/json")


def parse_body(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    ctype = (event.get("headers") or {}).get("content-type", "")
    if "json" in ctype:
        return json.loads(raw or "{}")
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}


def cookie(event, name):
    raw = (event.get("headers") or {}).get("cookie", "")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return urllib.parse.unquote(v)
    return None


def set_cookie(name, value, max_age=SESSION_TTL):
    return f"{name}={urllib.parse.quote(value)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={max_age}"


def current_user(conn, event):
    """Signed-in person (GitHub login): signed session cookie, or (only when GitHub is not configured) a token cookie."""
    sess = cookie(event, "ledger_session")
    if not sess:
        return None
    user = verify_session(sess)
    if user:
        return user
    if not github_config():
        return user_for_token(conn, sess)
    return None


def bearer(event):
    auth = (event.get("headers") or {}).get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


# ---------- formatting ----------
def esc(x):
    return html.escape("" if x is None else str(x))


def fmt_tok(n):
    n = n or 0
    return f"{n / 1e6:.2f}M" if n >= 1e6 else (f"{n / 1e3:.0f}K" if n >= 1e3 else str(int(n)))


def fmt_usd(x):
    return "n/a" if x is None else f"${x:,.2f}"


def pct(num, den):
    return f"{100 * (num or 0) / den:.0f}%" if den else "-"


def short_date(s):
    return (s or "")[:10]


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · PR Token Ledger</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#e5e5e5;--accent:#2563eb;--soft:#f5f6f8;--warn:#b45309}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111214;--fg:#eceef1;--muted:#9aa0a6;--line:#2a2d31;--accent:#7aa2ff;--soft:#1a1c20;--warn:#f0b35b}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:16px}}header{{border-bottom:1px solid var(--line)}}
header div{{max-width:1100px;margin:0 auto;padding:12px 16px;display:flex;gap:18px;align-items:center;flex-wrap:wrap}}
header b{{font-size:17px;margin-right:6px}}header a{{color:var(--fg);text-decoration:none;opacity:.8}}header a.on,header a:hover{{opacity:1;color:var(--accent)}}
header span.me{{margin-left:auto;color:var(--muted);font-size:13px}}
h1{{font-size:22px;margin:12px 0 4px}}p.sub{{color:var(--muted);margin:0 0 16px}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 24px}}th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}}td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
tr:hover td{{background:var(--soft)}}code{{font-size:13px;background:var(--soft);padding:1px 5px;border-radius:4px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:12px 0 20px}}
.tile{{border:1px solid var(--line);border-radius:8px;padding:12px}}.tile b{{display:block;font-size:22px}}.tile span{{color:var(--muted);font-size:13px}}
.tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:10px;background:var(--soft);color:var(--muted);margin-left:4px}}
.bar{{height:8px;background:var(--soft);border-radius:4px;overflow:hidden}}.bar i{{display:block;height:100%;background:var(--accent)}}
.note{{border-left:3px solid var(--warn);padding:8px 12px;background:var(--soft);margin:12px 0;font-size:14px}}
form.box{{border:1px solid var(--line);border-radius:8px;padding:16px;max-width:520px}}input[type=email],input[type=text]{{width:100%;padding:8px;font-size:15px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}}
button{{margin-top:10px;padding:8px 14px;font-size:15px;border:0;border-radius:6px;background:var(--accent);color:#fff;cursor:pointer}}
pre{{background:var(--soft);padding:12px;border-radius:8px;overflow:auto;font-size:13px}}a{{color:var(--accent)}}
:root{{--bar:#2f6fed}}@media(prefers-color-scheme:dark){{:root{{--bar:#6f9cff}}}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px;margin:8px 0 24px}}
.chart{{border:1px solid var(--line);border-radius:8px;padding:14px 16px}}.chart h3{{margin:0 0 2px;font-size:15px}}.chart p{{margin:0 0 10px;color:var(--muted);font-size:13px}}
.chart .row{{display:grid;grid-template-columns:minmax(110px,30%) 1fr auto;gap:10px;align-items:center;padding:4px 0}}
.chart .row code{{font-size:12px;background:none;padding:0}}.chart .track{{height:22px;position:relative}}
.chart .fill{{position:absolute;left:0;top:2px;height:18px;background:var(--bar);border-radius:0 4px 4px 0;min-width:2px}}
.chart .row:hover .fill{{filter:brightness(1.15)}}.chart .val{{font-variant-numeric:tabular-nums;font-size:13px;white-space:nowrap;min-width:60px;text-align:right}}
.chart .axis{{display:grid;grid-template-columns:minmax(110px,30%) 1fr auto;gap:10px;color:var(--muted);font-size:11px;margin-top:4px}}.chart .axis span:nth-child(2){{display:flex;justify-content:space-between}}
</style></head><body><header><div><b>🧾 PR Token Ledger</b>{nav}<span class="me">{me}</span></div></header><main>{body}</main></body></html>"""

NAV = [("/", "My PRs"), ("/tickets", "My tickets"), ("/models", "Models"), ("/unattributed", "Unattributed"), ("/setup", "Setup")]


def page(title, body, path="", me=""):
    nav = "".join(f'<a href="{href}" class="{"on" if href == path else ""}">{label}</a>' for href, label in NAV) if me else ""
    me_html = f'{esc(me)} · <a href="/logout">log out</a>' if me else '<a href="/login">sign in</a>'
    return resp(200, PAGE.format(title=esc(title), nav=nav, me=me_html, body=body))


# ---------- views ----------
def bar_chart(title, subtitle, rows, fmt, unit=""):
    """Horizontal single-series bar chart. rows = [(label, value, tooltip)], sorted by caller."""
    mx = max((v for _, v, _ in rows), default=0) or 1
    out = f"<div class='chart'><h3>{esc(title)}</h3><p>{esc(subtitle)}</p>"
    for label, v, tip in rows:
        out += (f"<div class='row' title='{esc(tip)}'><code>{esc(label)}</code><div class='track'><div class='fill' style='width:{100 * (v or 0) / mx:.1f}%'></div></div>"
                f"<span class='val'>{esc(fmt(v))}</span></div>")
    out += f"<div class='axis'><span></span><span><span>0</span><span>{esc(fmt(mx))}{esc(unit)}</span></span><span></span></div></div>"
    return out


def tiles(items):
    return '<div class="tiles">' + "".join(f'<div class="tile"><b>{esc(v)}</b><span>{esc(k)}</span></div>' for k, v in items) + "</div>"


def view_prs(conn, me):
    rows = conn.execute("""
      SELECT p.repo, p.number, p.title, p.state, p.url, p.ticket_id, p.additions, p.deletions, p.ai_trailers, p.merged_at,
             SUM(e.cost_usd) cost, COUNT(DISTINCT e.session_id) sessions,
             SUM(e.input_tok+e.output_tok+e.cache_write_5m_tok+e.cache_write_1h_tok+e.cache_read_tok) tokens,
             SUM(e.input_tok) inp, SUM(e.cache_write_5m_tok+e.cache_write_1h_tok) cw, SUM(e.cache_read_tok) cr,
             SUM(CASE WHEN a.confidence='inferred' THEN 1 ELSE 0 END) inferred
      FROM pr p JOIN attribution a ON a.repo=p.repo AND a.pr_number=p.number
      JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
      GROUP BY p.repo, p.number ORDER BY cost DESC""", (me,)).fetchall()
    models = conn.execute("""
      SELECT e.model, SUM(e.cost_usd) cost FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id
      WHERE e.user=? AND a.pr_number IS NOT NULL GROUP BY e.model""", (me,)).fetchall()
    tot = conn.execute("SELECT SUM(cost_usd) c, COUNT(DISTINCT session_id) s FROM usage_event WHERE user=?", (me,)).fetchone()
    pr_cost = sum(r["cost"] or 0 for r in rows)
    merged = [r for r in rows if r["state"] == "MERGED"]
    med = sorted(r["cost"] or 0 for r in merged)
    median = med[len(med) // 2] if med else 0
    body = "<h1>My PRs</h1><p class='sub'>Estimated AI cost per pull request, from your local Claude Code and Codex sessions. API-equivalent list prices.</p>"
    body += tiles([("PRs with AI usage", len(rows)), ("cost linked to PRs", fmt_usd(pr_cost)),
                   ("median cost, merged PR", fmt_usd(median)), ("all usage", fmt_usd(tot["c"])), ("sessions", tot["s"] or 0)])
    if not rows:
        body += "<div class='note'>No data yet. Run <code>ledger sync</code> on your laptop.</div>"
        return body
    mx = max(r["cost"] or 0 for r in rows) or 1
    body += "<table><tr><th>PR</th><th>title</th><th>ticket</th><th class=n>size</th><th class=n>sessions</th><th class=n>tokens</th><th class=n>cache hit</th><th class=n>cost</th><th style='width:120px'></th></tr>"
    for r in rows:
        trailers = ", ".join(json.loads(r["ai_trailers"] or "[]"))
        tag = f"<span class=tag>{esc(r['state'].lower())}</span>" if r["state"] else ""
        inf = f"<span class=tag title='includes usage inferred from the default branch'>{r['inferred']} inferred</span>" if r["inferred"] else ""
        ticket = f"<a href='/tickets#{esc(r['ticket_id'])}'>{esc(r['ticket_id'])}</a>" if r["ticket_id"] else ""
        body += (f"<tr><td><a href='/pr?repo={esc(r['repo'])}&number={r['number']}'>{esc(r['repo'].split('/')[-1])}#{r['number']}</a>{tag}</td>"
                 f"<td>{esc(r['title'])}{' <span class=tag title=\"Co-Authored-By trailers\">' + esc(trailers) + '</span>' if trailers else ''}</td>"
                 f"<td>{ticket}</td><td class=n>+{r['additions'] or 0} −{r['deletions'] or 0}</td><td class=n>{r['sessions']}{inf}</td>"
                 f"<td class=n>{fmt_tok(r['tokens'])}</td><td class=n>{pct(r['cr'], (r['inp'] or 0) + (r['cw'] or 0) + (r['cr'] or 0))}</td>"
                 f"<td class=n><b>{fmt_usd(r['cost'])}</b></td><td><div class=bar><i style='width:{100 * (r['cost'] or 0) / mx:.0f}%'></i></div></td></tr>")
    body += "</table>"
    if models:
        body += "<h2 style='font-size:17px'>Cost linked to PRs, by model</h2><table><tr><th>model</th><th class=n>cost</th><th class=n>share</th></tr>"
        for m in sorted(models, key=lambda m: -(m["cost"] or 0)):
            body += f"<tr><td><code>{esc(m['model'])}</code></td><td class=n>{fmt_usd(m['cost'])}</td><td class=n>{pct(m['cost'], pr_cost)}</td></tr>"
        body += "</table>"
    return body


def view_pr(conn, me, repo, number):
    pr = conn.execute("SELECT * FROM pr WHERE repo=? AND number=?", (repo, number)).fetchone()
    if not pr:
        return "<h1>PR not found</h1>"
    models = conn.execute("""
      SELECT e.model, SUM(e.input_tok) inp, SUM(e.output_tok) out, SUM(e.cache_write_5m_tok+e.cache_write_1h_tok) cw,
             SUM(e.cache_read_tok) cr, SUM(e.cost_usd) cost, COUNT(*) n
      FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
      WHERE a.repo=? AND a.pr_number=? GROUP BY e.model ORDER BY cost DESC""", (me, repo, number)).fetchall()
    sessions = conn.execute("""
      SELECT e.session_id, e.source, MIN(e.ts) first_ts, MAX(e.ts) last_ts, COUNT(*) n, SUM(e.cost_usd) cost,
             GROUP_CONCAT(DISTINCT e.model) models, MIN(a.confidence) confidence, e.branch
      FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
      WHERE a.repo=? AND a.pr_number=? GROUP BY e.session_id ORDER BY first_ts""", (me, repo, number)).fetchall()
    total = sum(m["cost"] or 0 for m in models)
    body = (f"<h1>{esc(repo)}#{number}: {esc(pr['title'])}</h1><p class='sub'>{esc(pr['state'])} · branch <code>{esc(pr['head_branch'])}</code> · "
            f"+{pr['additions'] or 0} −{pr['deletions'] or 0} · <a href='{esc(pr['url'])}'>GitHub</a>"
            + (f" · ticket {esc(pr['ticket_id'])}" if pr["ticket_id"] else "") + "</p>")
    body += tiles([("cost", fmt_usd(total)), ("sessions", len(sessions)), ("responses", sum(m["n"] for m in models)),
                   ("cost per changed line", fmt_usd(total / ((pr["additions"] or 0) + (pr["deletions"] or 0))) if (pr["additions"] or pr["deletions"]) else "-")])
    tok_rows = sorted(((m["model"], (m["inp"] or 0) + (m["cw"] or 0) + (m["cr"] or 0) + (m["out"] or 0),
                        f"input {fmt_tok(m['inp'])} · cache write {fmt_tok(m['cw'])} · cache read {fmt_tok(m['cr'])} · output {fmt_tok(m['out'])}") for m in models),
                      key=lambda r: -r[1])
    cost_rows = [(m["model"], m["cost"] or 0, f"{m['n']} responses") for m in models]
    if models:
        body += "<div class='charts'>" + bar_chart("Tokens by model", "All tokens on this PR. Hover for the split.", tok_rows, fmt_tok) \
                + bar_chart("Cost by model", "API-equivalent list prices.", cost_rows, fmt_usd) + "</div>"
    body += "<table><tr><th>model</th><th class=n>input</th><th class=n>cache write</th><th class=n>cache read</th><th class=n>output</th><th class=n>cache hit</th><th class=n>cost</th></tr>"
    for m in models:
        body += (f"<tr><td><code>{esc(m['model'])}</code></td><td class=n>{fmt_tok(m['inp'])}</td><td class=n>{fmt_tok(m['cw'])}</td><td class=n>{fmt_tok(m['cr'])}</td>"
                 f"<td class=n>{fmt_tok(m['out'])}</td><td class=n>{pct(m['cr'], (m['inp'] or 0) + (m['cw'] or 0) + (m['cr'] or 0))}</td><td class=n><b>{fmt_usd(m['cost'])}</b></td></tr>")
    body += "</table><h2 style='font-size:17px'>Sessions</h2><table><tr><th>started</th><th>source</th><th>branch</th><th>models</th><th class=n>responses</th><th>link</th><th class=n>cost</th></tr>"
    for s in sessions:
        body += (f"<tr><td>{esc(s['first_ts'][:16].replace('T', ' '))}</td><td>{esc(s['source'])}</td><td><code>{esc(s['branch'])}</code></td><td>{esc(s['models'])}</td>"
                 f"<td class=n>{s['n']}</td><td>{esc(s['confidence'])}</td><td class=n>{fmt_usd(s['cost'])}</td></tr>")
    body += "</table>"
    return body


def view_tickets(conn, me):
    rows = conn.execute("""
      SELECT p.ticket_id, t.title, t.state, t.estimate, COUNT(DISTINCT p.number) prs, SUM(e.cost_usd) cost,
             COUNT(DISTINCT e.session_id) sessions, GROUP_CONCAT(DISTINCT p.repo || '#' || p.number) pr_list, t.url
      FROM pr p JOIN attribution a ON a.repo=p.repo AND a.pr_number=p.number
      JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
      LEFT JOIN ticket t ON t.id=p.ticket_id
      WHERE p.ticket_id IS NOT NULL GROUP BY p.ticket_id ORDER BY cost DESC""", (me,)).fetchall()
    body = "<h1>My tickets</h1><p class='sub'>Linear tickets, totalled across their PRs. Estimate (points) shown next to cost when Linear is connected.</p>"
    if not rows:
        return body + "<div class='note'>No tickets linked yet. Tickets are read from branch names like <code>yifan/eng-123-fix</code>, then PR titles. Set <code>ledger setup --linear-api-key ...</code> or <code>--linear-team-keys ENG,EVRG</code> so titles can be matched.</div>"
    body += "<table><tr><th>ticket</th><th>title</th><th>state</th><th class=n>estimate</th><th class=n>PRs</th><th class=n>sessions</th><th class=n>cost</th><th class=n>cost / point</th></tr>"
    for r in rows:
        link = f"<a id='{esc(r['ticket_id'])}' href='{esc(r['url'])}'>{esc(r['ticket_id'])}</a>" if r["url"] else f"<span id='{esc(r['ticket_id'])}'>{esc(r['ticket_id'])}</span>"
        per = fmt_usd((r["cost"] or 0) / r["estimate"]) if r["estimate"] else "-"
        body += (f"<tr><td>{link}</td><td>{esc(r['title'] or '')}<br><span class=tag>{esc(r['pr_list'])}</span></td><td>{esc(r['state'] or '')}</td>"
                 f"<td class=n>{esc(r['estimate'] or '-')}</td><td class=n>{r['prs']}</td><td class=n>{r['sessions']}</td><td class=n><b>{fmt_usd(r['cost'])}</b></td><td class=n>{per}</td></tr>")
    return body + "</table>"


def view_models(conn, me):
    per_pr = conn.execute("""
      SELECT e.model, p.repo, p.number, (p.additions + p.deletions) size, p.state, SUM(e.cost_usd) cost
      FROM pr p JOIN attribution a ON a.repo=p.repo AND a.pr_number=p.number
      JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
      GROUP BY e.model, p.repo, p.number""", (me,)).fetchall()
    overall = conn.execute("""
      SELECT model, COUNT(*) n, COUNT(DISTINCT session_id) sessions, SUM(cost_usd) cost,
             SUM(input_tok) inp, SUM(output_tok) out, SUM(cache_write_5m_tok+cache_write_1h_tok) cw, SUM(cache_read_tok) cr
      FROM usage_event WHERE user=? GROUP BY model ORDER BY cost DESC""", (me,)).fetchall()
    body = "<h1>Models</h1><p class='sub'>Which model is worth it for which work? Usage and cost by model, then cost per merged PR by PR size.</p>"
    tok_rows = sorted(((m["model"], (m["inp"] or 0) + (m["cw"] or 0) + (m["cr"] or 0) + (m["out"] or 0),
                        f"input {fmt_tok(m['inp'])} · cache write {fmt_tok(m['cw'])} · cache read {fmt_tok(m['cr'])} · output {fmt_tok(m['out'])}") for m in overall),
                      key=lambda r: -r[1])
    cost_rows = sorted(((m["model"], m["cost"] or 0, f"{m['n']} responses in {m['sessions']} sessions") for m in overall), key=lambda r: -r[1])
    body += "<div class='charts'>" + bar_chart("Tokens by model", "All tokens: input, cache write, cache read and output. Hover for the split.", tok_rows, fmt_tok) \
            + bar_chart("Cost by model", "API-equivalent list prices.", cost_rows, fmt_usd) + "</div>"
    body += "<table><tr><th>model</th><th class=n>responses</th><th class=n>sessions</th><th class=n>input</th><th class=n>output</th><th class=n>cache hit</th><th class=n>cost</th></tr>"
    for m in overall:
        body += (f"<tr><td><code>{esc(m['model'])}</code></td><td class=n>{m['n']}</td><td class=n>{m['sessions']}</td><td class=n>{fmt_tok((m['inp'] or 0) + (m['cw'] or 0) + (m['cr'] or 0))}</td>"
                 f"<td class=n>{fmt_tok(m['out'])}</td><td class=n>{pct(m['cr'], (m['inp'] or 0) + (m['cw'] or 0) + (m['cr'] or 0))}</td><td class=n><b>{fmt_usd(m['cost'])}</b></td></tr>")
    body += "</table>"

    def bucket(size):
        return "small (<100 lines)" if size < 100 else ("medium (100-500)" if size < 500 else "large (500+)")
    agg = {}
    for r in per_pr:
        if r["state"] != "MERGED":
            continue
        key = (r["model"], bucket(r["size"] or 0))
        agg.setdefault(key, []).append(r["cost"] or 0)
    allm = {}
    for r in per_pr:
        if r["state"] == "MERGED":
            allm.setdefault(r["model"], []).append(r["cost"] or 0)
    body += "<h2 style='font-size:17px'>Merged PRs: median cost by model and PR size</h2>"
    if not allm:
        return body + "<div class='note'>No merged PRs with usage yet.</div>"
    order = ["small (<100 lines)", "medium (100-500)", "large (500+)"]
    body += "<table><tr><th>model</th><th class=n>merged PRs</th><th class=n>median / PR</th>" + "".join(f"<th class=n>{b}</th>" for b in order) + "</tr>"
    for model, costs in sorted(allm.items(), key=lambda kv: -sum(kv[1])):
        cs = sorted(costs)
        body += f"<tr><td><code>{esc(model)}</code></td><td class=n>{len(cs)}</td><td class=n><b>{fmt_usd(cs[len(cs) // 2])}</b></td>"
        for b in order:
            v = sorted(agg.get((model, b), []))
            body += f"<td class=n>{fmt_usd(v[len(v) // 2]) + f' <span class=tag>{len(v)}</span>' if v else '-'}</td>"
        body += "</tr>"
    body += "</table><div class='note'>A PR worked on with two models counts once per model. Medians over a handful of PRs are noisy; read these as a trend, not a score.</div>"
    return body


def view_unattributed(conn, me):
    rows = conn.execute("""
      SELECT a.bucket, e.repo, e.branch, COUNT(*) n, COUNT(DISTINCT e.session_id) sessions, SUM(e.cost_usd) cost, MAX(e.ts) last_ts
      FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
      WHERE a.confidence='unattributed' GROUP BY a.bucket, e.repo, e.branch ORDER BY cost DESC""", (me,)).fetchall()
    tot = conn.execute("""SELECT SUM(e.cost_usd) c FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
                          WHERE a.confidence='unattributed'""", (me,)).fetchone()["c"]
    explain = {"unshipped": "branch work that never became a PR (exploration)", "default-branch": "work on main/master not within an hour before a PR branch started",
               "no-repo": "working directory is not a git repo", "no-branch": "no branch recorded"}
    body = "<h1>Unattributed</h1><p class='sub'>Usage that could not be linked to a PR. Shown honestly rather than hidden.</p>"
    body += tiles([("unattributed cost", fmt_usd(tot))])
    body += "<table><tr><th>bucket</th><th>repo</th><th>branch</th><th class=n>responses</th><th class=n>sessions</th><th>last used</th><th class=n>cost</th></tr>"
    for r in rows:
        body += (f"<tr><td>{esc(r['bucket'])}<br><span class=tag>{esc(explain.get(r['bucket'], ''))}</span></td><td>{esc(r['repo'] or '-')}</td><td><code>{esc(r['branch'] or '-')}</code></td>"
                 f"<td class=n>{r['n']}</td><td class=n>{r['sessions']}</td><td>{esc(short_date(r['last_ts']))}</td><td class=n><b>{fmt_usd(r['cost'])}</b></td></tr>")
    body += "</table><div class='note'>Cursor, Claude.ai and cloud Claude Code sessions have no local logs with a branch, so they do not appear here yet.</div>"
    return body


def view_register(msg=""):
    return ("<h1>Get your ingest token</h1><p class='sub'>One token per person. It lets <code>ledger sync</code> upload your usage and lets you see your own dashboard. Nobody else sees your data.</p>"
            + (f"<div class='note'>{msg}</div>" if msg else "")
            + "<form class='box' method='post' action='/register'><label>Kikoff email<br><input type='email' name='email' required placeholder='you@kikoff.com'></label><button>Create token</button></form>")


def view_login(msg=""):
    note = f"<div class='note'>{msg}</div>" if msg else ""
    if github_config():
        return ("<h1>Sign in</h1><p class='sub'>Use your GitHub account. You will only ever see your own data.</p>" + note
                + "<p><a href='/auth/login'><button type='button'>Sign in with GitHub</button></a></p>")
    return ("<h1>Sign in</h1><div class='note'>GitHub sign-in is not configured yet; using tokens for now.</div>"
            "<p class='sub'>Paste the ingest token you saved from <code>ledger setup</code> (it is in <code>~/.config/pr-token-ledger/config.json</code>).</p>" + note
            + "<form class='box' method='post' action='/login'><label>Token<br><input type='text' name='token' required autocomplete='off' spellcheck='false'></label><button>Sign in</button></form>"
            "<p>No token yet? <a href='/register'>Create one</a>.</p>")


def view_setup(conn, user, host, new_token=None):
    row = conn.execute("SELECT created_at, last_sync FROM users WHERE email=?", (user,)).fetchone()
    url = f"https://{host}"
    body = f"<h1>Setup</h1><p class='sub'>Signed in as <b>{esc(user)}</b>. Last upload: {esc((row and row['last_sync']) or 'never')}.</p>"
    if github_config():
        body += ("<p>On your laptop, from a checkout of <code>pr-token-ledger</code>, sign in once with GitHub and sync:</p>"
                 "<pre>./ledger.sh login\n./ledger.sh sync</pre>"
                 "<p><code>ledger login</code> shows a short code to enter on github.com. It asks only for read access to your profile; it never sees your repositories.</p>")
    if new_token:
        body += (f"<div class='note'>Your new ingest token. It is shown once; the old one stops working.</div><pre>{esc(new_token)}</pre>"
                 f"<pre>./ledger.sh setup --email {esc(user)} --server {esc(url)} --token {esc(new_token)}\n./ledger.sh sync</pre>")
    elif not github_config():
        body += ("<p>The collector on your laptop uploads with a personal ingest token. Generate one here, then run the commands it shows.</p>"
                 "<form method='post' action='/setup/token'><button>Generate ingest token</button></form>")
    body += ("<h2 style='font-size:17px;margin-top:24px'>What gets uploaded</h2><p>Only metadata: timestamps, model, token counts, repo, branch, session id and PR numbers. "
             "Never prompt or response text. Only you can see your rows.</p>")
    return body


def view_token(email, token, host):
    url = f"https://{host}"
    return (f"<h1>Token for {esc(email)}</h1><div class='note'>Shown once. Save it now.</div><pre>{esc(token)}</pre>"
            f"<p>On your laptop, from the pr-token-ledger checkout:</p><pre>./ledger.sh setup --email {esc(email)} --server {esc(url)} --token {esc(token)}\n./ledger.sh sync</pre>"
            f"<p>Then open <a href='/login?token={esc(token)}'>your dashboard</a> (this link signs you in).</p>")


# ---------- ingest ----------
def upsert(conn, table, cols, rows, fixed=None):
    if not rows:
        return 0
    sql = f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    out = []
    for r in rows:
        r = dict(r, **(fixed or {}))
        out.append([json.dumps(v) if isinstance(v, (list, dict)) else v for v in (r.get(c) for c in cols)])
    conn.executemany(sql, out)
    return len(out)


def api_ingest(conn, event):
    email = auth_user(conn, bearer(event))
    if not email:
        return json_resp(401, {"error": "bad token"}), False
    ensure_user(conn, email)
    body = parse_body(event)
    if body.get("email") and body["email"].lower() != email:
        return json_resp(403, {"error": f"token belongs to {email}, not {body['email']}"}), False
    n = {"events": upsert(conn, "usage_event", EVENT_COLS, body.get("events") or [], {"user": email}),
         "prs": upsert(conn, "pr", PR_COLS, body.get("prs") or []),
         "tickets": upsert(conn, "ticket", TICKET_COLS, body.get("tickets") or []),
         "attributions": upsert(conn, "attribution", ATTR_COLS, body.get("attributions") or [])}
    conn.execute("UPDATE users SET last_sync=? WHERE email=?", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), email))
    return json_resp(200, {"ok": True, "upserted": n}), True


def api_pr_json(conn, event, repo, number):
    email = auth_user(conn, bearer(event))
    if not email:
        return json_resp(401, {"error": "bad token"})
    rows = conn.execute("""SELECT e.model, SUM(e.input_tok) input_tok, SUM(e.output_tok) output_tok,
        SUM(e.cache_write_5m_tok+e.cache_write_1h_tok) cache_write_tok, SUM(e.cache_read_tok) cache_read_tok, SUM(e.cost_usd) cost_usd,
        COUNT(DISTINCT e.session_id) sessions FROM attribution a JOIN usage_event e ON e.id=a.usage_event_id AND e.user=?
        WHERE a.repo=? AND a.pr_number=? GROUP BY e.model""", (email, repo, number)).fetchall()
    return json_resp(200, {"repo": repo, "number": number, "models": [dict(r) for r in rows]})


# ---------- router ----------
def handler(event, context):
    path = event.get("path") or "/"
    method = (event.get("httpMethod") or "GET").upper()
    qs = event.get("queryStringParameters") or {}
    host = (event.get("headers") or {}).get("host", "")
    if path == "/healthz":
        return resp(200, "ok pr-token-ledger", ctype="text/plain")
    conn = load_db()
    try:
        if path == "/api/ingest" and method == "POST":
            r, changed = api_ingest(conn, event)
            if changed:
                save_db(conn)
                conn = None
            return r
        if path == "/api/admin/reset-user" and method == "POST":
            if not admin_token() or bearer(event) != admin_token():
                return json_resp(401, {"error": "admin token required"})
            email = (parse_body(event).get("email") or "").strip().lower()
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT OR REPLACE INTO users (email, token_hash, created_at) VALUES (?,?,?)",
                         (email, thash(token), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            save_db(conn)
            conn = None
            return json_resp(200, {"email": email, "token": token})
        if path.startswith("/api/pr/"):
            parts = path.split("/")  # /api/pr/owner/repo/number
            if len(parts) == 6:
                return api_pr_json(conn, event, f"{parts[3]}/{parts[4]}", int(parts[5]))
            return json_resp(404, {"error": "use /api/pr/<owner>/<repo>/<number>"})
        if path == "/api/config":
            return json_resp(200, {"github_client_id": (github_config() or {}).get("client_id"), "server": f"https://{host}"})
        if path == "/auth/login":
            if not github_config():
                return redirect("/login")
            state = secrets.token_urlsafe(16)
            q = urllib.parse.urlencode({"client_id": github_config()["client_id"], "redirect_uri": f"https://{host}/auth/callback",
                                        "scope": "read:user", "state": state})
            return redirect(f"https://github.com/login/oauth/authorize?{q}", {"set-cookie": set_cookie("ledger_oauth_state", state, 600)})
        if path == "/auth/callback":
            if not github_config():
                return redirect("/login")
            if qs.get("error"):
                return page("Sign in", view_login(f"GitHub error: {esc(qs.get('error_description') or qs['error'])}"))
            if not qs.get("state") or qs.get("state") != cookie(event, "ledger_oauth_state"):
                return page("Sign in", view_login("Sign-in session expired. Try again."))
            cfg = github_config()
            data = urllib.parse.urlencode({"client_id": cfg["client_id"], "client_secret": cfg["client_secret"], "code": qs.get("code", ""),
                                           "redirect_uri": f"https://{host}/auth/callback"}).encode()
            try:
                req = urllib.request.Request("https://github.com/login/oauth/access_token", data=data, method="POST",
                                             headers={"Accept": "application/json", "User-Agent": "pr-token-ledger"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    tokens = json.loads(r.read())
                if "access_token" not in tokens:
                    return page("Sign in", view_login(f"GitHub rejected the sign-in: {esc(tokens.get('error_description') or tokens.get('error') or 'no token')}"))
                user = (github_get("/user", tokens["access_token"]).get("login") or "").lower()
            except urllib.error.HTTPError as e:
                return page("Sign in", view_login(f"GitHub rejected the sign-in ({e.code}). Ask the project owner to check the OAuth app settings."))
            if not user:
                return page("Sign in", view_login("GitHub did not return a username."))
            if ensure_user(conn, user):
                save_db(conn)
                conn = load_db()
            return redirect("/", {"set-cookie": set_cookie("ledger_session", sign_session(user))})
        if path == "/register":
            if github_config():
                return redirect("/auth/login")
            if method == "POST":
                email = (parse_body(event).get("email") or "").strip().lower()
                if not email.endswith("@kikoff.com"):
                    return page("Register", view_register("Use your @kikoff.com address."))
                if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                    return page("Register", view_register(f"{esc(email)} already has a token. If you lost it, ask the project owner to reset it."))
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO users (email, token_hash, created_at) VALUES (?,?,?)",
                             (email, thash(token), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
                save_db(conn)
                conn = None
                return page("Your token", view_token(email, token, host))
            return page("Register", view_register())
        if path == "/login":
            if github_config():
                return page("Sign in", view_login())
            token = ((parse_body(event).get("token") if method == "POST" else qs.get("token", "")) or "").strip()
            if not token:
                return page("Sign in", view_login())
            if not user_for_token(conn, token):
                return page("Sign in", view_login("That token is not recognised. Check for missing characters, or get a new one below."))
            return redirect("/", {"set-cookie": set_cookie("ledger_session", token)})
        if path == "/logout":
            return redirect("/login", {"set-cookie": "ledger_session=; Path=/; HttpOnly; Secure; Max-Age=0"})
        me = current_user(conn, event)
        if not me:
            how = "<a href='/auth/login'><button type='button'>Sign in with GitHub</button></a>" if github_config() else "<a href='/login'>Sign in</a> with your token, or <a href='/register'>get a token</a> to start."
            return page("Welcome", "<h1>PR Token Ledger</h1><p class='sub'>The AI tokens and cost behind every pull request, by model. Private by default: you only see your own data.</p>"
                        f"<p>{how}</p>")
        if path == "/setup":
            return page("Setup", view_setup(conn, me, host), "/setup", me)
        if path == "/setup/token" and method == "POST":
            token = issue_token(conn, me)
            save_db(conn)
            conn = load_db()
            return page("Setup", view_setup(conn, me, host, new_token=token), "/setup", me)
        if path == "/":
            return page("My PRs", view_prs(conn, me), "/", me)
        if path == "/pr":
            return page("PR", view_pr(conn, me, qs.get("repo", ""), int(qs.get("number", "0"))), "/", me)
        if path == "/tickets":
            return page("My tickets", view_tickets(conn, me), "/tickets", me)
        if path == "/models":
            return page("Models", view_models(conn, me), "/models", me)
        if path == "/unattributed":
            return page("Unattributed", view_unattributed(conn, me), "/unattributed", me)
        return resp(404, "not found", ctype="text/plain")
    finally:
        if conn is not None:
            conn.close()
