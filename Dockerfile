FROM python:3.11-slim

WORKDIR /app

# System deps (kept minimal; no compiler needed — all wheels)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# app.py: the dashboard (Flask UI + log/SSE + per-guild runs).
# our_gen.py: OUR account generator (replaces only the bot's creation).
# jwt.py: kept, unused by the run path (the original bot does its own login).
# bd_bot.py + message_ids.py + reqClan_pb2.py + data_pb2.py +
# StartMatch_pb2.py + thunderFF_pb2.py + homor_rudp.py: the ORIGINAL BD
# bot, byte-identical to the author's zip. Do not edit these.
COPY app.py our_gen.py jwt.py bd_bot.py message_ids.py reqClan_pb2.py data_pb2.py StartMatch_pb2.py thunderFF_pb2.py homor_rudp.py ./
COPY templates ./templates

# The original bot may write small files at runtime (e.g. devices.json);
# the container filesystem is writable, no volume needed.
EXPOSE 8080

# Single worker (in-memory log buffer stays consistent) with threads so the
# SSE log stream does not block the dashboard. Railway sets $PORT.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT:-8080} app:app"]
