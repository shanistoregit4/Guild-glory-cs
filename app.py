"""
SHANI VIP account generator — Railway web dashboard.

- Serves a dashboard at / with a form (team size, guild IDs, region).
- POST /api/start launches one task per guild ID, in parallel
  (one thread per guild).
- GET /api/status returns per-guild progress.
- GET /api/logs returns the last 100 log lines; GET /api/logs/stream is a
  Server-Sent Events stream that pushes new lines live.
- POST /api/stop cancels all running guild tasks.
- POST /api/guilds/<guild_id>/stop stops/removes ONE guild only; other
  guilds keep running.

THE FLOW — the ORIGINAL BD bot, with only account creation replaced:

- Account creation is OUR generator (our_gen.py, from fast_gen.py):
  Garena token grant + MajorRegister + MajorLogin + ChooseRegion,
  directly against Garena (loginbp.mainecoonnodex.com; ggbluefox for
  ME/TH). ChooseRegion field 1 is PK for BD accounts (user is in PK).
  No author server, no Vercel reporting.
- EVERYTHING after creation is the ORIGINAL BD bot code, imported
  as-is (bd_bot.py + its sibling modules) and called directly:
  per account, the bot's own worker `lIIIllIIIlllIIl`
  (uid, password, region_override, clan_override, squad_id) does its
  combined login (its own version check + token grant + MajorLogin +
  GetLoginData), then its post-login driver (lobby TCP auth, UDP/match
  join, its own guild join `lllIIIIll`, TCP auth again), then its
  follow-up call, and plays/idles to the match end. The dashboard
  does NOT re-implement any of that: no rewritten login, no rewritten
  guild join, no rewritten match/team/UDP code, no guessed values.
  The dashboard only wraps it: the form replaces the terminal prompts
  (team size / guild IDs / region), the bot's printed output is captured
  into the same log ring buffer, and per-guild stop cancels its workers.
- jwt.py is kept in the folder (original login module, ppmainecoonghj.com,
  like the emote bot) but is NOT used by the run path anymore — the
  original bot does its own login. The dashboard's old login_account /
  guild_join (and their HTTP-400 rule) are gone with the rewrite they
  belonged to; the guild join is now the original bot's own, and its
  success/failure is whatever the original bot prints.

Importing bd_bot runs its module-level code (string decryptors, an
httpx client, and a connectivity check that raises SystemExit when
there is no internet) — so the import is guarded with BaseException,
and NO_PROXY is sanitised first (see _sanitize_no_proxy): the bot
builds httpx proxy URL patterns from NO_PROXY, and IPv6 entries
produce an invalid 'all://*[::1]' pattern that httpx 0.29 rejects.
"""

import asyncio
import importlib.util
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

APP_DIR = os.path.dirname(os.path.abspath(__file__))
GEN_PATH = os.path.join(APP_DIR, "our_gen.py")
BD_BOT_PATH = os.path.join(APP_DIR, "bd_bot.py")

# Our generator's fixed name/password prefixes (fast_gen defaults).
NAME_PREFIX = "shani"
PASSWORD_PREFIX = "pak"
# 70 workers per guild, same concurrency as the old dashboard.
GEN_WORKERS = 70
# Stop a guild after this many consecutive generation failures.
MAX_CONSEC_FAILURES = 10

LOG_MAX = 100
MAX_TEAM_SIZE = 100
MAX_GUILDS = 10

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Import-path fix: NO_PROXY sanitising (see module docstring).
# The original bot builds httpx proxy URL patterns from NO_PROXY; an IPv6
# entry (e.g. ::1 / [::1]) makes it build the invalid pattern
# 'all://*[::1]', which httpx 0.29 rejects at import time. Drop every
# entry containing ':' (IPv6 literals) from NO_PROXY/no_proxy before the
# bot is imported. Harmless on Railway (no proxy vars there) and only
# affects proxy-bypass for IPv6 localhost here.
# ---------------------------------------------------------------------------

def _sanitize_no_proxy():
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key)
        if not val:
            continue
        kept = [p.strip() for p in val.split(",")
                if p.strip() and ":" not in p]
        os.environ[key] = ",".join(kept)


