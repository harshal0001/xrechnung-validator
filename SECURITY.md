# Security

## Reporting a vulnerability

Use GitHub's private reporting for this repository: **Security → Report a
vulnerability**. It reaches the maintainer without creating a public issue.
Please do not open a public issue for anything that could be exploited.

You can expect an acknowledgement within a week.

## What the service holds

Nothing. An uploaded invoice is validated in memory and discarded with the
response. No document, filename, or value from a document is written to a log;
`tests/integration/test_logging.py` validates real invoices and searches every
log line for every value in them.

## What is in scope

- The service under `src/`: upload handling, XML and PDF parsing, validation,
  the HTTP surface.
- The deployed instance at xrechnung.harshalkothari.tech.

The KoSIT rule set, the EN 16931 Schematron and SaxonC-HE are upstream
projects; a problem in one of them should go to its maintainers, though a note
here is welcome if it affects this service.

## Hardening already in place

- XML is parsed with external entities, DTD loading and network access off.
- Uploads are refused past a size cap before the body is read in full.
- PDFs are read with pikepdf; encrypted ones are refused.
- The deployed function has no AWS permissions beyond writing its own logs,
  and the public endpoint is throttled.
