FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Create non-root user and data directory for SQLite WAL persistence
RUN groupadd -r jobrecall && useradd -r -g jobrecall jobrecall \
    && mkdir -p /app/data \
    && chown -R jobrecall:jobrecall /app

COPY pyproject.toml /app/
RUN pip install --upgrade pip && pip install .

COPY . /app
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh && chown -R jobrecall:jobrecall /app

USER jobrecall

EXPOSE 8765

ENTRYPOINT ["/app/entrypoint.sh"]
