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

from mutation.derive import (  # noqa: E402
    SVRL,
    TARGET_PREFIX,
    Derived,
    DocumentIndex,
    Edit,
    Plan,
    apply_edits,
    newly_fired,
    plans,
    read_stylesheet,
)
from xrv.core import Syntax  # noqa: E402
from xrv.ingest import identify  # noqa: E402
from xrv.rules import Ruleset  # noqa: E402
from xrv.validate import ValidationEngine, ValidationError  # noqa: E402

_engine: ValidationEngine | None = None
_plans: dict[Syntax, dict[str, Plan]] = {}
_corpus = Path()


def _start(ruleset_root: str, corpus: str) -> None:
    global _engine, _corpus
    # Saxon prints a stack of template frames to the process's stderr for every
    # transform that stops on an uncastable value, and thousands do. A failure
    # of the search itself is a Python exception and still reaches the parent.
    os.dup2(os.open(os.devnull, os.O_WRONLY), 2)
    ruleset = Ruleset(root=Path(ruleset_root))
    _engine = ValidationEngine(ruleset)
    _corpus = Path(corpus)
    for syntax in Syntax:
        _plans[syntax] = plans(read_stylesheet(sheet) for sheet in ruleset.stylesheets(syntax))


def _fired(engine: ValidationEngine, document: bytes, syntax: Syntax) -> Counter[str]:
    return Counter(finding.rule_id for finding in engine.rule_findings(document, syntax))


def _contexts(engine: ValidationEngine, document: bytes, syntax: Syntax) -> frozenset[str]:
    """Rule contexts the stylesheets evaluated for this document.

    A `Finding` records an assertion that failed. Whether a rule was evaluated
    at all is in the raw report, as `svrl:fired-rule`, and it is the difference
    between a rule no edit could breach and one the reference messages never
    exercise.
    """
    return frozenset(
        rule.get("context") or ""
        for report in engine.reports(document, syntax)
        for rule in etree.fromstring(report.encode()).iter(f"{{{SVRL}}}fired-rule")
    )


def _sweep(base: str) -> dict[str, object]:
    """Try every edit the rules suggest for one reference message.

    Everything is judged by the engine the service runs: the scenario the
    document belongs to decides which stylesheets apply and how each rule is
    graded, here as there.
    """
    assert _engine is not None
    document = (_corpus / base).read_bytes()
    syntax = identify(document).syntax

    if any(finding.blocking for finding in _engine.findings(document, syntax)):
        return {"base": base, "skipped": True}
    before = _fired(_engine, document, syntax)

    index = DocumentIndex(document)
    edits: dict[Edit, None] = {}
    suggested: dict[str, int] = {}
    for rule_id, plan in _plans[syntax].items():
        own = set(index.edits(plan))
        suggested[rule_id] = len(own)
        for edit in own:
            edits.setdefault(edit)

    hits, failed = _try(document, syntax, before, [(edit,) for edit in edits])
    return {
        "base": base,
        "skipped": False,
        "syntax": syntax,
        "contexts": _contexts(_engine, document, syntax),
        "suggested": suggested,
        "trials": len(edits),
        "failed": failed,
        "hits": hits,
    }


def _try(
    document: bytes, syntax: Syntax, before: Counter[str], candidates: list[tuple[Edit, ...]]
) -> tuple[list[tuple[tuple[Edit, ...], frozenset[str], bool]], int]:
    """Run each candidate and keep the ones that made something new fire."""
    assert _engine is not None
    hits = []
    failed = 0
    for edits in candidates:
        edited = apply_edits(document, edits)
        try:
            after = _fired(_engine, edited, syntax)
        except ValidationError:
            # A value the stylesheet cannot cast — text where an amount goes —
            # stops the transform. That is not a rule firing.
            failed += 1
            continue
        fired = newly_fired(before, after)
        if fired:
            hits.append((edits, fired, bool(_engine.structure.findings(edited, syntax))))
    return hits, failed


#: Pairs tried per rule and reference message. Enough to pair every way into a
#: rule's context with every way of breaking it in a typical message; a cap so
#: a rule with many literals does not take the afternoon.
_MAX_PAIRS = 600


