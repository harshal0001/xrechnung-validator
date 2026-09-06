# XRechnung / ZUGFeRD validator

[![CI](https://github.com/harshal0001/xrechnung-validator/actions/workflows/ci.yml/badge.svg)](https://github.com/harshal0001/xrechnung-validator/actions/workflows/ci.yml)

Validates German e-invoices against EN 16931 and the KoSIT XRechnung rule set, and
explains each failure in plain German.

Validators for this already exist. What is missing is the layer between
`BR-DE-15 failed` and *"the buyer reference is missing, which the customer's system
needs in order to route the invoice to the right cost centre."* That layer is the
point of this project.

> **Status: in development.** The validation core is not finished yet. See
> [Status](#status) for what works today. This service validates against the
> published KoSIT rule set; it is not a legal compliance certification.

## What it does

- Accepts an invoice as **XML** (UBL 2.1 or UN/CEFACT CII) or as a **ZUGFeRD PDF/A-3**,
  sniffs the payload and routes it.
- Validates in two layers: **XSD** for structure, **KoSIT Schematron** (compiled to
  XSLT 2.0, executed via Saxon) for the business rules that actually fail in
  production — `BR-*`, `BR-CO-*`, `BR-DE-*`.
- Returns a structured list of findings, each with its rule ID, severity, verbatim
  rule text, XPath location and offending value.
- Explains each finding in German, grounded strictly in the rule text.

Rule sets are downloaded, hashed and recorded as data — never vendored as constants.
Every response carries the `ruleset_version` and `ruleset_sha256` that produced it,
so a result can always be traced back to a specific published configuration.

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | |
| API | FastAPI + Pydantic v2 | typed models, OpenAPI for free |
| Bindings | xsdata | typed dataclasses generated from the official XSDs |
| XSLT | **SaxonC-HE** (`saxonche`) | KoSIT ships XSLT 2.0; `lxml` only does 1.0 |
| XML | lxml | XSD validation and tree work |
| PDF | pikepdf | reads PDF/A-3 embedded attachments |
| Frontend | React + TypeScript + Vite | static build |
| Container | Docker | `saxonche` bundles a native library |
| Tests | pytest | including the mutation suite |

`saxonche` bundling a native library is the constraint that shapes deployment: it
rules out restricted serverless Python runtimes and makes the container the unit of
deployment.

## Architecture

```
Upload (XML or PDF)
   |
   +-- ingest/     sniff payload, unwrap PDF/A-3, detect ZUGFeRD profile
   |
   +-- bindings/   xsdata-generated types (committed, never hand-edited)
   |
   +-- validate/   XSD structural check, then Saxon over KoSIT Schematron,
   |               SVRL parsed into typed Findings
   |
   +-- explain/    grounded German explanation per finding
   |
   +-- api/        FastAPI routes
```

`rules/` owns fetching, hashing and versioning the KoSIT configuration.

**The boundary that matters:** `explain/` never receives the invoice. Its port is

```python
class ExplanationProvider(Protocol):
    def explain(self, finding: Finding) -> str: ...
```

The document is not in the signature, so it cannot reach a model. Explanations are
generated per rule, offline, once per rule set version, reviewed, and committed
alongside the rules they describe — so the service serves grounded German text with
no model call in the request path.

### Severity

A Schematron `failed-assert` is not automatically an error. KoSIT carries severity in
the SVRL `flag` attribute, and valid invoices legitimately emit informational
asserts. Only `fatal` and `error` block; `warning` and `information` are reported
without failing the document.

## Status

| | |
|---|---|
| Dependency spikes (Saxon, xsdata) | done — verified in-container on amd64 and arm64 |
| Rule set fetching, hashing, versioning | done |
| Core domain model, rule set registry | done |
| SVRL parsing, Saxon validation engine | done |
| XSD structural validation | done |
| ZUGFeRD PDF unwrapping, profile detection | not started |
| Mutation test suite | in progress — 15 rules proven, both syntaxes and both layers |
| API, frontend, explanations | not started |
| Deployment | not started |
| CI (lint, types, tests, multi-arch image build) | done |

### Measured results

Filled in from real runs, not estimates. Empty until measured.

Two numbers are needed, not one. Zero false positives is easy to reach by
reporting nothing; proving rules fire is easy to reach by reporting everything.
Only the pair means anything.

| Metric | Value |
|---|---|
| Business rules proven to fire | **15** across 21 mutations (14 German CIUS, 7 EN 16931 core), UBL and CII |
| Rules firing that should not | **0** — every mutation trips its target rule and nothing beyond what it declares |
| False positives on the valid reference corpus | **0** across 66 KoSIT reference messages (33 UBL, 33 CII), structural and business rules |
| Validation latency p50 / p95 | 72 ms/document mean, warm, XSD + both rule sets — not yet split by percentile |
| Cold start to first response | — |
| Explanation accuracy on the eval set | — |

## Local development

```bash
uv sync --all-extras          # resolves from uv.lock, so CI and local match

# Fetch the current KoSIT rule set and reference invoices
uv run python scripts/fetch_ruleset.py --testsuite
```

Run the checks CI runs:

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src/xrv
uv run pytest
```

Tests that need a fetched rule set skip when there is not one, so the suite passes
on a clean checkout. CI sets `XRV_REQUIRE_RULESET=1` to turn that skip into a
failure, because a silent skip there would hide rule set drift.

Verify the toolchain end to end — Saxon compiling and executing real KoSIT
stylesheets, and xsdata generating and parsing with the official schemas:

```bash
bash scripts/run_spike.sh            # host + amd64 container
bash scripts/run_spike.sh --arm64    # also arm64 (emulated; slow)
```

Binding generation is not yet wired into a script of its own — `scripts/spike_xsdata.py`
generates from the XSDs shipped in the rule set and is the reference for how it is done.
Two things it settled, both worth keeping:

> `xsdata` shells out to `ruff` to format generated code, so `ruff` must be on
> `PATH`. Generate with `--structure-style single-package`: the `clusters` layout
> splits UBL 2.1 into thousands of modules and makes import times an order of
> magnitude worse.

## Not in scope

Invoice generation, Peppol transmission (Germany mandates the format, not the
channel), ERP connectors, PDF/A-3 conformance checking, and the ZUGFeRD `EXTENDED`
profile edge cases.

## References

- [KoSIT validator configuration](https://github.com/itplr-kosit/validator-configuration-xrechnung)
- [KoSIT test suite](https://github.com/itplr-kosit/xrechnung-testsuite)
- [SaxonC-HE](https://pypi.org/project/saxonche/) · [xsdata](https://pypi.org/project/xsdata/)
