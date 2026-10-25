# SPDX-License-Identifier: Apache-2.0
"""Schema steps of the SQLite working index, one module per step.

Each ``m<NNNN>_<name>.py`` module defines ``VERSION`` (an integer in the
block its piece of work owns, see :mod:`media_archivist.indexdb`), ``NAME``
and ``apply(conn, index)``. A step runs once, inside a write transaction,
and is recorded in the ``migrations`` table.
"""
