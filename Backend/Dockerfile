# python:3.12-slim, not the 3.14 used in local dev - 3.12 has mature, broadly
# published wheels for every dependency here (notably fastembed's onnxruntime);
# nothing in this codebase needs 3.13+ syntax.
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer is cached across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source. Includes app/agents/instructions/*.md (read from disk at runtime) and
# the mock document corpus - everything the app needs is a plain file copy, no
# build step.
COPY app ./app
COPY scripts ./scripts

# Run as a non-root user; own /data too since the fastembed cache volume mounts there.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/fastembed_cache \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

# Generous start_period: a cold cache means downloading the ~130MB embedding model
# before the app reports healthy, on top of MCP tool loading and agent building.
HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=10 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

# --host 0.0.0.0 is required for the container to be reachable from outside itself -
# HOST/PORT in .env only affect the `python app/main.py` entrypoint, not this CLI form.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
