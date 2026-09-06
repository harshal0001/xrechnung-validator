"""Turn SVRL — the Schematron Validation Report Language — into typed findings.

Saxon runs the KoSIT stylesheets and hands back an SVRL document. This module is
the only place that understands its shape, so nothing downstream has to know that
a rule violation is spelled `svrl:failed-assert`.

Three things about real KoSIT SVRL that the shape of this code follows from:

  - Every assertion carries both `id` and `flag`. Verified across all four
    shipped stylesheets — 1936 assertions, none missing either.
  - `location` is an EQName path (`/Q{urn:…}Invoice[1]`), not a prefixed XPath.
    It is kept verbatim: it is what the validator said, and an explanation
    grounded in a rewritten location is grounded in something invented.
  - `svrl:text` repeats the rule id inline, as `[BR-52]-…` or `[BR-DE-1] …`.
    That prefix is dropped, because `rule_id` already carries it and leaving it
    doubles it in every rendered explanation.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from lxml import etree

from xrv.core import Finding, severity_from_kosit_flag

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"

FAILED_ASSERT = f"{{{SVRL_NS}}}failed-assert"

# Schematron's other way of reporting a problem: `sch:report` fires when its test
# is *true*. The shipped KoSIT stylesheets emit none today, but a future ruleset
# using one would otherwise vanish from the report without a trace.
SUCCESSFUL_REPORT = f"{{{SVRL_NS}}}successful-report"

TEXT = f"{{{SVRL_NS}}}text"

_WHITESPACE = re.compile(r"\s+")


class SvrlError(ValueError):
    """The transform output was not usable SVRL."""


def _clean_text(element: etree._Element, rule_id: str) -> str:
    """Collapse whitespace and drop the rule id the text repeats.

    SVRL text is pretty-printed in the stylesheet, so it arrives full of newlines
    and indentation that would end up inside an explanation verbatim.
    """
    node = element.find(TEXT)
    # method="text" over itertext(): it serialises descendant text only, leaving
    # out comments and processing instructions, which itertext() would splice
    # into the middle of a rule sentence.
    raw = etree.tostring(node, method="text", encoding="unicode") if node is not None else ""
    text = _WHITESPACE.sub(" ", raw).strip()

    if rule_id:
        # "[BR-52]-Each …" and "[BR-DE-1] Eine …" both occur.
        text = re.sub(rf"^\[{re.escape(rule_id)}\]\s*-?\s*", "", text)
    return text


def _findings(root: etree._Element) -> Iterator[Finding]:
    for element in root.iter(FAILED_ASSERT, SUCCESSFUL_REPORT):
        rule_id = (element.get("id") or "").strip()
        yield Finding(
            rule_id=rule_id or "UNKNOWN",
            severity=severity_from_kosit_flag(element.get("flag")),
            rule_text=_clean_text(element, rule_id),
            xpath=(element.get("location") or "").strip(),
        )


def parse_svrl(document: str | bytes) -> tuple[Finding, ...]:
    """Parse one SVRL document into findings, in the order the rules fired.

    Order is preserved rather than sorted by severity: it follows the document,
    so two runs over the same invoice produce identical reports.
    """
    if isinstance(document, str):
        document = document.encode("utf-8")

    if not document.strip():
        raise SvrlError("empty transform output — the stylesheet produced nothing")

    try:
        root = etree.fromstring(document)
    except etree.XMLSyntaxError as exc:
        raise SvrlError(f"transform output is not well-formed XML: {exc}") from exc

    if root.tag != f"{{{SVRL_NS}}}schematron-output":
        raise SvrlError(
            f"expected an svrl:schematron-output root, got {root.tag!r} — "
            f"the stylesheet may not be a compiled Schematron"
        )

    return tuple(_findings(root))
