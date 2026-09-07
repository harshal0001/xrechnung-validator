"""FastAPI routes and request handling.

Contains no validation logic — that is `validate/`. It reads an upload, calls
the service, and turns domain errors into status codes.
"""

from xrv.api.app import app, build_service
from xrv.api.service import ValidationService

__all__ = ["ValidationService", "app", "build_service"]
