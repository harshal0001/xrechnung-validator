"""Ruleset resolution: which KoSIT configuration produced a result, and where it lives.

This package knows how to *find* and *describe* a ruleset. It never executes one —
that is `validate/`.
"""

from xrv.rules.registry import (
    Ruleset,
    RulesetNotFoundError,
    RulesetRegistry,
    default_registry,
)

__all__ = ["Ruleset", "RulesetNotFoundError", "RulesetRegistry", "default_registry"]
