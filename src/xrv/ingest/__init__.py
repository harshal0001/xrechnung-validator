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
    DEFAULT_MAX_BYTES,
    MAX_BYTES,
    MalformedXmlError,
    PayloadTooLargeError,
    configured_max_bytes,
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
    "DEFAULT_MAX_BYTES",
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
    "configured_max_bytes",
    "detect_media",
    "detect_profile",
    "extract_xml",
    "identify",
    "parse",
    "read_guideline_id",
    "safe_parser",
    "to_text",
]
