"""XSD validation — the layer that can say "this is not an invoice".

Schematron cannot answer that question. It evaluates business rules against a
shape it assumes is already there, so pointed at arbitrary XML it reports
whatever happens to match rather than the fact that the document is the wrong
thing entirely. The schema answers it directly.

Order matters, and so does stopping. A document the schema rejects has no
reliable shape, so business rules run over it produce findings about a structure
that is not there. Structural failure is therefore FATAL: reported on its own,
with the rules not run.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType
from typing import Self, cast

from lxml import etree

from xrv.core import Finding, Severity, Syntax
from xrv.rules import Ruleset

_WHITESPACE = re.compile(r"\s+")


class MalformedDocumentError(ValueError):
    """The bytes are not well-formed XML, so nothing can be said about them.

    Distinct from "structurally invalid": a document that parses and breaches
    the schema gets a report. One that does not parse gets an error, because
    there is no document to report on.
    """


class StructureValidator:
    """Compiled schemas for one ruleset version.

    Schemas are compiled up front, like the stylesheets — measured at well under
    a second for all three, which fits inside the same unbilled startup window
    and keeps the cost off the request path.
    """

    def __init__(self, ruleset: Ruleset, syntaxes: tuple[Syntax, ...] | None = None) -> None:
        self.ruleset = ruleset
        self._schemas: dict[tuple[Syntax, str], etree.XMLSchema] = {}
        for syntax in syntaxes if syntaxes is not None else tuple(Syntax):
            for root in ruleset.document_roots(syntax):
                schema_doc = etree.parse(str(ruleset.xsd(syntax, root)))
                self._schemas[(syntax, root)] = etree.XMLSchema(schema_doc)

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(sorted({root for _, root in self._schemas}))

    def findings(self, source: Path | str, syntax: Syntax) -> tuple[Finding, ...]:
        """Validate one document. Empty result means structurally sound."""
        path = Path(source)
        try:
            tree = etree.parse(str(path))
        except OSError as exc:
            raise MalformedDocumentError(f"cannot read {path}: {exc}") from exc
        except etree.XMLSyntaxError as exc:
            raise MalformedDocumentError(f"{path.name} is not well-formed XML: {exc}") from exc

        root_name = etree.QName(tree.getroot()).localname
        schema = self._schemas.get((syntax, root_name))
        if schema is None:
            # Not a schema failure — the document is not a member of this syntax
            # at all, so there is no schema to judge it against.
            return (
                Finding(
                    rule_id="XSD-UNKNOWN-ROOT",
                    severity=Severity.FATAL,
                    rule_text=(
                        f"Root element '{root_name}' is not a document type this service "
                        f"validates as {syntax}. Expected one of: {', '.join(self.roots)}."
                    ),
                    xpath=f"/{root_name}",
                ),
            )

        if schema.validate(tree):
            return ()
        # lxml-stubs types error_log as the abstract base, which declares no
        # iteration; the concrete log libxml2 returns is a sequence of entries.
        entries = cast("Iterable[etree._LogEntry]", schema.error_log)
        return tuple(_finding(entry) for entry in entries)

    def close(self) -> None:
        self._schemas.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _finding(entry: etree._LogEntry) -> Finding:
    """One libxml2 schema error as a Finding.

    `type_name` is libxml2's own constant — SCHEMAV_ELEMENT_CONTENT,
    SCHEMAV_CVC_DATATYPE_VALID_1_2_1 — prefixed so a mixed report shows at a
    glance which layer produced a finding.

    `offending_value` stays empty here. libxml2's path uses prefixes it invents
    for the message rather than the document's own, so resolving it back to a
    node to read the value would be guesswork; the message already quotes the
    bad value where there is one, and it is carried verbatim.
    """
    message = _WHITESPACE.sub(" ", entry.message or "").strip()
    return Finding(
        rule_id=f"XSD-{entry.type_name}",
        severity=Severity.FATAL,
        rule_text=message,
        xpath=entry.path or (f"(line {entry.line})" if entry.line else ""),
    )
