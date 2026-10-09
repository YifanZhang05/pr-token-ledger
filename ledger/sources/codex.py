"""Parse Codex CLI session logs (~/.codex/sessions/**/*.jsonl) into usage events.

Codex writes a session_meta line (cwd + git branch), turn_context lines (model) and
token_count events whose last_token_usage is the usage of the latest response.
OpenAI reports cached tokens as a subset of input tokens; we split them out so
input_tok is the uncached remainder, matching the Claude convention.
"""
import hashlib
import json
import os
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"


def iter_events(root: Path = DEFAULT_ROOT):
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        session_id = path.stem
        cwd = branch = model = None
        prev_total = None
        n = 0
        try:
            fh = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"session_meta"' not in line and '"turn_context"' not in line and '"token_count"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = d.get("type")
                p = d.get("payload") or {}
                if t == "session_meta":
                    session_id = p.get("id") or session_id
                    cwd = p.get("cwd") or cwd
                    git = p.get("git") or {}
                    branch = git.get("branch") or branch
                    model = p.get("model") or model
                elif t == "turn_context":
                    cwd = p.get("cwd") or cwd
                    model = p.get("model") or model
                elif t == "event_msg" and p.get("type") == "token_count":
                    info = p.get("info") or {}
                    last = info.get("last_token_usage") or {}
                    total = info.get("total_token_usage") or {}
                    # Codex emits several token_count lines per response; only count when the
                    # running total moved.
                    key = (total.get("input_tokens"), total.get("output_tokens"))
                    if key == prev_total or not last or not last.get("total_tokens"):
                        continue
                    prev_total = key
                    n += 1
                    cached = int(last.get("cached_input_tokens") or 0)
                    inp = max(int(last.get("input_tokens") or 0) - cached, 0)
                    if not model or (inp + cached + int(last.get("output_tokens") or 0)) == 0:
                        continue  # bookkeeping line with no real usage
                    eid = hashlib.sha1(f"codex:{session_id}:{n}:{d.get('timestamp')}".encode()).hexdigest()
                    yield {
                        "id": eid,
                        "source": "codex",
                        "session_id": session_id,
                        "ts": d.get("timestamp"),
                        "cwd": cwd,
                        "branch": branch,
                        "model": model,
                        "input_tok": inp,
                        "output_tok": int(last.get("output_tokens") or 0),
                        "cache_write_5m_tok": int(last.get("cache_write_input_tokens") or 0),
                        "cache_write_1h_tok": 0,
                        "cache_read_tok": cached,
                        "tool_version": None,
                    }
