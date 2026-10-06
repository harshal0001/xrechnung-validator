"""Recognise which KoSIT scenario a document belongs to.

`rules/` reads the scenarios as data; this runs them. Each carries the XPath
KoSIT wrote to recognise its document type, and that expression is evaluated as
written, by Saxon, against the document — no customization identifier is
spelled in this code, so a release that adds or renames a scenario changes the
behaviour here by changing the file.

The expressions are XPath 2.0, which is why Saxon evaluates them and lxml does
not. Saxon compiles an XPath each time it is asked to evaluate one, so asking
eleven questions costs eleven compilations per document. The scenarios are
therefore folded into a single conditional that answers with the position of
the first one that matches.
"""

from __future__ import annotations

from collections.abc import Iterable

from saxonche import PySaxonProcessor, PyXdmNode, PyXPathProcessor

from xrv.rules import Scenario


class ScenarioMatcher:
    """The scenarios of one rule set, ready to be asked about a document.

    Belongs to the thread that owns the processor, like everything else Saxon.
    """

    def __init__(self, processor: PySaxonProcessor, scenarios: Iterable[Scenario]) -> None:
        self._groups: list[tuple[PyXPathProcessor, str, tuple[Scenario, ...]]] = []
        for group in _consistent_groups(scenarios):
            xpath = processor.new_xpath_processor()
            for prefix, uri in _bindings(group).items():
                xpath.declare_namespace(prefix, uri)
            self._groups.append((xpath, _first_match(group), group))

    def match(self, node: PyXdmNode) -> Scenario | None:
        """The first scenario that recognises the document, as KoSIT resolves it."""
        for xpath, expression, scenarios in self._groups:
            xpath.set_context(xdm_item=node)
            position = int(str(xpath.evaluate_single(expression)))
            if position:
                return scenarios[position - 1]
        return None


def _bindings(scenarios: Iterable[Scenario]) -> dict[str, str]:
    return {prefix: uri for s in scenarios for prefix, uri in s.namespaces.items()}


def _consistent_groups(scenarios: Iterable[Scenario]) -> list[tuple[Scenario, ...]]:
    """Split the scenarios into runs that agree on what every prefix means.

    One expression can only be evaluated under one set of bindings. The shipped
    configuration uses its prefixes consistently and comes out as a single run;
    one that reused a prefix for a different namespace would come out as more,
    still asked in order.
    """
    groups: list[list[Scenario]] = []
    bound: dict[str, str] = {}
    for scenario in scenarios:
        clash = any(bound.get(p, uri) != uri for p, uri in scenario.namespaces.items())
        if clash or not groups:
            groups.append([])
            bound = {}
        groups[-1].append(scenario)
        bound.update(scenario.namespaces)
    return [tuple(group) for group in groups]


def _first_match(scenarios: tuple[Scenario, ...]) -> str:
    """`if (first) then 1 else if (second) then 2 … else 0`."""
    expression = "0"
    for position, scenario in reversed(list(enumerate(scenarios, start=1))):
        expression = f"if ({scenario.match}) then {position} else {expression}"
    return expression
