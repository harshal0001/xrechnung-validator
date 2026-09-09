"""The HTTP surface, against the real rule set.

Nothing is mocked: the app that runs here compiles the same schemas and
stylesheets it would in production and validates real reference invoices. The
tests that matter most are the error mappings — a validation service is judged
on what it says when something is wrong at least as much as when nothing is.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xrv.api import app
from xrv.ingest import MAX_BYTES

UBL = "01.01a-INVOICE_ubl.xml"
CII = "01.01a-INVOICE_uncefact.xml"


@pytest.fixture(scope="module")
def client(real_ruleset) -> Iterator[TestClient]:
    """A started app, so startup compilation happens once for the module."""
    with TestClient(app) as started:
        yield started


def upload(client: TestClient, payload: bytes, name: str = "invoice.xml", **params):
    return client.post("/validate", files={"file": (name, payload)}, params=params)


class TestValidating:
    @pytest.mark.parametrize(("name", "syntax"), [(UBL, "UBL"), (CII, "CII")])
    def test_a_valid_invoice_comes_back_clean(
        self, client: TestClient, corpus: Path, name: str, syntax: str
    ) -> None:
        response = upload(client, (corpus / name).read_bytes())
        assert response.status_code == 200
        body = response.json()
        assert body["syntax"] == syntax
        assert body["source"] == "xml"
        assert not [f for f in body["findings"] if f["severity"] in {"fatal", "error"}]

    def test_every_response_carries_its_provenance(self, client: TestClient, corpus: Path) -> None:
        """A result that cannot say which rules produced it stops meaning
        anything the moment the rules move."""
        body = upload(client, (corpus / UBL).read_bytes()).json()
        assert body["ruleset_version"]
        assert len(body["ruleset_sha256"]) == 64
        assert body["duration_ms"] > 0

    def test_a_broken_invoice_reports_the_rule(self, client: TestClient, corpus: Path) -> None:
        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        from lxml import etree

        tree = etree.parse(str(corpus / UBL))
        tree.getroot().remove(tree.getroot().find(f"{{{cbc}}}BuyerReference"))
        payload = etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

        body = upload(client, payload).json()
        assert "BR-DE-15" in {f["rule_id"] for f in body["findings"]}

    def test_a_zugferd_pdf_is_unwrapped(
        self, client: TestClient, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        body = upload(client, make_zugferd_pdf(cii_invoice), name="invoice.pdf").json()
        assert body["source"] == "zugferd-pdf"
        assert body["syntax"] == "CII"
        assert body["profile"] == "XRECHNUNG"
        assert body["mandate_ready"] is True

    def test_a_thin_profile_is_flagged_rather_than_drowned_in_failures(
        self, client: TestClient, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """MINIMUM has no line items. Reporting dozens of rule failures would
        read as "your invoice is broken" when the truth is "this profile is not
        an invoice"."""
        thin = cii_invoice.replace(
            b"urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
            b"urn:factur-x.eu:1p0:minimum",
        )
        body = upload(client, make_zugferd_pdf(thin), name="invoice.pdf").json()
        assert body["mandate_ready"] is False
        assert len(body["findings"]) == 1
        assert body["findings"][0]["rule_id"] == "PROFILE-NOT-MANDATE-READY"


