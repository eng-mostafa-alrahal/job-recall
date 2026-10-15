#!/bin/sh
set -e

mkdir -p /app/data

echo "[JobRecall] Running Alembic database migrations..."
alembic upgrade head

echo "[JobRecall] Starting FastAPI server on ${JOBRECALL_HOST:-0.0.0.0}:${JOBRECALL_PORT:-8765}..."
exec uvicorn app.main:app --host "${JOBRECALL_HOST:-0.0.0.0}" --port "${JOBRECALL_PORT:-8765}"
