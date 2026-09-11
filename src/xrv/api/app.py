"""HTTP surface: one validation endpoint, plus provenance and liveness.

Deliberately thin. It reads the upload, hands it to the service, and translates
domain errors into status codes — no validation logic lives here.

Two things it does own, because they are properties of being on the network
rather than of validating an invoice: refusing an oversized body before reading
all of it, and keeping the liveness check off the Saxon path so a health probe
cannot be blocked behind a document being validated.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from xrv.api.service import ValidationService
from xrv.core import ValidationReport, render
from xrv.explain import DEFAULT_LANGUAGE, LANGUAGES, LanguageNotAvailableError
from xrv.ingest import MAX_BYTES, MalformedXmlError, PayloadTooLargeError, UnsupportedDocumentError
from xrv.ingest.zugferd import ZugferdError
from xrv.rules import RulesetNotFoundError

#: Read in chunks so a huge upload is refused partway rather than after the
#: whole thing has been buffered. FastAPI would otherwise happily hold it all.
CHUNK = 1 << 16

#: The built frontend, when there is one. Serving it from the same process means
#: one container, one origin and no CORS — the demo is a URL, not a setup guide.
#: Absent in development, where Vite serves the UI and proxies the API.
FRONTEND_DIST = Path("frontend/dist")

DESCRIPTION = """
Validates German e-invoices against EN 16931 and the KoSIT XRechnung rule set,
and explains each failure in plain German.

Accepts UBL 2.1 XML, UN/CEFACT CII XML, or a ZUGFeRD / Factur-X PDF. Every
response records which rule set version produced it and the SHA-256 of that
version's bytes, so a result stays meaningful after the rules move.
""".strip()


def build_service() -> ValidationService:
    return ValidationService(catalogue_dir=Path("explanations"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Compile schemas and stylesheets before the first request.

    ~2.4s of work that is unbilled during Lambda's init phase and would
    otherwise land on whoever arrives first.
    """
    service = build_service()
    app.state.service = service
    app.state.warm_version = service.warm()
    try:
        yield
    finally:
        service.close()


app = FastAPI(
    title="XRechnung validator",
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
)


def get_service(request: Request) -> ValidationService:
    service = getattr(request.app.state, "service", None)
    if not isinstance(service, ValidationService):  # pragma: no cover - lifespan did not run
        raise HTTPException(status_code=503, detail="the service is still starting")
    return service


Service = Annotated[ValidationService, Depends(get_service)]