class TestExplanations:
    def test_explain_is_off_by_default(self, client: TestClient, corpus: Path) -> None:
        """The fast path stays fast; explanations are opt-in."""
        body = upload(client, (corpus / UBL).read_bytes()).json()
        assert all(f["explanation"] is None for f in body["findings"])

    def test_a_valid_invoice_gets_no_explanations(self, client: TestClient, corpus: Path) -> None:
        """Nothing blocking fires on a reference invoice, so there is nothing to
        explain — only the informational BR-DE-TMP-32, which has no entry."""
        body = upload(client, (corpus / UBL).read_bytes(), explain="true").json()
        assert all(f["explanation"] is None for f in body["findings"])

    def test_a_reviewed_rule_is_explained_in_two_fields(
        self, client: TestClient, corpus: Path
    ) -> None:
        """The whole stack, end to end: a broken invoice comes back with the
        reviewed German attached, and the grounded restatement and the sourced
        context arrive as separate fields rather than one concatenated string."""
        from lxml import etree

        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        tree = etree.parse(str(corpus / UBL))
        tree.getroot().remove(tree.getroot().find(f"{{{cbc}}}BuyerReference"))
        payload = etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

        body = upload(client, payload, explain="true").json()
        finding = next(f for f in body["findings"] if f["rule_id"] == "BR-DE-15")
        assert finding["explanation"] == "Die Käuferreferenz (BT-10) fehlt."
        assert "Leitweg-ID" in finding["context"]
        assert finding["context"] not in finding["explanation"]

    def test_explanations_stay_off_unless_asked(self, client: TestClient, corpus: Path) -> None:
        """Even for a reviewed rule, the default path serves none."""
        from lxml import etree

        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        tree = etree.parse(str(corpus / UBL))
        tree.getroot().remove(tree.getroot().find(f"{{{cbc}}}BuyerReference"))
        payload = etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

        body = upload(client, payload).json()
        finding = next(f for f in body["findings"] if f["rule_id"] == "BR-DE-15")
        assert finding["explanation"] is None
        assert finding["context"] is None

    def test_the_rule_text_is_always_there_regardless(
        self, client: TestClient, corpus: Path
    ) -> None:
        """An explanation improves a finding; it never replaces having one."""
        body = upload(client, (corpus / UBL).read_bytes(), explain="true").json()
        assert all(f["rule_text"] for f in body["findings"])

    def test_explanation_and_context_are_separate_fields(
        self, client: TestClient, corpus: Path
    ) -> None:
        """One restates the rule and is checkable against it; the other is
        editorial. Concatenating them in the response would hand a consumer no
        way to tell which is which."""
        schema = client.get("/openapi.json").json()
        finding = schema["components"]["schemas"]["Finding"]["properties"]
        assert "explanation" in finding
        assert "context" in finding

        body = upload(client, (corpus / UBL).read_bytes(), explain="true").json()
        assert all("context" in f for f in body["findings"])


class TestReturningTheValidatedSource:
    """`include_source` exists so a client can show what was actually checked."""

    def test_it_is_omitted_by_default(self, client: TestClient, corpus: Path) -> None:
        """It is the caller's own document coming back; most callers have it."""
        assert upload(client, (corpus / UBL).read_bytes()).json()["source_xml"] is None

    def test_it_returns_the_uploaded_xml(self, client: TestClient, corpus: Path) -> None:
        body = upload(client, (corpus / UBL).read_bytes(), include_source="true").json()
        assert body["source_xml"] is not None
        assert "Invoice" in body["source_xml"]

    def test_for_a_pdf_it_returns_the_extracted_xml_not_the_pdf(
        self, client: TestClient, make_zugferd_pdf, cii_invoice: bytes
    ) -> None:
        """The whole reason the field exists: the invoice inside a ZUGFeRD PDF is
        the thing that was validated, and the client has no other way to see it."""
        pdf = make_zugferd_pdf(cii_invoice)
        body = upload(client, pdf, name="invoice.pdf", include_source="true").json()
        assert body["source_xml"] is not None
        assert not body["source_xml"].startswith("%PDF")
        assert "CrossIndustryInvoice" in body["source_xml"]

    def test_the_findings_point_into_the_source_it_returns(
        self, client: TestClient, corpus: Path
    ) -> None:
        """A location that names an element absent from the returned source would
        make the client's highlighting point at nothing."""
        from lxml import etree

        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        tree = etree.parse(str(corpus / UBL))
        tree.getroot().remove(tree.getroot().find(f"{{{cbc}}}BuyerReference"))
        payload = etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

        body = upload(client, payload, include_source="true").json()
        source = body["source_xml"]
        for finding in body["findings"]:
            if not finding["xpath"].startswith("/"):
                continue
            leaf = finding["xpath"].rstrip("]0123456789[").split("}")[-1].split(":")[-1]
            assert leaf in source, f"{finding['rule_id']} points at {leaf}, absent from source"


