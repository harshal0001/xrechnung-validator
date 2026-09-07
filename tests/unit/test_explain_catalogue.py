"""The frozen explanation catalogue and the guarantees it makes.

Two of these matter more than the rest. Unreviewed text is withheld, because
"every explanation was read by a person" is the claim the frozen catalogue
exists to support. And an explanation written against a rule that has since been
reworded is withheld too — an explanation confidently describing the wrong rule
is worse than no explanation at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xrv.core import Finding, Severity
from xrv.explain import Catalogue, CatalogueError, CatalogueProvider, Entry, rule_text_digest

RULE_TEXT = 'Das Element "Buyer reference" (BT-10) muss übermittelt werden.'


def finding(rule_id: str = "BR-DE-15", rule_text: str = RULE_TEXT) -> Finding:
    return Finding(rule_id=rule_id, severity=Severity.ERROR, rule_text=rule_text, xpath="/Invoice")


def write_catalogue(tmp_path: Path, entries: dict) -> Path:
    path = tmp_path / "2026-08-31.de.json"
    path.write_text(
        json.dumps({"ruleset_version": "2026-08-31", "language": "de", "entries": entries}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def reviewed(tmp_path: Path) -> Catalogue:
    return Catalogue.load(
        write_catalogue(
            tmp_path,
            {
                "BR-DE-15": {
                    "explanation": "Die Käuferreferenz fehlt.",
                    "reviewed": True,
                    "rule_text_digest": rule_text_digest(RULE_TEXT),
                }
            },
        )
    )


class TestServing:
    def test_a_reviewed_entry_is_served(self, reviewed: Catalogue) -> None:
        assert CatalogueProvider(reviewed).explain(finding()) == "Die Käuferreferenz fehlt."

    def test_a_rule_with_no_entry_returns_none(self, reviewed: Catalogue) -> None:
        """Not an error. The caller falls back to the normative rule text."""
        assert CatalogueProvider(reviewed).explain(finding("BR-99")) is None

    def test_explained_attaches_without_mutating(self, reviewed: Catalogue) -> None:
        original = finding()
        (explained,) = CatalogueProvider(reviewed).explained((original,))
        assert explained.explanation == "Die Käuferreferenz fehlt."
        assert original.explanation is None

    def test_findings_without_an_entry_pass_through(self, reviewed: Catalogue) -> None:
        unknown = finding("BR-99")
        assert CatalogueProvider(reviewed).explained((unknown,)) == (unknown,)


class TestReviewGate:
    @pytest.fixture
    def draft(self, tmp_path: Path) -> Catalogue:
        return Catalogue.load(
            write_catalogue(
                tmp_path,
                {
                    "BR-DE-15": {
                        "explanation": "Entwurf, noch nicht geprüft.",
                        "reviewed": False,
                        "rule_text_digest": rule_text_digest(RULE_TEXT),
                    }
                },
            )
        )

    def test_unreviewed_text_is_withheld_by_default(self, draft: Catalogue) -> None:
        """A reader would reasonably assume someone checked it. Until someone
        has, the service says nothing rather than something unchecked."""
        assert CatalogueProvider(draft).explain(finding()) is None

    def test_it_can_be_served_deliberately(self, draft: Catalogue) -> None:
        """The review workflow needs to see its own drafts."""
        assert CatalogueProvider(draft, require_reviewed=False).explain(finding())

    def test_the_reviewed_count_is_reported(self, draft: Catalogue, reviewed: Catalogue) -> None:
        assert draft.reviewed_count == 0
        assert reviewed.reviewed_count == 1


class TestDriftGate:
    def test_an_explanation_for_reworded_text_is_withheld(self, reviewed: Catalogue) -> None:
        """If KoSIT rewords a rule, the explanation may describe something else
        entirely. Confidently wrong is worse than absent."""
        reworded = finding(rule_text="Etwas ganz anderes ist jetzt vorgeschrieben.")
        assert CatalogueProvider(reviewed).explain(reworded) is None

    def test_the_gate_can_be_lifted(self, reviewed: Catalogue) -> None:
        reworded = finding(rule_text="Etwas ganz anderes.")
        assert CatalogueProvider(reviewed, require_current_text=False).explain(reworded)

    def test_the_digest_ignores_whitespace_and_case(self) -> None:
        """Reformatting is not rewording, and should not invalidate a review."""
        assert rule_text_digest("Das  Element\n fehlt.") == rule_text_digest("das element fehlt.")

    def test_the_digest_changes_when_the_meaning_could_have(self) -> None:
        assert rule_text_digest("Das Element muss") != rule_text_digest("Das Element darf nicht")


class TestLoading:
    def test_a_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(CatalogueError, match="no explanation catalogue"):
            Catalogue.load(tmp_path / "absent.json")

    def test_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        with pytest.raises(CatalogueError, match="not valid JSON"):
            Catalogue.load(path)

    def test_entries_missing_an_explanation(self, tmp_path: Path) -> None:
        path = write_catalogue(tmp_path, {"BR-DE-15": {"reviewed": True}})
        with pytest.raises(CatalogueError, match="malformed entries"):
            Catalogue.load(path)

    def test_reviewed_defaults_to_false(self, tmp_path: Path) -> None:
        """An entry that forgot to say is treated as unreviewed, not reviewed."""
        path = write_catalogue(tmp_path, {"BR-DE-15": {"explanation": "x"}})
        assert Catalogue.load(path).entries["BR-DE-15"].reviewed is False


class TestEntry:
    def test_matches_current_text(self) -> None:
        entry = Entry("x", True, rule_text_digest(RULE_TEXT))
        assert entry.matches(RULE_TEXT)
        assert not entry.matches("something else")


class TestTheCommittedCatalogue:
    """The real file in the repository, checked against the fetched rule set."""

    def test_it_covers_every_rule_the_mutation_corpus_proves(self, catalogue: Catalogue) -> None:
        """Those are the rules known to fire, so they are the ones a reader is
        most likely to meet."""
        from mutation.catalogue import MUTATIONS

        proven = {m.rule_id for m in MUTATIONS}
        assert proven <= set(catalogue.entries), sorted(proven - set(catalogue.entries))

    def test_every_explanation_is_german_prose_not_a_restatement(
        self, catalogue: Catalogue
    ) -> None:
        """An explanation that just repeats the rule id adds nothing."""
        for rule_id, entry in catalogue.entries.items():
            assert len(entry.explanation) > 60, rule_id
            assert rule_id not in entry.explanation, rule_id

    def test_no_explanation_has_drifted_from_its_rule_text(
        self, catalogue: Catalogue, real_ruleset
    ) -> None:
        """Guards the committed file against a rule set bump reworking a rule."""
        import re

        from lxml import etree

        svrl = "http://purl.oclc.org/dsdl/svrl"
        texts: dict[str, str] = {}
        for key in real_ruleset.paths:
            if not key.startswith("xslt_"):
                continue
            for node in etree.parse(str(real_ruleset.path(key))).findall(
                f".//{{{svrl}}}failed-assert"
            ):
                rule_id = node.get("id")
                text_node = node.find(f"{{{svrl}}}text")
                if rule_id and rule_id not in texts and text_node is not None:
                    raw = " ".join("".join(text_node.itertext()).split())
                    texts[rule_id] = re.sub(rf"^\[{re.escape(rule_id)}\]\s*-?\s*", "", raw)

        drifted = [
            rule_id
            for rule_id, entry in catalogue.entries.items()
            if rule_id in texts and not entry.matches(texts[rule_id])
        ]
        assert not drifted, f"explanations written against text that has changed: {drifted}"

    def test_it_names_the_ruleset_it_belongs_to(self, catalogue: Catalogue, real_ruleset) -> None:
        assert catalogue.ruleset_version == real_ruleset.version
