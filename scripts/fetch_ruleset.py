#!/usr/bin/env python3
"""Fetch, hash and record a KoSIT validator configuration.

The ruleset is *data*, never a vendored constant. Every fetch writes a manifest
recording which release produced it and what its bytes hashed to, so
`ruleset_version` / `ruleset_sha256` on a ValidationReport trace back to
something real.

    python scripts/fetch_ruleset.py                 # latest release
    python scripts/fetch_ruleset.py --version 2026-08-31
    python scripts/fetch_ruleset.py --testsuite     # also fetch sample invoices

The archive is self-contained: it carries the CII 16B and UBL 2.1 XSDs *and* the
Schematron-compiled XSLT. There is no second source to fetch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULESET_DIR = ROOT / "rulesets"
CORPUS_DIR = ROOT / "tests" / "corpus" / "_downloaded"

CONFIG_REPO = "itplr-kosit/validator-configuration-xrechnung"
TESTSUITE_REPO = "itplr-kosit/xrechnung-testsuite"

# Paths inside the extracted archive. Asserted after extraction rather than
# assumed — if KoSIT reorganises the layout, this fails loudly at fetch time
# instead of mysteriously at validation time.
EXPECTED_LAYOUT = {
    "xslt_en16931_cii": "resources/cii/16b/xsl/EN16931-CII-validation.xsl",
    "xslt_en16931_ubl": "resources/ubl/2.1/xsl/EN16931-UBL-validation.xsl",
    "xslt_xrechnung_cii": "resources/xrechnung/3.0.2/xsl/XRechnung-CII-validation.xsl",
    "xslt_xrechnung_ubl": "resources/xrechnung/3.0.2/xsl/XRechnung-UBL-validation.xsl",
    "xsd_cii": "resources/cii/16b/xsd/CrossIndustryInvoice_100pD16B.xsd",
    "xsd_ubl": "resources/ubl/2.1/xsd/maindoc/UBL-Invoice-2.1.xsd",
    # A credit note is a different UBL root element with its own schema. The
    # Schematron handles both; validating one against the Invoice schema would
    # reject a perfectly valid document.
    "xsd_ubl_creditnote": "resources/ubl/2.1/xsd/maindoc/UBL-CreditNote-2.1.xsd",
    "scenarios": "scenarios.xml",
}


def _get_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json"}

    # Unauthenticated GitHub API calls are limited to 60/hour per IP, and CI
    # runners share an IP with everyone else on the same host. A token lifts that
    # to 5000/hour. Optional: a developer running this locally needs no token.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def resolve_release(repo: str, version: str | None) -> tuple[str, str, str]:
    """Return (version, asset_name, download_url) for a release."""
    if version:
        rel = _get_json(f"https://api.github.com/repos/{repo}/releases/tags/v{version}")
    else:
        rel = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")

    assets = [a for a in rel.get("assets", []) if a["name"].endswith(".zip")]
    if not assets:
        raise SystemExit(f"no .zip asset on release {rel.get('tag_name')} of {repo}")

    tag = rel["tag_name"].removeprefix("v")
    asset = assets[0]
    return tag, asset["name"], asset["browser_download_url"]


def download(url: str, dest: Path) -> str:
    """Download to dest, returning the sha256 of the bytes actually written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=180) as r, dest.open("wb") as f:
        while chunk := r.read(1 << 16):
            digest.update(chunk)
            f.write(chunk)
    return digest.hexdigest()


def safe_extract(zf: zipfile.ZipFile, target: Path) -> None:
    """Extract, refusing any member that would escape the target directory."""
    target = target.resolve()
    for member in zf.infolist():
        resolved = (target / member.filename).resolve()
        if not resolved.is_relative_to(target):
            raise SystemExit(f"refusing unsafe archive member: {member.filename}")
    zf.extractall(target)


