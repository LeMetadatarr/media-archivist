# SPDX-License-Identifier: Apache-2.0
import pytest


@pytest.fixture(autouse=True)
def _isolated_index_state(monkeypatch):
    """Each test starts with an in-memory metadata pause and no open index.

    ``create_app`` moves the process-wide limiter's pause into the index of
    the database it serves; without this, that binding would leak into the
    next test.
    """
    from media_archivist import indexdb, ytmeta

    monkeypatch.setattr(ytmeta, "LIMITER", ytmeta.MetadataLimiter())
    yield
    indexdb.close_all()