_sanitize_no_proxy()


# ---------------------------------------------------------------------------
# Log ring buffer + live subscribers
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()
_log_buffer = deque(maxlen=LOG_MAX)          # list of {"ts", "guild", "line"}
_subscribers = []                            # list of queue.Queue
_subscribers_lock = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


def log_line(line, guild=None):
    """Append one log line to the ring buffer and push it to SSE subscribers."""
    line = _strip_ansi(line.rstrip("\n"))
    if not line.strip():
        return
    entry = {"ts": _now_iso(), "guild": guild, "line": line}
    with _log_lock:
        _log_buffer.append(entry)
    with _subscribers_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(entry)
            except queue.Full:
                pass


# ---------------------------------------------------------------------------
# stdout capture: the bot prints via print(); capture it into the log
# buffer. Each guild thread is registered to a guild id so lines get tagged.
# ---------------------------------------------------------------------------

_orig_stdout = sys.stdout
_thread_guild = {}            # threading.get_ident() -> guild_id
_thread_guild_lock = threading.Lock()
_partial = threading.local()  # per-thread partial line buffer


class _TeeWriter:
    """Writes through to the real stdout and also captures lines into the log."""

    def write(self, s):
        if not s:
            return 0
        if isinstance(s, bytes):
            s = s.decode("utf-8", errors="replace")
        try:
            _orig_stdout.write(s)
            _orig_stdout.flush()
        except Exception:
            pass
        # Buffer per thread until a newline arrives, then log complete lines.
        buf = getattr(_partial, "buf", "")
        buf += s
        while "\n" in buf:
            one, buf = buf.split("\n", 1)
            one = one.replace("\r", "")
            if one.strip():
                with _thread_guild_lock:
                    guild = _thread_guild.get(threading.get_ident())
                log_line(one, guild=guild)
        _partial.buf = buf
        return len(s)

    def flush(self):
        try:
            _orig_stdout.flush()
        except Exception:
            pass

    def isatty(self):
        return False


sys.stdout = _TeeWriter()


def _log(msg, guild=None):
    """Dashboard's own log helper (goes to the same buffer + real stdout)."""
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# Modules (lazy import)
# ---------------------------------------------------------------------------

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_gen = None        # our_gen module
_gen_lock = threading.Lock()
_gen_error = None


def get_gen():
    """Return our generator module, importing once. Safe: importing our_gen
    builds its IP/device/UA pools (prints a few lines, takes a few
    seconds) but starts nothing (it is __main__-guarded)."""
    global _gen, _gen_error
    if _gen is not None:
        return _gen
    if _gen_error is not None:
        raise RuntimeError(f"generator failed to import earlier: {_gen_error}")
    with _gen_lock:
        if _gen is not None:
            return _gen
        try:
            _log("[dashboard] importing our generator (this takes a few seconds)...")
            our_gen = _load_module("our_gen", GEN_PATH)
            if not hasattr(our_gen, "generate_single_account"):
                raise RuntimeError("our_gen has no generate_single_account")
            _gen = our_gen
            _log("[dashboard] our generator imported OK")
            return _gen
        except Exception as exc:  # noqa: BLE001 - remember the failure
            _gen_error = exc
            raise


_bd_bot = None     # the ORIGINAL BD bot module
_bd_bot_lock = threading.Lock()
_bd_bot_error = None


