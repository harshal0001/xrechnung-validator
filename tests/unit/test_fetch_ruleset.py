"""Completeness checks in the fetch script.

`manifest.json` is committed; the resources it points at are not. So a fresh
clone has a ruleset directory that exists and is empty, and anything deciding
"do I need to download this?" by asking whether the directory exists gets the
wrong answer — silently, and in a way that only surfaces when an invoice is
validated. That happened once. These tests are why it should not again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fetch_ruleset

LAYOUT = {
    "xslt_en16931_cii": "resources/cii/16b/xsl/EN16931-CII-validation.xsl",
    "xsd_ubl": "resources/ubl/2.1/xsd/maindoc/UBL-Invoice-2.1.xsd",
}


def make_ruleset(root: Path, *, complete: bool = True, manifest: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if manifest:
        (root / "manifest.json").write_text(
            json.dumps({"version": root.name, "sha256": "a" * 64, "paths": LAYOUT})
        )
    if complete:
        for rel in LAYOUT.values():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("<xsl/>")
    return root


class TestMissingPaths:
    def test_complete_ruleset_is_not_missing_anything(self, tmp_path: Path) -> None:
        assert fetch_ruleset.missing_paths(make_ruleset(tmp_path / "2026-08-31")) == []

    def test_manifest_only_reports_every_path(self, tmp_path: Path) -> None:
        """The shape of a fresh clone: committed manifest, no resources."""
        root = make_ruleset(tmp_path / "2026-08-31", complete=False)
        assert fetch_ruleset.missing_paths(root) == sorted(LAYOUT)

    def test_partial_extraction_reports_only_what_is_absent(self, tmp_path: Path) -> None:
        root = make_ruleset(tmp_path / "2026-08-31")
        (root / LAYOUT["xsd_ubl"]).unlink()
        assert fetch_ruleset.missing_paths(root) == ["xsd_ubl"]

    def test_a_directory_is_not_a_file(self, tmp_path: Path) -> None:
        """A path that exists as a directory must not count as present."""
        root = make_ruleset(tmp_path / "2026-08-31", complete=False)
        (root / LAYOUT["xsd_ubl"]).mkdir(parents=True)
        assert "xsd_ubl" in fetch_ruleset.missing_paths(root)

    @pytest.mark.parametrize("body", ["", "not json", "[]", '{"no_paths": 1}'])
    def test_unreadable_manifest_falls_back_to_the_expected_layout(
        self, tmp_path: Path, body: str
    ) -> None:
        root = tmp_path / "2026-08-31"
        root.mkdir()
        (root / "manifest.json").write_text(body)
        assert fetch_ruleset.missing_paths(root) == sorted(fetch_ruleset.EXPECTED_LAYOUT)

    def test_no_manifest_at_all(self, tmp_path: Path) -> None:
        root = tmp_path / "2026-08-31"
        root.mkdir()
        assert fetch_ruleset.missing_paths(root) == sorted(fetch_ruleset.EXPECTED_LAYOUT)


class TestVerify:
    def test_complete_passes(self, tmp_path: Path) -> None:
        make_ruleset(tmp_path / "2026-08-31")
        assert fetch_ruleset.verify(tmp_path) == 0

    def test_manifest_only_fails(self, tmp_path: Path) -> None:
        """This is the case that built a green image with no rules in it."""
        make_ruleset(tmp_path / "2026-08-31", complete=False)
        assert fetch_ruleset.verify(tmp_path) == 1

    def test_empty_directory_fails(self, tmp_path: Path) -> None:
        assert fetch_ruleset.verify(tmp_path) == 1

    def test_missing_directory_fails(self, tmp_path: Path) -> None:
        assert fetch_ruleset.verify(tmp_path / "nope") == 1

    def test_one_bad_ruleset_fails_the_whole_check(self, tmp_path: Path) -> None:
        make_ruleset(tmp_path / "2026-01-31")
        make_ruleset(tmp_path / "2026-08-31", complete=False)
        assert fetch_ruleset.verify(tmp_path) == 1
