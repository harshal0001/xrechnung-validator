# Service image.
#
# The point of this file is that the SAME image runs locally, on Lambda (via the
# Web Adapter extension), on Cloud Run, and on Render — with no code branches.
# That is what keeps the hosting decision reversible.
#
#   docker build -t xrv:spike .
#   docker run --rm xrv:spike python scripts/spike_saxon.py
#
# Multi-arch check (the decision this spike exists to make):
#   docker build --platform linux/arm64 -t xrv:spike-arm64 .

FROM python:3.12-slim AS base

# libxml2/libxslt for lxml, qpdf for pikepdf, and a JRE-free Saxon — SaxonC-HE
# bundles its own native library, so there is deliberately no Java here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 \
        libxslt1.1 \
        qpdf \
    && rm -rf /var/lib/apt/lists/*

# uv gives a reproducible install from the lockfile and a much faster layer.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/usr/local

# ---- dependency layer -------------------------------------------------------
# Copied alone so a source edit does not reinstall a 42 MB Saxon wheel.
COPY pyproject.toml ./
RUN uv pip install --system \
        "saxonche>=13.0.0,<14" \
        "lxml>=5.3,<7" \
        "pikepdf>=9,<10" \
        "fastapi>=0.115,<1" \
        "uvicorn[standard]>=0.32,<1" \
        "pydantic>=2.9,<3" \
        "pydantic-settings>=2.6,<3"

# ---- app layer --------------------------------------------------------------
# The ruleset IS baked in. A cold start that also downloads and unzips a ruleset
# is a cold start nobody sits through.
#
# The test corpus is deliberately NOT baked in. It is 5.6 MB of test data with no
# place in a production image, so the spike mounts it instead:
#   -v "$PWD/tests/corpus/_downloaded:/app/tests/corpus/_downloaded:ro"
# That keeps the image exactly production-shaped, which is what makes the
# measured image size worth writing down.
COPY scripts/ ./scripts/
COPY rulesets/ ./rulesets/

# COPY is happy to copy a directory containing nothing but a manifest, which is
# exactly what a fresh clone has — manifest.json is committed, the resources are
# not. Without this the build goes green and the image only fails when someone
# uploads an invoice.
RUN python scripts/fetch_ruleset.py --verify /app/rulesets

ENV XRV_RULESET_DIR=/app/rulesets

# Replaced with the uvicorn CMD once the API lands. For now the image's job is to
# prove the native library loads and the stylesheets run.
CMD ["python", "scripts/spike_saxon.py"]
