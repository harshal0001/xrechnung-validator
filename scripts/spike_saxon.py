#!/usr/bin/env python3
"""SPIKE 1 — does saxonche execute a real KoSIT stylesheet in this container?

This is the spike that can invalidate the platform choice, so it runs first.
saxonche ships a native library; if it does not load on this architecture,
nothing downstream matters.

It answers four questions, and prints them as a verdict:

  1. Does the native library load at all on this arch?
  2. Does a real KoSIT XSLT 2.0 stylesheet compile?
  3. Does executing it against a real reference invoice produce SVRL?
  4. How long does the compile take, and how much memory does it hold?

(2) is the one lxml cannot do. (4) decides the eager-vs-lazy init question and
the container memory floor.

    python scripts/spike_saxon.py
    python scripts/spike_saxon.py --json      # machine-readable, for the runner
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import resource
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULESET_DIR = ROOT / "rulesets"
CORPUS_DIR = ROOT / "tests" / "corpus" / "_downloaded"

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"


def rss_mb() -> float:
    """Peak resident set size in MB. ru_maxrss is KB on Linux, bytes on macOS."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1024 if sys.platform != "darwin" else peak / (1024 * 1024)


def latest_dir(base: Path) -> Path:
    candidates = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
    if not candidates:
        raise SystemExit(
            f"nothing in {base.relative_to(ROOT)} — run:\n"
            f"  python scripts/fetch_ruleset.py --testsuite"
        )
    return candidates[-1]


