#!/usr/bin/env bash
# Week-1 spike runner.
#
# Answers, in one command, the three questions that decide the platform:
#   1. does saxonche run a real KoSIT stylesheet — on amd64 AND arm64?
#   2. does xsdata produce usable typed CII bindings?
#   3. what do compile time and peak memory say about the hosting tier?
#
#   bash scripts/run_spike.sh              # host + amd64 container
#   bash scripts/run_spike.sh --arm64      # also build and run arm64 (slow, emulated)
#
# Results land in spike-results.json.

set -uo pipefail
cd "$(dirname "$0")/.."

ARM64=0
[[ "${1:-}" == "--arm64" ]] && ARM64=1

RESULTS="spike-results.json"
echo '{"runs":[]}' > "$RESULTS"

record() {  # record <label> <exit_code> <json_file>
  python3 - "$1" "$2" "$3" "$RESULTS" <<'PY'
import json, sys, pathlib
label, code, src, dest = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
try:
    payload = json.loads(pathlib.Path(src).read_text())
except Exception as e:
    payload = {"ok": False, "error": f"unparseable output: {e}"}
doc = json.loads(pathlib.Path(dest).read_text())
doc["runs"].append({"label": label, "exit_code": code, "result": payload})
pathlib.Path(dest).write_text(json.dumps(doc, indent=2) + "\n")
PY
}

hr() { printf '\n%s\n' "======================================================================"; }

# The image is production-shaped: ruleset baked in, test corpus not. Mount the
# corpus read-only so the spike has invoices to run against.
CORPUS_MOUNT="-v $PWD/tests/corpus/_downloaded:/app/tests/corpus/_downloaded:ro"

# Prefer the project venv. Without this the host spikes silently skip — the deps
# live in .venv, `python3` is the system interpreter, and the only sign is a
# "not installed" line most people scroll past.
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi
echo "interpreter: $PY"

# ---- prerequisites ----------------------------------------------------------
if [[ ! -d rulesets ]] || [[ -z "$(ls -A rulesets 2>/dev/null)" ]]; then
  echo "fetching ruleset + testsuite first..."
  "$PY" scripts/fetch_ruleset.py --testsuite || exit 1
fi

FAILED=0

# ---- 1. host ----------------------------------------------------------------
hr; echo "HOST  $(uname -m)"; hr
if "$PY" -c "import saxonche" 2>/dev/null; then
  "$PY" scripts/spike_saxon.py
  "$PY" scripts/spike_saxon.py --json > /tmp/s.json 2>/dev/null
  record "host-saxon" $? /tmp/s.json
else
  echo "  saxonche not installed on host — container run below is the real check."
  echo "  to test on host:  uv venv && uv pip install saxonche lxml 'xsdata[cli]'"
fi

if "$PY" -c "import xsdata" 2>/dev/null; then
  echo
  "$PY" scripts/spike_xsdata.py
  "$PY" scripts/spike_xsdata.py --json > /tmp/x.json 2>/dev/null
  record "host-xsdata" $? /tmp/x.json
else
  echo "  xsdata not installed on host — skipping spike 2."
fi

# ---- 2. amd64 container -----------------------------------------------------
hr; echo "CONTAINER  linux/amd64"; hr
if docker build --platform linux/amd64 -t xrv:spike-amd64 . ; then
  SIZE=$(docker image inspect xrv:spike-amd64 --format '{{.Size}}')
  echo "  image size  $(( SIZE / 1000000 )) MB"
  docker run --rm --platform linux/amd64 $CORPUS_MOUNT xrv:spike-amd64 \
      python scripts/spike_saxon.py
  docker run --rm --platform linux/amd64 $CORPUS_MOUNT xrv:spike-amd64 \
      python scripts/spike_saxon.py --json > /tmp/ca.json 2>/dev/null
  record "container-amd64-saxon" $? /tmp/ca.json
  [[ $? -ne 0 ]] && FAILED=1
else
  echo "  BUILD FAILED"; FAILED=1
fi

# ---- 3. arm64 container -----------------------------------------------------
# This is the one that decides Lambda arm64 (Graviton) vs x86_64. Emulated
# under qemu, so slow — the timings are meaningless, the PASS/FAIL is not.
if [[ $ARM64 -eq 1 ]]; then
  hr; echo "CONTAINER  linux/arm64  (emulated — timings not meaningful)"; hr
  if docker build --platform linux/arm64 -t xrv:spike-arm64 . ; then
    docker run --rm --platform linux/arm64 $CORPUS_MOUNT xrv:spike-arm64 \
        python scripts/spike_saxon.py
    docker run --rm --platform linux/arm64 $CORPUS_MOUNT xrv:spike-arm64 \
        python scripts/spike_saxon.py --json > /tmp/cr.json 2>/dev/null
    record "container-arm64-saxon" $? /tmp/cr.json
  else
    echo "  arm64 BUILD FAILED — use x86_64 on Lambda (one Terraform line, ~20% more GB-s)"
  fi
fi

hr; echo "SUMMARY"; hr
python3 - "$RESULTS" <<'PY'
import json, sys, pathlib
doc = json.loads(pathlib.Path(sys.argv[1]).read_text())
if not doc["runs"]:
    print("  no runs recorded"); raise SystemExit(1)
worst = 0
for run in doc["runs"]:
    r = run["result"]
    ok = r.get("ok")
    print(f"  {'PASS' if ok else 'FAIL'}  {run['label']}")
    if r.get("rss_peak_mb"):
        print(f"          peak rss {r['rss_peak_mb']:.0f} MB, "
              f"compile {r.get('compile_total_ms', 0)/1000:.1f}s")
    if not ok:
        worst = 1
        if r.get("error"):
            print(f"          {str(r['error'])[:300]}")
print(f"\n  full results: spike-results.json")
raise SystemExit(worst)
PY
