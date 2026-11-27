FROM python:3.11-slim

WORKDIR /app

# System deps (kept minimal; no compiler needed — all wheels)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py our_gen.py jwt.py ./
COPY templates ./templates

# The generator writes nothing at runtime (accounts are only logged, not
# saved to disk). No volume needed.
EXPOSE 8080

# Single worker (in-memory log buffer stays consistent) with threads so the
# SSE log stream does not block the dashboard. Railway sets $PORT.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-8080} app:app"]
