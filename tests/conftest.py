"""Shared fixtures.

Tests that need a real KoSIT ruleset skip when one has not been fetched, so a
clean checkout can still run the suite. CI fetches one, so nothing is skipped
where it matters.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xrv.rules import Ruleset, RulesetRegistry

ROOT = Path(__file__).resolve().parent.parent
REAL_RULESETS = ROOT / "rulesets"


@pytest.fixture
def real_ruleset() -> Ruleset:
    """The newest ruleset actually fetched into ./rulesets."""
    registry = RulesetRegistry(base=REAL_RULESETS)
    if not registry.versions():
        pytest.skip("no ruleset fetched — run: python scripts/fetch_ruleset.py")
    return registry.latest()


@pytest.fixture
def fake_registry(tmp_path: Path) -> RulesetRegistry:
    """Two rulesets with the expected layout, built from empty files.

    Layout only — these carry no real stylesheets, so they exercise resolution
    and error paths without depending on a 40 MB download.
    """
    for version, sha in [("2026-01-31", "a" * 64), ("2026-08-31", "b" * 64)]:
        root = tmp_path / version
        paths = {
            "xslt_en16931_cii": "resources/cii/16b/xsl/EN16931-CII-validation.xsl",
            "xslt_en16931_ubl": "resources/ubl/2.1/xsl/EN16931-UBL-validation.xsl",
            "xslt_xrechnung_cii": "resources/xrechnung/3.0.2/xsl/XRechnung-CII-validation.xsl",
            "xslt_xrechnung_ubl": "resources/xrechnung/3.0.2/xsl/XRechnung-UBL-validation.xsl",
            "xsd_cii": "resources/cii/16b/xsd/CrossIndustryInvoice_100pD16B.xsd",
            "xsd_ubl": "resources/ubl/2.1/xsd/maindoc/UBL-Invoice-2.1.xsd",
        }
        for rel in paths.values():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("")
        (root / "manifest.json").write_text(
            json.dumps({"version": version, "sha256": sha, "paths": paths})
        )
    return RulesetRegistry(base=tmp_path)
