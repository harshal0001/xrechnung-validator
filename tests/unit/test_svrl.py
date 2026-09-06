"""SVRL parsing, without Saxon.

These run on inline documents rather than transform output so they are fast and
need no ruleset. The shapes are taken from real KoSIT output, including the parts
that are easy to guess wrong: EQName locations, the rule id repeated inside the
text, and whitespace from the stylesheet's pretty-printing.
"""

from __future__ import annotations

import pytest

from xrv.core import Severity
from xrv.validate import SvrlError, parse_svrl

HEADER = '<svrl:schematron-output xmlns:svrl="http://purl.oclc.org/dsdl/svrl">'
FOOTER = "</svrl:schematron-output>"


def svrl(*body: str) -> str:
    return HEADER + "".join(body) + FOOTER


def assertion(
    *,
    rule_id: str = "BR-DE-15",
    flag: str = "fatal",
    location: str = "/Q{urn:x}Invoice[1]",
    text: str = "Die Käuferreferenz fehlt.",
    element: str = "failed-assert",
) -> str:
    return (
        f'<svrl:{element} id="{rule_id}" flag="{flag}" location="{location}">'
        f"<svrl:text>{text}</svrl:text>"
        f"</svrl:{element}>"
    )


class TestExtraction:
    def test_reads_every_field(self) -> None:
        (finding,) = parse_svrl(svrl(assertion()))
        assert finding.rule_id == "BR-DE-15"
        assert finding.severity is Severity.ERROR
        assert finding.xpath == "/Q{urn:x}Invoice[1]"
        assert finding.rule_text == "Die Käuferreferenz fehlt."

    def test_location_is_kept_verbatim(self) -> None:
        """EQName, not a prefixed XPath. Rewriting it would unground the finding."""
        (finding,) = parse_svrl(
            svrl(assertion(location="/Q{urn:oasis:…:Invoice-2}Invoice[1]/Q{urn:x}ID[1]"))
        )
        assert finding.xpath == "/Q{urn:oasis:…:Invoice-2}Invoice[1]/Q{urn:x}ID[1]"

    def test_accepts_str_and_bytes(self) -> None:
        document = svrl(assertion())
        assert parse_svrl(document) == parse_svrl(document.encode("utf-8"))

    def test_order_follows_the_document(self) -> None:
        """Stable output: the same invoice must produce the same report twice."""
        findings = parse_svrl(
            svrl(assertion(rule_id="BR-01"), assertion(rule_id="BR-02"), assertion(rule_id="BR-03"))
        )
        assert [f.rule_id for f in findings] == ["BR-01", "BR-02", "BR-03"]

    def test_no_assertions_is_a_clean_document_not_an_error(self) -> None:
        assert parse_svrl(svrl('<svrl:fired-rule context="ubl:Invoice"/>')) == ()

    def test_successful_report_is_also_a_finding(self) -> None:
        """sch:report fires when its test is true. KoSIT ships none today; if a
        future ruleset adds one it must not vanish silently."""
        (finding,) = parse_svrl(svrl(assertion(element="successful-report")))
        assert finding.rule_id == "BR-DE-15"

    def test_non_svrl_elements_are_ignored(self) -> None:
        findings = parse_svrl(
            svrl(
                '<svrl:active-pattern name="p"/>',
                '<svrl:fired-rule context="x"/>',
                assertion(),
                '<svrl:ns-prefix-in-attribute-values prefix="cbc" uri="urn:x"/>',
            )
        )
        assert len(findings) == 1


class TestSeverity:
    @pytest.mark.parametrize(
        ("flag", "expected"),
        [
            ("fatal", Severity.ERROR),
            ("warning", Severity.WARNING),
            ("information", Severity.INFO),
        ],
    )
    def test_flag_drives_severity(self, flag: str, expected: Severity) -> None:
        (finding,) = parse_svrl(svrl(assertion(flag=flag)))
        assert finding.severity is expected

    def test_a_missing_flag_does_not_silently_pass(self) -> None:
        document = svrl(
            '<svrl:failed-assert id="BR-01" location="/x"><svrl:text>t</svrl:text>'
            "</svrl:failed-assert>"
        )
        (finding,) = parse_svrl(document)
        assert finding.severity is Severity.ERROR


class TestRuleText:
    @pytest.mark.parametrize(
        "text",
        ["[BR-52]-Each document shall have an id.", "[BR-52] Each document shall have an id."],
    )
    def test_repeated_rule_id_is_stripped(self, text: str) -> None:
        """Both separators occur in the shipped stylesheets."""
        (finding,) = parse_svrl(svrl(assertion(rule_id="BR-52", text=text)))
        assert finding.rule_text == "Each document shall have an id."

    def test_a_bracket_that_is_not_the_rule_id_survives(self) -> None:
        (finding,) = parse_svrl(svrl(assertion(rule_id="BR-52", text="[BT-122] must be given.")))
        assert finding.rule_text == "[BT-122] must be given."

    def test_whitespace_from_pretty_printing_is_collapsed(self) -> None:
        (finding,) = parse_svrl(svrl(assertion(text="\n        Eine\n   Rechnung   soll.\n     ")))
        assert finding.rule_text == "Eine Rechnung soll."

    def test_comments_do_not_leak_into_the_sentence(self) -> None:
        document = svrl(
            '<svrl:failed-assert id="BR-01" flag="fatal" location="/x">'
            "<svrl:text>Eine <!-- internal note --> Rechnung.</svrl:text>"
            "</svrl:failed-assert>"
        )
        (finding,) = parse_svrl(document)
        assert finding.rule_text == "Eine Rechnung."

    def test_an_assertion_with_no_text_is_still_a_finding(self) -> None:
        document = svrl('<svrl:failed-assert id="BR-01" flag="fatal" location="/x"/>')
        (finding,) = parse_svrl(document)
        assert finding.rule_text == ""
        assert finding.rule_id == "BR-01"

    def test_missing_id_is_marked_not_invented(self) -> None:
        document = svrl('<svrl:failed-assert flag="fatal" location="/x"/>')
        (finding,) = parse_svrl(document)
        assert finding.rule_id == "UNKNOWN"


class TestRejects:
    def test_empty_output(self) -> None:
        with pytest.raises(SvrlError, match="empty"):
            parse_svrl("   ")

    def test_malformed_xml(self) -> None:
        with pytest.raises(SvrlError, match="well-formed"):
            parse_svrl("<svrl:schematron-output>")

    def test_a_document_that_is_not_svrl(self) -> None:
        """Pointing the engine at an ordinary stylesheet must not look like a pass."""
        with pytest.raises(SvrlError, match="schematron-output"):
            parse_svrl("<html><body>not a report</body></html>")
