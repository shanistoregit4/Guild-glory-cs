# ARIYANA9X — Account Generator Dashboard (Railway)

A web dashboard for the BD_IND_GLORY Free Fire account generator bot.
No terminal prompts, no Skip option — everything runs from the dashboard.

## What it does

- **Team size** — how many accounts to create per guild (1–100, default 4).
- **Guild IDs** — one per line; multiple guilds run **in parallel**, each with
  its own progress card.
- **Region** — BD or IND.
- **Live log** — the last 100 lines, streamed in real time (Server-Sent Events),
  auto-scrolling, tagged per guild.
- **Per-guild progress** — created/total with a progress bar, state
  (running / done / error / stopped).
- **Stop all** — cancels every running guild task.

The bot itself is unchanged: it does its own version check, creates the
accounts (up to 70 at a time), saves `accounts.json`, and joins them to the
guild. It also reports the created accounts to the author's backend
(`ariyan-join-a9x.vercel.app/join`) — that is the bot's own behaviour.

## Deploy on Railway

1. Push this folder to a GitHub repo (or use Railway's "Deploy from GitHub").
   - `bd_bot.py` is part of the app — it is **never served to the browser**
     and never logged. Keep the repo private.
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
   Railway detects the `Dockerfile` automatically (`railway.json` pins it).
3. No environment variables are needed. Railway sets `$PORT` itself.
4. Wait for the build, then open the Railway-assigned domain.
   The dashboard loads at `/`.

That is it — no build command, no start command, no env vars.

## Use the dashboard

1. Enter **Team size** (accounts per guild).
2. Paste **Guild IDs**, one per line.
3. Pick **Region** (BD / IND).
4. Press **Start**. Each guild gets its own progress card; the log streams live.
5. Press **Stop all** to cancel running guilds.

Accounts are saved to `accounts.json` inside the Railway container
(ephemeral — download it from Railway's volume/shell if you need to keep it,
or attach a Railway Volume mounted at `/app`).

## Run locally (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # dashboard at http://127.0.0.1:5000
```

## Files

- `app.py` — Flask app (dashboard + API + log capture + per-guild runners).
- `bd_bot.py` — the deobfuscated bot (imported as a module, never edited).
- `homor_rudp.py`, `message_ids.py`, `StartMatch_pb2.py`, `data_pb2.py`,
  `reqClan_pb2.py`, `thunderFF_pb2.py` — the bot's own helper modules from
  the original zip (required at import time; do not remove).
- `templates/index.html` — the dashboard (inline CSS/JS, no CDN).
- `requirements.txt`, `Dockerfile`, `railway.json`.

## Notes / limits

- One gunicorn worker with 8 threads: the in-memory log buffer stays
  consistent, and the SSE stream does not block the dashboard.
- The dashboard never restarts a finished run automatically.
- If a guild errors, the others keep running; the error shows on its card.
