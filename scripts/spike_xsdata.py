#!/usr/bin/env python3
"""SPIKE 2 — does xsdata generate usable CII bindings, and what do they cost?

CII is the larger and messier schema set, so it was assumed to be the risk. This
answers that up front, before anything is built on top of the bindings.

Three questions:

  1. Does xsdata generate bindings from the official CII 16B and UBL 2.1 XSDs?
  2. Do the generated modules import, and can they parse a real invoice into
     typed objects (not an XML tree)?
  3. What does importing them cost in time and memory? This feeds directly into
     the cold-start budget, because a large generated module imported at module
     scope is the classic cold-start trap.

Generation runs in a subprocess against the XSDs that came with the ruleset —
there is no separate schema download.

    python scripts/spike_xsdata.py
    python scripts/spike_xsdata.py --json
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULESET_DIR = ROOT / "rulesets"
CORPUS_DIR = ROOT / "tests" / "corpus" / "_downloaded"
OUT_DIR = ROOT / ".spike-out"


def rss_mb() -> float:
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


def codegen_env() -> dict:
    """Environment for the xsdata subprocess.

    xsdata 26.x shells out to `ruff` to format what it generates. In a venv,
    `ruff` sits next to the interpreter but is not necessarily on PATH — so
    invoking via `.venv/bin/python` without activating fails with a bare
    FileNotFoundError halfway through a two-minute run. Put the interpreter's
    own bin directory on PATH so generation works activated or not.
    """
    import os

    env = os.environ.copy()
    bindir = str(Path(sys.executable).parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    return env


def last_exception_line(output: str) -> str:
    """Pull the actual exception off the end of a traceback.

    Tail-slicing a traceback usually lands mid-frame and hides the one line that
    says what broke. Walk backwards to the last `Something: message` instead.
    """
    for line in reversed(output.strip().splitlines()):
        stripped = line.strip()
        if stripped and not stripped.startswith(("File ", "    ", "Traceback")):
            return stripped
    return output.strip()[-300:]


def generate(xsd: Path, package: str, workdir: Path) -> dict:
    """Run xsdata against one schema into its own directory.

    With --structure-style single-package, xsdata writes `<package>.py` and
    `__init__.py` straight into the working directory rather than into a
    subpackage. Two schemas generated into one directory therefore fight over
    `__init__.py`, so each target gets its own workdir and its own sys.path
    entry.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    entry: dict = {"package": package, "xsd": xsd.name}
    t = time.perf_counter()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "xsdata",
            "generate",
            str(xsd),
            "--package",
            package,
            # MEASURED, not guessed. "clusters" looks right — one module per root,
            # import only what you need — but UBL 2.1 explodes into 2705 modules and
            # takes 26s to import even with bytecode precompiled, because the cost is
            # the filesystem walk, not compilation. "single-package" emits 2 files
            # and imports in 1.5s.
            "--structure-style",
            "single-package",
            "--docstring-style",
            "Google",
            "--slots",  # lower per-object memory
        ],
        cwd=workdir,
        capture_output=True,
        text=True,
        timeout=900,
        env=codegen_env(),
    )
    entry["generate_s"] = round(time.perf_counter() - t, 1)
    entry["returncode"] = proc.returncode

    if proc.returncode != 0:
        raw = proc.stderr or proc.stdout
        entry["ok"] = False
        entry["failed_at"] = "generate"
        entry["error"] = last_exception_line(raw)
        entry["error_full"] = raw[-4000:]
        return entry

    modules = sorted(workdir.rglob("*.py"))
    entry["module_count"] = len(modules)
    entry["total_lines"] = sum(len(m.read_text(errors="replace").splitlines()) for m in modules)
    entry["total_kb"] = round(sum(m.stat().st_size for m in modules) / 1024, 1)
    entry["ok"] = len(modules) > 0
    if not entry["ok"]:
        entry["failed_at"] = "no_modules_emitted"
        entry["error"] = f"xsdata exited 0 but wrote no .py files into {workdir}"
    return entry


