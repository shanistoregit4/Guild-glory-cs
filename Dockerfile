FROM python:3.11-slim

WORKDIR /app

# System deps (kept minimal; no compiler needed — all wheels)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py bd_bot.py homor_rudp.py message_ids.py StartMatch_pb2.py data_pb2.py reqClan_pb2.py thunderFF_pb2.py ./
COPY templates ./templates

# The bot writes accounts.json / devices.json in the working dir at runtime.
EXPOSE 8080

# Single worker (in-memory log buffer stays consistent) with threads so the
# SSE log stream does not block the dashboard. Railway sets $PORT.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-8080} app:app"]
