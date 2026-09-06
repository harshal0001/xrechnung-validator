"""The severity mapping is only correct as long as KoSIT's vocabulary holds.

`severity_from_kosit_flag` hardcodes three flag values. This re-derives them from
the stylesheets actually shipped in the fetched ruleset, so a fourth value in a
future release breaks the build with a clear message instead of being silently
mapped to ERROR in production.
"""

from __future__ import annotations

import re

from xrv.core.models import KNOWN_KOSIT_FLAGS
from xrv.rules import Ruleset

FLAG = re.compile(r'flag="([^"]*)"')


def test_ruleset_uses_no_flag_we_do_not_map(real_ruleset: Ruleset) -> None:
    found: set[str] = set()
    for key in real_ruleset.paths:
        if not key.startswith("xslt_"):
            continue
        found |= set(FLAG.findall(real_ruleset.path(key).read_text(errors="replace")))

    assert found, "no flag attributes found — the stylesheet layout may have changed"

    unmapped = found - KNOWN_KOSIT_FLAGS
    assert not unmapped, (
        f"ruleset {real_ruleset.version} uses flag(s) {sorted(unmapped)} that "
        f"severity_from_kosit_flag does not map; update _KOSIT_FLAG_TO_SEVERITY"
    )