def probe_import_and_parse(workdir: Path, package: str, invoice: Path, root_hint: str) -> dict:
    """Import the generated package in a *fresh* interpreter and parse an invoice.

    Fresh subprocess matters: import cost measured after the parent has already
    imported xsdata would understate the cold-start hit we are trying to size.
    """
    code = f"""
import json, resource, sys, time
sys.path.insert(0, {str(workdir)!r})

def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

base = rss()
t = time.perf_counter()
from xsdata.formats.dataclass.parsers import XmlParser
from xsdata.formats.dataclass.context import XmlContext
parser_ms = (time.perf_counter() - t) * 1000

t = time.perf_counter()
import importlib
mod = importlib.import_module({package!r})
names = [n for n in dir(mod) if {root_hint!r}.lower() in n.lower()]
import_ms = (time.perf_counter() - t) * 1000
after_import = rss()

result = {{
    "parser_import_ms": round(parser_ms, 1),
    "bindings_import_ms": round(import_ms, 1),
    "rss_after_import_mb": round(after_import, 1),
    "rss_delta_mb": round(after_import - base, 1),
    "root_candidates": names[:5],
}}

if not names:
    result["ok"] = False
    result["failed_at"] = "no_root_class"
else:
    root = getattr(mod, names[0])
    t = time.perf_counter()
    try:
        parser = XmlParser(context=XmlContext())
        obj = parser.parse({str(invoice)!r}, root)
        result["parse_ms"] = round((time.perf_counter() - t) * 1000, 1)
        result["parsed_type"] = type(obj).__name__
        # Evidence it is a typed object, not a tree: attributes exist as fields.
        result["field_count"] = len(getattr(obj, "__dataclass_fields__", {{}}))
        result["rss_peak_mb"] = round(rss(), 1)
        result["ok"] = result["field_count"] > 0
    except Exception as e:
        result["ok"] = False
        result["failed_at"] = "parse"
        result["error"] = f"{{type(e).__name__}}: {{e}}"

print("__RESULT__" + json.dumps(result))
"""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line.removeprefix("__RESULT__"))
    return {
        "ok": False,
        "failed_at": "probe",
        "error": (proc.stderr or proc.stdout)[-2000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--keep", action="store_true", help="keep generated code in .spike-out/")
    args = ap.parse_args()

    out: dict = {
        "spike": "xsdata",
        "python": platform.python_version(),
        "machine": platform.machine(),
    }
    log = (lambda *a: None) if args.json else print

    log(f"SPIKE 2 — xsdata bindings on {platform.machine()} / Python {platform.python_version()}")
    log("=" * 68)

    try:
        import xsdata  # noqa: F401
        from xsdata import __version__ as xsdata_version

        out["xsdata_version"] = xsdata_version
    except Exception as e:
        out["ok"] = False
        out["failed_at"] = "import_xsdata"
        out["error"] = f"{type(e).__name__}: {e}"
        out["hint"] = "install the codegen extra:  uv sync --extra codegen"
        print(json.dumps(out, indent=2) if args.json else f"\nFAIL: {e}\n{out['hint']}")
        return 1

    log(f"  xsdata version             {out['xsdata_version']}")

    ruleset = latest_dir(RULESET_DIR)
    corpus = latest_dir(CORPUS_DIR)
    manifest = json.loads((ruleset / "manifest.json").read_text())
    out["ruleset_version"] = manifest["version"]

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    # CII first: it was assumed to be the riskier of the two.
    targets = [
        ("CII", "xsd_cii", "cii", "CrossIndustryInvoice", "_uncefact.xml"),
        ("UBL", "xsd_ubl", "ubl", "Invoice", "_ubl.xml"),
    ]

    results = []
    for label, xsd_key, package, root_hint, invoice_suffix in targets:
        log("")
        log(f"  {label}")
        xsd = ruleset / manifest["paths"][xsd_key]
        workdir = OUT_DIR / package
        entry = generate(xsd, package, workdir)
        entry["syntax"] = label

        if not entry.get("ok"):
            log(f"    GENERATE FAILED after {entry['generate_s']}s")
            log(f"      {entry.get('error', '')}")
            results.append(entry)
            continue

        log(
            f"    generate                 {entry['generate_s']:7.1f} s   "
            f"{entry['module_count']} modules, {entry['total_lines']:,} lines, "
            f"{entry['total_kb']:,.0f} KB"
        )

        matches = sorted((corpus / "instances" / "standard").glob(f"*{invoice_suffix}"))
        if not matches:
            entry["ok"] = False
            entry["failed_at"] = "no_invoice"
            results.append(entry)
            continue

        probe = probe_import_and_parse(workdir, package, matches[0], root_hint)
        entry["invoice"] = matches[0].name
        entry.update(probe)

        if probe.get("ok"):
            log(
                f"    import bindings          {probe['bindings_import_ms']:7.1f} ms  "
                f"(+{probe['rss_delta_mb']:.1f} MB)"
            )
            log(
                f"    parse -> {probe['parsed_type']:<16} {probe['parse_ms']:7.1f} ms  "
                f"{probe['field_count']} typed fields"
            )
        else:
            log(f"    PROBE FAILED at {probe.get('failed_at')}: {str(probe.get('error'))[:400]}")

        results.append(entry)

    out["targets"] = results
    out["ok"] = all(r.get("ok") for r in results) and len(results) == 2
    out["import_total_ms"] = round(sum(r.get("bindings_import_ms", 0) for r in results), 1)
    out["rss_total_delta_mb"] = round(sum(r.get("rss_delta_mb", 0) for r in results), 1)

    if not args.keep:
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    else:
        out["generated_at"] = str(OUT_DIR)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1

    log("")
    log("-" * 68)
    log(f"  bindings import (both)     {out['import_total_ms']:8.1f} ms")
    log(f"  bindings memory (both)     {out['rss_total_delta_mb']:8.1f} MB")
    log("")

    # Budget: Lambda INIT is unbilled full-vCPU up to ~10s and Saxon already spends
    # ~2s of it, so bindings need to land well under the remainder.
    if out["import_total_ms"] > 5000:
        log(f"  DECISION import is heavy ({out['import_total_ms'] / 1000:.1f}s).")
        log("           Import bindings inside the request path, not at module scope,")
        log("           and check --structure-style: clusters costs ~26s on UBL.")
    else:
        log(
            f"  DECISION import is affordable ({out['import_total_ms'] / 1000:.1f}s) — "
            f"eager module scope fits the INIT budget alongside Saxon."
        )

    if args.keep:
        log(f"  generated code kept in {OUT_DIR.relative_to(ROOT)}")

    log("")
    log(f"  VERDICT  {'PASS' if out['ok'] else 'FAIL'}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
