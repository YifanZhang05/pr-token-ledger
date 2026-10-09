"""Parse Claude Code session logs (~/.claude/projects/**/*.jsonl) into usage events.

Only metadata and token counts are extracted. Prompt and response text is never read
into the event, let alone uploaded.
"""
import hashlib
import json
import os
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"


def _event_id(message_id: str, request_id: str, uuid: str) -> str:
    key = f"claude:{message_id or uuid}:{request_id or ''}"
    return hashlib.sha1(key.encode()).hexdigest()


def iter_events(root: Path = DEFAULT_ROOT):
    """Yield raw usage events. One per API response (duplicate log lines are collapsed)."""
    seen: set[str] = set()
    if not root.exists():
        return
    for path in sorted(root.rglob("*.jsonl")):
        try:
            fh = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"type":"assistant"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                usage = msg.get("usage")
                if not usage:
                    continue
                eid = _event_id(msg.get("id"), d.get("requestId"), d.get("uuid"))
                if eid in seen:
                    continue
                seen.add(eid)
                cc = usage.get("cache_creation") or {}
                cache_write = int(usage.get("cache_creation_input_tokens") or 0)
                cw_1h = int(cc.get("ephemeral_1h_input_tokens") or 0)
                cw_5m = int(cc.get("ephemeral_5m_input_tokens") or 0)
                if cw_1h + cw_5m == 0:
                    cw_5m = cache_write  # older logs do not split by TTL
                yield {
                    "id": eid,
                    "source": "claude-code",
                    "session_id": d.get("sessionId") or path.stem,
                    "ts": d.get("timestamp"),
                    "cwd": d.get("cwd"),
                    "branch": d.get("gitBranch"),
                    "model": msg.get("model"),
                    "input_tok": int(usage.get("input_tokens") or 0),
                    "output_tok": int(usage.get("output_tokens") or 0),
                    "cache_write_5m_tok": cw_5m,
                    "cache_write_1h_tok": cw_1h,
                    "cache_read_tok": int(usage.get("cache_read_input_tokens") or 0),
                    "tool_version": d.get("version"),
                }
