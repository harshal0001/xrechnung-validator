"""Upload to report, with nothing mocked.

Everything below runs the real rule set against real reference invoices, taking
the same route a request would: bytes arrive, ingest decides what they are, and
both validation layers run on what it hands back.

The PDFs are constructed, because the KoSIT test suite ships XML only. That is a
real limit on what these prove — the extraction is exercised, the habits of
real-world PDF producers are not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xrv.core import Source, Syntax
from xrv.ingest import Document, Profile, identify
from xrv.validate import ValidationEngine


def validate(engine: ValidationEngine, payload: bytes) -> tuple[Document, tuple]:
    document = identify(payload)
    return document, engine.findings(document.content, document.syntax)


class TestUploadedXml:
    @pytest.mark.parametrize("suffix", ["*_ubl.xml", "*_uncefact.xml"])
    def test_every_reference_invoice_survives_the_whole_pipeline(
        self, engine: ValidationEngine, corpus: Path, suffix: str
    ) -> None:
        """Routed by content, validated at both layers, nothing blocking."""
        for invoice in sorted(corpus.glob(suffix)):
            document, findings = validate(engine, invoice.read_bytes())
            assert document.source is Source.XML
            blocking = [f.rule_id for f in findings if f.blocking]
            assert not blocking, f"{invoice.name}: {blocking}"


class TestUploadedZugferdPdf:
    def test_a_pdf_reaches_the_same_verdict_as_its_xml(
        self, engine: ValidationEngine, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """The PDF is a container. Wrapping an invoice must not change its result."""
        _, from_xml = validate(engine, cii_invoice)
        _, from_pdf = validate(engine, make_zugferd_pdf(cii_invoice))
        assert from_pdf == from_xml

    def test_the_report_knows_it_came_from_a_pdf(
        self, engine: ValidationEngine, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        document, _ = validate(engine, make_zugferd_pdf(cii_invoice))
        assert document.source is Source.ZUGFERD_PDF
        assert document.syntax is Syntax.CII
        assert document.profile is Profile.XRECHNUNG

    def test_every_cii_reference_invoice_works_when_wrapped(
        self, engine: ValidationEngine, corpus: Path, make_zugferd_pdf
    ) -> None:
        """Not just the one convenient fixture."""
        for invoice in sorted(corpus.glob("*_uncefact.xml")):
            payload = invoice.read_bytes()
            document, findings = validate(engine, make_zugferd_pdf(payload))
            assert document.content == payload
            blocking = [f.rule_id for f in findings if f.blocking]
            assert not blocking, f"{invoice.name} wrapped in a PDF: {blocking}"


class TestMandateReadiness:
    def test_a_profile_below_en16931_is_flagged_not_validated_away(
        self, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """MINIMUM has no line items. The service says so rather than reporting
        dozens of failures for data the profile never claimed to carry."""
        downgraded = cii_invoice.replace(
            b"urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
            b"urn:factur-x.eu:1p0:minimum",
        )
        assert downgraded != cii_invoice, "the guideline id was not replaced"

        document = identify(make_zugferd_pdf(downgraded))
        assert document.profile is Profile.MINIMUM
        assert not document.mandate_ready

    def test_a_full_profile_is_mandate_ready(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        assert identify(make_zugferd_pdf(cii_invoice)).mandate_ready
