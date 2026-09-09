# XRechnung / ZUGFeRD validator

[![CI](https://github.com/harshal0001/xrechnung-validator/actions/workflows/ci.yml/badge.svg)](https://github.com/harshal0001/xrechnung-validator/actions/workflows/ci.yml)

Validates German e-invoices against EN 16931 and the KoSIT XRechnung rule set, and
explains each failure in plain German or English.

Validators for this already exist. What is missing is the layer between
`BR-DE-15 failed` and *"the buyer reference is missing — the federal invoice
platform routes on that field, and the ordering authority gives it to you with the
purchase order."* That layer is the point of this project.

> This service validates against the published KoSIT rule set. It is not a legal
> compliance certification, and it does not claim to be.

---

## What it does

Accepts an invoice as **UBL 2.1 XML**, **UN/CEFACT CII XML**, or a **ZUGFeRD /
Factur-X PDF**, sniffs the payload and routes it. Validates in two layers — XSD for
structure, KoSIT Schematron (compiled to XSLT 2.0, executed via Saxon) for the
business rules. Returns a typed report, and shows the failing line in the document.

Every response records which rule set version produced it and the SHA-256 of that
version's bytes.

---

## What makes it different

Four things, in the order they matter.

### The explanation is grounded, and the grounding is checkable

Each entry is split in two, and the split is the product:

| Field | What it is |
|---|---|
| `what` | Restates the rule. Must be derivable from the official rule text alone. |
| `why` | Context — cause, consequence, legal background. Useful, human-approved, and **not** in any rule text. |

They stay separate fields through the model, the API and the interface. Folding them
together would present editorial text as though it had the standing of a grounded
restatement.

Every `why` sentence traces to a cited public source — § 14 Abs. 4 UStG, the federal
e-invoicing portal, the EN 16931 abstract model, or a named rule in the rule set —
with the citation recorded per entry. Anything that could not be sourced was **cut**,
not softened. Four entries ship with an empty `why` because that was the honest state.

### A language model never sees the invoice

The explainer's signature is `explain(finding) -> str`. It receives a frozen object
carrying four grounding fields and no reference to the document. There is no code path
by which it could obtain the invoice, because nothing hands it one.

That is tested, not asserted: a test validates a real invoice and checks that none of
its distinctive values — IBAN, party names, line items — appear anywhere in the
findings an explainer receives. It is verified non-vacuous by injecting one and
confirming the test catches it.

### Explanations are reviewed, and review expires

Each entry records the digest of the rule text it was written against, and serves only
while that digest still matches. If KoSIT rewords a rule, the entry **un-reviews
itself** rather than leaving a stale approval standing over text nobody read.

All 50 entries — 25 German, 25 English — have been read and approved by a person
against their cited sources.

### Accuracy is measured in both directions

Zero false positives is trivially achievable by reporting nothing. Proving rules fire
is trivially achievable by reporting everything. Only the pair means anything.

---

## Measured results

Every figure from a real run against the official KoSIT corpus and rule set. None is
an estimate; an empty cell is correct until measured.

| Metric | Value |
|---|---|
| False positives on the valid reference corpus | **0** across 66 KoSIT reference messages (33 UBL, 33 CII), structural and business rules |
| Business rules proven to fire | **25** across 38 mutations — German CIUS, EN 16931 core, calculation, code list — both syntaxes |
| Rules firing that should not | **0** — every mutation trips its target rule and nothing beyond what it declares |
| Explanations reviewed by a person | **50 of 50** (25 DE, 25 EN), each traced to the rule text and a cited source |
| Validation latency | 27 ms/document warm, XSD and both rule sets |
| Startup | 2.4 s to compile three schemas and four stylesheets |
| Container image | 218 MB, runs on amd64 and arm64 |
| Cold start to first response | — |

---

## Architecture

Ports and adapters. Each module owns one thing; the column that matters is the third.

| Module | Owns | Must not |
|---|---|---|
| `core/` | `Finding`, `ValidationReport`, `Severity` — the contract everything speaks | Import Saxon, lxml or the web framework |
| `ingest/` | Sniffing, routing, ZUGFeRD unwrapping, hardened parsing | Know anything about rules |
| `rules/` | Downloading, hashing and version-addressing KoSIT configurations | Execute anything |
| `validate/` | XSD and Saxon execution; SVRL turned into typed findings | Format text for humans |
| `explain/` | Grounded explanation, review state, language selection | **See the invoice** |
| `api/` | Routes, upload handling, error translation | Contain validation logic |

### The dependency that shapes everything

SaxonC-HE bundles a **native library**. KoSIT ships Schematron compiled to XSLT 2.0,
and lxml only does 1.0 — so Saxon is not negotiable, and the native library rules out
most plain serverless Python runtimes. That single fact makes a container the
deployment unit and sets the memory floor.

It is also not thread-safe, so validation is serialised inside each process and
concurrency comes from more processes. That is how both Lambda and Cloud Run scale a
container anyway.

### The rule set is data, never a constant

The KoSIT validator configuration is downloaded, hashed and recorded — never vendored
into code. Every fetch writes a manifest naming the release and the SHA-256 of its
bytes, and every validation response carries both forward.

This exists because **XRechnung 4.0 is in flight**. Building against version-addressed
rules makes that migration a configuration change rather than a rewrite.

### Severity is where a naive implementation goes wrong

A failed Schematron assertion is *not* automatically an error. KoSIT carries severity
in a `flag` attribute, and its `fatal` means a business-rule breach, not a structural
failure. Reference invoices that are valid by construction still emit informational
assertions.

Treating every failed assertion as a failure would report **33 of 66 valid documents
as broken**.

---

## Deployment

**One image, no platform branches.** It carries the AWS Lambda Web Adapter, which the
Lambda runtime loads from `/opt/extensions` and every other host never reads — so the
same bytes run on Lambda behind a Function URL, on Cloud Run, on Render, or on a
laptop. Nothing in the application knows where it is running: there is no Lambda
handler and no second Dockerfile.

Deployed as a container on **arm64 Graviton** in **`eu-central-1` (Frankfurt)**, so
German invoices are processed in Germany. Lambda's init phase is unbilled at full
vCPU, which is a real fit here: the 2.4 s of schema and stylesheet compilation lands
where nobody pays for it.

The frontend is served by the same process — one origin, no CORS, one thing to deploy.

---

## Stack

| Layer | Choice | Why this and not the obvious alternative |
|---|---|---|
| XSLT engine | **SaxonC-HE** | Not negotiable — KoSIT ships XSLT 2.0 and lxml only does 1.0 |
| Language | Python 3.12 | The floor the code generator supports |
| XML | lxml | XSD validation and tree work, with entity resolution off at the boundary |
| PDF | pikepdf | Reads PDF/A-3 embedded attachments |
| API | FastAPI + Pydantic v2 | Typed models, schema for free |
| Frontend | React + TypeScript + Vite | Static build, served from the same container |
| Container | Docker | Required — Saxon ships a native library |
| Tests | pytest, vitest | 493 backend, 21 frontend |
| CI | GitHub Actions | Lint, types, tests and a multi-architecture image build on every change |

---

## Running it

```bash
docker build -t xrv . && docker run --rm -p 8080:8080 xrv
```

Then open `http://localhost:8080`. Three sample invoices are bundled, so it can be
tried without having an XRechnung file to hand.

---

## Not in scope

Keeping this list short is what makes the project finishable.

- **Invoice generation.** This validates and explains; it does not author.
- **Peppol transmission.** Germany mandates the format, not the channel.
- **ERP connectors.**
- **PDF/A-3 conformance checking.** That is veraPDF's job.
- **Any claim of legal compliance certification.** The service validates against the
  published rule set. That statement is true and defensible; the other one is not.

---

## References

- [KoSIT validator configuration](https://github.com/itplr-kosit/validator-configuration-xrechnung)
- [EN 16931 Schematron](https://github.com/ConnectingEurope/eInvoicing-EN16931)
- [German CIUS Schematron](https://github.com/itplr-kosit/xrechnung-schematron)
- Sample invoices derive from the [KoSIT test suite](https://github.com/itplr-kosit/xrechnung-testsuite) (Apache-2.0); see `frontend/public/samples/NOTICE.md`
