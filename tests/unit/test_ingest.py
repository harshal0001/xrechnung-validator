"""Sniffing, routing, and the untrusted boundary.

This is the only module that sees bytes nobody vouched for, so roughly half of
these tests are about what it refuses rather than what it accepts.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from lxml import etree

from xrv.core import Source, Syntax
from xrv.ingest import (
    MAX_BYTES,
    Document,
    MalformedXmlError,
    Media,
    PayloadTooLargeError,
    UnsupportedDocumentError,
    detect_media,
    identify,
    parse,
)

UBL_INVOICE = b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
UBL_CREDIT_NOTE = b'<CreditNote xmlns="urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"/>'
CII_NS = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
CII = f'<rsm:CrossIndustryInvoice xmlns:rsm="{CII_NS}"/>'.encode()


class TestMediaSniffing:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (b"%PDF-1.7\n", Media.PDF),
            (b"   \n%PDF-1.4", Media.PDF),
            (UBL_INVOICE, Media.XML),
            (b'<?xml version="1.0"?><Invoice/>', Media.XML),
            (b"\n\n  <Invoice/>", Media.XML),
            (b"\x89PNG\r\n\x1a\n", Media.UNKNOWN),
            (b"PK\x03\x04", Media.UNKNOWN),
            (b"", Media.UNKNOWN),
            (b"just some text", Media.UNKNOWN),
        ],
    )
    def test_classification(self, payload: bytes, expected: Media) -> None:
        assert detect_media(payload) is expected

    def test_a_utf8_bom_does_not_hide_the_xml(self) -> None:
        """Windows tooling emits these, and a BOM must not make a valid invoice
        unrecognisable."""
        assert detect_media(b"\xef\xbb\xbf" + UBL_INVOICE) is Media.XML

    def test_sniffing_does_not_parse(self) -> None:
        """Classification happens before parsing, so a 12 MB PDF is rejected as a
        PDF rather than handed to an XML parser to fail slowly."""
        assert detect_media(b"%PDF-" + b"\x00" * 5_000_000) is Media.PDF


class TestRouting:
    @pytest.mark.parametrize(
        ("payload", "syntax", "root"),
        [
            (UBL_INVOICE, Syntax.UBL, "Invoice"),
            (UBL_CREDIT_NOTE, Syntax.UBL, "CreditNote"),
            (CII, Syntax.CII, "CrossIndustryInvoice"),
        ],
    )
    def test_each_permitted_root(self, payload: bytes, syntax: Syntax, root: str) -> None:
        document = identify(payload)
        assert document.syntax is syntax
        assert document.root == root
        assert document.source is Source.XML

    def test_credit_notes_are_recognised_as_such(self) -> None:
        """They need a different schema from invoices, so the distinction has to
        survive routing."""
        assert identify(UBL_CREDIT_NOTE).is_credit_note
        assert not identify(UBL_INVOICE).is_credit_note

    def test_content_is_kept_byte_for_byte(self) -> None:
        """Not re-serialised: a hash of the content should mean something."""
        assert identify(UBL_INVOICE).content == UBL_INVOICE

    def test_routing_ignores_the_prefix(self) -> None:
        """The namespace decides, not the prefix a sender happened to choose."""
        odd = b'<x:Invoice xmlns:x="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        assert identify(odd).syntax is Syntax.UBL

    def test_real_corpus_documents_route_correctly(self, corpus: Path) -> None:
        """Routing is by content, so the filename convention must not be needed."""
        for invoice in sorted(corpus.glob("*.xml")):
            document = identify(invoice.read_bytes())
            expected = Syntax.UBL if invoice.name.endswith("_ubl.xml") else Syntax.CII
            assert document.syntax is expected, invoice.name


class TestRejections:
    def test_a_pdf_says_what_to_do_instead(self) -> None:
        with pytest.raises(UnsupportedDocumentError, match="ZUGFeRD"):
            identify(b"%PDF-1.7\n1 0 obj")

    def test_a_binary_file(self) -> None:
        with pytest.raises(UnsupportedDocumentError, match="neither XML nor a PDF"):
            identify(b"\x89PNG\r\n\x1a\n")

    def test_xml_that_is_not_an_invoice(self) -> None:
        order = b'<Order xmlns="urn:oasis:names:specification:ubl:schema:xsd:Order-2"/>'
        with pytest.raises(UnsupportedDocumentError, match="Order"):
            identify(order)

    def test_an_invoice_without_a_namespace(self) -> None:
        """Right element name, wrong document — the namespace is what identifies it."""
        with pytest.raises(UnsupportedDocumentError, match="namespace 'none'"):
            identify(b"<Invoice/>")

    def test_malformed_xml_is_told_apart_from_unsupported(self) -> None:
        """Different problems deserve different answers: one is a broken file,
        the other is the wrong kind of file."""
        with pytest.raises(MalformedXmlError):
            identify(b"<Invoice")

    def test_rejection_messages_say_what_is_accepted(self) -> None:
        for payload in (b"\x89PNG", b"%PDF-1.7"):
            with pytest.raises(UnsupportedDocumentError) as caught:
                identify(payload)
            assert "XML" in str(caught.value) or "ZUGFeRD" in str(caught.value)


class TestUntrustedInput:
    """The parser configuration is load-bearing, so it is asserted, not assumed."""

    def test_external_entities_are_not_resolved(self, tmp_path: Path) -> None:
        """XXE: without this, uploading an invoice reads files off the server."""
        secret = tmp_path / "secret.txt"
        secret.write_text("CANARY-8f21")
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE r [<!ENTITY xxe SYSTEM "file://' + str(secret).encode() + b'">]>\n'
            b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">'
            b"<ID>&xxe;</ID></Invoice>"
        )
        tree = parse(payload)
        assert "CANARY-8f21" not in etree.tostring(tree, encoding="unicode")

    def test_a_permissive_parser_would_have_leaked_it(self, tmp_path: Path) -> None:
        """Proves the test above is not vacuous — the attack does work in general."""
        secret = tmp_path / "secret.txt"
        secret.write_text("CANARY-8f21")
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE r [<!ENTITY xxe SYSTEM "file://' + str(secret).encode() + b'">]>\n'
            b"<Invoice><ID>&xxe;</ID></Invoice>"
        )
        permissive = etree.XMLParser(resolve_entities=True, load_dtd=True, no_network=False)
        leaked = etree.tostring(etree.fromstring(payload, parser=permissive), encoding="unicode")
        assert "CANARY-8f21" in leaked

    def test_entity_expansion_is_capped(self) -> None:
        """Billion laughs: a few hundred bytes that expand to gigabytes."""
        entities = b"".join(b'<!ENTITY e%d "&e%d;&e%d;">' % (i, i - 1, i - 1) for i in range(1, 20))
        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE l ['
            + b'<!ENTITY e0 "'
            + b"x" * 64
            + b'">'
            + entities
            + b"]><Invoice>&e19;</Invoice>"
        )
        with pytest.raises(MalformedXmlError):
            parse(bomb)

    def test_oversized_payloads_are_refused_before_parsing(self) -> None:
        with pytest.raises(PayloadTooLargeError, match="limit is"):
            parse(b"<a/>" + b" " * (MAX_BYTES + 1))

    def test_an_empty_payload(self) -> None:
        with pytest.raises(MalformedXmlError, match="empty"):
            parse(b"   \n  ")

    def test_broken_xml_is_not_silently_repaired(self) -> None:
        """recover=True would validate a document nobody sent."""
        with pytest.raises(MalformedXmlError):
            parse(b"<Invoice><ID>1</Invoice>")

    def test_an_external_dtd_is_not_fetched(self) -> None:
        """no_network / load_dtd: a DTD pointing at a host must not cause a request.

        A parser that fetches referenced DTDs turns every upload into a request
        the uploader chose the destination of. The unroutable address here would
        hang for a long time if it were ever contacted.
        """
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE Invoice SYSTEM "http://192.0.2.1/never.dtd">\n'
            b'<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"/>'
        )
        started = time.perf_counter()
        tree = parse(payload)
        assert etree.QName(tree.getroot()).localname == "Invoice"
        assert time.perf_counter() - started < 2.0, "the DTD looks like it was fetched"


class TestEncodings:
    def test_a_non_utf8_document_survives_routing(self) -> None:
        """The declared encoding is honoured rather than guessed at."""
        payload = (
            '<?xml version="1.0" encoding="ISO-8859-1"?>'
            '<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">'
            "<Note>Grüße</Note></Invoice>"
        ).encode("iso-8859-1")
        document = identify(payload)
        assert document.syntax is Syntax.UBL
        assert etree.fromstring(document.content).findtext("{*}Note") == "Grüße"


class TestDocumentShape:
    def test_is_frozen(self) -> None:
        document = identify(UBL_INVOICE)
        with pytest.raises((AttributeError, TypeError)):
            document.syntax = Syntax.CII  # type: ignore[misc]

    def test_carries_everything_the_validator_needs(self) -> None:
        document = identify(CII)
        assert isinstance(document, Document)
        assert (document.syntax, document.root, document.source) == (
            Syntax.CII,
            "CrossIndustryInvoice",
            Source.XML,
        )
