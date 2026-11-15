# SPDX-License-Identifier: Apache-2.0
"""Outbound webhook notifications — Discord / ntfy / generic JSON.

A homelab automator wants to know when new items get archived, a download
finishes (or fails), or a subscription sync adds items — without polling the
API. This module is the single place that fires those notifications: point
``MEDIA_ARCHIVIST_WEBHOOK_URL`` at a webhook (Discord, ntfy, or any generic
JSON receiver — a "generic webhook" per the ntfy/Apprise/n8n convention this
project already leans on) and every caller below gets a best-effort POST.

Deliberately dumb: no queueing, no retries, no delivery guarantees. A
notification is a side channel, never a source of truth — the archive/
download/sync operation it describes has already fully committed by the
time :func:`notify` is called, so a webhook outage must never be allowed to
fail (or even slow down) the operation itself. Every path in :func:`notify`
catches and logs; it never raises.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

LOG = logging.getLogger("media_archivist.notify")

ENV_WEBHOOK_URL = "MEDIA_ARCHIVIST_WEBHOOK_URL"
ENV_NTFY_TOPIC = "MEDIA_ARCHIVIST_NTFY_TOPIC"

_TIMEOUT_S = 5


def _webhook_url() -> Optional[str]:
    return os.environ.get(ENV_WEBHOOK_URL, "").strip() or None


def _ntfy_topic() -> Optional[str]:
    return os.environ.get(ENV_NTFY_TOPIC, "").strip() or None


def _is_discord(url: str) -> bool:
    return "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url


def _is_ntfy(url: str) -> bool:
    return "ntfy.sh" in url or bool(_ntfy_topic())


def _payload_for(url: str, event: str, message: str,
                  data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Shape the POST body to match the target webhook's expected format.

    Small dispatch on URL shape, kept intentionally simple/extensible: add
    another ``_is_x`` predicate + branch here for a new well-known target,
    everything else falls through to the generic JSON envelope.
    """
    if _is_discord(url):
        return {"content": f"**{event}**: {message}"}
    if _is_ntfy(url):
        payload: Dict[str, Any] = {"message": message, "title": event}
        topic = _ntfy_topic()
        if topic:
            payload["topic"] = topic
        return payload
    return {"event": event, "message": message, "data": data or {}}


def notify(event: str, message: str, data: Optional[Dict[str, Any]] = None) -> bool:
    """Best-effort webhook POST for *event*; never raises.

    Returns ``True`` if a POST was attempted and didn't raise, ``False``
    when no webhook is configured (a clean no-op — callers don't need to
    guard on config presence themselves) or when the POST failed (logged
    as a warning, never propagated: a broken webhook must never break
    archiving/downloading/syncing).
    """
    url = _webhook_url()
    if not url:
        return False

    try:
        import requests

        payload = _payload_for(url, event, message, data)
        resp = requests.post(url, json=payload, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — webhook failures must never propagate
        LOG.warning("notify: webhook POST failed for event=%s: %s", event, e)
        return False


def cmd_notify_test(args) -> int:
    """``media-archivist notify-test`` — send a test notification.

    Lets an operator verify their ``MEDIA_ARCHIVIST_WEBHOOK_URL`` /
    ``MEDIA_ARCHIVIST_NTFY_TOPIC`` configuration without waiting for a real
    archive/download/sync event.
    """
    import sys

    if not _webhook_url():
        print(f"error: {ENV_WEBHOOK_URL} is not set", file=sys.stderr)
        return 1
    message = getattr(args, "message", None) or "media-archivist test notification"
    ok = notify("test", message, {"source": "notify-test"})
    if ok:
        print("notify-test: sent", file=sys.stderr)
        return 0
    print("notify-test: failed to send (see warning log)", file=sys.stderr)
    return 1