def pick_invoice(corpus: Path, syntax: str) -> Path:
    """Pick one reference invoice of the given syntax. These are valid by design."""
    suffix = "_ubl.xml" if syntax == "UBL" else "_uncefact.xml"
    matches = sorted((corpus / "instances" / "standard").glob(f"*{suffix}"))
    if not matches:
        raise SystemExit(f"no {syntax} instance found under {corpus}")
    return matches[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    out: dict = {
        "spike": "saxon",
        "python": platform.python_version(),
        "machine": platform.machine(),
        "platform": platform.platform(),
    }
    log = (lambda *a: None) if args.json else print

    log(f"SPIKE 1 — saxonche on {platform.machine()} / Python {platform.python_version()}")
    log("=" * 68)

    # ---- Q1: does the native library load? --------------------------------
    t0 = time.perf_counter()
    try:
        from saxonche import PySaxonProcessor
    except Exception as e:
        out["ok"] = False
        out["failed_at"] = "import"
        out["error"] = f"{type(e).__name__}: {e}"
        print(json.dumps(out, indent=2) if args.json else f"\nFAIL import: {e}")
        return 1

    import_ms = (time.perf_counter() - t0) * 1000
    out["import_ms"] = round(import_ms, 1)
    out["rss_after_import_mb"] = round(rss_mb(), 1)
    log(
        f"  import saxonche            {import_ms:8.1f} ms   "
        f"rss {out['rss_after_import_mb']:6.1f} MB"
    )

    ruleset = latest_dir(RULESET_DIR)
    corpus = latest_dir(CORPUS_DIR)
    manifest = json.loads((ruleset / "manifest.json").read_text())
    out["ruleset_version"] = manifest["version"]
    out["ruleset_sha256"] = manifest["sha256"]
    log(f"  ruleset                    {manifest['version']}  sha256 {manifest['sha256'][:16]}…")

    results = []
    with PySaxonProcessor(license=False) as proc:
        out["saxon_version"] = proc.version
        log(f"  saxon version              {proc.version}")
        log("")

        xslt = proc.new_xslt30_processor()

        for label, xslt_key, syntax in [
            ("EN16931 CII", "xslt_en16931_cii", "CII"),
            ("EN16931 UBL", "xslt_en16931_ubl", "UBL"),
            ("XRechnung CII", "xslt_xrechnung_cii", "CII"),
            ("XRechnung UBL", "xslt_xrechnung_ubl", "UBL"),
        ]:
            entry: dict = {"stylesheet": label, "syntax": syntax}
            sheet = ruleset / manifest["paths"][xslt_key]
            invoice = pick_invoice(corpus, syntax)
            entry["invoice"] = invoice.name

            # ---- Q2: does a real KoSIT XSLT 2.0 stylesheet compile? -------
            t = time.perf_counter()
            try:
                executable = xslt.compile_stylesheet(stylesheet_file=str(sheet))
            except Exception as e:
                entry["ok"] = False
                entry["failed_at"] = "compile"
                entry["error"] = f"{type(e).__name__}: {e}"
                results.append(entry)
                log(f"  {label:<14} COMPILE FAILED: {e}")
                continue
            entry["compile_ms"] = round((time.perf_counter() - t) * 1000, 1)

            # ---- Q3: does execution produce SVRL? -------------------------
            t = time.perf_counter()
            try:
                svrl = executable.transform_to_string(source_file=str(invoice))
            except Exception as e:
                entry["ok"] = False
                entry["failed_at"] = "transform"
                entry["error"] = f"{type(e).__name__}: {e}"
                results.append(entry)
                log(f"  {label:<14} TRANSFORM FAILED: {e}")
                continue
            entry["transform_ms"] = round((time.perf_counter() - t) * 1000, 1)

            # Cheap structural checks. Full SVRL parsing is week 2 work; here we
            # only need evidence that Saxon really ran the Schematron.
            svrl = svrl or ""
            entry["svrl_bytes"] = len(svrl)
            entry["is_svrl"] = SVRL_NS in svrl
            entry["fired_rules"] = svrl.count("<svrl:fired-rule")
            entry["failed_asserts"] = svrl.count("<svrl:failed-assert")

            # A failed-assert is NOT automatically an error. KoSIT carries severity
            # in the flag attribute — "fatal", "warning", "information". Reference
            # messages are valid yet still emit informational asserts, so treating
            # any failed-assert as a failure invents false positives on a corpus
            # whose whole purpose is proving you have none. This mapping is what
            # Severity in the core model has to encode.
            flags = re.findall(r'<svrl:failed-assert[^>]*?flag="([^"]+)"', svrl)
            entry["failed_by_flag"] = dict(Counter(flags))
            entry["blocking"] = sum(1 for f in flags if f not in {"information", "warning"})

            # Reference messages are valid by construction: zero blocking findings.
            entry["ok"] = entry["is_svrl"] and entry["fired_rules"] > 0 and entry["blocking"] == 0
            results.append(entry)

            flag_note = (
                " ".join(f"{k}:{v}" for k, v in sorted(entry["failed_by_flag"].items())) or "none"
            )
            log(
                f"  {label:<14} compile {entry['compile_ms']:7.1f} ms  "
                f"transform {entry['transform_ms']:6.1f} ms  "
                f"fired {entry['fired_rules']:4d}  "
                f"asserts[{flag_note}]  blocking {entry['blocking']}  "
                f"{'ok' if entry['ok'] else 'SUSPECT'}"
            )

    out["stylesheets"] = results
    out["rss_peak_mb"] = round(rss_mb(), 1)
    compile_total = sum(r.get("compile_ms", 0) for r in results)
    out["compile_total_ms"] = round(compile_total, 1)
    out["ok"] = all(r.get("ok") for r in results) and len(results) == 4

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1

    log("")
    log("-" * 68)
    log(f"  peak RSS                   {out['rss_peak_mb']:8.1f} MB")
    log(f"  total compile time         {compile_total:8.1f} ms")
    log("")

    # ---- Q4: the decisions this spike exists to make ----------------------
    # Plan section 6.5 says lazy-load everything. On Lambda the INIT phase is
    # unbilled full-vCPU up to ~10s, so an eager compile that fits is free.
    if compile_total < 8000:
        log(f"  DECISION eager init is safe ({compile_total / 1000:.1f}s < 8s budget).")
        log("           Compile at module scope: free on Lambda INIT, warm on Cloud Run.")
    else:
        log(f"  DECISION compile is slow ({compile_total / 1000:.1f}s >= 8s).")
        log("           Lazy-load per plan section 6.5; keep /healthz off the Saxon path.")

    floor = out["rss_peak_mb"]
    if floor < 400:
        log(
            f"  DECISION {floor:.0f} MB peak — 512 MB tiers are viable. "
            f"Render stays a real backup."
        )
    elif floor < 900:
        log(f"  DECISION {floor:.0f} MB peak — needs 1 GiB. 512 MB tiers are out.")
    else:
        log(f"  DECISION {floor:.0f} MB peak — size above 1 GiB and re-measure under load.")

    log("")
    log(f"  VERDICT  {'PASS' if out['ok'] else 'FAIL'}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
