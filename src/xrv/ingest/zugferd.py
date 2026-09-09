"""Unwrap the invoice XML from a ZUGFeRD / Factur-X PDF, and read its profile.

A ZUGFeRD PDF is a PDF/A-3 carrying the CII invoice as an embedded file
attachment: a person reads the PDF, a machine reads the XML, and the two are
meant to say the same thing. This module takes the machine half out.

Profile matters as much as extraction. ZUGFeRD defines several, and the ones
below EN 16931 carry too few fields to satisfy the German mandate — MINIMUM and
BASIC WL hold little more than a payment summary. Validating those against the
full rule set would bury the reader in failures for data the profile never
claimed to have. They are detected and flagged instead.
"""

from __future__ import annotations

import io
from collections.abc import Mapping
from enum import StrEnum

import pikepdf

from xrv.core import LocalisedError
from xrv.ingest.xml import MAX_BYTES, parse

RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"

GUIDELINE_PATH = (
    f"{{{RSM}}}ExchangedDocumentContext"
    f"/{{{RAM}}}GuidelineSpecifiedDocumentContextParameter"
    f"/{{{RAM}}}ID"
)

#: Attachment names in circulation. ZUGFeRD 1.x used a different one from 2.x,
#: Factur-X another, and XRechnung-in-PDF another again — all still arriving.
KNOWN_ATTACHMENTS = (
    "factur-x.xml",
    "zugferd-invoice.xml",
    "xrechnung.xml",
    "cii.xml",
    "order-x.xml",
)


class ZugferdError(LocalisedError):
    """The PDF carries no invoice this service can read."""


class Profile(StrEnum):
    """ZUGFeRD / Factur-X profiles, in ascending order of completeness."""

    MINIMUM = "MINIMUM"
    BASIC_WL = "BASIC WL"
    BASIC = "BASIC"
    EN16931 = "EN 16931"
    EXTENDED = "EXTENDED"
    XRECHNUNG = "XRECHNUNG"
    UNKNOWN = "UNKNOWN"

    @property
    def mandate_ready(self) -> bool:
        """Whether the profile carries enough to be validated as a real invoice.

        MINIMUM and BASIC WL are booking aids, not invoices — they have no line
        items. An unrecognised profile is treated the same way: claiming a
        document is mandate-ready on the strength of a URN nobody recognises
        would be a guess presented as a fact.
        """
        return self not in _NOT_MANDATE_READY


_NOT_MANDATE_READY = frozenset({Profile.MINIMUM, Profile.BASIC_WL, Profile.UNKNOWN})

#: EN 16931 profiles are identified by a URN built from the standard's own,
#: with `#compliant#` marking a national CIUS and `#conformant#` an extension.
#: Matching on structure rather than an exhaustive list is deliberate: XRechnung
#: 3.0 and 4.0 have different URNs, and a table would silently stop recognising
#: documents the day the next one ships.
_EN16931_PREFIX = "urn:cen.eu:en16931:2017"


def detect_profile(guideline_id: str | None) -> Profile:
    """Classify a guideline URN.

    Order matters. Every national CIUS URN also starts with the EN 16931 prefix,
    so the specific cases have to be tested before the general one.
    """
    if not guideline_id:
        return Profile.UNKNOWN
    urn = guideline_id.strip().lower()

    if "xrechnung" in urn:
        return Profile.XRECHNUNG
    if urn.endswith(":minimum"):
        return Profile.MINIMUM
    if urn.endswith(":basicwl"):
        return Profile.BASIC_WL
    if "conformant" in urn and "extended" in urn:
        return Profile.EXTENDED
    if urn.endswith(":basic") or ":basic#" in urn:
        return Profile.BASIC
    if urn.startswith(_EN16931_PREFIX):
        return Profile.EN16931
    return Profile.UNKNOWN


def read_guideline_id(invoice_xml: bytes) -> str | None:
    """The guideline URN declared inside the CII document itself.

    Read from the XML rather than from PDF metadata: the XML is what gets
    validated, and a producer that disagrees with itself should be believed on
    the half that carries the data.
    """
    element = parse(invoice_xml).find(GUIDELINE_PATH)
    return element.text.strip() if element is not None and element.text else None


def extract_xml(payload: bytes) -> tuple[bytes, str]:
    """Pull the invoice XML out of a ZUGFeRD PDF.

    Returns the XML and the attachment name it came from. Raises rather than
    guessing when there is nothing recognisable to take.
    """
    try:
        pdf = pikepdf.open(io.BytesIO(payload))
    except pikepdf.PasswordError as exc:
        raise ZugferdError(
            "this PDF is password-protected, so its invoice cannot be read",
            code="pdf_encrypted",
        ) from exc
    except pikepdf.PdfError as exc:
        raise ZugferdError(
            f"this file is not a readable PDF: {exc}", code="pdf_unreadable"
        ) from exc

    with pdf:
        attachments = dict(pdf.attachments)
        if not attachments:
            raise ZugferdError(
                "this PDF carries no embedded invoice. A ZUGFeRD or Factur-X PDF "
                "has the invoice XML attached to it; a scanned or printed-to-PDF "
                "invoice does not.",
                code="pdf_no_attachment",
            )

        name = _pick_attachment(attachments)
        try:
            data = bytes(attachments[name].get_file().read_bytes())
        except Exception as exc:  # pikepdf raises assorted low-level errors
            raise ZugferdError(
                f"the embedded file '{name}' could not be read: {exc}",
                code="pdf_attachment_unreadable",
                attachment=name,
            ) from exc

    if len(data) > MAX_BYTES:
        raise ZugferdError(
            f"the embedded invoice is {len(data):,} bytes; the limit is {MAX_BYTES:,}",
            code="payload_too_large",
            size=f"{len(data):,}",
            limit=f"{MAX_BYTES:,}",
        )
    if not data.strip():
        raise ZugferdError(
            f"the embedded file '{name}' is empty", code="xml_empty", attachment=name
        )
    return data, name


def _pick_attachment(attachments: Mapping[str, object]) -> str:
    """Choose which embedded file is the invoice.

    Prefer a name the standards actually specify. Fall back to a lone XML
    attachment, because producers do get the name wrong — but never guess
    between several, since picking the wrong one would validate a document the
    sender did not mean to send.
    """
    by_lower = {name.lower(): name for name in attachments}
    for candidate in KNOWN_ATTACHMENTS:
        if candidate in by_lower:
            return by_lower[candidate]

    xml_named = [name for name in attachments if name.lower().endswith(".xml")]
    if len(xml_named) == 1:
        return xml_named[0]
    if not xml_named:
        raise ZugferdError(
            f"none of the {len(attachments)} embedded files is XML "
            f"(found: {', '.join(sorted(attachments))})"
        )
    raise ZugferdError(
        f"this PDF has {len(xml_named)} embedded XML files and none uses a standard "
        f"invoice name, so which one is the invoice is ambiguous "
        f"(found: {', '.join(sorted(xml_named))})",
        code="pdf_ambiguous",
        count=len(xml_named),
    )