def missing_paths(root: Path) -> list[str]:
    """Logical keys from the manifest whose files are not on disk.

    Read from the manifest rather than EXPECTED_LAYOUT so an already-extracted
    ruleset is judged against the layout it was actually fetched with.
    """
    manifest_file = root / "manifest.json"
    if not manifest_file.is_file():
        return sorted(EXPECTED_LAYOUT)
    try:
        paths = json.loads(manifest_file.read_text())["paths"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return sorted(EXPECTED_LAYOUT)
    return sorted(k for k, rel in paths.items() if not (root / rel).is_file())


def fetch_ruleset(version: str | None, force: bool) -> Path:
    tag, asset_name, url = resolve_release(CONFIG_REPO, version)
    out = RULESET_DIR / tag

    # "Already present" means the files are present, not that the directory is.
    # manifest.json is committed to the repository, so after a fresh clone the
    # directory exists and holds nothing else — testing existence alone would
    # skip the download and leave the ruleset empty.
    if out.exists() and not force:
        incomplete = missing_paths(out)
        if not incomplete:
            here = out.relative_to(ROOT)
            print(f"  ruleset {tag} already present at {here} (--force to refetch)")
            return out
        print(f"  {out.relative_to(ROOT)} is incomplete ({len(incomplete)} missing) — refetching")
    if out.exists():
        shutil.rmtree(out)

    print(f"  release   {tag}")
    print(f"  asset     {asset_name}")

    tmp_zip = RULESET_DIR / f".{tag}.zip"
    sha256 = download(url, tmp_zip)
    print(f"  sha256    {sha256}")
    print(f"  size      {tmp_zip.stat().st_size:,} bytes")

    with zipfile.ZipFile(tmp_zip) as zf:
        safe_extract(zf, out)
    tmp_zip.unlink()

    missing = [k for k, rel in EXPECTED_LAYOUT.items() if not (out / rel).is_file()]
    if missing:
        raise SystemExit(
            f"archive layout changed — missing: {', '.join(missing)}\n"
            f"inspect {out} and update EXPECTED_LAYOUT in this script."
        )

    manifest = {
        "version": tag,
        "sha256": sha256,
        "source_url": url,
        "asset_name": asset_name,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "paths": EXPECTED_LAYOUT,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  layout    verified, {len(EXPECTED_LAYOUT)} expected paths present")
    print(f"  manifest  {(out / 'manifest.json').relative_to(ROOT)}")
    return out


def fetch_testsuite(force: bool) -> Path:
    tag, _asset_name, url = resolve_release(TESTSUITE_REPO, None)
    out = CORPUS_DIR / tag

    if out.exists() and not force:
        print(f"  testsuite {tag} already present (--force to refetch)")
        return out
    if out.exists():
        shutil.rmtree(out)

    print(f"  release   {tag}")
    tmp_zip = CORPUS_DIR / f".{tag}.zip"
    sha256 = download(url, tmp_zip)
    print(f"  sha256    {sha256}")

    with zipfile.ZipFile(tmp_zip) as zf:
        safe_extract(zf, out)
    tmp_zip.unlink()

    xml_count = sum(1 for _ in out.rglob("*.xml"))
    print(f"  extracted {xml_count} XML files to {out.relative_to(ROOT)}")
    return out


def verify(base: Path) -> int:
    """Check every ruleset under `base` is complete. No network access.

    The image bakes the ruleset in, and COPY is happy to copy a directory that
    holds nothing but a manifest. Running this at build time turns that into a
    failed build instead of an image that only fails when someone uploads an
    invoice.
    """
    roots = sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []
    if not roots:
        print(f"no ruleset under {base}", file=sys.stderr)
        return 1

    failed = False
    for root in roots:
        incomplete = missing_paths(root)
        if incomplete:
            print(f"  {root.name}  INCOMPLETE — missing {', '.join(incomplete)}", file=sys.stderr)
            failed = True
        else:
            print(f"  {root.name}  ok")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", help="ruleset version, e.g. 2026-08-31 (default: latest release)")
    ap.add_argument("--testsuite", action="store_true", help="also fetch KoSIT reference messages")
    ap.add_argument("--force", action="store_true", help="refetch even if already present")
    ap.add_argument(
        "--verify",
        metavar="DIR",
        nargs="?",
        const=str(RULESET_DIR),
        help="check rulesets on disk are complete and exit; no download",
    )
    args = ap.parse_args()

    if args.verify:
        print("verifying rulesets")
        return verify(Path(args.verify))

    print("KoSIT validator configuration")
    ruleset = fetch_ruleset(args.version, args.force)

    if args.testsuite:
        print("\nKoSIT test suite")
        fetch_testsuite(args.force)

    print(f"\nready: {ruleset.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
