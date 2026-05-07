"""Client demo for the ``media-archivist serve`` HTTP surface.

Walks through every endpoint with a real ``httpx`` client. Designed to
be run against a live ``media-archivist serve`` process — see
``examples/live/run_server_smoke.sh`` for an end-to-end harness that
boots the server, runs this script, and tears it down.

Usage::

    python examples/server_client.py http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
import time
from typing import Optional

try:
    import httpx
except ImportError:
    print("install httpx: pip install httpx", file=sys.stderr)
    sys.exit(2)


def banner(title: str) -> None:
    print(f"\n=== {title} ===")


def main(base: str) -> int:
    base = base.rstrip("/")
    with httpx.Client(base_url=base, timeout=20.0) as c:

        banner("GET /stats")
        r = c.get("/stats")
        r.raise_for_status()
        stats = r.json()
        print(json.dumps(stats, indent=2))

        banner("GET /entries?limit=5")
        r = c.get("/entries", params={"limit": 5})
        r.raise_for_status()
        body = r.json()
        print(f"total={body['total']}, returned={len(body['entries'])}")
        for e in body["entries"][:3]:
            print(f"  {e['source']:15} {e['title'][:40]:40} {e['url']}")

        banner("GET /entries?where=...")
        r = c.get("/entries",
                  params={"where": "duration > 60", "limit": 3})
        if r.status_code == 200:
            print(f"matched: {r.json()['total']}")
        else:
            print(f"status {r.status_code}: {r.json()}")

        banner("GET /entries?source=bandcamp&has_stream=true")
        r = c.get("/entries",
                  params={"source": "bandcamp", "has_stream": True})
        print(f"status {r.status_code}, total={r.json().get('total', 0)}")

        banner("GET /feed.rss?limit=5")
        r = c.get("/feed.rss", params={"limit": 5})
        print(f"status {r.status_code}, content-type={r.headers['content-type']}")
        print(r.text[:200], "..." if len(r.text) > 200 else "")

        banner("GET /m3u?limit=5")
        r = c.get("/m3u", params={"limit": 5})
        print(f"status {r.status_code}, content-type={r.headers['content-type']}")
        print(r.text.splitlines()[0] if r.text else "(empty)")

        banner("POST /archive (queue + poll)")
        r = c.post("/archive", json={
            "url": "https://www.youtube.com/@nope-no-such-channel",
            "backend": "youtube",
        })
        if r.status_code != 200:
            print(f"submit failed: {r.status_code} {r.text}")
        else:
            task = r.json()
            print(f"queued task {task['id']} ({task['status']})")
            for _ in range(8):
                r = c.get(f"/tasks/{task['id']}")
                t = r.json()
                print(f"  status={t['status']}, rows_added={t.get('rows_added')}")
                if t["status"] in {"ok", "error"}:
                    break
                time.sleep(0.5)

        banner("GET /docs (OpenAPI Swagger UI)")
        r = c.get("/docs")
        print(f"status {r.status_code}, len={len(r.text)} (HTML rendering)")

        banner("GET /openapi.json (schema)")
        r = c.get("/openapi.json")
        schema = r.json()
        print(f"OpenAPI {schema.get('openapi')}; {len(schema.get('paths', {}))} paths")

    print("\nclient demo finished")
    return 0


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    raise SystemExit(main(base_url))
