# SPDX-License-Identifier: Apache-2.0
"""Tests for media_archivist.notify — mocked requests, no real network."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from media_archivist import notify


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(notify.ENV_WEBHOOK_URL, raising=False)
    monkeypatch.delenv(notify.ENV_NTFY_TOPIC, raising=False)


def test_notify_discord_shape(monkeypatch):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL,
                        "https://discord.com/api/webhooks/123/abc")
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200)
        ok = notify.notify("archived", "3 new items", {"n": 3})
    assert ok is True
    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["json"] == {"content": "**archived**: 3 new items"}


def test_notify_ntfy_by_url(monkeypatch):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL, "https://ntfy.sh/mytopic")
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200)
        ok = notify.notify("download_complete", "done", {"entry_id": "x"})
    assert ok is True
    _, kwargs = post.call_args
    assert kwargs["json"]["message"] == "done"
    assert kwargs["json"]["title"] == "download_complete"


def test_notify_ntfy_by_topic_env(monkeypatch):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL, "https://example.com/generic-endpoint")
    monkeypatch.setenv(notify.ENV_NTFY_TOPIC, "mytopic")
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200)
        notify.notify("archived", "hi")
    _, kwargs = post.call_args
    assert kwargs["json"]["topic"] == "mytopic"
    assert kwargs["json"]["message"] == "hi"


def test_notify_generic_shape(monkeypatch):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL, "https://example.com/hook")
    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200)
        ok = notify.notify("subscription_sync", "2 new", {"rows_added": 2})
    assert ok is True
    _, kwargs = post.call_args
    assert kwargs["json"] == {
        "event": "subscription_sync",
        "message": "2 new",
        "data": {"rows_added": 2},
    }


def test_notify_no_url_configured_is_noop():
    with patch("requests.post") as post:
        ok = notify.notify("archived", "should not send")
    assert ok is False
    post.assert_not_called()


def test_notify_post_raises_is_swallowed(monkeypatch):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL, "https://example.com/hook")
    with patch("requests.post", side_effect=RuntimeError("boom")):
        ok = notify.notify("archived", "should not raise")
    assert ok is False


def test_notify_non_2xx_raises_and_is_swallowed(monkeypatch):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL, "https://example.com/hook")
    resp = MagicMock()
    resp.raise_for_status.side_effect = RuntimeError("500")
    with patch("requests.post", return_value=resp):
        ok = notify.notify("archived", "should not raise")
    assert ok is False


def test_cmd_notify_test_no_url(capsys):
    class Args:
        message = None

    rc = notify.cmd_notify_test(Args())
    assert rc == 1
    assert "MEDIA_ARCHIVIST_WEBHOOK_URL" in capsys.readouterr().err


def test_cmd_notify_test_sends(monkeypatch, capsys):
    monkeypatch.setenv(notify.ENV_WEBHOOK_URL, "https://example.com/hook")

    class Args:
        message = "hello"

    with patch("requests.post") as post:
        post.return_value = MagicMock(status_code=200)
        rc = notify.cmd_notify_test(Args())
    assert rc == 0
    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["json"]["message"] == "hello"


def test_cli_notify_test_wired():
    from media_archivist.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["notify-test", "--message", "hi"])
    assert args.func.__name__ == "cmd_notify_test"
    assert args.message == "hi"
