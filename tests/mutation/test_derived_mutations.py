"""Replay the derived mutations through the engine the service runs.

`scripts/derive_mutations.py` found these by search, with its own handle on
Saxon. Nothing found that way is trusted until it is shown again here: each
edit is applied to its reference message and validated by `ValidationEngine`,
and the rule it was recorded against has to fire.

The second assertion is the one that keeps the count honest. A validator that
reported every rule on every document would pass "the rule fires" for all of
them, so each case also asserts that what newly fired is exactly what the file
says — the rule and its recorded collateral, nothing more.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest

from mutation.catalogue import MUTATIONS as HAND_WRITTEN
from mutation.derive import TARGET_PREFIX, Derived, newly_fired, read_stylesheet
from xrv.core import Finding, Syntax
from xrv.rules import Ruleset
from xrv.validate import ValidationEngine

pytestmark = pytest.mark.usefixtures("corpus")

#: One file per rule set version, named for it. The newest is the one replayed;
#: `test_was_derived_from_the_rule_set_in_use` fails if that is not the rule set
#: the suite is running against.
DERIVED = sorted((Path(__file__).parent / "derived").glob("*.json"))[-1]
FILE = json.loads(DERIVED.read_text(encoding="utf-8"))
MUTATIONS = tuple(Derived.from_json(record) for record in FILE["mutations"])
UNREACHED = tuple((Syntax(u["syntax"]), u["rule_id"]) for u in FILE["unreached"])

Findings = tuple[Finding, ...]


def ids(mutations: tuple[Derived, ...]) -> list[str]:
    return [m.name for m in mutations]


@pytest.fixture(scope="module")
def findings(
    engine: ValidationEngine, corpus: Path
) -> Callable[[Derived], tuple[Findings, Findings]]:
    """Rule findings for a mutation's base message and for the message once edited."""
    instances = corpus.parent
    before: dict[str, Findings] = {}
    after: dict[str, tuple[Findings, Findings]] = {}

    def run(mutation: Derived) -> tuple[Findings, Findings]:
        if mutation.name not in after:
            document = (instances / mutation.base).read_bytes()
            if mutation.base not in before:
                before[mutation.base] = engine.rule_findings(document, mutation.syntax)
            edited = mutation.edit.apply(document)
            after[mutation.name] = (
                before[mutation.base],
                engine.rule_findings(edited, mutation.syntax),
            )
        return after[mutation.name]

    return run


def fired(pair: tuple[Findings, Findings]) -> frozenset[str]:
    before, after = pair
    return newly_fired(Counter(f.rule_id for f in before), Counter(f.rule_id for f in after))


class TestTheFile:
    def test_was_derived_from_the_rule_set_in_use(self, real_ruleset: Ruleset) -> None:
        """Mutations are data for one rule set, like the explanations are."""
        assert FILE["ruleset_sha256"] == real_ruleset.sha256, (
            f"{DERIVED.name} was derived from rule set {FILE['ruleset_version']}, not "
            f"{real_ruleset.version} — run scripts/derive_mutations.py against it"
        )

    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_accounts_for_every_business_rule(self, real_ruleset: Ruleset, syntax: Syntax) -> None:
        """Proven or listed as unreached — never silently absent."""
        in_rule_set = {
            assertion.rule_id
            for sheet in real_ruleset.stylesheets(syntax)
            for assertion in read_stylesheet(sheet).assertions
            if assertion.rule_id.startswith(TARGET_PREFIX)
        }
        proven = {m.rule_id for m in MUTATIONS if m.syntax is syntax}
        unreached = {rule_id for s, rule_id in UNREACHED if s is syntax}
        assert not proven & unreached
        assert proven | unreached == in_rule_set

    def test_holds_one_mutation_per_rule_and_syntax(self) -> None:
        assert len({(m.syntax, m.rule_id) for m in MUTATIONS}) == len(MUTATIONS)

    def test_every_base_message_exists(self, corpus: Path) -> None:
        for base in {m.base for m in MUTATIONS}:
            assert (corpus.parent / base).is_file(), base


class TestRulesFire:
    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_the_rule_is_reported(self, findings, mutation: Derived) -> None:
        reported = fired(findings(mutation))
        assert mutation.rule_id in reported, (
            f"{mutation.edit.describe()} in {mutation.base} — expected {mutation.rule_id}, "
            f"got {sorted(reported) or 'nothing'}"
        )

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_nothing_unrecorded_is_reported(self, findings, mutation: Derived) -> None:
        reported = fired(findings(mutation))
        assert reported == mutation.expected, (
            f"{mutation.edit.describe()} in {mutation.base} — "
            f"unrecorded: {sorted(reported - mutation.expected)}, "
            f"missing: {sorted(mutation.expected - reported)}"
        )

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_the_finding_carries_text_to_explain(self, findings, mutation: Derived) -> None:
        _, after = findings(mutation)
        finding = next(f for f in after if f.rule_id == mutation.rule_id)
        assert finding.rule_text.strip()
        assert finding.xpath.strip()

    @pytest.mark.parametrize("mutation", MUTATIONS, ids=ids(MUTATIONS))
    def test_the_schema_overlap_is_as_recorded(
        self, engine: ValidationEngine, corpus: Path, mutation: Derived
    ) -> None:
        """Whether the XSD stops the edited document first is a property of the
        rule set, so it is asserted in both directions."""
        edited = mutation.edit.apply((corpus.parent / mutation.base).read_bytes())
        structural = engine.structure.findings(edited, mutation.syntax)
        assert bool(structural) is mutation.caught_by_schema


class TestCoverage:
    """What the search reached, asserted so it cannot quietly shrink."""

    def test_reaches_every_rule_the_hand_written_catalogue_does(self) -> None:
        """The search replaces nothing it cannot do itself."""
        derived = {(m.syntax, m.rule_id) for m in MUTATIONS}
        assert {(m.syntax, m.rule_id) for m in HAND_WRITTEN} <= derived

    def test_most_rules_are_shown_in_the_pipeline_a_user_gets(self) -> None:
        """A mutation the schema rejects proves the rule only with the rule
        layer run on its own. That should be the exception."""
        through = [m for m in MUTATIONS if not m.caught_by_schema]
        assert len(through) > 0.9 * len(MUTATIONS)

    @pytest.mark.parametrize("syntax", list(Syntax))
    def test_each_syntax_is_covered(self, syntax: Syntax) -> None:
        assert len([m for m in MUTATIONS if m.syntax is syntax]) > 100
