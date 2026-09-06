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
from collections.abc import Iterator
from pathlib import Path

import pytest

from xrv.rules import Ruleset, RulesetRegistry
from xrv.validate import ValidationEngine

ROOT = Path(__file__).resolve().parent.parent

# Same override the service honours, so a test run can be pointed at a different
# ruleset directory the same way the container is. Absolute default, because the
# suite must not depend on the working directory it was invoked from.
REAL_RULESETS = Path(os.environ.get("XRV_RULESET_DIR", ROOT / "rulesets"))


@pytest.fixture(scope="session")
def real_ruleset() -> Ruleset:
    """The newest ruleset actually fetched into ./rulesets."""
    registry = RulesetRegistry(base=REAL_RULESETS)
    if not registry.versions():
        _require(f"no ruleset under {REAL_RULESETS}", "python scripts/fetch_ruleset.py")
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


CORPUS_DIR = Path(os.environ.get("XRV_CORPUS_DIR", ROOT / "tests" / "corpus" / "_downloaded"))


def _require(what: str, hint: str) -> None:
    if os.environ.get("XRV_REQUIRE_RULESET") == "1":
        pytest.fail(f"{what} — run: {hint}")
    pytest.skip(f"{what} — run: {hint}")


@pytest.fixture(scope="session")
def corpus() -> Path:
    """KoSIT reference messages: business cases that are valid by construction.

    Passing them proves the service emits no false positives. It proves nothing
    about rule coverage — that needs the mutation corpus, which does not exist yet.
    """
    releases = sorted(p for p in CORPUS_DIR.iterdir() if p.is_dir()) if CORPUS_DIR.is_dir() else []
    if not releases:
        _require(
            f"no test suite under {CORPUS_DIR}",
            "python scripts/fetch_ruleset.py --testsuite",
        )
    return releases[-1] / "instances" / "standard"


@pytest.fixture(scope="session")
def engine(real_ruleset: Ruleset) -> Iterator[ValidationEngine]:
    """One engine for the whole session — compiling the stylesheets costs ~1.7s."""
    with ValidationEngine(real_ruleset) as built:
        yield built
