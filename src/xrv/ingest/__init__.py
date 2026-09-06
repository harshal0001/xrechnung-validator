"""Sniffing, routing and safe parsing of whatever was uploaded.

Owns the untrusted boundary. Knows about document shapes; knows nothing about
business rules.
"""

from xrv.ingest.detect import (
    ROOT_NAMESPACES,
    Document,
    Media,
    UnsupportedDocumentError,
    detect_media,
    identify,
)
from xrv.ingest.xml import (
    MAX_BYTES,
    MalformedXmlError,
    PayloadTooLargeError,
    parse,
    safe_parser,
    to_text,
)
from xrv.ingest.zugferd import (
    KNOWN_ATTACHMENTS,
    Profile,
    ZugferdError,
    detect_profile,
    extract_xml,
    read_guideline_id,
)

__all__ = [
    "KNOWN_ATTACHMENTS",
    "MAX_BYTES",
    "ROOT_NAMESPACES",
    "Document",
    "MalformedXmlError",
    "Media",
    "PayloadTooLargeError",
    "Profile",
    "UnsupportedDocumentError",
    "ZugferdError",
    "detect_media",
    "detect_profile",
    "extract_xml",
    "identify",
    "parse",
    "read_guideline_id",
    "safe_parser",
    "to_text",
]
