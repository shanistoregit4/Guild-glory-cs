# SHANI VIP — Account Generator Dashboard (Railway)

A web dashboard for our own Free Fire account generator (the one from the
emote bot / fast_gen) driving the ORIGINAL BD_IND_GLORY bot. No terminal
prompts, no Skip option — everything runs from the dashboard.

## What it does

- **Number of teams** — how many teams to run per guild (1–10, default 1).
  A CS squad is 4 players, so 1 team = 4 members, 2 teams = 8 members,
  N teams = N × 4 members. The team count is editable; the squad size
  (4) is fixed.
- **Guild IDs** — one per line; multiple guilds run **in parallel**, each with
  its own progress card.
- **Region** — BD or IND (passed to the generator; name prefix `shani`,
  password prefix `pak`).
- **Live log** — the last 100 lines, streamed in real time (Server-Sent Events),
  auto-scrolling, tagged per guild. The original bot's own printed output
  flows into this log.
- **Per-guild progress** — teams, members, created/total with a progress bar,
  per-team created counts, state (running / done / error / stopped), and the
  worker stage (`creating` → `joining` → `done` / `stopped` / `error`).
- **Stop all** — cancels every running guild task.
- **Remove (✕) on each guild card** — stops and removes just that one guild
  (all its teams); other guilds keep running. Also removes finished guilds
  from the view.

## The flow — ORIGINAL BD bot, only creation replaced

The dashboard is a wrapper. It does not re-implement any bot logic.

1. **Account creation — OUR generator only.** `our_gen.py` is our
   `fast_gen.py`: guest register → Garena token grant → MajorRegister →
   MajorLogin → ChooseRegion, all against Garena directly
   (loginbp.mainecoonnodex.com; ggbluefox for ME/TH). ChooseRegion field 1
   is PK for BD accounts (user is in PK; in the BD bot PK is treated as
   BD). No author server, nothing reported to anyone. Accounts are created
   in waves with 70 workers, like the original's creation loop (create all
   accounts first, join phase after).
2. **Everything after creation — the ORIGINAL BD bot, as-is.**
   `bd_bot.py` (byte-identical to the author's `main.py`, deobfuscated)
   plus its sibling modules (`message_ids.py`, `reqClan_pb2.py`,
   `data_pb2.py`, `StartMatch_pb2.py`, `thunderFF_pb2.py`,
   `homor_rudp.py`) are imported unchanged. Per account the dashboard
   calls the bot's own per-account worker, exactly like the original
   create path's join phase (bd_bot.py line 30473):
   - `squad_id = bd_bot.IIIllIlIlllII(uid, preferred_index=i)`
   - `bd_bot.lIIIllIIIlllIIl(uid, password, region_override=region,
     clan_override=guild_id, squad_id=squad_id)`

   The squad helper returns squad = index // 4 (verified by calling
   it: indices 0-3 → squad 0, 4-7 → squad 1), so the first 4 accounts
   run as team 1 (squad 0), the next 4 as team 2 (squad 1), and so on.
   Each team's 4 workers run as that squad, all teams of a guild in
   parallel in the same event loop; one team's failure never stops
   the other teams (gather with return_exceptions=True).

   Workers are launched as asyncio tasks 0.25 s apart (the original's
   gap) and gathered, in one event loop per guild thread. The worker
   does its combined login (its own version check against
   version.ggwhitehawk.com, token grant, MajorLogin, GetLoginData),
   then its post-login driver in the ORIGINAL order, verified by
   reading bd_bot.py (post-login driver, inner at line 27689):
   UDP setup (line 27810) → **lobby TCP auth FIRST (line 27834)** →
   **its own guild join `lllIIIIll` (line 27948)** → TCP auth again
   (line 27972) — then its follow-up call, and plays/idles to the
   match end. So the flow is: login → FIRST TCP connect → guild join
   → match start → play. The order was NOT changed; it is the
   original's own.
   The first TCP connect is made visible in the log: the TCP auth
   prints nothing on its own, so app.py wraps the bot's TCP-auth
   function (`bd_bot.IIIIIllIIllIl`) with a thin observer that only
   logs `[TCP-CONNECT] ID <account_id>` the first time an account's
   TCP auth returns successfully. The wrapper calls the original
   unchanged and returns its result unchanged; bd_bot.py stays
   byte-identical. The bot's own guild-join print
   (`[GUILD ...] Clan join sent`) comes after it in the log.
   The Vercel wrapper is never called from this path — it only exists
   in the original's separate join helper, which the dashboard does
   not call.
