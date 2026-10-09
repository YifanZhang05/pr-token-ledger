"""Sign in with GitHub from the terminal (OAuth device flow, scope read:user only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Accept": "application/json", "User-Agent": "pr-token-ledger"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def client_id_from_server(server_url):
    try:
        with urllib.request.urlopen(f"{server_url.rstrip('/')}/api/config", timeout=20) as r:
            return json.loads(r.read()).get("github_client_id")
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {server_url}: {e.reason}. Is Twingate connected?") from None


def device_login(client_id, log=print):
    d = _post("https://github.com/login/device/code", {"client_id": client_id, "scope": "read:user"})
    log(f"\nOpen {d['verification_uri']} and enter the code:  {d['user_code']}\n")
    try:
        import webbrowser
        webbrowser.open(d["verification_uri"])
    except Exception:
        pass
    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        t = _post("https://github.com/login/oauth/access_token",
                  {"client_id": client_id, "device_code": d["device_code"], "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if t.get("access_token"):
            return t["access_token"]
        err = t.get("error")
        if err == "slow_down":
            interval += 5
        elif err not in ("authorization_pending", None):
            raise RuntimeError(f"GitHub login failed: {t.get('error_description') or err}")
    raise RuntimeError("GitHub login timed out")


def whoami(token):
    req = urllib.request.Request("https://api.github.com/user", headers={"Authorization": f"Bearer {token}", "User-Agent": "pr-token-ledger"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["login"].lower()
