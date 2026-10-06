"""KoSIT scenarios: which kind of document this is, and what that changes.

A validator configuration does not apply one flat list of rules. Its
`scenarios.xml` names the document types it knows — XRechnung, the XRechnung
extension, CVD, plain EN 16931, each per syntax — gives each an XPath that
recognises it, and may re-grade individual rules for it with `customLevel`.

The re-grading is not cosmetic. EN 16931 marks an unknown unit code as fatal and
KoSIT accepts it with a warning; the extension allows a payable amount the core
arithmetic rule rejects. Reporting the stylesheet's own flag in those cases
calls a valid document invalid, or the reverse.

This module reads that file as data. It evaluates nothing: a scenario's `match`
is kept as the string KoSIT wrote, and running it is `validate/`'s job.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from xrv.core import Severity, severity_from_kosit_level

_NS = "{http://www.xoev.de/de/validator/framework/1/scenarios}"


@dataclass(frozen=True)
class Scenario:
    """One document type a configuration validates."""

    name: str
    #: XPath 2.0, true for a document of this type. Verbatim from the file.
    match: str
    #: Prefix bindings the match is written against.
    namespaces: Mapping[str, str]
    #: Severities this scenario assigns in place of the stylesheet's own flag.
    levels: Mapping[str, Severity]
    #: The Schematron stylesheets this scenario validates with, as paths inside
    #: the configuration. A plain EN 16931 invoice names one; an XRechnung names
    #: two, because the German CIUS applies only to a document that claims it.
    stylesheets: tuple[str, ...] = ()

    def severity(self, rule_id: str, default: Severity) -> Severity:
        return self.levels.get(rule_id, default)


def read_scenarios(path: Path) -> tuple[Scenario, ...]:
    """Every scenario in a `scenarios.xml`, in the order the file lists them.

    Order is kept because it is how KoSIT's validator resolves a document that
    more than one scenario would accept: the first match wins.
    """
    scenarios = []
    for element in ElementTree.parse(path).getroot().iter(f"{_NS}scenario"):
        match = (element.findtext(f"{_NS}match") or "").strip()
        if not match:
            continue
        scenarios.append(
            Scenario(
                name=" ".join((element.findtext(f"{_NS}name") or "").split()),
                match=match,
                namespaces={
                    ns.get("prefix", ""): (ns.text or "").strip()
                    for ns in element.iter(f"{_NS}namespace")
                    if ns.get("prefix")
                },
                levels={
                    (level.text or "").strip(): severity_from_kosit_level(level.get("level"))
                    for level in element.iter(f"{_NS}customLevel")
                    if (level.text or "").strip()
                },
                stylesheets=tuple(
                    location
                    for step in element.iter(f"{_NS}validateWithSchematron")
                    for resource in step.iter(f"{_NS}location")
                    if (location := (resource.text or "").strip())
                ),
            )
        )
    return tuple(scenarios)
