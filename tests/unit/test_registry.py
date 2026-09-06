"""Ruleset resolution and its failure modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xrv.core import Syntax
from xrv.rules import Ruleset, RulesetNotFoundError, RulesetRegistry, default_registry


class TestResolution:
    def test_lists_versions_in_order(self, fake_registry: RulesetRegistry) -> None:
        assert fake_registry.versions() == ("2026-01-31", "2026-08-31")

    def test_latest_is_the_newest(self, fake_registry: RulesetRegistry) -> None:
        assert fake_registry.latest().version == "2026-08-31"

    def test_get_defaults_to_latest(self, fake_registry: RulesetRegistry) -> None:
        assert fake_registry.get().version == "2026-08-31"

    def test_get_by_version(self, fake_registry: RulesetRegistry) -> None:
        assert fake_registry.get("2026-01-31").sha256 == "a" * 64

    def test_unknown_version_lists_what_is_available(
        self, fake_registry: RulesetRegistry
    ) -> None:
        with pytest.raises(RulesetNotFoundError, match="2026-01-31, 2026-08-31"):
            fake_registry.get("2025-01-01")

    def test_empty_directory(self, tmp_path: Path) -> None:
        with pytest.raises(RulesetNotFoundError, match="fetch_ruleset"):
            RulesetRegistry(base=tmp_path).latest()

    def test_missing_directory_is_not_a_crash(self, tmp_path: Path) -> None:
        registry = RulesetRegistry(base=tmp_path / "nope")
        assert registry.versions() == ()

    def test_directory_without_a_manifest_is_ignored(
        self, fake_registry: RulesetRegistry
    ) -> None:
        """A half-extracted download must not be offered as a ruleset."""
        (fake_registry.base / "2027-01-01").mkdir()
        assert fake_registry.versions() == ("2026-01-31", "2026-08-31")


class TestPaths:
    @pytest.mark.parametrize(
        ("syntax", "expected"),
        [
            (Syntax.CII, ("EN16931-CII-validation.xsl", "XRechnung-CII-validation.xsl")),
            (Syntax.UBL, ("EN16931-UBL-validation.xsl", "XRechnung-UBL-validation.xsl")),
        ],
    )
    def test_stylesheets_are_core_then_cius(
        self, fake_registry: RulesetRegistry, syntax: Syntax, expected: tuple[str, ...]
    ) -> None:
        """EN 16931 first, then the German CIUS layered on top."""
        names = tuple(p.name for p in fake_registry.latest().stylesheets(syntax))
        assert names == expected

    def test_xsd_per_syntax(self, fake_registry: RulesetRegistry) -> None:
        latest = fake_registry.latest()
        assert latest.xsd(Syntax.UBL).name == "UBL-Invoice-2.1.xsd"
        assert latest.xsd(Syntax.CII).name == "CrossIndustryInvoice_100pD16B.xsd"

    def test_unknown_key_names_the_known_ones(self, fake_registry: RulesetRegistry) -> None:
        with pytest.raises(RulesetNotFoundError, match="xslt_en16931_cii"):
            fake_registry.latest().path("xslt_nonsense")

    def test_declared_but_absent_file_fails_here_not_in_saxon(
        self, fake_registry: RulesetRegistry
    ) -> None:
        latest = fake_registry.latest()
        latest.path("xsd_ubl").unlink()
        with pytest.raises(RulesetNotFoundError, match="does not exist"):
            latest.xsd(Syntax.UBL)


class TestManifest:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(RulesetNotFoundError, match="fetch_ruleset"):
            _ = Ruleset(root=tmp_path).version

    def test_manifest_without_a_sha(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(json.dumps({"version": "2026-08-31"}))
        with pytest.raises(RulesetNotFoundError, match="sha256"):
            _ = Ruleset(root=tmp_path).sha256

    def test_manifest_that_is_not_an_object(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("[]")
        with pytest.raises(RulesetNotFoundError, match="not a JSON object"):
            _ = Ruleset(root=tmp_path).version


class TestDefaultRegistry:
    def test_honours_the_container_env_var(
        self, fake_registry: RulesetRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XRV_RULESET_DIR", str(fake_registry.base))
        assert default_registry().latest().version == "2026-08-31"

    def test_falls_back_to_the_fetch_script_location(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XRV_RULESET_DIR", raising=False)
        assert default_registry().base == Path("rulesets")


class TestAgainstTheRealRuleset:
    """Guards against the fetched layout drifting away from what the code expects."""

    def test_every_declared_path_exists(self, real_ruleset: Ruleset) -> None:
        for key in real_ruleset.paths:
            assert real_ruleset.path(key).is_file()

    def test_stylesheets_resolve_for_both_syntaxes(self, real_ruleset: Ruleset) -> None:
        for syntax in Syntax:
            assert len(real_ruleset.stylesheets(syntax)) == 2
            assert real_ruleset.xsd(syntax).is_file()

    def test_sha256_looks_like_a_sha256(self, real_ruleset: Ruleset) -> None:
        assert len(real_ruleset.sha256) == 64
        assert set(real_ruleset.sha256) <= set("0123456789abcdef")
