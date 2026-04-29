"""Abstract base + tiny registry for metadata providers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Dict, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from media_archivist.models.external_ids import ExternalIds
from media_archivist.models.signals import Medium, Signals


class ProviderMatch(BaseModel):
    """One provider's response: what they say the work is."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    confidence: float = Field(ge=0.0, le=1.0)
    signals: Signals = Field(default_factory=Signals)
    external_ids: ExternalIds = Field(default_factory=ExternalIds)


class MetadataProvider(ABC):
    """Look a row up against an external authoritative DB."""

    name: ClassVar[str] = ""
    media: ClassVar[Set[Medium]] = set()

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the provider has all the configuration it needs."""

    @abstractmethod
    def lookup(self, signals: Signals) -> Optional[ProviderMatch]:
        """Return a match for ``signals``, or ``None`` if nothing was found."""


_REGISTRY: Dict[str, MetadataProvider] = {}


def register(provider: MetadataProvider) -> MetadataProvider:
    """Register a provider instance under its ``name``."""
    if not provider.name:
        raise ValueError("provider must declare a `name` class attribute")
    _REGISTRY[provider.name] = provider
    return provider
