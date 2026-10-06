"""One JSON line per thing that happened — and never the invoice.

The service had no logs at all, so a failure in production left nothing to read.
What it writes now is deliberately narrow. An invoice is somebody's commercial
record: names, bank details, prices. None of that is needed to operate the
service, so none of it is logged — not the document, not a filename, not a value
a rule tripped over, not the text of an exception, which Saxon is happy to build
out of whatever value it could not cast.

What is logged is what the service decided: which rules fired, how the document
was classified, how long it took. Rule identifiers and counts say everything an
operator needs and nothing about whose invoice it was.

Lines go to stdout as JSON, which is what Lambda ships to CloudWatch and what a
metric filter can count without a parser.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
import uuid
from datetime import UTC, datetime
from types import TracebackType

#: Sent back on every response, and accepted on a request so a caller's own
#: logs and these can be lined up.
HEADER = "X-Request-Id"

_LOGGER = logging.getLogger("xrv")

#: What an incoming id may look like. It ends up in a log line and a response
#: header, so it is not free text: anything else is replaced, not escaped.
_ACCEPTABLE = re.compile(r"[A-Za-z0-9._-]{8,64}")


class _JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        return json.dumps(line, ensure_ascii=False, separators=(",", ":"), default=str)


class _Stdout(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever stdout is now, not what it was when the handler was made.

    A handler that captures the stream once keeps writing to it after something
    has swapped stdout out from under it, which a test runner does per test.
    """

    @property
    def stream(self):  # type: ignore[no-untyped-def]
        return sys.stdout

    @stream.setter
    def stream(self, _: object) -> None:
        pass


_handler: logging.Handler | None = None


def configure() -> None:
    """Attach the JSON handler once. Safe to call again.

    The logger does not propagate: Lambda's runtime hangs its own handler on the
    root logger, and every line would otherwise be written twice in two formats.
    """
    global _handler
    if _handler is None:
        _handler = _Stdout()
        _handler.setFormatter(_JsonLines())
        _LOGGER.addHandler(_handler)
        _LOGGER.propagate = False
    _LOGGER.setLevel(os.environ.get("XRV_LOG_LEVEL", "INFO").upper())


def log(event: str, /, *, level: int = logging.INFO, **fields: object) -> None:
    """Write one event. Fields with no value are left out rather than logged as null."""
    kept = {name: value for name, value in fields.items() if value is not None}
    _LOGGER.log(level, event, extra={"fields": kept})


def request_id(supplied: str | None) -> str:
    """The caller's id if it is one, otherwise a new one."""
    if supplied and _ACCEPTABLE.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


def frames(tb: TracebackType | None) -> list[str]:
    """Where an exception came from, without what it said.

    File, line and function locate a fault. The message is left out because it
    is assembled from the data being processed — a cast error quotes the value
    it could not cast.
    """
    return [
        f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
        for frame in traceback.extract_tb(tb)
    ]
