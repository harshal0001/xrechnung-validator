"""Messages a person reads, in the languages this service speaks.

Errors are raised deep in `ingest/`, which has no business knowing what language
the caller asked for — so they carry a stable `code` and the values that go in
the sentence, and the HTTP layer renders them. The English text on the exception
stays as the canonical fallback: a code with no translation still produces a
readable message rather than a blank one.

This is why the codes are worth having beyond translation. An API consumer can
branch on `error: "pdf_no_attachment"` without parsing prose in a language it did
not choose.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "de"

#: Rendered messages, by code and language. A code absent here falls back to the
#: English text the exception was raised with.
MESSAGES: dict[str, dict[str, str]] = {
    "not_xml_or_pdf": {
        "de": "Dies ist weder XML noch ein PDF. Laden Sie eine XRechnung-XML-Datei "
        "(UBL oder UN/CEFACT CII) oder ein ZUGFeRD-PDF hoch.",
        "en": "This is neither XML nor a PDF. Upload an XRechnung XML file "
        "(UBL or UN/CEFACT CII) or a ZUGFeRD PDF.",
    },
    "not_an_einvoice": {
        "de": "'{local}' im Namensraum '{namespace}' ist keine E-Rechnung, die dieser "
        "Dienst prüft. Erwartet wird eine UBL-Invoice oder -CreditNote oder eine "
        "UN/CEFACT CrossIndustryInvoice.",
        "en": "'{local}' in namespace '{namespace}' is not an e-invoice this service "
        "validates. Expected a UBL Invoice or CreditNote, or a UN/CEFACT "
        "CrossIndustryInvoice.",
    },
    "pdf_content_not_einvoice": {
        "de": "Die in diesem PDF eingebettete Datei ('{attachment}') ist keine "
        "E-Rechnung: Ihr Wurzelelement ist '{local}' im Namensraum '{namespace}'.",
        "en": "The file embedded in this PDF ('{attachment}') is not an e-invoice: "
        "its root is '{local}' in namespace '{namespace}'.",
    },
    "pdf_no_attachment": {
        "de": "Dieses PDF enthält keine eingebettete Rechnung. Ein ZUGFeRD- oder "
        "Factur-X-PDF trägt die Rechnungs-XML als Anhang; ein eingescanntes oder "
        "gedrucktes PDF nicht.",
        "en": "This PDF carries no embedded invoice. A ZUGFeRD or Factur-X PDF has the "
        "invoice XML attached to it; a scanned or printed-to-PDF invoice does not.",
    },
    "pdf_unreadable": {
        "de": "Diese Datei ist kein lesbares PDF.",
        "en": "This file is not a readable PDF.",
    },
    "pdf_encrypted": {
        "de": "Dieses PDF ist passwortgeschützt, daher kann die Rechnung nicht gelesen werden.",
        "en": "This PDF is password-protected, so its invoice cannot be read.",
    },
    "pdf_ambiguous": {
        "de": "Dieses PDF enthält {count} eingebettete XML-Dateien und keine trägt einen "
        "Standardnamen, daher ist nicht eindeutig, welche die Rechnung ist.",
        "en": "This PDF has {count} embedded XML files and none uses a standard invoice "
        "name, so which one is the invoice is ambiguous.",
    },
    "xml_malformed": {
        "de": "Die Datei ist kein wohlgeformtes XML.",
        "en": "The file is not well-formed XML.",
    },
    "xml_empty": {
        "de": "Die Datei ist leer.",
        "en": "The file is empty.",
    },
    "payload_too_large": {
        "de": "Die Datei ist {size} Bytes groß; das Limit liegt bei {limit} Bytes.",
        "en": "The file is {size} bytes; the limit is {limit} bytes.",
    },
    "unknown_language": {
        "de": "Für '{language}' gibt es keine Erklärungen. Verfügbar: {available}.",
        "en": "No explanations in '{language}'. Available: {available}.",
    },
    "ruleset_not_found": {
        "de": "Regelsatz '{version}' nicht gefunden. Verfügbar: {available}.",
        "en": "Rule set '{version}' not found. Available: {available}.",
    },
    "profile_not_mandate_ready": {
        "de": "Dieses Dokument nutzt das Profil {profile}, das zu wenige Felder enthält, "
        "um die deutsche Rechnungspflicht zu erfüllen. Geschäftsregeln wurden nicht "
        "geprüft, weil die meisten an Daten scheitern würden, die das Profil gar "
        "nicht zu enthalten beansprucht.",
        "en": "This document uses the {profile} profile, which carries too few fields to "
        "satisfy the German e-invoicing mandate. Business rules were not evaluated, "
        "because most would fail on data the profile does not claim to contain.",
    },
}


class LocalisedError(ValueError):
    """An error whose message can be rendered in more than one language.

    The English text passed to the constructor stays as `str(exc)`, so anything
    reading the exception directly — a log line, a traceback, a test — is
    unaffected by translation existing.

    `code` is optional. Plenty of these errors describe a broken installation
    rather than something a user did — a missing rule set file, a malformed
    manifest — and nobody needs those in two languages. An empty code renders as
    the English message, which is the right outcome for them.
    """

    def __init__(self, message: str, *, code: str = "", **params: Any) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


def render(code: str, params: dict[str, Any], language: str, fallback: str) -> str:
    """The message for this code in this language, or the fallback.

    Falls back rather than raising: a missing translation should degrade to an
    English sentence, never to an empty error or a KeyError inside a handler.
    """
    template = MESSAGES.get(code, {}).get(language)
    if template is None:
        return fallback
    try:
        return template.format(**params)
    except (KeyError, IndexError):  # pragma: no cover - a template/params mismatch
        return fallback
