"""The frozen explanation catalogue and the guarantees it makes.

Three matter more than the rest.

`what` and `why` stay separate all the way out, because one is checkable against
the official rule text and the other is editorial, and folding them together
would present the second as if it had the standing of the first.

Unreviewed text is withheld — "every explanation was read by a person" is the
claim the frozen catalogue exists to support.

And review is recorded as a digest rather than a boolean, so a KoSIT rewording
un-reviews the entry by itself. A stale approval standing over text nobody read
is exactly the failure a boolean cannot catch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xrv.core import Finding, Severity
from xrv.explain import (
    LANGUAGES,
    SCHEMA_VERSION,
    Catalogue,
    CatalogueError,
    CatalogueProvider,
    Entry,
    LanguageNotAvailableError,
    rule_text_digest,
)

RULE_TEXT = 'Das Element "Buyer reference" (BT-10) muss übermittelt werden.'
WHAT = "Die Käuferreferenz (BT-10) fehlt."
WHY = "Öffentliche Auftraggeber nutzen dieses Feld, meist die Leitweg-ID."


def finding(rule_id: str = "BR-DE-15", rule_text: str = RULE_TEXT) -> Finding:
    return Finding(rule_id=rule_id, severity=Severity.ERROR, rule_text=rule_text, xpath="/Invoice")


def write_catalogue(tmp_path: Path, entries: dict, schema: int = SCHEMA_VERSION) -> Path:
    path = tmp_path / "2026-08-31.de.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": schema,
                "ruleset_version": "2026-08-31",
                "language": "de",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    return path


def entry_json(*, reviewed: bool, text: str = RULE_TEXT, why: str = WHY) -> dict:
    digest = rule_text_digest(text)
    return {
        "what": WHAT,
        "why": why,
        "rule_text_digest": digest,
        "reviewed_digest": digest if reviewed else None,
        "reviewed_by": "a reviewer" if reviewed else None,
        "reviewed_at": "2026-09-08" if reviewed else None,
    }


@pytest.fixture
def reviewed(tmp_path: Path) -> Catalogue:
    return Catalogue.load(write_catalogue(tmp_path, {"BR-DE-15": entry_json(reviewed=True)}))


@pytest.fixture
def draft(tmp_path: Path) -> Catalogue:
    return Catalogue.load(write_catalogue(tmp_path, {"BR-DE-15": entry_json(reviewed=False)}))


class TestWhatAndWhyStaySeparate:
    def test_explain_returns_only_the_grounded_half(self, reviewed: Catalogue) -> None:
        provider = CatalogueProvider(reviewed)
        assert provider.explain(finding()) == WHAT
        assert WHY not in (provider.explain(finding()) or "")

    def test_context_returns_only_the_editorial_half(self, reviewed: Catalogue) -> None:
        assert CatalogueProvider(reviewed).context(finding()) == WHY

    def test_they_arrive_on_the_finding_as_two_fields(self, reviewed: Catalogue) -> None:
        """Concatenating them would make editorial text indistinguishable from
        the restatement a fidelity check can actually verify."""
        (explained,) = CatalogueProvider(reviewed).explained((finding(),))
        assert explained.explanation == WHAT
        assert explained.context == WHY

    def test_an_entry_with_no_context_still_serves(self, tmp_path: Path) -> None:
        catalogue = Catalogue.load(
            write_catalogue(tmp_path, {"BR-DE-15": entry_json(reviewed=True, why="")})
        )
        provider = CatalogueProvider(catalogue)
        assert provider.explain(finding()) == WHAT
        assert provider.context(finding()) is None


class TestServing:
    def test_a_rule_with_no_entry_returns_none(self, reviewed: Catalogue) -> None:
        """Not an error. The caller falls back to the normative rule text."""
        assert CatalogueProvider(reviewed).explain(finding("BR-99")) is None

    def test_explained_does_not_mutate_the_original(self, reviewed: Catalogue) -> None:
        original = finding()
        CatalogueProvider(reviewed).explained((original,))
        assert original.explanation is None
        assert original.context is None

    def test_findings_without_an_entry_pass_through(self, reviewed: Catalogue) -> None:
        unknown = finding("BR-99")
        assert CatalogueProvider(reviewed).explained((unknown,)) == (unknown,)


class TestReviewGate:
    def test_unreviewed_text_is_withheld_by_default(self, draft: Catalogue) -> None:
        """A reader would reasonably assume someone checked it. Until someone
        has, the service says nothing rather than something unchecked."""
        assert CatalogueProvider(draft).explain(finding()) is None
        assert CatalogueProvider(draft).context(finding()) is None

    def test_it_can_be_served_deliberately(self, draft: Catalogue) -> None:
        """The review workflow needs to see its own drafts."""
        assert CatalogueProvider(draft, require_reviewed=False).explain(finding()) == WHAT

    def test_the_reviewed_count_is_reported(self, draft: Catalogue, reviewed: Catalogue) -> None:
        assert draft.reviewed_count == 0
        assert reviewed.reviewed_count == 1


class TestDriftUnreviewsAnEntry:
    """Why review is a digest and not a boolean."""

    def test_a_reworded_rule_withholds_a_previously_reviewed_entry(
        self, reviewed: Catalogue
    ) -> None:
        """The approval was for text that no longer exists. A boolean would have
        kept saying yes."""
        reworded = finding(rule_text="Etwas ganz anderes ist jetzt vorgeschrieben.")
        assert CatalogueProvider(reviewed).explain(reworded) is None

    def test_drift_is_withheld_even_when_the_review_gate_is_lifted(
        self, reviewed: Catalogue
    ) -> None:
        """An entry written against different text describes a different rule.
        Serving it is worse than serving nothing, reviewed or not."""
        reworded = finding(rule_text="Etwas ganz anderes.")
        assert CatalogueProvider(reviewed, require_reviewed=False).explain(reworded) is None

    def test_reviewing_then_refreshing_the_digest_does_not_re_approve(self, tmp_path: Path) -> None:
        """A refresh rewrites rule_text_digest and must not touch
        reviewed_digest — otherwise a rule set bump would launder unreviewed
        text into production."""
        body = entry_json(reviewed=True)
        body["rule_text_digest"] = rule_text_digest("a completely different rule")
        catalogue = Catalogue.load(write_catalogue(tmp_path, {"BR-DE-15": body}))
        assert catalogue.reviewed_count == 0

    def test_the_digest_ignores_whitespace_and_case(self) -> None:
        """Reformatting is not rewording, and must not invalidate a review."""
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

    def test_a_v1_catalogue_is_refused_with_the_migration_command(self, tmp_path: Path) -> None:
        """Silently reading a v1 file would mean serving unreviewed text: v1 has
        no reviewed_digest, so nothing would gate it."""
        path = tmp_path / "old.json"
        path.write_text(json.dumps({"entries": {}, "ruleset_version": "2026-08-31"}))
        with pytest.raises(CatalogueError, match="--migrate"):
            Catalogue.load(path)

    def test_entries_missing_what(self, tmp_path: Path) -> None:
        path = write_catalogue(tmp_path, {"BR-DE-15": {"why": "context only"}})
        with pytest.raises(CatalogueError, match="malformed entries"):
            Catalogue.load(path)

    def test_an_entry_that_does_not_say_it_was_reviewed_is_not(self, tmp_path: Path) -> None:
        path = write_catalogue(tmp_path, {"BR-DE-15": {"what": WHAT}})
        assert Catalogue.load(path).entries["BR-DE-15"].reviewed_digest is None


class TestEntry:
    def test_matches_current_text(self) -> None:
        entry = Entry(what=WHAT, why=WHY, rule_text_digest=rule_text_digest(RULE_TEXT))
        assert entry.matches(RULE_TEXT)
        assert not entry.matches("something else")

    def test_is_reviewed_for_needs_both_halves(self) -> None:
        digest = rule_text_digest(RULE_TEXT)
        assert not Entry(WHAT, WHY, digest).is_reviewed_for(RULE_TEXT)
        assert Entry(WHAT, WHY, digest, reviewed_digest=digest).is_reviewed_for(RULE_TEXT)
        assert not Entry(WHAT, WHY, digest, reviewed_digest=digest).is_reviewed_for("other text")


class TestTheCommittedCatalogue:
    """The real file in the repository, checked against the fetched rule set."""

    def test_it_covers_every_rule_the_mutation_corpus_proves(self, catalogue: Catalogue) -> None:
        from mutation.catalogue import MUTATIONS

        proven = {m.rule_id for m in MUTATIONS}
        assert proven <= set(catalogue.entries), sorted(proven - set(catalogue.entries))

    def test_every_entry_has_a_grounded_restatement(self, catalogue: Catalogue) -> None:
        for rule_id, entry in catalogue.entries.items():
            assert entry.what.strip(), rule_id
            assert rule_id not in entry.what, rule_id

    def test_no_entry_has_drifted_from_its_rule_text(
        self, catalogue: Catalogue, rule_texts: dict[str, str]
    ) -> None:
        drifted = [
            rule_id
            for rule_id, entry in catalogue.entries.items()
            if rule_id in rule_texts and not entry.matches(rule_texts[rule_id])
        ]
        assert not drifted, f"entries written against text that has changed: {drifted}"

    def test_it_names_the_ruleset_it_belongs_to(self, catalogue: Catalogue, real_ruleset) -> None:
        assert catalogue.ruleset_version == real_ruleset.version


class TestLanguages:
    def test_a_language_the_service_does_not_write_in_is_refused(self, tmp_path: Path) -> None:
        """Not a missing file: a language that is not on the list at all."""
        with pytest.raises(LanguageNotAvailableError, match="fr"):
            Catalogue.for_ruleset("2026-08-31", tmp_path, language="fr")

    def test_both_committed_catalogues_load(
        self, catalogue: Catalogue, en_catalogue: Catalogue
    ) -> None:
        assert catalogue.language == "de"
        assert en_catalogue.language == "en"
        assert set(LANGUAGES) == {"de", "en"}

    def test_the_two_catalogues_cover_the_same_rules(
        self, catalogue: Catalogue, en_catalogue: Catalogue
    ) -> None:
        """A rule explained in one language and not the other is a gap a reader
        would hit by switching language mid-report."""
        assert set(catalogue.entries) == set(en_catalogue.entries)

    def test_the_two_catalogues_were_written_against_the_same_rule_text(
        self, catalogue: Catalogue, en_catalogue: Catalogue
    ) -> None:
        """Same digest per rule: both explain the text now in force, so a KoSIT
        rewording un-reviews both at once rather than leaving one stale."""
        for rule_id, de in catalogue.entries.items():
            assert en_catalogue.entries[rule_id].rule_text_digest == de.rule_text_digest, rule_id

    def test_no_entry_is_reviewed_against_stale_text(
        self, catalogue: Catalogue, en_catalogue: Catalogue
    ) -> None:
        """An approval only counts if it was given for the text now in force.

        This replaces a snapshot assertion (English reviewed_count == 0) that the
        review itself broke. The durable property is the invariant behind the
        count: every entry with a reviewed_digest has one matching its
        rule_text_digest, so reviewed_count never silently under-reports a
        review that drifted.
        """
        for language, cat in (("de", catalogue), ("en", en_catalogue)):
            approved = [e for e in cat.entries.values() if e.reviewed_digest is not None]
            assert cat.reviewed_count == len(approved), language
            for entry in approved:
                assert entry.reviewed_digest == entry.rule_text_digest, language
