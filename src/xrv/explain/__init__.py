"""Grounded explanation of findings. Never sees the invoice.

The one rule this package exists to enforce: an explainer receives a `Finding`
and returns text. See `port` for why that signature is the whole safety story.
"""

from xrv.explain.catalogue import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    SCHEMA_VERSION,
    Catalogue,
    CatalogueError,
    CatalogueProvider,
    Entry,
    LanguageNotAvailableError,
    rule_text_digest,
)
from xrv.explain.port import (
    EXPLANATION_FIELDS,
    GROUNDING_FIELDS,
    ExplanationProvider,
    NullProvider,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "EXPLANATION_FIELDS",
    "GROUNDING_FIELDS",
    "LANGUAGES",
    "SCHEMA_VERSION",
    "Catalogue",
    "CatalogueError",
    "CatalogueProvider",
    "Entry",
    "ExplanationProvider",
    "LanguageNotAvailableError",
    "NullProvider",
    "rule_text_digest",
]
