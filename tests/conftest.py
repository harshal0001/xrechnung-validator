"""Shared fixtures.

Tests that need a real KoSIT ruleset skip when one has not been fetched, so a
clean checkout can still run the suite.

That skip is right locally and wrong in CI, where a silently skipped test hides
exactly the drift the guard exists to catch. Setting XRV_REQUIRE_RULESET=1 turns
the skip into a failure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from xrv.rules import Ruleset, RulesetRegistry

ROOT = Path(__file__).resolve().parent.parent

# Same override the service honours, so a test run can be pointed at a different
# ruleset directory the same way the container is. Absolute default, because the
# suite must not depend on the working directory it was invoked from.
REAL_RULESETS = Path(os.environ.get("XRV_RULESET_DIR", ROOT / "rulesets"))


@pytest.fixture
def real_ruleset() -> Ruleset:
    """The newest ruleset actually fetched into ./rulesets."""
    registry = RulesetRegistry(base=REAL_RULESETS)
    if not registry.versions():
        missing = f"no ruleset under {REAL_RULESETS} — run: python scripts/fetch_ruleset.py"
        if os.environ.get("XRV_REQUIRE_RULESET") == "1":
            pytest.fail(missing)
        pytest.skip(missing)
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
