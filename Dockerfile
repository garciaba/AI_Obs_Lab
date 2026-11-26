# syntax=docker/dockerfile:1
# Imagen única parametrizada para los servicios Python del lab.
#   --build-arg PACKAGES="wonder-agent"            → agente (LangGraph + FastAPI)
#   --build-arg PACKAGES="wonder-mcp wonder-seed"  → servidor MCP y seed (+ modelo de embeddings)
#   --build-arg PACKAGES="wonder-evals wonder-scenarios" → herramientas (evals, escenarios)
# Las versiones de todas las dependencias salen de uv.lock.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION=0.11.32
ARG PACKAGES
ARG PRELOAD_EMBEDDINGS=false
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /lab
COPY pyproject.toml uv.lock ./
COPY app/common app/common
COPY app/agent app/agent
COPY app/mcp-server app/mcp-server
COPY app/seed app/seed
COPY evals evals
COPY scenarios scenarios
# El secreto opcional `extra_ca` permite construir detrás de un proxy TLS corporativo:
#   docker compose build --secret id=extra_ca,src=/ruta/ca.pem   (ver docs/runbook.md)
RUN --mount=type=secret,id=extra_ca,required=false \
    --mount=type=cache,target=/root/.cache/uv \
    if [ -s /run/secrets/extra_ca ]; then \
      export SSL_CERT_FILE=/run/secrets/extra_ca PIP_CERT=/run/secrets/extra_ca \
             REQUESTS_CA_BUNDLE=/run/secrets/extra_ca; \
    fi; \
    pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv sync --frozen --no-dev --no-editable \
         $(for p in ${PACKAGES}; do printf -- '--package %s ' "$p"; done) \
    && if [ "${PRELOAD_EMBEDDINGS}" = "true" ]; then \
         HOME=/lab/home /lab/.venv/bin/python -c "from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2; ONNXMiniLM_L6_V2()(['warm-up'])"; \
       else mkdir -p /lab/home; fi

FROM ${PYTHON_IMAGE}
RUN useradd --create-home --uid 10001 --home-dir /home/app app
COPY --from=builder --chown=app:app /lab/.venv /lab/.venv
# all-MiniLM-L6-v2 (ONNX) ya descargado: el contenedor no necesita red para embeddings.
COPY --from=builder --chown=app:app /lab/home/ /home/app/
ENV PATH=/lab/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
WORKDIR /home/app
