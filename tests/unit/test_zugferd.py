"""Unwrapping ZUGFeRD PDFs, and deciding what a profile is worth validating.

A ZUGFeRD PDF is a PDF/A-3 carrying the CII invoice as an embedded attachment.
Two things can go wrong that a naive reader would get wrong quietly: taking the
wrong attachment, and validating a profile that never claimed to carry the data
the rules ask for.

Note on evidence: the KoSIT test suite is XML only, so every PDF here is
constructed. These tests prove the extraction logic; they do not prove anything
about the quirks of real-world PDF producers.
"""

from __future__ import annotations

import pytest

from xrv.core import Source, Syntax
from xrv.ingest import (
    KNOWN_ATTACHMENTS,
    MalformedXmlError,
    Media,
    Profile,
    UnsupportedDocumentError,
    ZugferdError,
    detect_media,
    detect_profile,
    extract_xml,
    identify,
    read_guideline_id,
)

MINIMUM = "urn:factur-x.eu:1p0:minimum"
BASIC_WL = "urn:factur-x.eu:1p0:basicwl"
BASIC = "urn:cen.eu:en16931:2017#compliant#urn:factur-x.eu:1p0:basic"
EN16931 = "urn:cen.eu:en16931:2017"
EXTENDED = "urn:cen.eu:en16931:2017#conformant#urn:factur-x.eu:1p0:extended"
XRECHNUNG = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0"


class TestProfileDetection:
    @pytest.mark.parametrize(
        ("urn", "expected"),
        [
            (MINIMUM, Profile.MINIMUM),
            (BASIC_WL, Profile.BASIC_WL),
            (BASIC, Profile.BASIC),
            (EN16931, Profile.EN16931),
            (EXTENDED, Profile.EXTENDED),
            (XRECHNUNG, Profile.XRECHNUNG),
        ],
    )
    def test_known_profiles(self, urn: str, expected: Profile) -> None:
        assert detect_profile(urn) is expected

    def test_a_national_cius_is_not_mistaken_for_plain_en16931(self) -> None:
        """Every CIUS URN also starts with the EN 16931 prefix, so a prefix test
        alone would classify every XRechnung document as generic EN 16931."""
        assert XRECHNUNG.startswith(EN16931)
        assert detect_profile(XRECHNUNG) is Profile.XRECHNUNG

    def test_basicwl_is_not_read_as_basic(self) -> None:
        """One is mandate-ready and the other is not, and the URNs differ by two
        characters."""
        assert detect_profile(BASIC_WL) is Profile.BASIC_WL
        assert detect_profile(BASIC) is Profile.BASIC

    @pytest.mark.parametrize("urn", [None, "", "   ", "urn:something:nobody:has:heard:of"])
    def test_unrecognised_urns(self, urn: str | None) -> None:
        assert detect_profile(urn) is Profile.UNKNOWN

    def test_case_and_whitespace_do_not_matter(self) -> None:
        assert detect_profile(f"  {XRECHNUNG.upper()}  ") is Profile.XRECHNUNG

    def test_a_future_xrechnung_version_is_still_recognised(self) -> None:
        """XRechnung 4.0 will have a different URN. Structure is matched rather
        than an exhaustive list, so it will not stop being recognised."""
        future = "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_4.0"
        assert detect_profile(future) is Profile.XRECHNUNG


class TestMandateReadiness:
    @pytest.mark.parametrize(
        "profile", [Profile.BASIC, Profile.EN16931, Profile.EXTENDED, Profile.XRECHNUNG]
    )
    def test_profiles_that_carry_an_invoice(self, profile: Profile) -> None:
        assert profile.mandate_ready

    @pytest.mark.parametrize("profile", [Profile.MINIMUM, Profile.BASIC_WL])
    def test_profiles_below_en16931_are_not_mandate_ready(self, profile: Profile) -> None:
        """They have no line items. Running the full rule set over one reports
        dozens of failures for data the profile never claimed to carry."""
        assert not profile.mandate_ready

    def test_an_unknown_profile_is_not_assumed_ready(self) -> None:
        """Claiming mandate-readiness on the strength of an unrecognised URN
        would be a guess presented as a fact."""
        assert not Profile.UNKNOWN.mandate_ready