3. **Guild join is the original bot's own, after the first TCP
   connect.** There is no dashboard login, no dashboard guild join,
   and no dashboard match code anymore. The old `login_account` /
   `guild_join` (and their HTTP-400 rule) were deleted with the
   rewrite they belonged to. Join success/failure is whatever the
   original bot prints in the log. The dashboard only logs
   `[CREATED] Bot n/total | Team t | ID: <in-game account_id>` from
   our gen's own MajorLogin, `[TCP-CONNECT] ID: <account_id>` when an
   account's first lobby TCP auth succeeds, and tracks how many worker
   tasks ran to completion (`matched` on the card — the worker returns
   None whether its login/join/match succeeded or failed, so the real
   per-account outcome is in the original bot's own log lines).
4. **Stop** — a per-guild stop cancels the running worker tasks
   (bounded 30 s wait for them to unwind) and marks the guild stopped.

`jwt.py` is kept in the folder (our login module, MajorLogin on
loginbp.ppmainecoonghj.com, like the emote bot) but is NOT used by the
run path anymore — the original bot does its own login.

## Import notes (why the dashboard sanitises NO_PROXY)

Importing the original bot runs its module-level code: string
decryptors, an httpx client, and a connectivity check that raises
SystemExit when there is no internet. Two environment gotchas, both
handled in `app.py` without touching the bot:

- The bot builds httpx proxy URL patterns from `NO_PROXY`. An IPv6
  entry (e.g. `::1` / `[::1]`) makes it build the invalid pattern
  `all://*[::1]`, which httpx 0.29 rejects at import time. The
  dashboard drops every `:`-containing entry from `NO_PROXY` /
  `no_proxy` at startup. Harmless on Railway (no proxy vars there).
- A failed bot import (including the SystemExit from its connectivity
  check) is caught as `BaseException` and reported as a clean guild
  error, so one guild cannot hang as "running" forever.
- The bot needs its sibling modules on `sys.path` (`message_ids`,
  `reqClan_pb2`, ...); the dashboard inserts its own folder before
  importing it.

## Deploy on Railway

1. Push this folder to a GitHub repo (or use Railway's "Deploy from GitHub").
   - `our_gen.py`, `jwt.py`, `bd_bot.py` and the sibling modules are part
     of the app — they are **never served to the browser** and never
     logged. Keep the repo private.
2. In Railway: **New Project → Deploy from GitHub repo** → pick the repo.
   Railway detects the `Dockerfile` automatically (`railway.json` pins it).
3. No environment variables are needed. Railway sets `$PORT` itself.
4. Wait for the build, then open the Railway-assigned domain.
   The dashboard loads at `/`.

That is it — no build command, no start command, no env vars.

To redeploy after an update: push the new files to the same repo — Railway
auto-redeploys from the repo. (If the repo was set up with the previous
version, the deleted `match.py` / `udp_crypto.py` are gone; Railway
rebuilds from the Dockerfile, so the deleted files do not linger.)

## Use the dashboard

1. Enter **Number of teams** (1–10; each team is 4 members, so 2 teams
   = 8 members running for the guild).
2. Paste **Guild IDs**, one per line.
3. Pick **Region** (BD / IND).
4. Press **Start**. Each guild gets its own progress card; the log streams live.
5. Press **Stop all** to cancel running guilds, or **✕ Remove** on one card
   to stop and remove just that guild (all its teams).

## Run locally (optional)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py          # dashboard at http://127.0.0.1:5000
```

## Files

- `app.py` — Flask app (dashboard + API + log capture + per-guild runners
  + original-bot wiring). The only file that was written for the dashboard.
- `our_gen.py` — our account generator (fast_gen, unchanged logic except
  ChooseRegion field 1 maps BD → PK).
- `jwt.py` — our login module (kept, not used by the run path).
- `bd_bot.py` — the ORIGINAL BD bot (`main.py`, deobfuscated),
  byte-identical to the author's file. Do not edit.
- `message_ids.py`, `reqClan_pb2.py`, `data_pb2.py`, `StartMatch_pb2.py`,
  `thunderFF_pb2.py`, `homor_rudp.py` — the bot's sibling modules,
  byte-identical to the author's zip. Do not edit.
- `templates/index.html` — the dashboard (inline CSS/JS, no CDN).
- `requirements.txt`, `Dockerfile`, `railway.json`.

## Notes / limits

- One gunicorn worker with 8 threads: the in-memory log buffer stays
  consistent, and the SSE stream does not block the dashboard.
- The dashboard never restarts a finished run automatically.
- If a guild errors, the others keep running; the error shows on its card.
- The original bot's worker runs one event loop per guild thread. The
  bot itself runs a single loop in the original; the dashboard's
  one-loop-per-guild is the only structural difference, and it is
  unproven live — the first live run will show whether the bot's
  shared httpx client is happy across guild loops.
- The worker's internals (login URLs, guild-join host, match join,
  movement) are the original's own and are not re-verified here — the
  dashboard runs them, it does not claim them.
- Order note: the driver's UDP setup call (line 27810) comes before
  the first TCP auth (line 27834); its purpose for that specific call
  is unproven (it takes addresses + a starter packet, so it reads as
  lobby-connection setup, not the match start — the match join chain
  B → D runs later, after the guild join). The TCP → guild-join order
  itself is verified by line number (27834 < 27948).
- The `[TCP-CONNECT]` line comes from the app.py observer wrapper
  around the bot's TCP-auth function, not from the bot itself — the
  bot prints nothing on TCP success. If the wrapper ever fails to
  install (bot has no such function), the run still works; the log
  just won't show the TCP line.
