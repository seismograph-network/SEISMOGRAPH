# syntax=docker/dockerfile:1
# SEISMOGRAPH Ingestion Gateway
# ================================
# Minimal production image for the SEISMOGRAPH gateway service.
#
# Build:  docker build -t seismograph-gateway .
# Run:    docker run -p 8000:8000 seismograph-gateway
#
# For the full multi-service stack (gateway + ClickHouse + Redis):
#   docker-compose up
#
# Environment variables are documented in .env.example.

FROM python:3.11-slim

# Metadata
LABEL org.opencontainers.image.title="SEISMOGRAPH Gateway"
LABEL org.opencontainers.image.description=\
    "Privacy-preserving LLM semantic drift detection ingestion gateway"
LABEL org.opencontainers.image.version="0.2.0"

# Non-root runtime user (security best practice)
RUN groupadd --gid 1001 seismograph \
    && useradd --uid 1001 --gid seismograph \
        --no-create-home --shell /bin/false seismograph

WORKDIR /app

# ---------------------------------------------------------------------------
# Dependency layer (cache-friendly: invalidated only on pyproject.toml change)
# ---------------------------------------------------------------------------

COPY pyproject.toml ./

# Install runtime dependencies declared in pyproject.toml.
# We do not run `pip install .` here to avoid requiring the full source
# tree in the dependency layer -- only pyproject.toml is needed for the
# deps themselves. fastapi + uvicorn are gateway runtime requirements
# not listed in pyproject.toml (probe SDK deps only).
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "clickhouse-connect>=0.7" \
        "cryptography>=41.0" \
        "opentelemetry-sdk>=1.20" \
        "redis>=4.0" \
        "fastapi>=0.100" \
        "uvicorn[standard]>=0.23" \
        "httpx>=0.24"

# ---------------------------------------------------------------------------
# Application source
# Only the directories needed by the gateway process are copied.
# probe/ (client SDK) and tests/ are intentionally excluded.
# ---------------------------------------------------------------------------

COPY engine/    ./engine/
COPY gateway/   ./gateway/
COPY dashboard/ ./dashboard/
COPY data/      ./data/

# Transfer ownership to non-root user
RUN chown -R seismograph:seismograph /app

USER seismograph

EXPOSE 8000

# Healthcheck: lightweight ping of the weather endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c \
        "import urllib.request; urllib.request.urlopen(\
'http://localhost:8000/v1/weather')"

CMD ["uvicorn", "gateway.main:app",
     "--host", "0.0.0.0",
     "--port", "8000",
     "--workers", "1"]
