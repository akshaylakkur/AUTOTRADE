# =============================================================================
# Project ÆON — Autonomous Economic Operating Node
# =============================================================================
# Build:  docker build -t auton-aeon .
# Run:    docker run --rm -v $(pwd)/data:/app/data -v $(pwd)/cold_storage:/app/cold_storage --env-file .env auton-aeon
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder: install Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build tools needed by some packages (cryptography, numpy, etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        libffi-dev \
        cargo \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ---------------------------------------------------------------------------
# Stage 2 — Runtime: minimal production image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1

WORKDIR /app

# ---------------------------------------------------------------------------
# Create non-root user and data directories
# ---------------------------------------------------------------------------
RUN groupadd --system aeon \
    && useradd --system --no-create-home --gid aeon aeon \
    && mkdir -p /app/data /app/cold_storage \
    && chown -R aeon:aeon /app

# ---------------------------------------------------------------------------
# Copy installed dependencies from builder stage
# ---------------------------------------------------------------------------
COPY --from=builder /install /usr/local

# ---------------------------------------------------------------------------
# Copy application source
# ---------------------------------------------------------------------------
COPY auton/ ./auton/
COPY tests/ ./tests/

# ---------------------------------------------------------------------------
# Copy configuration files needed at build time (optional)
# ---------------------------------------------------------------------------
COPY requirements.txt ./

# Ensure the runtime user owns all application files
RUN chown -R aeon:aeon /app

# ---------------------------------------------------------------------------
# Drop root privileges
# ---------------------------------------------------------------------------
USER aeon

# ---------------------------------------------------------------------------
# Health check — verify the SQLite ledger is accessible
# ---------------------------------------------------------------------------
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sqlite3; sqlite3.connect('/app/data/aeon_ledger.db').close(); print('OK')" || exit 1

# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
ENTRYPOINT ["python", "-m", "auton.aeon"]
