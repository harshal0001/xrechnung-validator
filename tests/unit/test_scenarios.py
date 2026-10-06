"""Reading scenarios, and folding them into one question, without a rule set.

The file is in the shape KoSIT ships. What it says is invented, so the tests do
not move when a release re-grades a rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xrv.core import Severity, severity_from_kosit_level
from xrv.rules import Scenario, read_scenarios
from xrv.validate.scenario import _consistent_groups, _first_match

SCENARIOS = """<scenarios xmlns="http://www.xoev.de/de/validator/framework/1/scenarios">
  <name>A configuration</name>
  <scenario>
    <name>First
      kind</name>
    <namespace prefix="doc">urn:first</namespace>
    <namespace prefix="rep">urn:report</namespace>
    <match> exists(/doc:Invoice[doc:Kind = 'first']) </match>
    <validateWithXmlSchema>
      <resource><name>Schema</name><location>resources/first.xsd</location></resource>
    </validateWithXmlSchema>
    <validateWithSchematron>
      <resource><name>Core</name><location>resources/core.xsl</location></resource>
    </validateWithSchematron>
    <validateWithSchematron>
      <resource><name>National</name><location> resources/national.xsl </location></resource>
    </validateWithSchematron>
    <createReport>
      <resource><name>Report</name><location>resources/report.xsl</location></resource>
      <customLevel level="warning">BR-A</customLevel>
      <customLevel level="error">BR-B</customLevel>
      <customLevel level="information">BR-C</customLevel>
    </createReport>
  </scenario>
  <scenario>
    <name>Second kind</name>
    <namespace prefix="doc">urn:first</namespace>
    <match>exists(/doc:Invoice[doc:Kind = 'second'])</match>
  </scenario>
  <noScenarioReport><resource><name>none</name></resource></noScenarioReport>
</scenarios>
"""


@pytest.fixture
def scenarios(tmp_path: Path) -> tuple[Scenario, ...]:
    path = tmp_path / "scenarios.xml"
    path.write_text(SCENARIOS, encoding="utf-8")
    return read_scenarios(path)


class TestReading:
    def test_keeps_the_order_of_the_file(self, scenarios: tuple[Scenario, ...]) -> None:
        """Order is how a document two scenarios accept is resolved."""
        assert [s.name for s in scenarios] == ["First kind", "Second kind"]

    def test_the_match_is_kept_as_written(self, scenarios: tuple[Scenario, ...]) -> None:
        assert scenarios[0].match == "exists(/doc:Invoice[doc:Kind = 'first'])"

    def test_prefixes_are_read_per_scenario(self, scenarios: tuple[Scenario, ...]) -> None:
        assert scenarios[0].namespaces == {"doc": "urn:first", "rep": "urn:report"}
        assert scenarios[1].namespaces == {"doc": "urn:first"}

    def test_only_the_schematron_stylesheets_are_what_it_validates_with(
        self, scenarios: tuple[Scenario, ...]
    ) -> None:
        """Not the schema and not the report template, which sit beside them."""
        assert scenarios[0].stylesheets == ("resources/core.xsl", "resources/national.xsl")
        assert scenarios[1].stylesheets == ()

    def test_overrides_are_read_as_severities(self, scenarios: tuple[Scenario, ...]) -> None:
        assert scenarios[0].levels == {
            "BR-A": Severity.WARNING,
            "BR-B": Severity.ERROR,
            "BR-C": Severity.INFO,
        }

    def test_a_scenario_without_overrides_changes_nothing(
        self, scenarios: tuple[Scenario, ...]
    ) -> None:
        assert scenarios[1].levels == {}
        assert scenarios[1].severity("BR-A", Severity.ERROR) is Severity.ERROR

    def test_an_override_replaces_the_stylesheets_grade(
        self, scenarios: tuple[Scenario, ...]
    ) -> None:
        assert scenarios[0].severity("BR-A", Severity.ERROR) is Severity.WARNING
        assert scenarios[0].severity("BR-B", Severity.WARNING) is Severity.ERROR

    def test_a_rule_it_does_not_name_keeps_its_grade(self, scenarios: tuple[Scenario, ...]) -> None:
        assert scenarios[0].severity("BR-Z", Severity.WARNING) is Severity.WARNING


class TestLevels:
    @pytest.mark.parametrize(
        ("level", "severity"),
        [
            ("error", Severity.ERROR),
            ("warning", Severity.WARNING),
            ("information", Severity.INFO),
            (" Warning ", Severity.WARNING),
        ],
    )
    def test_are_mapped(self, level: str, severity: Severity) -> None:
        assert severity_from_kosit_level(level) is severity

    @pytest.mark.parametrize("level", [None, "", "fatal", "notice"])
    def test_one_we_do_not_know_is_loud(self, level: str | None) -> None:
        """ "fatal" is a stylesheet flag, not a report level. It is not guessed at."""
        assert severity_from_kosit_level(level) is Severity.ERROR


class TestOneQuestion:
    def test_answers_with_the_position_of_the_first_match(
        self, scenarios: tuple[Scenario, ...]
    ) -> None:
        assert _first_match(scenarios) == (
            "if (exists(/doc:Invoice[doc:Kind = 'first'])) then 1 else "
            "if (exists(/doc:Invoice[doc:Kind = 'second'])) then 2 else 0"
        )

    def test_scenarios_that_agree_on_their_prefixes_are_asked_together(
        self, scenarios: tuple[Scenario, ...]
    ) -> None:
        assert _consistent_groups(scenarios) == [scenarios]

    def test_a_prefix_that_changes_meaning_starts_a_new_question(self) -> None:
        """One expression is evaluated under one set of bindings."""
        first = Scenario("a", "exists(/p:A)", {"p": "urn:one"}, {})
        second = Scenario("b", "exists(/p:B)", {"p": "urn:two"}, {})
        third = Scenario("c", "exists(/p:C)", {"p": "urn:two"}, {})
        assert _consistent_groups([first, second, third]) == [(first,), (second, third)]

    def test_no_scenarios_is_no_question(self) -> None:
        assert _consistent_groups([]) == []