class TestExtraction:
    def test_a_standard_zugferd_pdf(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        data, name = extract_xml(make_zugferd_pdf(cii_invoice))
        assert data == cii_invoice
        assert name == "factur-x.xml"

    @pytest.mark.parametrize("filename", KNOWN_ATTACHMENTS)
    def test_every_attachment_name_in_circulation(
        self, make_zugferd_pdf, cii_invoice: bytes, filename: str
    ) -> None:
        """ZUGFeRD 1.x, ZUGFeRD 2.x, Factur-X and XRechnung-in-PDF each chose a
        different name, and documents using all of them are still arriving."""
        data, name = extract_xml(make_zugferd_pdf(cii_invoice, filename=filename))
        assert data == cii_invoice
        assert name == filename

    def test_the_name_is_matched_case_insensitively(
        self, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        data, _ = extract_xml(make_zugferd_pdf(cii_invoice, filename="ZUGFeRD-invoice.xml"))
        assert data == cii_invoice

    def test_a_standard_name_wins_over_other_attachments(
        self, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """Real PDFs carry terms and conditions, logos and delivery notes too."""
        pdf = make_zugferd_pdf(
            cii_invoice,
            extras={"terms.xml": b"<terms/>", "logo.png": b"\x89PNG\r\n\x1a\n"},
        )
        data, name = extract_xml(pdf)
        assert name == "factur-x.xml"
        assert data == cii_invoice

    def test_a_single_oddly_named_xml_is_accepted(
        self, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """Producers do get the name wrong, and one unambiguous XML is still
        unambiguous."""
        data, name = extract_xml(make_zugferd_pdf(cii_invoice, filename="rechnung_2026.xml"))
        assert data == cii_invoice
        assert name == "rechnung_2026.xml"

    def test_the_bytes_are_not_altered(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        """A hash of the extracted invoice has to mean something."""
        data, _ = extract_xml(make_zugferd_pdf(cii_invoice))
        assert data == cii_invoice


class TestExtractionRefusals:
    def test_a_pdf_with_nothing_attached(self, make_zugferd_pdf) -> None:
        """A scanned or printed-to-PDF invoice, which is the common mistake."""
        with pytest.raises(ZugferdError, match="no embedded invoice"):
            extract_xml(make_zugferd_pdf(None))

    def test_ambiguity_is_refused_rather_than_guessed(
        self, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """Picking wrong would validate a document the sender did not send."""
        pdf = make_zugferd_pdf(None, extras={"one.xml": cii_invoice, "two.xml": b"<other/>"})
        with pytest.raises(ZugferdError, match="ambiguous"):
            extract_xml(pdf)

    def test_attachments_that_are_not_xml(self, make_zugferd_pdf) -> None:
        pdf = make_zugferd_pdf(None, extras={"scan.png": b"\x89PNG", "notes.txt": b"hi"})
        with pytest.raises(ZugferdError, match="none of the"):
            extract_xml(pdf)

    def test_an_empty_attachment(self, make_zugferd_pdf) -> None:
        with pytest.raises(ZugferdError, match="empty"):
            extract_xml(make_zugferd_pdf(b"   "))

    def test_a_password_protected_pdf(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        pdf = make_zugferd_pdf(cii_invoice, password="hunter2")
        with pytest.raises(ZugferdError, match="password-protected"):
            extract_xml(pdf)

    def test_a_file_that_is_not_really_a_pdf(self) -> None:
        with pytest.raises(ZugferdError, match="not a readable PDF"):
            extract_xml(b"%PDF-1.7\nbut then nothing valid at all")


class TestGuidelineId:
    def test_read_from_a_real_invoice(self, cii_invoice: bytes) -> None:
        assert "xrechnung" in (read_guideline_id(cii_invoice) or "").lower()

    def test_absent_when_the_document_does_not_declare_one(self) -> None:
        bare = (
            b"<rsm:CrossIndustryInvoice xmlns:rsm="
            b'"urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"/>'
        )
        assert read_guideline_id(bare) is None


class TestRoutingAPdf:
    def test_a_pdf_is_sniffed_as_a_pdf(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        assert detect_media(make_zugferd_pdf(cii_invoice)) is Media.PDF

    def test_it_routes_to_the_embedded_invoice(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        document = identify(make_zugferd_pdf(cii_invoice))
        assert document.source is Source.ZUGFERD_PDF
        assert document.syntax is Syntax.CII
        assert document.root == "CrossIndustryInvoice"
        assert document.attachment == "factur-x.xml"

    def test_the_content_is_the_xml_not_the_pdf(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        assert identify(make_zugferd_pdf(cii_invoice)).content == cii_invoice

    def test_the_profile_is_reported(self, make_zugferd_pdf, cii_invoice: bytes) -> None:
        document = identify(make_zugferd_pdf(cii_invoice))
        assert document.profile is Profile.XRECHNUNG
        assert document.mandate_ready

    def test_plain_xml_carries_no_profile(self) -> None:
        """An uploaded XML was sent as an invoice; it has no ZUGFeRD profile to
        report, and must still count as mandate-ready."""
        ubl = b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        document = identify(ubl)
        assert document.profile is None
        assert document.attachment is None
        assert document.mandate_ready

    def test_an_embedded_file_that_is_not_an_invoice_is_refused(self, make_zugferd_pdf) -> None:
        """A PDF is a container, not a reason to trust what is inside it."""
        order = b'<Order xmlns="urn:oasis:names:specification:ubl:schema:xsd:Order-2"/>'
        pdf = make_zugferd_pdf(order)
        with pytest.raises(UnsupportedDocumentError, match="not an e-invoice"):
            identify(pdf)

    def test_a_malformed_embedded_file_is_refused(self, make_zugferd_pdf) -> None:
        with pytest.raises(MalformedXmlError, match="well-formed"):
            identify(make_zugferd_pdf(b"<CrossIndustryInvoice"))
