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

__all__ = [
    "MAX_BYTES",
    "ROOT_NAMESPACES",
    "Document",
    "MalformedXmlError",
    "Media",
    "PayloadTooLargeError",
    "UnsupportedDocumentError",
    "detect_media",
    "identify",
    "parse",
    "safe_parser",
    "to_text",
]
