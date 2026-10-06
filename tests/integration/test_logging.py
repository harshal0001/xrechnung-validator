"""What the service writes down about a request, and what it must not.

The second half is the point. An invoice carries names, bank details and
prices, and a log outlives the request by weeks. These tests validate real
reference invoices and then search every line written for anything that came
out of the document.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from lxml import etree

from xrv.api import app, logs
from xrv.api.service import ValidationService

UBL = "01.01a-INVOICE_ubl.xml"
CII = "01.01a-INVOICE_uncefact.xml"

#: A name no log line has any business containing.
FILENAME = "geheim-kunde-4711.xml"


class _Lines(logging.Handler):
    """Collects what the JSON handler would have written."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(logs._JsonLines())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))

    def events(self, name: str) -> list[dict[str, object]]:
        return [e for e in map(json.loads, self.lines) if e["event"] == name]


@pytest.fixture(scope="module")
def client(real_ruleset) -> Iterator[TestClient]:
    with TestClient(app) as started:
        yield started


@pytest.fixture
def written() -> Iterator[_Lines]:
    handler = _Lines()
    logging.getLogger("xrv").addHandler(handler)
    try:
        yield handler
    finally:
        logging.getLogger("xrv").removeHandler(handler)


def upload(client: TestClient, payload: bytes, **params: object):
    return client.post("/validate", files={"file": (FILENAME, payload)}, params=params)


def everything_in(document: bytes) -> set[str]:
    """Every value the document holds that is long enough to recognise."""
    root = etree.fromstring(document)
    values = {(element.text or "").strip() for element in root.iter()}
    values |= {value.strip() for element in root.iter() for value in element.attrib.values()}
    return {value for value in values if len(value) >= 6}


class TestRequestIds:
    def test_every_response_carries_one(self, client: TestClient) -> None:
        assert len(client.get("/healthz").headers[logs.HEADER]) >= 8

    def test_two_requests_get_two_ids(self, client: TestClient) -> None:
        first = client.get("/healthz").headers[logs.HEADER]
        assert client.get("/healthz").headers[logs.HEADER] != first

    def test_a_callers_own_id_is_kept(self, client: TestClient) -> None:
        """So their logs and ours can be lined up."""
        response = client.get("/healthz", headers={logs.HEADER: "order-2026-0042"})
        assert response.headers[logs.HEADER] == "order-2026-0042"

    @pytest.mark.parametrize("supplied", ["short", "x" * 65, 'a"b\\nc{}', "has space in it"])
    def test_an_id_that_is_not_one_is_replaced(self, client: TestClient, supplied: str) -> None:
        """It is written into a log line and a header. It is not free text."""
        response = client.get("/healthz", headers={logs.HEADER: supplied})
        assert response.headers[logs.HEADER] != supplied

    def test_an_error_response_carries_one_too(self, client: TestClient) -> None:
        response = upload(client, b"not an invoice")
        assert response.status_code == 415
        assert response.headers[logs.HEADER]


class TestWhatIsLogged:
    def test_a_validation_is_one_event_with_what_was_decided(
        self, client: TestClient, corpus: Path, written: _Lines
    ) -> None:
        document = re.sub(
            rb"<cbc:BuyerReference>[^<]*</cbc:BuyerReference>", b"", (corpus / UBL).read_bytes()
        )
        response = upload(client, document, explain="true", lang="en")

        (event,) = written.events("validation")
        assert event["request_id"] == response.headers[logs.HEADER]
        assert event["syntax"] == "UBL"
        assert event["bytes"] == len(document)
        assert event["valid"] is False
        assert "BR-DE-15" in event["rules"]
        assert event["findings"]["error"] >= 1
        assert "XRechnung" in event["scenario"]
        assert event["ruleset"] == response.json()["ruleset_version"]
        assert event["lang"] == "en"

    def test_every_api_request_is_one_line(
        self, client: TestClient, corpus: Path, written: _Lines
    ) -> None:
        upload(client, (corpus / UBL).read_bytes())
        (event,) = written.events("request")
        assert (event["method"], event["path"], event["status"]) == ("POST", "/validate", 200)
        assert event["duration_ms"] > 0

    def test_a_refusal_is_logged_by_its_code(self, client: TestClient, written: _Lines) -> None:
        upload(client, b"%PDF-1.7 not really")
        (event,) = written.events("refused")
        assert event["status"] == 422
        assert event["error"]
        assert written.events("request")[0]["status"] == 422

    def test_lines_are_json_with_a_time_and_a_level(
        self, client: TestClient, written: _Lines
    ) -> None:
        client.get("/healthz")
        for line in written.lines:
            event = json.loads(line)
            assert event["time"].endswith("+00:00")
            assert event["level"] == "info"


