"""The domain contract: what a finding is, and what a report is.

Nothing in here imports Saxon, lxml, FastAPI or the generated bindings. That is
deliberate — every other package depends on this one, so a dependency here would
propagate everywhere.
"""

from xrv.core.models import (
    Finding,
    Severity,
    Source,
    Syntax,
    ValidationReport,
    severity_from_kosit_flag,
)

__all__ = [
    "Finding",
    "Severity",
    "Source",
    "Syntax",
    "ValidationReport",
    "severity_from_kosit_flag",
]
