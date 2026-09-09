"""Read the rulesets fetched by `scripts/fetch_ruleset.py` and expose them as data.

The fetch script writes a `manifest.json` next to each extracted configuration
recording the release tag, the sha256 of the archive bytes, and the layout paths
it verified. This module is the read side of that contract: it turns a directory
on disk into a `Ruleset` that can answer "which version are you" and "where is
the XRechnung UBL stylesheet" without any caller hardcoding a path.

Layout produced by the fetch script:

    rulesets/
      2026-08-31/
        manifest.json
        resources/...
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from xrv.core import LocalisedError, Syntax

MANIFEST_NAME = "manifest.json"

# Logical stylesheet keys, per syntax, in execution order. EN 16931 is the
# European core; XRechnung is the German CIUS layered on top. Both run — a
# document can satisfy the core and still fail the national restriction.
_XSLT_KEYS: Mapping[Syntax, tuple[str, ...]] = {
    Syntax.CII: ("xslt_en16931_cii", "xslt_xrechnung_cii"),
    Syntax.UBL: ("xslt_en16931_ubl", "xslt_xrechnung_ubl"),
}

# UBL puts invoices and credit notes in different root elements with different
# schemas; CII carries both in one, distinguished by a type code. So the schema
# is chosen by root element, not by syntax alone. The first entry per syntax is
# the default when the root is not known yet.
_XSD_KEYS: Mapping[Syntax, Mapping[str, str]] = {
    Syntax.CII: {"CrossIndustryInvoice": "xsd_cii"},
    Syntax.UBL: {"Invoice": "xsd_ubl", "CreditNote": "xsd_ubl_creditnote"},
}


class RulesetNotFoundError(LocalisedError):
    """No ruleset matched, or the rulesets directory is empty."""


# No slots: `cached_property` needs a __dict__, and there is one instance per
# ruleset on disk, so there is nothing to save.
@dataclass(frozen=True)
class Ruleset:
    """One fetched KoSIT validator configuration.

    `version` and `sha256` are copied onto every `ValidationReport`, so they are
    read from the manifest rather than inferred from the directory name — the
    directory could be renamed; the recorded hash could not.
    """

    root: Path

    @cached_property
    def _manifest(self) -> Mapping[str, object]:
        path = self.root / MANIFEST_NAME
        if not path.is_file():
            raise RulesetNotFoundError(f"{path} is missing — run: python scripts/fetch_ruleset.py")
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise RulesetNotFoundError(f"{path} is not a JSON object")
        return data

    def _str_field(self, key: str) -> str:
        value = self._manifest.get(key)
        if not isinstance(value, str) or not value:
            raise RulesetNotFoundError(f"{self.root / MANIFEST_NAME}: missing '{key}'")
        return value

    @property
    def version(self) -> str:
        return self._str_field("version")

    @property
    def sha256(self) -> str:
        return self._str_field("sha256")

    @property
    def paths(self) -> Mapping[str, str]:
        paths = self._manifest.get("paths")
        if not isinstance(paths, dict):
            raise RulesetNotFoundError(f"{self.root / MANIFEST_NAME}: missing 'paths'")
        return paths

    def path(self, key: str) -> Path:
        """Resolve a logical layout key to a file on disk.

        Raises rather than returning a missing path: a stylesheet that is absent
        should fail here, with the key that was asked for, not later inside Saxon.
        """
        try:
            rel = self.paths[key]
        except KeyError:
            known = ", ".join(sorted(self.paths))
            raise RulesetNotFoundError(f"unknown ruleset path '{key}' (have: {known})") from None
        resolved = self.root / rel
        if not resolved.is_file():
            raise RulesetNotFoundError(
                f"ruleset {self.version} declares '{key}' at {rel}, but {resolved} does not exist"
            )
        return resolved

    def stylesheets(self, syntax: Syntax) -> tuple[Path, ...]:
        """The Schematron-compiled stylesheets to run for a syntax, in order."""
        return tuple(self.path(k) for k in _XSLT_KEYS[syntax])

    def xsd(self, syntax: Syntax, root: str | None = None) -> Path:
        """The structural schema for a syntax, chosen by root element name.

        Passing no root gives the invoice schema, which is the right default for
        a caller that has not looked at the document yet.
        """
        choices = _XSD_KEYS[syntax]
        if root is None:
            return self.path(next(iter(choices.values())))
        try:
            key = choices[root]
        except KeyError:
            known = ", ".join(choices)
            raise RulesetNotFoundError(
                f"{syntax} has no schema for root element '{root}' (have: {known})"
            ) from None
        return self.path(key)

    def document_roots(self, syntax: Syntax) -> tuple[str, ...]:
        """Root element names this syntax validates."""
        return tuple(_XSD_KEYS[syntax])

    def __str__(self) -> str:
        return f"{self.version} ({self.sha256[:12]}…)"


@dataclass(frozen=True, slots=True)
class RulesetRegistry:
    """The set of rulesets present on disk.

    Versions are KoSIT release tags in `YYYY-MM-DD` form, so lexicographic order
    is chronological order and `latest()` needs no date parsing.
    """

    base: Path

    def __iter__(self) -> Iterator[Ruleset]:
        if not self.base.is_dir():
            return
        for child in sorted(self.base.iterdir()):
            if (child / MANIFEST_NAME).is_file():
                yield Ruleset(root=child)

    def versions(self) -> tuple[str, ...]:
        return tuple(r.version for r in self)

    def latest(self) -> Ruleset:
        rulesets = list(self)
        if not rulesets:
            raise RulesetNotFoundError(
                f"no ruleset with a {MANIFEST_NAME} under {self.base} — run: "
                f"python scripts/fetch_ruleset.py"
            )
        return rulesets[-1]

    def get(self, version: str | None = None) -> Ruleset:
        """Resolve a version, defaulting to the newest present."""
        if version is None:
            return self.latest()
        for ruleset in self:
            if ruleset.version == version:
                return ruleset
        available = ", ".join(self.versions()) or "none"
        raise RulesetNotFoundError(
            f"ruleset '{version}' not found (available: {available})",
            code="ruleset_not_found",
            version=version,
            available=available,
        )


def default_registry() -> RulesetRegistry:
    """The registry the service uses unless told otherwise.

    `XRV_RULESET_DIR` is set in the container image, where the ruleset is baked in
    at /app/rulesets. Outside the container it falls back to ./rulesets, which is
    where the fetch script writes.
    """
    return RulesetRegistry(base=Path(os.environ.get("XRV_RULESET_DIR", "rulesets")))