def get_bd_bot():
    """Import the ORIGINAL BD bot (bd_bot.py + its sibling modules) once
    and return it. The bot is used as-is: the dashboard calls its own
    worker function; nothing in the bot is copied or rewritten.

    Importing it runs its module-level code (string decryptors, an httpx
    client, and a connectivity check that raises SystemExit when there is
    no internet), so BaseException is caught here, not just Exception.
    APP_DIR is put on sys.path first so the bot's sibling imports
    (message_ids, reqClan_pb2, ...) resolve."""
    global _bd_bot, _bd_bot_error
    if _bd_bot is not None:
        return _bd_bot
    if _bd_bot_error is not None:
        raise RuntimeError(f"original bot failed to import earlier: {_bd_bot_error}")
    with _bd_bot_lock:
        if _bd_bot is not None:
            return _bd_bot
        try:
            _log("[dashboard] importing original BD bot (this takes a few seconds)...")
            if APP_DIR not in sys.path:
                sys.path.insert(0, APP_DIR)
            mod = _load_module("bd_bot", BD_BOT_PATH)
            if not hasattr(mod, "lIIIllIIIlllIIl"):
                raise RuntimeError("bd_bot has no worker lIIIllIIIlllIIl")
            if not hasattr(mod, "IIIllIlIlllII"):
                raise RuntimeError("bd_bot has no squad helper IIIllIlIlllII")
            _bd_bot = mod
            _log("[dashboard] original BD bot imported OK")
            return _bd_bot
        except BaseException as exc:  # noqa: BLE001 - includes SystemExit from the bot's net check
            _bd_bot_error = exc
            raise RuntimeError(
                f"original BD bot failed to import: {type(exc).__name__}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Driving the ORIGINAL bot's per-account worker
# ---------------------------------------------------------------------------

def _run_bd_workers(bd_bot, accounts, region, guild_id, stop_event):
    """Hand every generated account to the ORIGINAL BD bot's own worker
    and run them all.

    Per account (i-th of the guild's team), exactly like the original
    create path's join phase:
      squad_id = bd_bot.IIIllIlIlllII(uid, preferred_index=i)
      bd_bot.lIIIllIIIlllIIl(uid, password,
                             region_override=region,
                             clan_override=guild_id,
                             squad_id=squad_id)
    Workers are launched as asyncio tasks 0.25s apart (the original's
    gap) and gathered, in one event loop in this (guild) thread. The
    worker itself does the combined login (its own version check +
    token grant + MajorLogin + GetLoginData), then its post-login driver
    (lobby TCP auth, UDP/match join, its own guild join, TCP auth
    again), then its follow-up call, and plays/idles to the match end.

    Returns the gather result list: None per worker that finished,
    the exception object per worker that raised (including
    CancelledError entries when stopped). A per-guild stop cancels
    the running worker tasks (bounded 30s wait for them to unwind).
    """
    async def _drive():
        tasks = []
        for i, acc in enumerate(accounts):
            if stop_event.is_set():
                break
            try:
                squad_id = bd_bot.IIIllIlIlllII(str(acc.get("uid")),
                                                preferred_index=i)
            except Exception:
                squad_id = None
            tasks.append(asyncio.create_task(bd_bot.lIIIllIIIlllIIl(
                str(acc.get("uid")), str(acc.get("password")),
                region_override=region,
                clan_override=str(guild_id),
                squad_id=squad_id)))
            await asyncio.sleep(0.25)
        if not tasks:
            return []
        # NOTE: asyncio.gather() already returns a Future — do NOT wrap it
        # in create_task() (that raises TypeError: a coroutine was expected).
        gather_fut = asyncio.gather(*tasks, return_exceptions=True)
        stopped = False
        while not gather_fut.done():
            if stop_event.is_set():
                stopped = True
                for t in tasks:
                    t.cancel()
                break
            await asyncio.sleep(0.5)
        if stopped:
            try:
                return await asyncio.wait_for(gather_fut, timeout=30)
            except asyncio.TimeoutError:
                return [asyncio.CancelledError()] * len(tasks)
        return await gather_fut

    return asyncio.run(_drive())


class _GuildFailed(Exception):
    """Raised when a guild run must stop with an error state."""


# ---------------------------------------------------------------------------
# Per-guild run state
# ---------------------------------------------------------------------------

# guild_id -> {
#   "guild_id", "state": "running"|"done"|"error"|"stopped",
#   "created": int, "total": int, "started_at": str, "ended_at": str|None,
#   "error": str|None, "thread": Thread|None, "stop_event": Event|None,
#   "match_stage": "creating"|"joining"|"done"|"stopped"|"error",
#   "matched": int, "match_end": str, "match_error": str|None,
# }
_guilds = {}
_guilds_lock = threading.Lock()


def _mark_created(guild_id):
    with _guilds_lock:
        g = _guilds.get(guild_id)
        if g is not None:
            g["created"] += 1


def _set_match_stage(guild_id, stage):
    """Set one guild's match_stage field (under _guilds_lock).
    Never touches state."""
    with _guilds_lock:
        g = _guilds.get(guild_id)
        if g is not None:
            g["match_stage"] = stage


# Hook CREATED counting into the log writer: count lines as they are logged.
_orig_log_line = log_line


def _counting_log_line(line, guild=None):
    _orig_log_line(line, guild=guild)
    if guild and "[CREATED]" in line:
        _mark_created(guild)


# Replace the module-level log_line with the counting version (the TeeWriter
# calls the module global by name, so this takes effect everywhere).
log_line = _counting_log_line


def _run_guild(guild_id, team_size, region):
    """One guild, in its own thread:
    1. Generate team_size accounts with OUR generator (70 workers, waves)
       and log [CREATED] per account (real in-game account_id from our
       gen's own MajorLogin).
    2. Hand every created account to the ORIGINAL BD bot's worker
       (login + guild join + match join + play), exactly like the
       original create path's join phase. The bot's own printed output
       flows into this guild's log.
    Runs one thread per guild, so multiple guilds run in parallel."""
    with _thread_guild_lock:
        _thread_guild[threading.get_ident()] = guild_id
    stop_event = threading.Event()
    with _guilds_lock:
        _guilds[guild_id]["stop_event"] = stop_event
    created = 0
    consecutive_failures = 0
    accounts = []  # our_gen dicts, in creation order
    try:
        our_gen = get_gen()
        bd_bot = get_bd_bot()
        _log(f"[{guild_id}] starting: {team_size} accounts, region {region} "
             f"(our generator, 70 workers; original BD bot for login/join/match)",
             guild=guild_id)
        # Generation waves: each wave submits up to 70 generation attempts
        # (never more than the accounts still needed), waits for them all,
        # counts the successes, and retries the failures in the next wave.
        # This mirrors the original's creation loop (create all accounts
        # first, join phase after) — accounts are only handed to the bot
        # once generation is done.
        while created < team_size and not stop_event.is_set():
            remaining = team_size - created
            batch = min(GEN_WORKERS, remaining)
            ex = ThreadPoolExecutor(max_workers=GEN_WORKERS)
            try:
                futs = [ex.submit(our_gen.generate_single_account, region,
                                  NAME_PREFIX, PASSWORD_PREFIX)
                        for _ in range(batch)]
                for fut in as_completed(futs):
                    if stop_event.is_set():
                        break
                    try:
                        acc = fut.result()
                    except Exception as exc:  # noqa: BLE001 - one bad attempt
                        acc = None
                        _log(f"[{guild_id}] generation error: "
                             f"{type(exc).__name__}: {exc}", guild=guild_id)
                    if not acc:
                        consecutive_failures += 1
                        _log(f"[{guild_id}] account failed "
                             f"({consecutive_failures} consecutive)",
                             guild=guild_id)
                        if consecutive_failures >= MAX_CONSEC_FAILURES:
                            raise _GuildFailed(
                                f"{MAX_CONSEC_FAILURES} consecutive "
                                f"generation failures")
                        continue
                    consecutive_failures = 0
                    created += 1
                    accounts.append(acc)
                    _log(f"[CREATED] Bot {created}/{team_size} | "
                         f"ID: {acc.get('account_id', '?')}", guild=guild_id)
            finally:
                # On stop, drop queued futures immediately; running attempts
                # finish in the background and their results are discarded.
                ex.shutdown(wait=False, cancel_futures=True)
        # -- Original-bot phase: login + guild join + match join + play.
        # Skipped if stopped (the final block below marks the guild
        # stopped) or if _GuildFailed raised (its except path runs
        # instead of this block). --
        if not stop_event.is_set() and accounts:
            _set_match_stage(guild_id, "joining")
            _log(f"[{guild_id}] handing {len(accounts)} account(s) to the "
                 f"original BD bot worker (login + guild join + match)",
                 guild=guild_id)
            try:
                results = _run_bd_workers(bd_bot, accounts, region,
                                          guild_id, stop_event)
            except Exception as exc:  # noqa: BLE001 - driver failure
                results = []
                _log(f"[{guild_id}] original-bot driver error: "
                     f"{type(exc).__name__}: {exc}", guild=guild_id)
                with _guilds_lock:
                    g = _guilds[guild_id]
                    g["match_stage"] = "error"
                    g["match_end"] = "driver error"
                    g["match_error"] = f"{type(exc).__name__}: {exc}"[:200]
            else:
                # matched = workers that ran to completion without raising.
                # (The worker returns None whether its login/join/match
                # succeeded or failed — the real per-account outcome is in
                # the original bot's own log lines, not in a return value.)
                finished = sum(1 for r in results
                               if not isinstance(r, BaseException))
                first_err = next((r for r in results
                                  if isinstance(r, BaseException)), None)
                with _guilds_lock:
                    g = _guilds[guild_id]
                    g["matched"] = finished
                    if stop_event.is_set():
                        g["match_stage"] = "stopped"
                        g["match_end"] = "stopped"
                    elif first_err is not None:
                        g["match_stage"] = "error"
                        g["match_end"] = "worker error (see log)"
                        g["match_error"] = (
                            f"{type(first_err).__name__}: {first_err}"[:200])
                    else:
                        g["match_stage"] = "done"
                        g["match_end"] = (f"workers done: {finished}/"
                                          f"{len(accounts)}")
        elif not stop_event.is_set():
            # No accounts created: nothing to hand to the bot.
            _log(f"[{guild_id}] no accounts created, nothing to join",
                 guild=guild_id)
            with _guilds_lock:
                g = _guilds[guild_id]
                g["match_stage"] = "done"
                g["match_end"] = "skip: no accounts created"
        with _guilds_lock:
            g = _guilds[guild_id]
            if g["state"] == "running":
                if stop_event.is_set():
                    g["state"] = "stopped"
                    _log(f"[{guild_id}] stopped by user "
                         f"({created}/{team_size} created)", guild=guild_id)
                else:
                    g["state"] = "done"
                    _log(f"[{guild_id}] done: {created}/{team_size} created",
                         guild=guild_id)
                g["ended_at"] = _now_iso()
    except _GuildFailed as exc:
        with _guilds_lock:
            g = _guilds[guild_id]
            g["state"] = "error"
            g["error"] = str(exc)
            g["ended_at"] = _now_iso()
        _log(f"[{guild_id}] ERROR: {exc}", guild=guild_id)
    except Exception as exc:  # noqa: BLE001 - surface any failure per guild
        with _guilds_lock:
            g = _guilds[guild_id]
            g["state"] = "error"
            g["error"] = f"{type(exc).__name__}: {exc}"
            g["ended_at"] = _now_iso()
        _log(f"[{guild_id}] ERROR: {type(exc).__name__}: {exc}", guild=guild_id)
        _log(traceback.format_exc(), guild=guild_id)
    finally:
        with _thread_guild_lock:
            _thread_guild.pop(threading.get_ident(), None)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def api_start():
    data = request.get_json(silent=True) or {}
    # ---- validate ----
    try:
        team_size = int(data.get("team_size", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "team_size must be a number"}), 400
    if team_size < 1 or team_size > MAX_TEAM_SIZE:
        return jsonify({"ok": False, "error": f"team_size must be between 1 and {MAX_TEAM_SIZE}"}), 400

    raw_ids = data.get("guild_ids", [])
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    guild_ids = []
    for gid in raw_ids:
        gid = str(gid).strip()
        if gid:
            guild_ids.append(gid)
    # de-duplicate, keep order
    seen = set()
    guild_ids = [g for g in guild_ids if not (g in seen or seen.add(g))]
    if not guild_ids:
        return jsonify({"ok": False, "error": "at least one guild ID is required"}), 400
    if len(guild_ids) > MAX_GUILDS:
        return jsonify({"ok": False, "error": f"at most {MAX_GUILDS} guild IDs at once"}), 400
    for gid in guild_ids:
        if not gid.isdigit():
            return jsonify({"ok": False, "error": f"guild ID must be numeric: {gid}"}), 400

    region = str(data.get("region", "BD")).strip().upper()
    if region not in ("BD", "IND"):
        return jsonify({"ok": False, "error": "region must be BD or IND"}), 400

    # ---- check both modules import before starting anything ----
    try:
        get_gen()
        get_bd_bot()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"module failed to load: {exc}"}), 500

    started = []
    skipped = []
    with _guilds_lock:
        for gid in guild_ids:
            g = _guilds.get(gid)
            if g is not None and g["state"] == "running":
                skipped.append(gid)
                continue
            _guilds[gid] = {
                "guild_id": gid,
                "state": "running",
                "created": 0,
                "total": team_size,
                "started_at": _now_iso(),
                "ended_at": None,
                "error": None,
                "thread": None,
                "stop_event": None,
                # match_stage: creating -> joining -> done/stopped/error;
                # matched counts worker tasks that ran to completion;
                # match_end / match_error describe the worker outcome.
                "match_stage": "creating",
                "matched": 0,
                "match_end": "",
                "match_error": None,
            }
            started.append(gid)

    for gid in started:
        t = threading.Thread(target=_run_guild, args=(gid, team_size, region),
                             name=f"gen-{gid}", daemon=True)
        with _guilds_lock:
            _guilds[gid]["thread"] = t
        t.start()

    return jsonify({"ok": True, "started": started, "skipped_already_running": skipped})


