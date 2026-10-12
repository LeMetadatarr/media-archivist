# SPDX-License-Identifier: Apache-2.0
"""Automatic export of the entries a subscription indexes.

The indexer calls :func:`on_entries_added` with the entries a listing or a
feed added to a subscription. When the subscription's ``export_automatic``
is on, those entries are exported to its ``export_target`` in its
``export_layout`` with its ``export_links``, into one automatic export
record per subscription and folder. Entries indexed before the switch was
turned on are not exported here; an explicit export does that. A
subscription created with the switch on exports its whole back catalogue,
because its first listing adds every entry; each poster is then one spaced
``thumb`` request.

The ``.strm`` files point at ``MEDIA_ARCHIVIST_PUBLIC_URL``; without it
there is no address to write and nothing is exported. A target outside the
library roots is refused as it is for an explicit export. A failure is
logged and never raised into the indexer.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Iterable, Optional

from media_archivist import indexdb, jellyfin, links, strm

LOG = logging.getLogger("media_archivist.export_hooks")


def _index_for(db_path: Optional[str]):
    if db_path is not None:
        return indexdb.open_index(db_path)
    with indexdb._open_lock:
        opened = list(indexdb._open.values())
    if len(opened) != 1:
        raise RuntimeError(f"{len(opened)} indexes are open; pass db_path")
    return opened[0]


def on_entries_added(subscription_id: str, entry_ids: Iterable[str],
                     db_path: Optional[str] = None) -> Optional[dict]:
    """Export ``entry_ids`` when ``subscription_id`` exports automatically.

    ``db_path`` is the JSON database; without it the one open index is
    used. Returns the export record, or ``None`` when nothing was exported.
    Posters are fetched in the background, then Jellyfin is asked to
    refresh when it is configured.
    """
    try:
        index = _index_for(db_path)
        row = index.execute("SELECT export_automatic, export_target, export_layout, export_links"
                            " FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
        if row is None or not row["export_automatic"] or not row["export_target"]:
            return None
        base = os.environ.get(links.ENV_PUBLIC_URL, "").strip()
        if not base:
            LOG.warning("subscription %s exports automatically but %s is not set; nothing exported",
                        subscription_id, links.ENV_PUBLIC_URL)
            return None
        key = links.secret(index) if row["export_links"] == "signed" else None

        def body_for(entry):
            return links.stream_url(base, entry.id, key=key) + "\n"

        configured = jellyfin.Jellyfin.from_env() is not None
        record, job = strm.run_export(
            str(index.json_path), index, scope="subscription", scope_id=subscription_id,
            target_dir=row["export_target"], body_for=body_for, layout=row["export_layout"],
            links=row["export_links"], app="user", entry_ids=list(entry_ids), automatic=True,
            refresh_jellyfin=configured, jellyfin_configured=configured)
    except strm.ExportRefused as e:
        LOG.warning("automatic export for %s refused: %s", subscription_id, e)
        return None
    except Exception:
        LOG.exception("automatic export for %s failed", subscription_id)
        return None
    threading.Thread(target=strm.finish_export, args=(index, job),
                     name=f"export-{job.export_id}", daemon=True).start()
    return strm.export_view(record)
