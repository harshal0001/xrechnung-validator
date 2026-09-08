"""Per-rule explanations, written once and frozen next to the rule set.

The text of BR-DE-15 is identical for every invoice that ever fails it. So each
rule's explanation is written once, reviewed by a person, and committed — and the
live path serves it with no model call at all.

Every entry is split in two, and the split is the point:

  `what`  restates the rule. It must be derivable from the official rule text
          alone, and it is the only field a fidelity check can verify.
  `why`   is context — typical causes, consequences, legal background. Useful,
          human-approved, and *not* in any rule text. Presenting it as though it
          were grounded would be a quiet lie, so it stays a separate field all
          the way out to the API and is labelled editorial in the UI.

Serving is gated on `reviewed_digest` matching the rule text now in force. That
is stronger than a boolean: if KoSIT rewords a rule, the entry un-reviews itself
rather than leaving a stale approval standing over text nobody checked.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from xrv.core import Finding

CATALOGUE_DIR = Path("explanations")
LANGUAGE = "de"
SCHEMA_VERSION = 2


class CatalogueError(ValueError):
    """The catalogue file is missing or does not say what it must."""


def rule_text_digest(rule_text: str) -> str:
    """A short digest of the normative text an explanation was written against.

    Normalised so reformatting is not mistaken for rewording: whitespace is
    collapsed and case folded. A changed `muss` to `darf nicht` still changes it,
    which is the point.
    """
    normalised = " ".join(rule_text.split()).casefold()
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Entry:
    """One rule's explanation, its provenance, and its review state."""

    #: Restates the rule. Must trace to the official text.
    what: str
    #: Editorial context. Never claimed as grounded.
    why: str
    #: Digest of the rule text this entry was written against.
    rule_text_digest: str
    #: Digest the reviewer approved. None until someone has read it.
    reviewed_digest: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    #: Notes from whoever drafted or migrated the entry. Never served.
    review_notes: Sequence[str] = field(default_factory=tuple)

    def matches(self, rule_text: str) -> bool:
        """Whether this entry was written against the text now in force."""
        return self.rule_text_digest == rule_text_digest(rule_text)

    def is_reviewed_for(self, rule_text: str) -> bool:
        """Whether a person approved this entry *for this exact text*.

        Both halves matter. An unreviewed entry has no approval; a reviewed one
        whose rule has since been reworded has approval for something else.
        """
        return self.reviewed_digest is not None and self.reviewed_digest == rule_text_digest(
            rule_text
        )


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

        schema = raw.get("schema_version")
        if schema != SCHEMA_VERSION:
            raise CatalogueError(
                f"{path} declares schema_version {schema!r}; this build reads "
                f"{SCHEMA_VERSION}. Migrate with: "
                f"python scripts/review_sheet.py --migrate"
            )

        try:
            entries = {
                rule_id: Entry(
                    what=body["what"],
                    why=body.get("why") or "",
                    rule_text_digest=body.get("rule_text_digest", ""),
                    reviewed_digest=body.get("reviewed_digest"),
                    reviewed_by=body.get("reviewed_by"),
                    reviewed_at=body.get("reviewed_at"),
                    review_notes=tuple(body.get("review_notes") or ()),
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
        """Entries a person approved for the text they were written against."""
        return sum(
            1
            for entry in self.entries.values()
            if entry.reviewed_digest is not None and entry.reviewed_digest == entry.rule_text_digest
        )


@dataclass(frozen=True)
class CatalogueProvider:
    """Serves explanations from a frozen catalogue. Makes no network call.

    Receives a `Finding` and returns text. It is handed no document, holds no
    reference to one, and has no means of obtaining one.
    """

    catalogue: Catalogue
    #: Unreviewed entries are withheld. Draft text is for the review workflow,
    #: not for a reader who would reasonably assume someone checked it.
    require_reviewed: bool = True

    def entry_for(self, finding: Finding) -> Entry | None:
        """The entry to serve for this finding, or None."""
        entry = self.catalogue.entries.get(finding.rule_id)
        if entry is None:
            return None
        if self.require_reviewed:
            return entry if entry.is_reviewed_for(finding.rule_text) else None
        # Even ungated, an entry written against different text describes a
        # different rule, and serving it would be worse than serving nothing.
        return entry if entry.matches(finding.rule_text) else None

    def explain(self, finding: Finding) -> str | None:
        """The grounded restatement, or None. Context is fetched separately."""
        entry = self.entry_for(finding)
        return entry.what if entry else None

    def context(self, finding: Finding) -> str | None:
        """Editorial context, or None. Never presented as grounded."""
        entry = self.entry_for(finding)
        return (entry.why or None) if entry else None

    def explained(self, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
        """Attach explanation and context where an entry exists."""
        out = []
        for finding in findings:
            entry = self.entry_for(finding)
            out.append(
                finding.with_explanation(entry.what, entry.why or None) if entry else finding
            )
        return tuple(out)
