"""The boundary that keeps the invoice away from a language model.

The whole safety story of this service is one type signature:

    explain(finding: Finding) -> str | None

An explainer is handed a `Finding` and nothing else. It has no reference to the
document, no way to ask for more, and no field on the object it receives that
carries anything beyond the four things a grounded explanation may be built
from — rule identifier, rule text, location, and the offending value.

That is a structural guarantee rather than a policy: there is no code path by
which an explainer could obtain the invoice, because nothing hands it one.
`tests/unit/test_explain_boundary.py` asserts it against real findings rather
than trusting the shape of this file.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from xrv.core import Finding

#: Fields of a Finding an explanation may be grounded in. Anything outside this
#: set either does not exist on the model or must not be used for grounding.
#: The boundary tests read this, so widening it is a deliberate, visible act.
GROUNDING_FIELDS = frozenset({"rule_id", "severity", "rule_text", "xpath", "offending_value"})


@runtime_checkable
class ExplanationProvider(Protocol):
    """Turns a finding into plain German, or declines.

    Returning None is a first-class answer: no explanation has been written and
    reviewed for this rule yet. The caller falls back to the normative rule text,
    which is always present. An explanation is an improvement on that text, never
    a replacement for having one.
    """

    def explain(self, finding: Finding) -> str | None: ...


class NullProvider:
    """Explains nothing. The default, so the service works with no catalogue."""

    def explain(self, finding: Finding) -> str | None:
        return None