@app.route("/api/status")
def api_status():
    with _guilds_lock:
        out = []
        for gid, g in _guilds.items():
            if g.get("hidden"):
                continue
            out.append({
                "guild_id": gid,
                "state": g["state"],
                "created": g["created"],
                "total": g["total"],
                "started_at": g["started_at"],
                "ended_at": g["ended_at"],
                "error": g["error"],
                "match_stage": g.get("match_stage", "creating"),
                "matched": g.get("matched", 0),
                "match_end": g.get("match_end", ""),
                "match_error": g.get("match_error"),
            })
    out.sort(key=lambda x: x["started_at"])
    return jsonify({"guilds": out})


@app.route("/api/logs")
def api_logs():
    with _log_lock:
        lines = list(_log_buffer)
    return jsonify({"lines": lines})


@app.route("/api/logs/stream")
def api_logs_stream():
    q = queue.Queue(maxsize=200)
    with _subscribers_lock:
        _subscribers.append(q)

    @stream_with_context
    def events():
        try:
            # Send the current buffer first so a fresh client sees the last 100 lines.
            with _log_lock:
                backlog = list(_log_buffer)
            for entry in backlog:
                yield f"data: {json.dumps(entry)}\n\n"
            while True:
                try:
                    entry = q.get(timeout=15)
                    yield f"data: {json.dumps(entry)}\n\n"
                except queue.Empty:
                    # heartbeat comment, keeps the connection alive
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    return Response(events(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    stopped = []
    with _guilds_lock:
        running = [g for g in _guilds.values() if g["state"] == "running"]
    for g in running:
        ev = g.get("stop_event")
        if ev is not None:
            ev.set()
            stopped.append(g["guild_id"])
    return jsonify({"ok": True, "stopped": stopped})


@app.route("/api/guilds/<guild_id>/stop", methods=["POST"])
def api_guild_stop(guild_id):
    """Stop/remove ONE guild only; other running guilds are untouched.

    A running guild gets its stop_event set and is marked stopped (its
    thread notices within one wave and exits; a running original-bot
    worker phase is cancelled within ~30s). In all cases the entry is
    hidden from the dashboard, so the card disappears and status polls
    never bring it back. The entry is kept internally (not deleted) so
    the running thread can still update it safely. 404 if unknown.
    """
    with _guilds_lock:
        g = _guilds.get(guild_id)
        if g is None:
            return jsonify({"ok": False, "error": "unknown guild ID"}), 404
        state = g["state"]
        log_stop = False
        if state == "running":
            ev = g.get("stop_event")
            if ev is not None:
                ev.set()
            g["state"] = "stopped"
            g["ended_at"] = _now_iso()
            state = "stopped"
            log_stop = True
            created, total = g["created"], g["total"]
        g["hidden"] = True
    if log_stop:
        _log(f"[{guild_id}] stopped by user ({created}/{total} created)",
             guild=guild_id)
    return jsonify({"ok": True, "guild_id": guild_id, "state": state,
                    "removed": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
