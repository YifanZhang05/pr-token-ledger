"""Map a working directory to a GitHub repo slug (owner/name)."""
import re
import subprocess
from functools import lru_cache
from pathlib import Path

_SLUG = re.compile(r"github\.com[:/](?P<owner>[^/]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$")


def remote_to_slug(url: str):
    m = _SLUG.search(url.strip())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


@lru_cache(maxsize=None)
def repo_for_cwd(cwd: str):
    """Walk up from cwd until a git repo answers. Returns owner/name or None."""
    p = Path(cwd)
    while True:
        if p.exists():
            try:
                out = subprocess.run(["git", "-C", str(p), "remote", "get-url", "origin"],
                                     capture_output=True, text=True, timeout=10)
                if out.returncode == 0:
                    return remote_to_slug(out.stdout)
                # Inside a directory that is not a repo at all: stop climbing when git says so.
                if "not a git repository" in out.stderr:
                    return None
            except (subprocess.TimeoutExpired, OSError):
                return None
        if p.parent == p:
            return None
        p = p.parent
