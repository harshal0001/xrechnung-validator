#!/usr/bin/env python
"""
review_sheet.py — side-by-side review sheet for per-rule explanations.

Reads the official assertion text straight out of the KoSIT ruleset directory
(compiled XSLT and/or source Schematron), pairs it with your explanations JSON,
and emits a Markdown sheet with one block per rule: official text, `what`, `why`,
digest status, and review flags.

    python review_sheet.py --ruleset rulesets/2026-08-31 \
                           --explanations explanations.de.json \
                           --out review.md

Optional:
    --migrate explanations.de.v2.json   write a v2 JSON (what/why split, review fields)
    --check                             exit 1 on any digest mismatch or unknown rule id
                                        (use this in CI)

Only dependency: lxml.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from xrv.explain.catalogue import rule_text_digest as _xrv_digest  # noqa: E402

SVRL = "http://purl.oclc.org/dsdl/svrl"
SCH = "http://purl.oclc.org/dsdl/schematron"
XSL = "http://www.w3.org/1999/XSL/Transform"

# Words that are understandable but not the XRechnung specification's vocabulary.
# The spec uses Verkäufer (Seller) and Käufer (Buyer) for the EN 16931 parties.
TERMINOLOGY = [
    (r"\bdes Rechnungsstellers\b", "des Verkäufers"),
    (r"\bRechnungssteller\b", "Verkäufer"),
    (r"\bdes Rechnungsempfängers\b", "des Käufers"),
    (r"\bRechnungsempfänger\b", "Käufer"),
    (r"\bEmpfängeranschrift\b", "Käuferanschrift"),
    (r"\bDer Empfänger\b", "Der Käufer"),
    (r"\bdes Empfängers\b", "des Käufers"),
]

# Phrases that almost always signal editorial content rather than rule text.
WHY_SIGNALS = [
    "Häufige Ursache",
    "Typische Ursache",
    "Meist ",
    "Häufig ",
    "häufig,",
    "umsatzsteuerlich",
    "Ohne sie ",
    "Ohne diese ",
    "Sie bestimmt",
    "Es bestimmt",
    "damit ",
    "wird abgewiesen",
    "in der Regel",
    "Leitweg-ID",
]


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Assertion:
    rule_id: str
    flag: str
    text: str
    source: str


def normalize(text: str) -> str:
    """Whitespace-normalise the visible rule text.

    If every digest in your JSON mismatches, your extractor normalises
    differently — align this function with it, not the other way round.
    """
    return re.sub(r"\s+", " ", text).strip()


def strip_rule_prefix(text: str, rule_id: str) -> str:
    """Drop the '[BR-DE-1] ' / '[BR-52]-' prefix svrl:text repeats (mirrors svrl._clean_text)."""
    return re.sub(rf"^\[{re.escape(rule_id)}\]\s*-?\s*", "", text) if rule_id else text


def digest(text: str) -> str:
    """Delegates to xrv so the script and the service can never disagree.

    This used to be a copy. A copy of a hash function is a copy that drifts, and
    the drift would show up as explanations silently withheld in production while
    the review sheet reported everything fine.
    """
    return _xrv_digest(text)


def _attr(el: etree._Element, name: str) -> str:
    """Attribute given literally, or via a child <xsl:attribute name=...>."""
    value = el.get(name)
    if value:
        return value.strip()
    child = el.find(f"{{{XSL}}}attribute[@name='{name}']")
    if child is not None:
        return normalize("".join(child.itertext()))
    return ""


def _svrl_text(el: etree._Element) -> str:
    node = el.find(f"{{{SVRL}}}text")
    return normalize("".join((node if node is not None else el).itertext()))


def extract(path: Path) -> list[Assertion]:
    try:
        tree = etree.parse(str(path))
    except etree.XMLSyntaxError as exc:  # pragma: no cover
        print(f"skip {path.name}: {exc}", file=sys.stderr)
        return []

    found: list[Assertion] = []

    # Compiled XSLT — both attribute styles (literal, or xsl:attribute children).
    for fa in tree.iter(f"{{{SVRL}}}failed-assert"):
        rule_id = _attr(fa, "id")
        if rule_id:
            text = strip_rule_prefix(_svrl_text(fa), rule_id)
            found.append(Assertion(rule_id, _attr(fa, "flag"), text, path.name))

    # Source Schematron.
    for a in tree.iter(f"{{{SCH}}}assert"):
        rule_id = (a.get("id") or "").strip()
        if rule_id:
            text = strip_rule_prefix(normalize("".join(a.itertext())), rule_id)
            found.append(Assertion(rule_id, (a.get("flag") or "").strip(), text, path.name))
    return found


def load_ruleset(root: Path) -> dict[str, list[Assertion]]:
    """rule_id -> assertions (de-duplicated on text; sources merged).

    If the directory carries a KoSIT-style manifest.json, only its `xslt_*` paths
    are read — the same files the validator runs, and none of the duplicate copies
    or report stylesheets that may sit beside them. Otherwise every .xsl/.xslt/.sch
    under the directory is scanned.
    """
    files: list[Path] = []
    manifest = root / "manifest.json"
    if manifest.is_file():
        paths = json.loads(manifest.read_text(encoding="utf-8-sig")).get("paths", {})
        files = [root / rel for key, rel in paths.items() if key.startswith("xslt_")]
        files = [f for f in files if f.is_file()]
    if not files:
        files = sorted(p for p in root.rglob("*") if p.suffix.lower() in {".xsl", ".xslt", ".sch"})
    if not files:
        sys.exit(f"no .xsl/.xslt/.sch files under {root}")

    by_id: dict[str, dict[str, Assertion]] = defaultdict(dict)
    for f in files:
        for a in extract(f):
            key = a.text
            if key in by_id[a.rule_id]:
                prev = by_id[a.rule_id][key]
                by_id[a.rule_id][key] = Assertion(
                    a.rule_id, prev.flag or a.flag, a.text, f"{prev.source}, {a.source}"
                )
            else:
                by_id[a.rule_id][key] = a
    return {rid: list(v.values()) for rid, v in by_id.items()}


# --------------------------------------------------------------------------- #
# Explanations
# --------------------------------------------------------------------------- #


def split_what_why(explanation: str) -> tuple[str, str]:
    """Sentence 1 -> what, rest -> why. Mechanical; the sheet flags it for review."""
    parts = re.split(r"(?<=[.!?])\s+", explanation.strip())
    what = parts[0] if parts else explanation.strip()
    why = " ".join(parts[1:]).strip()
    return what, why


def fix_terminology(text: str) -> str:
    for pattern, replacement in TERMINOLOGY:
        text = re.sub(pattern, replacement, text)
    return text


def why_flags(text: str) -> list[str]:
    return [s.strip() for s in WHY_SIGNALS if s in text]


def as_v2(entry: dict) -> dict:
    """Upgrade a v1 entry ({explanation, reviewed, rule_text_digest}) to v2."""
    if "what" in entry:  # already v2
        return entry
    what, why = split_what_why(entry["explanation"])
    return {
        "what": fix_terminology(what),
        "why": fix_terminology(why),
        "review_notes": [],
        "rule_text_digest": entry.get("rule_text_digest"),
        "reviewed_digest": None,
        "reviewed_by": None,
        "reviewed_at": None,
    }


# --------------------------------------------------------------------------- #
# Sheet
# --------------------------------------------------------------------------- #


def render(
    ruleset: dict[str, list[Assertion]],
    data: dict,
    ruleset_dir: Path,
    only: set[str] | None = None,
) -> tuple[str, int]:
    version = data.get("ruleset_version", "?")
    entries: dict = data["entries"]
    if only:
        entries = {k: v for k, v in entries.items() if k in only}
    problems = 0
    lines: list[str] = []
    w = lines.append

    w(f"# Review sheet — ruleset {version}\n")
    w(
        f"Official text read from `{ruleset_dir}`. Review each `what` against the official text — "
        "never against memory. `why` is editorial and needs a native-speaker read.\n"
    )
    w("| Symbol | Meaning |\n|---|---|")
    w("| ✅ | digest matches current rule text |")
    w("| ❌ | digest mismatch — text changed, or your normalisation differs |")
    w("| ⚠️ | rule id not found in ruleset |")
    w("| ✂️ | sentence looks editorial — belongs in `why` |")
    w("| 🔤 | terminology not the spec's (Verkäufer / Käufer) |\n")

    for rule_id in sorted(entries, key=_rule_sort_key):
        entry = as_v2(entries[rule_id])
        asserts = ruleset.get(rule_id, [])

        w(f"---\n\n## {rule_id}\n")

        if not asserts:
            problems += 1
            w(
                "⚠️ **Not found in ruleset.** Either the id is wrong or the rule was removed "
                "in this version.\n"
            )
        else:
            for i, a in enumerate(asserts, 1):
                label = f"Official text {i}/{len(asserts)}" if len(asserts) > 1 else "Official text"
                flag = f" · flag `{a.flag}`" if a.flag else ""
                w(f"**{label}**{flag} · `{a.source}`\n")
                w(f"> {a.text}\n")
            if len(asserts) > 1:
                w("_Text differs between syntaxes — make sure `what` holds for both._\n")

            current = [digest(a.text) for a in asserts]
            stored = entry.get("rule_text_digest")
            if stored in current:
                w(f"✅ digest `{stored}`\n")
            else:
                problems += 1
                w(f"❌ digest stored `{stored}`, current `{'`, `'.join(current)}`\n")

        w(f"**what** — {entry['what']}\n")
        raw_what = entries[rule_id].get("explanation", entry["what"])
        if any(re.search(p, raw_what) for p, _ in TERMINOLOGY):
            w("🔤 terminology adjusted to Verkäufer / Käufer — confirm against the spec.\n")

        if entry["why"]:
            w(f"**why** — {entry['why']}\n")
            flags = why_flags(entry["why"]) + why_flags(entry["what"])
            if flags:
                w(f"✂️ editorial signals: {', '.join(f'`{f}`' for f in flags)}\n")
        else:
            w("_no `why`_\n")

        if entry.get("review_notes"):
            w("**notes**")
            for n in entry["review_notes"]:
                w(f"- {n}")
            w("")

        cited = cited_rules(entry, rule_id)
        if cited:
            w("**every rule this entry cites, verbatim — check the claim against these**\n")
            for other in cited:
                found = ruleset.get(other, [])
                if found:
                    w(f"- `{other}` [{found[0].flag or '?'}] — {found[0].text}")
                else:
                    problems += 1
                    w(
                        f"- `{other}` — ⚠️ **not in this ruleset.** "
                        "The claim citing it is unsupported."
                    )
            w("")

        w(
            "**Reviewer:** ☐ what traces to text  ☐ BT/BG correct  ☐ terminology  "
            "☐ why is true (native read)  → set `reviewed_digest`\n"
        )

    if only:
        return "\n".join(lines), problems

    missing = sorted(set(ruleset) - set(entries), key=_rule_sort_key)
    w("---\n\n## Rules in the ruleset without an explanation\n")
    w(f"{len(missing)} of {len(ruleset)} rule ids have no entry yet.\n")
    if missing:
        w("```\n" + "\n".join(missing) + "\n```\n")

    return "\n".join(lines), problems


#: A rule id anywhere in prose: BR-48, BR-DE-2, BR-CO-17, BR-DE-CVD-05.
RULE_REF = re.compile(r"\bBR(?:-[A-Z]{1,4})*-\d+(?:-[a-z])?\b")


def cited_rules(entry: dict, own_id: str) -> list[str]:
    """Rule ids an entry leans on, in the order they first appear.

    An editorial claim of the form "BR-48 allows an exception" is only worth
    anything to a reviewer if BR-48 is in front of them. Resolving these turns
    a claim the reviewer has to trust into one they can read.
    """
    prose = " ".join([entry.get("why") or "", *entry.get("review_notes", [])])
    seen: dict[str, None] = {}
    for match in RULE_REF.finditer(prose):
        if match.group() != own_id:
            seen.setdefault(match.group(), None)
    return list(seen)


def _rule_sort_key(rule_id: str):
    parts = re.split(r"(\d+)", rule_id)
    return [int(p) if p.isdigit() else p for p in parts]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--ruleset",
        required=True,
        type=Path,
        help="ruleset directory (e.g. rulesets/2026-08-31)",
    )
    ap.add_argument("--explanations", required=True, type=Path, help="explanations JSON (v1 or v2)")
    ap.add_argument("--out", type=Path, default=Path("review.md"))
    ap.add_argument(
        "--only",
        help="comma-separated rule ids — review one batch without rereading the reviewed ones",
    )
    ap.add_argument("--migrate", type=Path, help="also write a v2 JSON to this path")
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit 1 on digest mismatch / unknown rule id",
    )
    args = ap.parse_args()

    ruleset = load_ruleset(args.ruleset)
    # utf-8-sig: tolerate the BOM that Windows editors add to UTF-8 files.
    data = json.loads(args.explanations.read_text(encoding="utf-8-sig"))

    only = {r.strip() for r in args.only.split(",")} if args.only else None
    sheet, problems = render(ruleset, data, args.ruleset, only)
    args.out.write_text(sheet, encoding="utf-8")
    print(
        f"wrote {args.out}  ({len(ruleset)} rule ids in ruleset, "
        f"{len(data['entries'])} explanations, {problems} problem(s))"
    )

    if args.migrate:
        v2 = {
            "schema_version": 2,
            "ruleset_version": data.get("ruleset_version"),
            "language": data.get("language", "de"),
            "_note": (
                "`what` must trace to the official rule text and is the only field the fidelity "
                "eval checks. `why` is editorial context, human-approved, not claimed as grounded. "
                "An entry is served only when reviewed_digest == rule_text_digest."
            ),
            "entries": {rid: as_v2(e) for rid, e in data["entries"].items()},
        }
        args.migrate.write_text(
            json.dumps(v2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.migrate}")

    if args.check and problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
