# syntax=docker/dockerfile:1

# Stage 1: Dependencies
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH=/root/.local/bin:$PATH \
    AEON_VAULT_KEY=""

WORKDIR /app

COPY --from=builder /root/.local /root/.local
COPY auton/ ./auton/
COPY tests/ ./tests/

CMD ["python", "-m", "auton.aeon"]
