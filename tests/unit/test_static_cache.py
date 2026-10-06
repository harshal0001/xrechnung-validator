"""Cache headers on the built page, without needing the page to be built.

The CI job that runs these tests has no `frontend/dist` — the frontend is built
in a job of its own — so the mount in `app.py` is absent there and nothing
exercised the class behind it. It is tested here on a directory made for the
purpose.

The behaviour is worth pinning because getting it wrong is invisible: the site
keeps working and keeps serving the previous build.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xrv.api.app import _CachedStatics


@pytest.fixture
def page(tmp_path: Path) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>page</title>")
    (tmp_path / "assets" / "index-BiE98Yrt.js").write_text("console.log('built')")
    (tmp_path / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")

    app = FastAPI()
    app.mount("/", _CachedStatics(directory=tmp_path, html=True))
    return TestClient(app)


def test_a_fingerprinted_asset_is_cached_for_good(page: TestClient) -> None:
    """Its name changes when its content does, so it can never be stale."""
    response = page.get("/assets/index-BiE98Yrt.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_the_page_that_names_the_bundle_is_always_revalidated(page: TestClient, path: str) -> None:
    """A browser holding an old index.html keeps loading the old bundle however
    many times the server is rebuilt."""
    response = page.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_a_file_whose_name_does_not_change_is_revalidated_too(page: TestClient) -> None:
    assert page.get("/favicon.svg").headers["cache-control"] == "no-cache"
