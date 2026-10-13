# SPDX-License-Identifier: Apache-2.0
import pytest


@pytest.fixture(autouse=True)
def _isolated_index_state(monkeypatch):
    """Each test starts with in-memory pauses, a fresh YouTube client and no open index.

    ``create_app`` moves the process-wide limiter's pause and the YouTube
    client into the index of the database it serves; without this, that
    binding, a pause or the client's last request would leak into the next
    test. The client's spacing is counted but never slept: tests that check
    the spacing give their client a clock of their own.
    """
    from media_archivist import indexdb, ytclient, ytmeta

    monkeypatch.setattr(ytmeta, "LIMITER", ytmeta.MetadataLimiter())
    monkeypatch.setattr(ytclient, "CLIENT", ytclient.YouTubeClient(sleep=lambda seconds: None))
    yield
    indexdb.close_all()


@pytest.fixture(autouse=True)
def _forget_resolved_plays():
    from media_archivist.server.api_v1 import stream

    stream.reset_play_state()
    yield
    stream.reset_play_state()
