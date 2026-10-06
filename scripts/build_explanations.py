#!/usr/bin/env python3
"""Prepare and check the per-rule explanation catalogue.

Explanations are written once per rule set version, reviewed by a person, and
committed. This script is the workflow around that: it reads the normative rule
texts out of the shipped stylesheets, tells you which rules still need an
explanation, and re-checks the ones that exist against the text now in force.

    python scripts/build_explanations.py --missing        # what still needs writing
    python scripts/build_explanations.py --check          # drift and review status
    python scripts/build_explanations.py --refresh        # rewrite digests in place

It deliberately does not write explanation text. Generating the German is a
separate, reviewed step: an explanation nobody read is exactly what the frozen
catalogue exists to avoid.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from xrv.explain.catalogue import (  # noqa: E402
    DEFAULT_LANGUAGE,
    LANGUAGES,
    Catalogue,
    as_written,
    digests,
    rule_text_digest,
)
from xrv.rules import default_registry  # noqa: E402

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"

#: Families worth explaining, most-likely-to-be-hit first. The rest — UBL-CR-*,
#: CII-SR-*, CII-DT-* and friends — are syntax binding rules that fire on
#: malformed structure rather than on anything a finance person chose to do.
PRIORITY = ("BR-DE-", "BR-CO-", "BR-CL-", "BR-S-", "BR-E-", "BR-Z-", "BR-AE-", "BR-G-", "BR-")


def rule_texts(ruleset) -> dict[str, tuple[str, ...]]:
    """Every rule id in the shipped stylesheets, with each wording of its text.

    Usually one. The UBL and CII stylesheets are written separately and do not
    always agree on a rule's wording, so a rule that fires in both can reach the
    service with two different texts — and an explanation has to be approved for
    each. Wordings that differ only in whitespace or case are one wording.
    """
    found: dict[str, dict[str, str]] = {}
    for key in ruleset.paths:
        if not key.startswith("xslt_"):
            continue
        tree = etree.parse(str(ruleset.path(key)))
        for assertion in tree.findall(f".//{{{SVRL_NS}}}failed-assert"):
            rule_id = assertion.get("id")
            if not rule_id:
                continue
            node = assertion.find(f"{{{SVRL_NS}}}text")
            text = " ".join("".join(node.itertext()).split()) if node is not None else ""
            text = re.sub(rf"^\[{re.escape(rule_id)}\]\s*-?\s*", "", text)
            found.setdefault(rule_id, {}).setdefault(rule_text_digest(text), text)
    return {rule_id: tuple(wordings.values()) for rule_id, wordings in found.items()}


def current_digests(wordings: tuple[str, ...]) -> frozenset[str]:
    return frozenset(rule_text_digest(text) for text in wordings)


def priority_of(rule_id: str) -> int:
    for index, prefix in enumerate(PRIORITY):
        if rule_id.startswith(prefix):
            return index
    return len(PRIORITY)


def catalogue_path(version: str, language: str) -> Path:
    return ROOT / "explanations" / f"{version}.{language}.json"


def report_missing(texts: dict[str, tuple[str, ...]], catalogue: Catalogue, limit: int) -> int:
    missing = sorted(
        (r for r in texts if r not in catalogue.entries),
        key=lambda r: (priority_of(r), r),
    )
    business = [r for r in missing if priority_of(r) < len(PRIORITY)]
    print(f"  {len(catalogue.entries)} written, {len(missing)} of {len(texts)} rules without one")
    print(f"  of those, {len(business)} are business rules worth explaining\n")
    for rule_id in business[:limit]:
        print(f"  {rule_id:<16} {texts[rule_id][0][:88]}")
    if len(business) > limit:
        print(f"  … and {len(business) - limit} more")
    return 0


def report_check(texts: dict[str, tuple[str, ...]], catalogue: Catalogue) -> int:
    unknown = sorted(set(catalogue.entries) - set(texts))
    drifted = sorted(
        rule_id
        for rule_id, entry in catalogue.entries.items()
        if rule_id in texts and entry.rule_text_digests != current_digests(texts[rule_id])
    )
    unreviewed = sorted(
        rule_id
        for rule_id, entry in catalogue.entries.items()
        if not entry.reviewed_digests & entry.rule_text_digests
    )
    partly = catalogue.partly_reviewed

    print(f"  entries      {len(catalogue.entries)}")
    print(f"  reviewed     {catalogue.reviewed_count}")
    print(f"  in part      {len(partly)}")
    for rule_id in partly:
        entry = catalogue.entries[rule_id]
        approved = len(entry.reviewed_digests & entry.rule_text_digests)
        print(
            f"                 {rule_id} — approved for {approved} of "
            f"{len(entry.rule_text_digests)} wordings, withheld for the rest"
        )
    print(f"  unreviewed   {len(unreviewed)}")
    for rule_id in unreviewed[:15]:
        print(f"                 {rule_id}")

    if unknown:
        print(f"\n  {len(unknown)} entries name rules this rule set does not have:")
        for rule_id in unknown:
            print(f"    {rule_id}")
    if drifted:
        print(f"\n  {len(drifted)} explanations do not match the rule text now in force:")
        for rule_id in drifted:
            print(f"    {rule_id}")
        print("  Either the text changed or a wording was never recorded. Re-read these")
        print("  before running --refresh; the rule may now mean something else.")

    return 1 if (unknown or drifted) else 0


def refresh(path: Path, texts: dict[str, tuple[str, ...]]) -> int:
    """Rewrite `rule_text_digest` to every wording of the rule now in force.

    Deliberately never touches `reviewed_digest`. That is what makes a KoSIT
    rewording un-review an entry: the approval is for a digest the rule no
    longer has, and the entry is withheld until a person reads the new text and
    approves it again. The same holds for a wording that was never recorded —
    recording it here does not approve it. Refreshing both would silently
    launder an unreviewed change into production.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    changed = []
    for rule_id, body in raw["entries"].items():
        if rule_id not in texts:
            continue
        current = current_digests(texts[rule_id])
        if digests(body.get("rule_text_digest")) != current:
            body["rule_text_digest"] = as_written(current)
            changed.append(rule_id)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  refreshed {len(changed)} digest(s)" + (f": {', '.join(changed)}" if changed else ""))
    if changed:
        print("  reviewed_digest left untouched — re-read whatever is no longer approved in full.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="rule set version (default: latest fetched)")
    ap.add_argument("--missing", action="store_true", help="list rules with no explanation")
    ap.add_argument("--check", action="store_true", help="report drift and review status")
    ap.add_argument("--refresh", action="store_true", help="rewrite digests in place")
    ap.add_argument("--limit", type=int, default=40, help="how many rules to list")
    ap.add_argument(
        "--language", default=DEFAULT_LANGUAGE, choices=LANGUAGES, help="catalogue language"
    )
    args = ap.parse_args()

    ruleset = default_registry().get(args.version)
    texts = rule_texts(ruleset)
    path = catalogue_path(ruleset.version, args.language)

    print(f"rule set {ruleset.version} — {len(texts)} distinct rules")
    print(f"catalogue {path.relative_to(ROOT)}\n")

    if args.refresh:
        return refresh(path, texts)

    catalogue = Catalogue.load(path)
    if args.missing:
        return report_missing(texts, catalogue, args.limit)
    return report_check(texts, catalogue)


if __name__ == "__main__":
    sys.exit(main())
