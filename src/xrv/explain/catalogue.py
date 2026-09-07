"""Per-rule explanations, written once and frozen next to the rule set.

The text of BR-DE-15 is identical for every invoice that ever fails it. So each
rule's explanation is written once, reviewed by a person, and committed — and the
live path serves it with no model call at all.

That is a stronger grounding claim than generating on demand, not a weaker one:
every explanation a user sees has been read by someone before it shipped. The
`reviewed` flag is what makes that claim checkable rather than aspirational, and
a provider refuses unreviewed entries unless explicitly told not to.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from xrv.core import Finding

CATALOGUE_DIR = Path("explanations")
LANGUAGE = "de"


class CatalogueError(ValueError):
    """The catalogue file is missing or does not say what it must."""


def rule_text_digest(rule_text: str) -> str:
    """A short digest of the normative text an explanation was written against.

    Stored per entry so drift is detectable: if KoSIT rewords a rule, the
    explanation may no longer describe it, and an explanation that quietly
    describes the wrong rule is worse than none.
    """
    normalised = " ".join(rule_text.split()).casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Entry:
    """One rule's explanation and its provenance."""

    explanation: str
    reviewed: bool
    rule_text_digest: str

    def matches(self, rule_text: str) -> bool:
        """Whether this explanation was written against the text now in force."""
        return self.rule_text_digest == rule_text_digest(rule_text)


@dataclass(frozen=True)
class Catalogue:
    """Explanations for one rule set version, in one language."""

    ruleset_version: str
    language: str
    entries: Mapping[str, Entry]

    @classmethod
    def load(cls, path: Path) -> Catalogue:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CatalogueError(f"no explanation catalogue at {path}") from exc
        except json.JSONDecodeError as exc:
            raise CatalogueError(f"{path} is not valid JSON: {exc}") from exc

        if not isinstance(raw, dict):
            raise CatalogueError(f"{path} is not a JSON object")

        try:
            entries = {
                rule_id: Entry(
                    explanation=body["explanation"],
                    reviewed=bool(body.get("reviewed", False)),
                    rule_text_digest=body.get("rule_text_digest", ""),
                )
                for rule_id, body in raw["entries"].items()
            }
        except (KeyError, TypeError, AttributeError) as exc:
            raise CatalogueError(f"{path}: malformed entries ({exc})") from exc

        return cls(
            ruleset_version=str(raw.get("ruleset_version", "")),
            language=str(raw.get("language", LANGUAGE)),
            entries=entries,
        )

    @classmethod
    def for_ruleset(cls, version: str, base: Path | None = None) -> Catalogue:
        directory = base if base is not None else CATALOGUE_DIR
        return cls.load(directory / f"{version}.{LANGUAGE}.json")

    @property
    def reviewed_count(self) -> int:
        return sum(1 for entry in self.entries.values() if entry.reviewed)


@dataclass(frozen=True)
class CatalogueProvider:
    """Serves explanations from a frozen catalogue. Makes no network call.

    Receives a `Finding` and returns text. It is handed no document, holds no
    reference to one, and has no means of obtaining one.
    """

    catalogue: Catalogue
    #: Unreviewed entries are withheld by default. Draft text is for the review
    #: workflow, not for a reader who would reasonably assume someone checked it.
    require_reviewed: bool = True
    #: Refuse an explanation written against a rule text that has since changed.
    require_current_text: bool = True

    def explain(self, finding: Finding) -> str | None:
        entry = self.catalogue.entries.get(finding.rule_id)
        if entry is None:
            return None
        if self.require_reviewed and not entry.reviewed:
            return None
        if self.require_current_text and not entry.matches(finding.rule_text):
            return None
        return entry.explanation

    def explained(self, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
        """Attach explanations where one exists, leaving the rest untouched."""
        return tuple(
            finding.with_explanation(text) if (text := self.explain(finding)) else finding
            for finding in findings
        )
