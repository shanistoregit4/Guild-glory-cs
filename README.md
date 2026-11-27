# SHANI VIP — Account Generator Dashboard (Railway)

A web dashboard for our own Free Fire account generator (the one from the
emote bot / fast_gen). No terminal prompts, no Skip option — everything runs
from the dashboard.

## What it does

- **Team size** — how many accounts to create per guild (1–100, default 4).
- **Guild IDs** — one per line; multiple guilds run **in parallel**, each with
  its own progress card.
- **Region** — BD or IND (passed to the generator; name prefix `shani`,
  password prefix `pak`).
- **Live log** — the last 100 lines, streamed in real time (Server-Sent Events),
  auto-scrolling, tagged per guild.
- **Per-guild progress** — created/total with a progress bar, state
  (running / done / error / stopped).
- **Stop all** — cancels every running guild task.

## Our generator (not the BD bot)

The old dashboard ran the BD_IND_GLORY (ARIYANA9X) bot, which did its version
check against the author's server and reported every created account to the
author's backend. This version replaces it with **our own generator**:

- `our_gen.py` — our `fast_gen.py`, unchanged: guest register → Garena token
  grant → MajorRegister → MajorLogin → GetLoginData, all against Garena
  directly. No author server, nothing reported to anyone.
- `jwt.py` — our login module (MajorLogin / GetLoginData / AES), unchanged.
- Guild join is our own `RequestJoinClan` (AES-CBC protobuf
  `{f1=guild_id, f2="PK"}`, Bearer JWT) against clientbp directly.
  HTTP 400 = already in that guild, treated as joined.
- Each guild runs with **70 workers**. A failed attempt is retried; a guild
  stops with an error after 10 consecutive failures. A join failure never
  stops generation of the remaining accounts.
- Accounts are only shown in the log (`[CREATED] Bot n/total | UID: ...`,
  `[JOINED] UID ... -> guild ...`). Nothing is saved to disk.

## Deploy on Railway

1. Push this folder to a GitHub repo (or use Railway's "Deploy from GitHub").
   - `our_gen.py` and `jwt.py` are part of the app — they are **never served
     to the browser** and never logged. Keep the repo private.
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
   Railway detects the `Dockerfile` automatically (`railway.json` pins it).
3. No environment variables are needed. Railway sets `$PORT` itself.
4. Wait for the build, then open the Railway-assigned domain.
   The dashboard loads at `/`.

That is it — no build command, no start command, no env vars.

To redeploy after an update: push the new files to the same repo — Railway
auto-redeploys from the repo. (If the repo was set up with the old BD-bot
version, the old `bd_bot.py` and its helper files are now gone; Railway
rebuilds from the Dockerfile, so the deleted files do not linger.)

## Use the dashboard

1. Enter **Team size** (accounts per guild).
2. Paste **Guild IDs**, one per line.
3. Pick **Region** (BD / IND).
4. Press **Start**. Each guild gets its own progress card; the log streams live.
5. Press **Stop all** to cancel running guilds.

## Run locally (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # dashboard at http://127.0.0.1:5000
```

## Files

- `app.py` — Flask app (dashboard + API + log capture + per-guild runners
  + RequestJoinClan).
- `our_gen.py` — our account generator (fast_gen, unchanged logic).
- `jwt.py` — our login module (unchanged).
- `templates/index.html` — the dashboard (inline CSS/JS, no CDN).
- `requirements.txt`, `Dockerfile`, `railway.json`.

## Notes / limits

- One gunicorn worker with 8 threads: the in-memory log buffer stays
  consistent, and the SSE stream does not block the dashboard.
- The dashboard never restarts a finished run automatically.
- If a guild errors, the others keep running; the error shows on its card.
