#!/usr/bin/env python
"""
derive_mutations.py — find, for every business rule, an edit that makes it fire.

Reads each assertion's context and test out of the compiled KoSIT stylesheets,
proposes single edits to the valid reference messages, runs every edited
document through the stylesheets, and records the best edit found for each
rule. The result is data for the rule set it was derived from, written beside
the hand-written mutation catalogue and replayed by the test suite.

    python scripts/derive_mutations.py --ruleset rulesets/2026-08-31 \
        --corpus tests/corpus/_downloaded/2026-08-31/instances \
        --out tests/mutation/derived/2026-08-31.json

This is a search, and it is slow: tens of thousands of validation runs. It is
run once per rule set release, never in CI. CI replays the file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from saxonche import PySaxonProcessor  # noqa: E402

from mutation.derive import (  # noqa: E402
    SVRL,
    TARGET_PREFIX,
    Derived,
    DocumentIndex,
    Edit,
    Plan,
    newly_fired,
    plans,
    read_stylesheet,
)
from xrv.core import Syntax  # noqa: E402
from xrv.ingest import identify, parse, to_text  # noqa: E402
from xrv.rules import Ruleset  # noqa: E402
from xrv.validate import StructureValidator, parse_svrl  # noqa: E402


class Runner:
    """The stylesheets, run for the search.

    Not the service's engine, because the search needs one thing the engine
    drops on purpose: which rule contexts were evaluated at all. That is the
    difference between a rule no edit could breach and a rule the reference
    messages never exercise, and it is in the raw report as `svrl:fired-rule`.
    Every mutation found here is replayed through the real engine by the tests,
    so a difference between the two would show up there as a rule not firing.
    """

    def __init__(self, ruleset: Ruleset) -> None:
        self._processor = PySaxonProcessor(license=False)
        compiler = self._processor.new_xslt30_processor()
        self._compiled = {
            syntax: tuple(
                compiler.compile_stylesheet(stylesheet_file=str(sheet))
                for sheet in ruleset.stylesheets(syntax)
            )
            for syntax in Syntax
        }
        self.structure = StructureValidator(ruleset)

    def run(self, document: bytes, syntax: Syntax) -> tuple[Counter[str], frozenset[str], bool]:
        """Rules fired, contexts evaluated, and whether anything fired is blocking."""
        node = self._processor.parse_xml(xml_text=to_text(parse(document)))
        fired: Counter[str] = Counter()
        contexts: set[str] = set()
        blocking = False
        for executable in self._compiled[syntax]:
            report = executable.transform_to_string(xdm_node=node)
            for finding in parse_svrl(report):
                fired[finding.rule_id] += 1
                blocking = blocking or finding.blocking
            for rule in etree.fromstring(report.encode()).iter(f"{{{SVRL}}}fired-rule"):
                contexts.add(rule.get("context") or "")
        return fired, frozenset(contexts), blocking


_runner: Runner | None = None
_plans: dict[Syntax, dict[str, Plan]] = {}
_corpus = Path()


def _start(ruleset_root: str, corpus: str) -> None:
    global _runner, _corpus
    # Saxon prints a stack of template frames to the process's stderr for every
    # transform that stops on an uncastable value, and thousands do. A failure
    # of the search itself is a Python exception and still reaches the parent.
    os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
    ruleset = Ruleset(root=Path(ruleset_root))
    _runner = Runner(ruleset)
    _corpus = Path(corpus)
    for syntax in Syntax:
        _plans[syntax] = plans(read_stylesheet(sheet) for sheet in ruleset.stylesheets(syntax))


def _sweep(base: str) -> dict[str, object]:
    """Try every edit the rules suggest for one reference message."""
    assert _runner is not None
    document = (_corpus / base).read_bytes()
    syntax = identify(document).syntax

    before, contexts, blocking = _runner.run(document, syntax)
    if blocking or _runner.structure.findings(document, syntax):
        return {"base": base, "skipped": True}

    index = DocumentIndex(document)
    edits: dict[Edit, None] = {}
    for plan in _plans[syntax].values():
        for edit in index.edits(plan):
            edits.setdefault(edit)

    hits = []
    failed = 0
    for edit in edits:
        edited = edit.apply(document)
        try:
            after, _, _ = _runner.run(edited, syntax)
        except Exception:
            # A value the stylesheet cannot cast — text where an amount goes —
            # stops the transform. That is not a rule firing.
            failed += 1
            continue
        fired = newly_fired(before, after)
        if fired:
            hits.append((edit, fired, bool(_runner.structure.findings(edited, syntax))))

    return {
        "base": base,
        "skipped": False,
        "syntax": syntax,
        "contexts": contexts,
        "trials": len(edits),
        "failed": failed,
        "hits": hits,
    }


def _natural(rule_id: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", rule_id))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ruleset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True, help="the test suite's instances/")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--only", help="substring of base names to sweep (for a quick look)")
    args = parser.parse_args()

    ruleset = Ruleset(root=args.ruleset.resolve())
    corpus = args.corpus.resolve()
    bases = sorted(str(p.relative_to(corpus)) for p in corpus.rglob("*.xml"))
    if args.only:
        bases = [base for base in bases if args.only in base]

    started = time.perf_counter()
    with ProcessPoolExecutor(
        max_workers=args.jobs, initializer=_start, initargs=(str(ruleset.root), str(corpus))
    ) as pool:
        swept = list(pool.map(_sweep, bases))
    elapsed = time.perf_counter() - started

    usable = [result for result in swept if not result["skipped"]]
    skipped = [result["base"] for result in swept if result["skipped"]]

    best: dict[tuple[Syntax, str], Derived] = {}
    contexts: dict[Syntax, set[str]] = {syntax: set() for syntax in Syntax}
    for result in usable:
        syntax = result["syntax"]
        contexts[syntax] |= result["contexts"]
        for edit, fired, caught in result["hits"]:
            for rule_id in fired:
                if not rule_id.startswith(TARGET_PREFIX):
                    continue
                found = Derived(
                    rule_id=rule_id,
                    syntax=syntax,
                    base=result["base"],
                    edit=edit,
                    collateral=fired - {rule_id},
                    caught_by_schema=caught,
                )
                held = best.get((syntax, rule_id))
                if held is None or found.rank() < held.rank():
                    best[(syntax, rule_id)] = found

    unreached = []
    for syntax in Syntax:
        sheets = [read_stylesheet(sheet) for sheet in ruleset.stylesheets(syntax)]
        for rule_id, plan in plans(sheets).items():
            if (syntax, rule_id) in best:
                continue
            exercised = bool(plan.contexts & contexts[syntax])
            unreached.append(
                {
                    "rule_id": rule_id,
                    "syntax": str(syntax),
                    "shape": str(plan.shape),
                    "reason": "no single edit breaches it"
                    if exercised
                    else "no reference message reaches its context",
                }
            )

    mutations = sorted(best.values(), key=lambda m: (str(m.syntax), _natural(m.rule_id)))
    unreached.sort(key=lambda u: (u["syntax"], _natural(u["rule_id"])))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "ruleset_version": ruleset.version,
                "ruleset_sha256": ruleset.sha256,
                "bases": len(usable),
                "mutations": [m.to_json() for m in mutations],
                "unreached": unreached,
            },
            indent=1,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    trials = sum(result["trials"] for result in usable)
    print(f"{len(usable)} reference messages, {trials} edits tried in {elapsed:.0f} s")
    if skipped:
        print(f"skipped, not clean to begin with: {', '.join(skipped)}")
    for syntax in Syntax:
        mine = [m for m in mutations if m.syntax is syntax]
        left = [u for u in unreached if u["syntax"] == str(syntax)]
        alone = sum(1 for m in mine if not m.collateral)
        through = sum(1 for m in mine if not m.caught_by_schema)
        print(
            f"{syntax}: {len(mine)} of {len(mine) + len(left)} business rules fire — "
            f"{alone} alone, {through} past the schema"
        )
    rules = {m.rule_id for m in mutations}
    every = rules | {u["rule_id"] for u in unreached}
    print(f"either syntax: {len(rules)} of {len(every)}")
    for reason, count in Counter((u["reason"], u["shape"]) for u in unreached).most_common():
        print(f"  unreached: {count:3}  {reason[0]} ({reason[1]})")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