def _sweep_pairs(task: tuple[str, list[str]]) -> dict[str, object]:
    """Try pairs of edits for rules no single edit reached in this message.

    Two kinds of rule need two edits. One is written as "A or B": both have to
    go. The other is never evaluated on any reference message, because none is
    in its context — no invoice in the corpus uses VAT category G — so one
    edit carries the document into the context and a second breaks the rule
    there. The pairs are the rule's own single edits, combined: an edit read
    from the context with one read from the test, and test edits with each
    other. Nothing outside what the rule reads is tried.
    """
    assert _engine is not None
    base, rule_ids = task
    document = (_corpus / base).read_bytes()
    syntax = identify(document).syntax
    before = _fired(_engine, document, syntax)
    index = DocumentIndex(document)

    candidates: dict[tuple[Edit, ...], None] = {}
    for rule_id in rule_ids:
        plan = _plans[syntax][rule_id]
        entering = list(dict.fromkeys(index.edits(plan, origin="context")))
        breaking = list(dict.fromkeys(index.edits(plan, origin="test")))
        pairs: list[tuple[Edit, ...]] = []
        for first in entering:
            pairs.extend((first, second) for second in breaking if second.path != first.path)
        for i, first in enumerate(breaking):
            pairs.extend(
                (first, second) for second in breaking[i + 1 :] if second.path != first.path
            )
        for pair in pairs[:_MAX_PAIRS]:
            candidates.setdefault(pair)

    hits, failed = _try(document, syntax, before, list(candidates))
    return {
        "base": base,
        "syntax": syntax,
        "trials": len(candidates),
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

    def keep(result: dict[str, object]) -> None:
        syntax = result["syntax"]
        for edits, fired, caught in result["hits"]:  # type: ignore[union-attr]
            for rule_id in fired:
                if not rule_id.startswith(TARGET_PREFIX):
                    continue
                found = Derived(
                    rule_id=rule_id,
                    syntax=syntax,  # type: ignore[arg-type]
                    base=str(result["base"]),
                    edits=edits,
                    collateral=fired - {rule_id},
                    caught_by_schema=caught,
                )
                held = best.get((syntax, rule_id))  # type: ignore[arg-type]
                if held is None or found.rank() < held.rank():
                    best[(syntax, rule_id)] = found  # type: ignore[index]

    for result in usable:
        contexts[result["syntax"]] |= result["contexts"]  # type: ignore[index]
        keep(result)

    every = {
        syntax: plans(read_stylesheet(sheet) for sheet in ruleset.stylesheets(syntax))
        for syntax in Syntax
    }

    # Second phase: pairs, for what single edits left unreached. Tried in the
    # four messages that suit the rule best: ones where its context was
    # evaluated at all come first — a rule about allowances needs a message
    # with an allowance in it, not one with the most charges — and among
    # those, the ones where the rule's own reading found the most to edit.
    tasks: dict[str, list[str]] = {}
    for syntax in Syntax:
        for rule_id, plan in every[syntax].items():
            if (syntax, rule_id) in best:
                continue
            ranked = sorted(
                (r for r in usable if r["syntax"] is syntax),
                key=lambda r: (
                    not (plan.contexts & r["contexts"]),  # type: ignore[operator]
                    -r["suggested"].get(rule_id, 0),  # type: ignore[union-attr]
                    r["base"],
                ),
            )
            for result in ranked[:4]:
                tasks.setdefault(str(result["base"]), []).append(rule_id)
    started_pairs = time.perf_counter()
    with ProcessPoolExecutor(
        max_workers=args.jobs, initializer=_start, initargs=(str(ruleset.root), str(corpus))
    ) as pool:
        paired = list(pool.map(_sweep_pairs, sorted(tasks.items())))
    pair_trials = sum(int(r["trials"]) for r in paired)  # type: ignore[call-overload]
    for result in paired:
        keep(result)
    elapsed_pairs = time.perf_counter() - started_pairs

    unreached = []
    for syntax in Syntax:
        for rule_id, plan in every[syntax].items():
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
    print(
        f"then {pair_trials} pairs of edits for the rules that left unreached, "
        f"in {elapsed_pairs:.0f} s"
    )
    if skipped:
        print(f"skipped, not clean to begin with: {', '.join(skipped)}")
    for syntax in Syntax:
        mine = [m for m in mutations if m.syntax is syntax]
        left = [u for u in unreached if u["syntax"] == str(syntax)]
        alone = sum(1 for m in mine if not m.collateral)
        through = sum(1 for m in mine if not m.caught_by_schema)
        paired_up = sum(1 for m in mine if len(m.edits) > 1)
        print(
            f"{syntax}: {len(mine)} of {len(mine) + len(left)} business rules fire — "
            f"{alone} alone, {through} past the schema, {paired_up} needing two edits"
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
