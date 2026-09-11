"""Fetch the KoSIT semantic model — the source of truth for German field names.

The ruleset says *which* rules exist and quotes them, but never what BT-119 is
called in German. Without that, every German field name in an explanation is a
translation somebody made up, and a reviewer with no copy of the specification
has nothing to check it against.

KoSIT publishes one, machine-readable, in the visualization repository: an XSD
whose annotations carry a BT/BG number and an official German description for
each of ~200 fields. This vendors it the way `fetch_ruleset.py` vendors the
rules — downloaded, hashed, version-addressed, never hand-edited.

    uv run python scripts/fetch_semantic_model.py --version 2026-08-31
    uv run python scripts/fetch_semantic_model.py --verify semantic-model
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

XS = "{http://www.w3.org/2001/XMLSchema}"
SOURCE = (
    "https://raw.githubusercontent.com/itplr-kosit/xrechnung-visualization/"
    "{ref}/src/xsd/xrechnung-semantic-model.xsd"
)
FILENAME = "xrechnung-semantic-model.xsd"


@dataclass(frozen=True)
class Field:
    """One BT or BG, as the specification names it."""

    id: str
    name: str
    german: str


def parse(path: Path) -> dict[str, Field]:
    """Every BT/BG in the model, by id.

    The number lives in `xs:appinfo` and the German text in `xs:documentation`,
    both inside the annotation of the element they describe.
    """
    fields: dict[str, Field] = {}
    for annotation in etree.parse(str(path)).iter(f"{XS}annotation"):
        appinfo = annotation.find(f"{XS}appinfo")
        if appinfo is None or not (appinfo.text or "").strip():
            continue
        documentation = annotation.find(f"{XS}documentation")
        parent = annotation.getparent()
        fields[appinfo.text.strip()] = Field(
            id=appinfo.text.strip(),
            name=parent.get("name", "") if parent is not None else "",
            german=" ".join((documentation.text or "").split())
            if documentation is not None
            else "",
        )
    return fields


def fetch(version: str, ref: str, out: Path) -> Path:
    target = out / version
    target.mkdir(parents=True, exist_ok=True)
    url = SOURCE.format(ref=ref)
    with urllib.request.urlopen(url, timeout=60) as response:
        payload = response.read()

    path = target / FILENAME
    path.write_bytes(payload)
    fields = parse(path)
    manifest = {
        "version": version,
        "source": url,
        "ref": ref,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "fields": len(fields),
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{path}  {len(payload):,} bytes, {len(fields)} BT/BG entries")
    print(f"  sha256 {manifest['sha256']}")
    return path


def verify(root: Path) -> int:
    """Every vendored copy still hashes to what its manifest recorded."""
    problems = 0
    for manifest_path in sorted(root.glob("*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = (manifest_path.parent / FILENAME).read_bytes()
        actual = hashlib.sha256(payload).hexdigest()
        if actual == manifest["sha256"]:
            print(f"  {manifest['version']}  OK  {manifest['fields']} fields")
        else:
            problems += 1
            print(f"  {manifest['version']}  MISMATCH  recorded {manifest['sha256']}, got {actual}")
    if not problems:
        print("all vendored semantic models verified")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="label to store it under, matching the ruleset version")
    ap.add_argument("--ref", default="master", help="git ref in the KoSIT repository")
    ap.add_argument("--out", type=Path, default=Path("semantic-model"))
    ap.add_argument("--verify", type=Path, help="check vendored copies against their manifests")
    args = ap.parse_args()

    if args.verify:
        raise SystemExit(1 if verify(args.verify) else 0)
    if not args.version:
        ap.error("--version is required unless --verify is given")
    fetch(args.version, args.ref, args.out)


if __name__ == "__main__":
    main()
