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
from xrv.rules.scenarios import Scenario, read_scenarios

__all__ = [
    "Ruleset",
    "RulesetNotFoundError",
    "RulesetRegistry",
    "Scenario",
    "default_registry",
    "read_scenarios",
]
