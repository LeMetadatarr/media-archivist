"""Optional enrichers — pull derived fields onto rows under ``_meta.enriched``.

Each enricher operates on a raw row and returns a partial
:class:`EnrichedBlock`. The orchestrator merges results in.
"""
from media_archivist.enrich.orchestrator import (
    EnrichKind,
    enrich,
    available_enrichers,
)

__all__ = ["EnrichKind", "enrich", "available_enrichers"]
