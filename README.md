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
  (all its teams) AND every account tied to that guild id; other guilds
  keep running. Also removes finished guilds from the view.

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
   then its post-login driver: UDP setup (line 27810) → Homer/roles
   setup `lIIlIIII(ad)` (line 27841: GiveNewbieRoles, then
   SelectPreset skill 5506) → **lobby TCP auth FIRST (line 27834)**
   → TCP auth again for chat (line 27972) — then its follow-up call,
   and plays/idles to the match end. The guild join sits between the
   two TCP auths in the bot's own line order (`lllIIIIll` at line
   27948, guarded by `if clan_override:` at line 27945), but the
   driver's own guild-join step is NEVER reached in practice: in
   execution order the driver awaits the chat TCP auth (line 27972)
   before it reaches line 27948, and the chat auth hangs and never
   returns (proven live 2026-11-27). So the dashboard fires the
   bot's OWN `lllIIIIll` itself, right after the Homer skill is
   equipped — per account the order is ChooseNewbie, Homer skill,
   guild join, then TCP. The flow is: login → Homer skill equip →
   guild join → FIRST TCP connect → match start → play.
   bd_bot.py is untouched.

   **Match loop — the SAME accounts play match after match until
   stopped.** After a match finishes, the next match starts
   immediately with the same accounts (same uid/password, same
   teams/squads); no new accounts are created per round. The log
   shows `MATCH 1 starting`, `MATCH 2 starting`, ... and the card
   shows the current round and matches played. Round 1 is the full
   flow above (login → Homer skill → guild join → TCP → match).
   **Round 2+ skips the guild join entirely** — the accounts are
   already in the guild. The dashboard calls the same original worker
   with `clan_override=None`, and the dashboard's guild-join targets
   are cleared for that round, so the Homer observer never fires the
   join; the ORIGINAL bot's driver would also skip its own join step
   by its own code (`if clan_override:` guards the join at
   bd_bot.py line 27945). So round 2+ is login → TCP → match ONLY,
   using the original's own functions, with nothing rewritten. All rounds of
   one guild share ONE event loop (created once per guild run), so
   any loop-bound objects the bot holds stay valid from round to
   round. A round that ends in error (driver error, or one or more
   workers raising) is logged and the next match starts after a
   15 s pause — the same interval as the original bot's own
   login-retry loop — and the loop never gives up on its own; only
   stopping the guild ends it. A clean round starts the next match
   immediately (no pause).
   The first TCP connect is made visible in the log: the TCP auth
   prints nothing on its own, so app.py wraps the bot's TCP-auth
   function (`bd_bot.IIIIIllIIllIl`) with a thin observer that logs
   `[TCP-CONNECT] ID <account_id>` the first time an account's TCP
   auth returns successfully. The wrapper calls the original
   unchanged and returns its result unchanged. bd_bot.py stays
   byte-identical. The observer's per-account set is cleared at the
   start of each match round, so every round logs its own
   TCP-CONNECT lines.
   The guild join itself is fired by a second wrapper, on the
   Homer/roles setup (`bd_bot.lIIlIIII`, driver line 27841): right
   after it returns True (skill 5506 equipped), on round 1 only, it
   calls the bot's own `lllIIIIll` with `ad['token']` and the guild
   id, logging `[GUILD-JOIN] after HOMER skill: ID <account_id> ->
   guild <id> (result: ...)`. A guild-join failure is logged and
   never breaks the driver flow.
   The Vercel wrapper is never called from this path — it only exists
   in the original's separate join helper, which the dashboard does
   not call.
3. **Guild join is the original bot's own function, fired right
   after the Homer skill is equipped.** There is no dashboard login, no
   dashboard guild-join re-implementation, and no dashboard match
   code. The old `login_account` / `guild_join` (and their HTTP-400
   rule) were deleted with the rewrite they belonged to. The
   dashboard's Homer observer calls the bot's own `lllIIIIll`
   (round 1 only) with the bot's own login token and the guild id,
   because the bot's driver never reaches its own guild-join step —
   its chat TCP auth hangs first. Join success/failure is whatever the
   original bot prints in the log, plus the dashboard's own
   `[GUILD-JOIN] after HOMER skill` line. The dashboard only logs
   `[CREATED] Bot n/total | Team t | ID: <in-game account_id>` from
   our gen's own MajorLogin, `[TCP-CONNECT] ID: <account_id>` when an
   account's first lobby TCP auth succeeds, and tracks how many worker
   tasks ran to completion (`matched` on the card — the worker returns
   None whether its login/join/match succeeded or failed, so the real
   per-account outcome is in the original bot's own log lines).
4. **Stop / Remove** — a per-guild stop cancels the running worker tasks
   (bounded 30 s wait for them to unwind), starts no new match
   round, and marks the guild stopped (the card shows how many
   matches were played). Stop-all does the same for every guild.
   Remove (✕) does all of that AND drops every account tied to that
   guild id — the guild's accounts list is cleared when you click it
   (logged as `[<guild>] removed: N account(s) removed`) and again
   when its thread exits, so no account of a removed guild keeps
   running or is kept around. The accounts are never shown in
   /api/status.

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
- The dashboard never restarts a finished run automatically — but a
  run does not finish on its own anymore: the SAME accounts loop
  match after match until the guild is stopped (see the match loop
  above). Stopping is the only way a run ends.
- If a guild errors, the others keep running; the error shows on its card.
- The original bot's worker runs one event loop per guild thread,
  shared by every match round of that guild. The bot itself runs a
  single loop in the original; the dashboard's one-loop-per-guild is
  the only structural difference, and it is unproven live — the first
  live run will show whether the bot's shared httpx client is happy
  across guild loops, and whether round 2+ login works on the shared
  loop. If round 2 fails with a loop-bound client error, that is the
  first thing to check.
- Round 2+ login + match with the worker stubbed is proven by the
  loop test (36 checks), but the REAL worker's round-2 login has not
  run live yet — the stubbed test cannot prove it, because the worker
  is stubbed. The first live run must confirm round 2 actually
  starts a match (not just that the loop calls the worker).
- The worker's internals (login URLs, guild-join host, match join,
  movement) are the original's own and are not re-verified here — the
  dashboard runs them, it does not claim them.
- Order note: the driver's UDP setup call (line 27810) comes before
  the first TCP auth (line 27834); its purpose for that specific call
  is unproven (it takes addresses + a starter packet, so it reads as
  lobby-connection setup, not the match start — the match join chain
  B → D runs later, after the guild join). The guild join now fires
  before the first TCP connect (right after the Homer skill), because
  the driver's own step at 27948 is never reached (the chat TCP auth
  at 27972 hangs first).
- The `[TCP-CONNECT]` line comes from the app.py observer wrapper
  around the bot's TCP-auth function, not from the bot itself — the
  bot prints nothing on TCP success. The guild join is fired by a
  separate wrapper on the Homer setup (see above), not by the TCP
  wrapper. If a wrapper ever fails to install (bot has no such
  function), the run still works; the log just won't show that line.
