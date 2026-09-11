"""Parsing bytes that arrived from outside.

Everything in this module assumes the payload is hostile until parsed. An upload
endpoint that hands untrusted XML to a default parser is the classic way to leak
files off a server or hang it, so the parser here is configured to refuse both
and the configuration is asserted by tests rather than trusted.
"""

from __future__ import annotations

import os

from lxml import etree

from xrv.core import LocalisedError

#: Larger than any real invoice by a wide margin, small enough that a malicious
#: upload cannot exhaust memory before parsing even begins. A ZUGFeRD PDF is the
#: bulky case and still sits far below this.
#:
#: A host may refuse uploads before they reach us, and then the caller gets the
#: platform's error instead of ours. Behind API Gateway and Lambda the ceiling is
#: ~4.4 MB measured, because the body is base64-encoded into Lambda's 6 MB
#: invocation payload. Setting ``XRV_MAX_UPLOAD_BYTES`` below a host's ceiling
#: keeps the refusal here, in the caller's language and with a code the frontend
#: can render.
DEFAULT_MAX_BYTES = 16 * 1024 * 1024


def configured_max_bytes(default: int = DEFAULT_MAX_BYTES) -> int:
    """The upload ceiling, lowered by the host if it has a tighter one."""
    raw = os.environ.get("XRV_MAX_UPLOAD_BYTES", "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


MAX_BYTES = configured_max_bytes()


class PayloadTooLargeError(LocalisedError):
    """The upload exceeds the size a real invoice could plausibly need."""


class MalformedXmlError(LocalisedError):
    """The bytes are not well-formed XML."""


def safe_parser() -> etree.XMLParser:
    """A parser with every remote and expansion route switched off.

    - `resolve_entities=False` stops XXE: an entity pointing at /etc/passwd or an
      internal URL is left unexpanded instead of being fetched and inlined.
    - `no_network=True` stops a DTD or schema reference reaching the network.
    - `load_dtd=False` means an external DTD is never fetched to begin with.
    - `huge_tree=False` keeps libxml2's depth and size limits in place, which is
      what stops nested-entity expansion attacks.
    - `recover=False` because silently repairing a broken invoice and validating
      the repair would report on a document nobody sent.
    """
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        recover=False,
    )


def parse(payload: bytes) -> etree._ElementTree:
    """Parse untrusted bytes into a tree, or say precisely why not.

    Bytes rather than text on purpose: an XML declaration may name an encoding
    other than UTF-8, and decoding before parsing would either guess wrong or
    throw away that declaration.
    """
    if len(payload) > MAX_BYTES:
        raise PayloadTooLargeError(
            f"payload is {len(payload):,} bytes; the limit is {MAX_BYTES:,}",
            code="payload_too_large",
            size=f"{len(payload):,}",
            limit=f"{MAX_BYTES:,}",
        )
    if not payload.strip():
        raise MalformedXmlError("payload is empty", code="xml_empty")

    try:
        root = etree.fromstring(payload, parser=safe_parser())
    except etree.XMLSyntaxError as exc:
        raise MalformedXmlError(f"not well-formed XML: {exc}", code="xml_malformed") from exc
    return root.getroottree()


def to_text(tree: etree._ElementTree) -> str:
    """Serialise a parsed tree to text for a consumer that only takes strings.

    Round-tripping through the parsed tree rather than decoding the original
    bytes is what makes a non-UTF-8 document work: the declared encoding was
    already honoured on the way in.
    """
    return etree.tostring(tree, encoding="unicode")
