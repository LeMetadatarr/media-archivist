# SPDX-License-Identifier: Apache-2.0
"""The YouTube index: which subscription lists which video, and since when.

:mod:`~media_archivist.ytindex.client` asks YouTube (through tutubo and the
channel feed), :mod:`~media_archivist.ytindex.engine` decides what is due
and writes what it learns with :mod:`~media_archivist.ytindex.store`,
:mod:`~media_archivist.ytindex.details` fills per-video details,
:mod:`~media_archivist.ytindex.archive` adds new videos to the JSON
database, and :mod:`~media_archivist.ytindex.query` answers the app API's
reads. Every request goes through :mod:`media_archivist.ytgate`.
"""