class TestLanguages:
    def _broken(self, corpus: Path) -> bytes:
        from lxml import etree

        cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
        tree = etree.parse(str(corpus / UBL))
        tree.getroot().remove(tree.getroot().find(f"{{{cbc}}}BuyerReference"))
        return etree.tostring(tree, xml_declaration=True, encoding="UTF-8")

    def test_german_is_the_default(self, client: TestClient, corpus: Path) -> None:
        body = upload(client, self._broken(corpus), explain="true").json()
        finding = next(f for f in body["findings"] if f["rule_id"] == "BR-DE-15")
        assert finding["explanation"] == "Die Käuferreferenz (BT-10) fehlt."

    def test_english_serves_exactly_what_has_been_reviewed(
        self, client: TestClient, corpus: Path, en_catalogue
    ) -> None:
        """The gate as a property rather than a snapshot: a finding gets an
        English explanation if and only if its rule's English entry has been
        approved for the text now in force. True at 0 reviewed, true at 25."""
        body = upload(client, self._broken(corpus), explain="true", lang="en").json()
        for finding in body["findings"]:
            entry = en_catalogue.entries.get(finding["rule_id"])
            reviewed = entry is not None and entry.is_reviewed_for(finding["rule_text"])
            assert (finding["explanation"] is not None) == reviewed, finding["rule_id"]
            if reviewed:
                assert finding["explanation"] == entry.what

    def test_an_unknown_language_is_refused_with_the_list(
        self, client: TestClient, corpus: Path
    ) -> None:
        response = upload(client, self._broken(corpus), lang="fr")
        assert response.status_code == 400
        assert response.json()["error"] == "unknown_language"
        assert "de" in response.json()["detail"] and "en" in response.json()["detail"]

    def test_rulesets_advertise_their_explanation_languages(self, client: TestClient) -> None:
        entries = client.get("/rulesets").json()["rulesets"]
        loaded = next(e for e in entries if e["loaded"])
        assert set(loaded["explanation_languages"]) >= {"de", "en"}


class TestErrorMapping:
    def test_an_image_is_unsupported_media(self, client: TestClient) -> None:
        response = upload(client, b"\x89PNG\r\n\x1a\n", name="scan.png")
        assert response.status_code == 415
        assert response.json()["error"] == "unsupported_document"

    def test_a_document_that_is_not_an_invoice(self, client: TestClient) -> None:
        order = b'<Order xmlns="urn:oasis:names:specification:ubl:schema:xsd:Order-2"/>'
        assert upload(client, order).status_code == 415

    def test_malformed_xml_is_unprocessable(self, client: TestClient) -> None:
        response = upload(client, b"<Invoice>")
        assert response.status_code == 422
        assert response.json()["error"] == "malformed_xml"

    def test_a_pdf_with_no_invoice_in_it(self, client: TestClient, make_zugferd_pdf) -> None:
        response = upload(client, make_zugferd_pdf(None), name="scan.pdf")
        assert response.status_code == 422
        assert response.json()["error"] == "unreadable_pdf"

    def test_an_oversized_upload_is_refused(self, client: TestClient) -> None:
        response = upload(client, b"<a/>" + b"\0" * (MAX_BYTES + 1))
        assert response.status_code == 413

    def test_an_unknown_ruleset_version(self, client: TestClient, corpus: Path) -> None:
        response = upload(client, (corpus / UBL).read_bytes(), ruleset="1999-01-01")
        assert response.status_code == 404
        assert response.json()["error"] == "ruleset_not_found"

    def test_errors_explain_themselves(self, client: TestClient) -> None:
        """A stack trace is not an error message someone can act on."""
        detail = upload(client, b"\x89PNG\r\n\x1a\n", name="scan.png").json()["detail"]
        assert "XML" in detail or "ZUGFeRD" in detail


class TestOperationalEndpoints:
    def test_healthz_reports_warm_state(self, client: TestClient) -> None:
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["warm"] is True
        assert body["rulesets_loaded"]

    def test_healthz_does_no_validation(self, client: TestClient) -> None:
        """A probe that validated a document would queue behind real work and
        start failing under exactly the load it exists to report on."""
        import time

        started = time.perf_counter()
        for _ in range(20):
            assert client.get("/healthz").status_code == 200
        assert (time.perf_counter() - started) < 1.0

    def test_rulesets_lists_provenance(self, client: TestClient) -> None:
        entries = client.get("/rulesets").json()["rulesets"]
        assert entries
        assert all(len(entry["sha256"]) == 64 for entry in entries)
        assert any(entry["loaded"] for entry in entries)

    def test_the_frontend_is_served_when_it_has_been_built(self, client: TestClient) -> None:
        """One container, one origin, no CORS.

        The built UI is not committed, so whether it is mounted depends on
        whether `npm run build` has run. Asserted conditionally rather than
        skipped, so this says something in both cases: with a build, the page is
        served; without one, the API still answers on its own paths.
        """
        from xrv.api.app import FRONTEND_DIST

        mounted = {route.name for route in app.routes if getattr(route, "name", None)}
        if FRONTEND_DIST.is_dir():
            assert "frontend" in mounted
            page = client.get("/")
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
        else:
            assert "frontend" not in mounted
        assert client.get("/healthz").status_code == 200

    def test_the_schema_documents_the_endpoint(self, client: TestClient) -> None:
        schema = client.get("/openapi.json").json()
        assert "/validate" in schema["paths"]
        responses = schema["paths"]["/validate"]["post"]["responses"]
        assert {"200", "413", "415", "422"} <= set(responses)
