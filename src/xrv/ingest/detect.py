"""Work out what arrived, and which validator it belongs to.

This module knows about document shapes and nothing about rules. It answers two
questions — is this XML or a PDF, and if XML, which of the syntaxes EN 16931
permits — and refuses everything else with a message a person can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from lxml import etree

from xrv.core import Source, Syntax
from xrv.ingest.xml import parse

#: Root namespaces EN 16931 permits, mapped to the syntax that validates them.
#: UBL splits invoices and credit notes across two namespaces; CII carries both
#: in one document type and distinguishes them by a type code inside.
ROOT_NAMESPACES: dict[str, Syntax] = {
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2": Syntax.UBL,
    "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2": Syntax.UBL,
    "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100": Syntax.CII,
}

PDF_MAGIC = b"%PDF-"


class Media(StrEnum):
    """What the bytes are, before asking what they mean."""

    XML = "xml"
    PDF = "pdf"
    UNKNOWN = "unknown"


class UnsupportedDocumentError(ValueError):
    """Recognisable as something, but not something this service validates."""


@dataclass(frozen=True, slots=True)
class Document:
    """A payload identified well enough to hand to a validator."""

    source: Source
    syntax: Syntax
    root: str
    #: The XML as received, byte for byte. Kept rather than re-serialised so a
    #: hash of it means something later.
    content: bytes

    @property
    def is_credit_note(self) -> bool:
        return self.root == "CreditNote"


def detect_media(payload: bytes) -> Media:
    """Classify raw bytes without parsing them.

    Cheap and done first: a 12 MB PDF should be recognised as a PDF rather than
    handed to an XML parser to fail slowly.
    """
    head = payload[:1024].lstrip()
    if head.startswith(PDF_MAGIC):
        return Media.PDF
    if head.startswith(b"<?xml") or head.startswith(b"<"):
        return Media.XML
    # A UTF-8 BOM is legal in front of an XML declaration and common from
    # Windows tooling, so it must not turn a valid invoice into "unknown".
    if head.startswith(b"\xef\xbb\xbf") and head[3:].lstrip().startswith(b"<"):
        return Media.XML
    return Media.UNKNOWN


def identify(payload: bytes) -> Document:
    """Route a payload to its syntax, or explain why it cannot be routed."""
    media = detect_media(payload)

    if media is Media.PDF:
        raise UnsupportedDocumentError(
            "This looks like a PDF. Extracting the invoice XML from a ZUGFeRD "
            "PDF is not available yet; upload the XML directly for now."
        )
    if media is Media.UNKNOWN:
        raise UnsupportedDocumentError(
            "This is neither XML nor a PDF. Upload an XRechnung XML file "
            "(UBL or UN/CEFACT CII) or a ZUGFeRD PDF."
        )

    tree = parse(payload)
    qname = etree.QName(tree.getroot())
    syntax = ROOT_NAMESPACES.get(qname.namespace or "")

    if syntax is None:
        raise UnsupportedDocumentError(
            f"'{qname.localname}' in namespace '{qname.namespace or 'none'}' is not "
            f"an e-invoice this service validates. Expected a UBL Invoice or "
            f"CreditNote, or a UN/CEFACT CrossIndustryInvoice."
        )

    return Document(
        source=Source.XML,
        syntax=syntax,
        root=qname.localname,
        content=payload,
    )
