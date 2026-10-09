"""Upload to the ledger web app. Idempotent: the server upserts by primary key."""
import json
import urllib.error
import urllib.request

BATCH = 500


def _post(url, token, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {token}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"server returned {e.code}: {body}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"cannot reach {url}: {e.reason}. Is Twingate connected?") from None


def push(server_url, token, email, events, prs, tickets, attributions):
    base = server_url.rstrip("/")
    # PRs/tickets first so attributions always point at known rows.
    _post(f"{base}/api/ingest", token, {"email": email, "prs": prs, "tickets": tickets})
    sent = 0
    for i in range(0, len(events), BATCH):
        chunk = events[i:i + BATCH]
        ids = {e["id"] for e in chunk}
        attrs = [a for a in attributions if a["usage_event_id"] in ids]
        _post(f"{base}/api/ingest", token, {"email": email, "events": chunk, "attributions": attrs})
        sent += len(chunk)
    return sent