class TestWhatIsNotLogged:
    @pytest.mark.parametrize("name", [UBL, CII])
    def test_nothing_from_a_valid_invoice(
        self, client: TestClient, corpus: Path, written: _Lines, name: str
    ) -> None:
        document = (corpus / name).read_bytes()
        assert upload(client, document, explain="true", include_source="true").status_code == 200

        assert written.events("validation")
        leaked = {value for value in everything_in(document) if value in "\n".join(written.lines)}
        assert not leaked

    def test_nothing_from_an_invoice_that_fails(
        self, client: TestClient, corpus: Path, written: _Lines
    ) -> None:
        """A failing rule is where a value is most likely to be quoted."""
        document = (corpus / UBL).read_bytes()
        broken = document.replace(b">EUR<", b">ZZZZZZ<")
        assert broken != document
        body = upload(client, broken, explain="true").json()
        assert [f for f in body["findings"] if f["severity"] in {"fatal", "error"}]

        text = "\n".join(written.lines)
        assert "ZZZZZZ" not in text
        assert not {value for value in everything_in(document) if value in text}

    def test_not_the_filename(self, client: TestClient, corpus: Path, written: _Lines) -> None:
        upload(client, (corpus / UBL).read_bytes())
        upload(client, b"not an invoice")
        assert "geheim" not in "\n".join(written.lines)

    def test_not_what_a_refused_document_contained(
        self, client: TestClient, written: _Lines
    ) -> None:
        """The refusal sentence names the root element. The log names the code."""
        response = upload(client, b'<Geheimvertrag xmlns="urn:kunde:intern"/>')
        assert response.status_code == 415
        assert "Geheimvertrag" in response.json()["detail"]
        assert "Geheimvertrag" not in "\n".join(written.lines)
        assert "urn:kunde:intern" not in "\n".join(written.lines)


class TestAFailureNobodyHandled:
    @pytest.fixture
    def failing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def explode(self: ValidationService, payload: bytes, **_: object) -> None:
            raise RuntimeError("cannot cast 'DE79 0000 0000 1234 5678 90' to xs:decimal")

        monkeypatch.setattr(ValidationService, "validate", explode)

    def test_is_answered_with_an_id_and_not_the_message(
        self, client: TestClient, corpus: Path, failing: None
    ) -> None:
        response = upload(client, (corpus / UBL).read_bytes(), lang="en")
        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "internal_error"
        assert body["request_id"] == response.headers[logs.HEADER]
        assert body["request_id"] in body["detail"]
        assert "DE79" not in response.text

    def test_is_logged_with_where_it_happened_and_not_what_it_said(
        self, client: TestClient, corpus: Path, failing: None, written: _Lines
    ) -> None:
        """An exception message is assembled from the data being processed."""
        response = upload(client, (corpus / UBL).read_bytes())
        (event,) = written.events("error")
        assert event["level"] == "error"
        assert event["exception"] == "RuntimeError"
        assert event["request_id"] == response.headers[logs.HEADER]
        assert any("explode" in frame for frame in event["trace"])
        assert "DE79" not in "\n".join(written.lines)
        assert written.events("request")[0]["status"] == 500


class TestTheLogModule:
    def test_fields_without_a_value_are_left_out(self, written: _Lines) -> None:
        logs.log("probe", kept=0, dropped=None)
        (event,) = written.events("probe")
        assert event["kept"] == 0
        assert "dropped" not in event

    def test_configuring_twice_does_not_write_every_line_twice(self, written: _Lines) -> None:
        """Also with somebody else's handler already attached, as here."""
        logs.configure()
        before = len(logging.getLogger("xrv").handlers)
        logs.configure()
        assert len(logging.getLogger("xrv").handlers) == before
        assert not logging.getLogger("xrv").propagate

    def test_frames_say_where_and_not_what(self) -> None:
        try:
            raise ValueError("the secret value")
        except ValueError as exc:
            where = logs.frames(exc.__traceback__)
        assert any("test_frames_say_where_and_not_what" in frame for frame in where)
        assert "secret" not in " ".join(where)