async def read_capped(upload: UploadFile) -> bytes:
    """Read an upload, refusing it once it exceeds the limit."""
    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(CHUNK):
        total += len(chunk)
        if total > MAX_BYTES:
            raise PayloadTooLargeError(
                f"the upload exceeds {MAX_BYTES:,} bytes, "
                "which is far larger than any real invoice",
                code="upload_too_large",
                limit=f"{MAX_BYTES:,}",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.post(
    "/validate",
    response_model=ValidationReport,
    summary="Validate an invoice",
    responses={
        400: {"description": "A language this service has no explanations in"},
        413: {"description": "Upload too large"},
        415: {"description": "Not an e-invoice this service validates"},
        422: {"description": "Recognisable but unreadable — malformed XML, or an unusable PDF"},
    },
)
async def validate(
    file: Annotated[UploadFile, File(description="An XRechnung XML file or a ZUGFeRD PDF")],
    service: Service,
    explain: Annotated[
        bool,
        Query(description="Attach a plain-German explanation where a reviewed one exists"),
    ] = False,
    ruleset: Annotated[
        str | None,
        Query(description="Rule set version; defaults to the newest available"),
    ] = None,
    lang: Annotated[
        str,
        Query(description="Language for explanations: de or en"),
    ] = DEFAULT_LANGUAGE,
    include_source: Annotated[
        bool,
        Query(
            description=(
                "Return the XML that was validated. For a ZUGFeRD PDF that is the "
                "extracted attachment, which the caller has no other way to see."
            )
        ),
    ] = False,
) -> ValidationReport:
    payload = await read_capped(file)
    return service.validate(
        payload,
        explain=explain,
        version=ruleset,
        language=lang,
        include_source=include_source,
    )


@app.get("/rulesets", summary="Rule set versions and their provenance")
def rulesets(service: Service) -> dict[str, object]:
    return {"rulesets": service.rulesets()}


@app.get("/healthz", summary="Liveness")
def healthz(request: Request) -> dict[str, object]:
    """Deliberately does no validation.

    A health probe that ran a document would queue behind real work and start
    failing under exactly the load it exists to report on.
    """
    service = getattr(request.app.state, "service", None)
    return {
        "status": "ok",
        "warm": bool(service and service.ready_versions),
        "rulesets_loaded": list(service.ready_versions) if service else [],
    }


def _requested_language(request: Request) -> str:
    """The language this request asked for, for rendering its error.

    Read from the query rather than Accept-Language: the caller already states
    it there for explanations, and an error in one language beside explanations
    in another would be worse than either alone.
    """
    asked = request.query_params.get("lang", DEFAULT_LANGUAGE)
    return asked if asked in LANGUAGES else DEFAULT_LANGUAGE


def _problem(request: Request, status: int, kind: str, exc: Exception) -> JSONResponse:
    """Render a domain error as a problem the caller can act on.

    `error` is the stable code — an API consumer branches on that rather than
    parsing prose. `detail` is the sentence, in the requested language where a
    translation exists and in English where it does not.
    """
    code = getattr(exc, "code", "") or kind
    params = getattr(exc, "params", {}) or {}
    detail = render(code, params, _requested_language(request), fallback=str(exc))
    return JSONResponse(status_code=status, content={"error": kind, "detail": detail})


@app.exception_handler(UnsupportedDocumentError)
async def _unsupported(request: Request, exc: UnsupportedDocumentError) -> JSONResponse:
    return _problem(request, 415, "unsupported_document", exc)


@app.exception_handler(MalformedXmlError)
async def _malformed(request: Request, exc: MalformedXmlError) -> JSONResponse:
    return _problem(request, 422, "malformed_xml", exc)


@app.exception_handler(ZugferdError)
async def _zugferd(request: Request, exc: ZugferdError) -> JSONResponse:
    return _problem(request, 422, "unreadable_pdf", exc)


@app.exception_handler(PayloadTooLargeError)
async def _too_large(request: Request, exc: PayloadTooLargeError) -> JSONResponse:
    return _problem(request, 413, "payload_too_large", exc)


@app.exception_handler(RulesetNotFoundError)
async def _no_ruleset(request: Request, exc: RulesetNotFoundError) -> JSONResponse:
    return _problem(request, 404, "ruleset_not_found", exc)


@app.exception_handler(LanguageNotAvailableError)
async def _no_language(request: Request, exc: LanguageNotAvailableError) -> JSONResponse:
    return _problem(request, 400, "unknown_language", exc)


class _CachedStatics(StaticFiles):
    """Static files with cache headers that match how the build names them.

    Vite fingerprints every asset, so `assets/index-BiE98Yrt.js` can be cached
    forever — the name changes when the content does. `index.html` cannot: it is
    the file that *names* the current bundle, and a browser holding an old copy
    keeps loading an old app no matter how many times the server is rebuilt.

    That is not hypothetical. Without this, a rebuilt UI kept serving the
    previous bundle from cache and looked like the fixes had not been made.
    """

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        fingerprinted = path.startswith("assets/")
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if fingerprinted else "no-cache"
        )
        return response


# Mounted last so every API route above wins the path it owns. html=True serves
# index.html for unknown paths, which is what a single-page app needs.
if FRONTEND_DIST.is_dir():
    app.mount("/", _CachedStatics(directory=FRONTEND_DIST, html=True), name="frontend")
