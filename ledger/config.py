"""Local config: ~/.config/pr-token-ledger/config.json plus env overrides."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("LEDGER_HOME", Path.home() / ".config" / "pr-token-ledger"))
CONFIG_FILE = CONFIG_DIR / "config.json"
DB_FILE = CONFIG_DIR / "ledger.db"
REPO_ROOT = Path(__file__).resolve().parent.parent
PRICES_FILE = REPO_ROOT / "prices.json"
DEFAULT_SERVER = "https://yifan-zhang-pr-token-ledger.a.hackweek.kikoff.dev"


def load() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            cfg = {}
    for key, env in (("server_url", "LEDGER_URL"), ("token", "LEDGER_TOKEN"),
                     ("email", "LEDGER_EMAIL"), ("linear_api_key", "LINEAR_API_KEY")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    cfg.setdefault("server_url", DEFAULT_SERVER)
    return cfg


def save(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    os.chmod(CONFIG_FILE, 0o600)
