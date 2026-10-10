# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Stage 1 â€” builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder
# uv binary from the pinned distroless release image
COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Venv must be created at its final runtime path (/app/.venv) â€” entry-point
# scripts embed absolute shebangs, so building elsewhere breaks them.
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/worker/pyproject.toml apps/worker/pyproject.toml
COPY packages/python/dw_kernel/pyproject.toml packages/python/dw_kernel/pyproject.toml
COPY packages/python/dw_platform/pyproject.toml packages/python/dw_platform/pyproject.toml
COPY packages/python/dw_agent_runtime/pyproject.toml packages/python/dw_agent_runtime/pyproject.toml
COPY packages/python/dw_knowledge/pyproject.toml packages/python/dw_knowledge/pyproject.toml
COPY packages/python/dw_memory/pyproject.toml packages/python/dw_memory/pyproject.toml
COPY packages/python/dw_connectors/pyproject.toml packages/python/dw_connectors/pyproject.toml
COPY packages/python/dw_observability/pyproject.toml packages/python/dw_observability/pyproject.toml
COPY packages/python/dw_evals/pyproject.toml packages/python/dw_evals/pyproject.toml
COPY packages/python/dw_supply_chain/pyproject.toml packages/python/dw_supply_chain/pyproject.toml

# --extra parsers pulls the heavy Docling+OCR stack (multi-format ingestion).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --no-dev --package dw-worker --extra parsers

COPY packages/python packages/python
COPY apps/worker apps/worker
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package dw-worker --extra parsers

# OCR weights, baked (supply-chain ticket ai-automation/21): Docling's layout
# and table models and EasyOCR's detector and `latin_g2` recogniser (the one
# `vi` reads with). A deployed worker never downloads a model at run time: it
# refuses to start without DW_WORKER_OCR_ARTIFACTS_PATH, and with it Docling
# loads every model from there with downloads off.
RUN /app/.venv/bin/python -c "from pathlib import Path; \
from docling.utils.model_downloader import download_models; \
download_models(Path('/app/models/ocr'), with_layout=True, with_tableformer=True, \
with_code_formula=False, with_picture_classifier=False, with_rapidocr=False, \
with_easyocr=True, easyocr_languages=['vi'])"

# ---------------------------------------------------------------------------
# Stage 2 â€” runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# Security updates from the base image's distribution, applied at build time.
# The runtime stage installs no packages of its own, so everything trivy reports
# here comes from the base — and a base image is rebuilt on its own schedule,
# which is slower than a CVE with a released fix deserves. Measured on
# 2026-09-18: three HIGH `libpcre2` issues (out-of-bounds write, arbitrary code
# execution via a crafted regular expression), all already fixed upstream.
# Deliberately `upgrade`, not a pinned package list: the next one will be in a
# different library, and a list would have to be edited to find it.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

# Shared libs Docling/OCR (opencv, image codecs) load at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 dw && useradd --uid 1001 --gid dw --create-home dw

WORKDIR /app
COPY --from=builder --chown=dw:dw /app/.venv /app/.venv
COPY --from=builder --chown=dw:dw /app/models/ocr /app/models/ocr

# Versioned runtime artifacts the research wiring loads from DW_REPO_ROOT: the
# worker config, the prompt bundle, the model profiles and the fixed corpus a
# lane reads in this phase. Without them the research consumer does not
# register, which is by design but would be a surprising way to find out.
COPY --chown=dw:dw configs /app/configs
COPY --chown=dw:dw evals/fixtures /app/evals/fixtures
# The release every run this process starts records (the BGĐ review
# reconcile starts runs); a deployed worker refuses to start without it.
COPY --chown=dw:dw contracts/release /app/contracts/release

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DW_REPO_ROOT=/app \
    DW_WORKER_HEARTBEAT_FILE=/home/dw/heartbeat \
    HF_HOME=/home/dw/.cache/huggingface \
    EASYOCR_MODULE_PATH=/home/dw/.EasyOCR \
    DW_WORKER_OCR_ARTIFACTS_PATH=/app/models/ocr

USER dw

# 90s, not the 20s measured on a developer's machine. On a CI runner that has
# just built four images and is still loading two embedding models, a start
# that is merely slow was being marked unhealthy at ~95s - and compose then
# refuses to start anything that depends on it, so the smoke test failed on
# the clock rather than on the application.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD ["python", "-m", "dw_worker.health"]

CMD ["python", "-m", "dw_worker.main"]
