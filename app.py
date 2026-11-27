"""
BD_IND_GLORY (ARIYANA9X) account generator — Railway web dashboard.

- Serves a dashboard at / with a form (team size, guild IDs, region).
- POST /api/start launches one create_bots_and_join task per guild ID, in
  parallel (one thread + one asyncio event loop per guild).
- GET /api/status returns per-guild progress.
- GET /api/logs returns the last 100 log lines; GET /api/logs/stream is a
  Server-Sent Events stream that pushes new lines live.
- POST /api/stop cancels all running guild tasks.

The bot file (bd_bot.py) is imported as a module. Its entry point is guarded
by `if __name__ == '__main__':`, so importing it does NOT start the bot.
The bot's own behaviour (version check, account creation, accounts.json,
guild join, reporting to the author's Vercel backend) is unchanged.
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
from collections import deque
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_PATH = os.path.join(APP_DIR, "bd_bot.py")
BOT_PASSWORD = "ARIYANA9X"

LOG_MAX = 100
MAX_TEAM_SIZE = 100
MAX_GUILDS = 10

app = Flask(__name__)

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
# stdout capture: the bot prints via print(); capture it into the log buffer.
# Each bot thread is registered to a guild id so lines get tagged correctly.
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
# Bot module (lazy import — importing takes a few seconds for a 5.5 MB file)
# ---------------------------------------------------------------------------

_bot_module = None
_bot_lock = threading.Lock()
_bot_error = None


def _sanitize_no_proxy():
    """httpx 0.28 crashes on bracketed IPv6 entries (e.g. [::1]) in no_proxy
    (InvalidURL: Invalid port: ':1]'). The bot builds an httpx.AsyncClient
    at import time, so strip those entries before importing it. This only
    affects the proxy-bypass list; outbound proxying itself is unchanged.
    Railway's environment has no such entries, so this is a no-op there."""
    for key in ("no_proxy", "NO_PROXY"):
        val = os.environ.get(key)
        if not val:
            continue
        kept = [e for e in val.split(",") if "[" not in e]
        if len(kept) != len(val.split(",")):
            os.environ[key] = ",".join(kept)
            _log(f"[dashboard] removed bracketed IPv6 entries from {key} (httpx workaround)")


def get_bot():
    global _bot_module, _bot_error
    if _bot_module is not None:
        return _bot_module
    if _bot_error is not None:
        raise RuntimeError(f"bot failed to import earlier: {_bot_error}")
    with _bot_lock:
        if _bot_module is not None:
            return _bot_module
        _sanitize_no_proxy()
        _log("[dashboard] importing bot module (this takes a few seconds)...")
        spec = importlib.util.spec_from_file_location("bd_bot", BOT_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # safe: entry point is __main__-guarded
        if not hasattr(mod, "create_bots_and_join"):
            raise RuntimeError("bot module has no create_bots_and_join")
        _bot_module = mod
        _log("[dashboard] bot module imported OK")
        return _bot_module


# ---------------------------------------------------------------------------
# Per-guild run state
# ---------------------------------------------------------------------------

# guild_id -> {
#   "guild_id", "state": "running"|"done"|"error"|"stopped",
#   "created": int, "total": int, "started_at": str, "ended_at": str|None,
#   "error": str|None, "thread": Thread|None, "loop": loop|None, "task": Task|None,
# }
_guilds = {}
_guilds_lock = threading.Lock()


def _mark_created(guild_id):
    with _guilds_lock:
        g = _guilds.get(guild_id)
        if g is not None:
            g["created"] += 1


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
    """Runs create_bots_and_join for one guild in its own thread + event loop."""
    with _thread_guild_lock:
        _thread_guild[threading.get_ident()] = guild_id
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = None
    try:
        bot = get_bot()
        with _guilds_lock:
            _guilds[guild_id]["loop"] = loop
        _log(f"[{guild_id}] starting: {team_size} accounts, region {region}", guild=guild_id)
        coro = bot.create_bots_and_join(region, guild_id, count=team_size, password=BOT_PASSWORD)
        task = loop.create_task(coro)
        with _guilds_lock:
            _guilds[guild_id]["task"] = task
        loop.run_until_complete(task)
        with _guilds_lock:
            g = _guilds[guild_id]
            if g["state"] == "running":
                g["state"] = "done"
                g["ended_at"] = _now_iso()
        _log(f"[{guild_id}] done: { _guilds[guild_id]['created']}/{team_size} created", guild=guild_id)
    except asyncio.CancelledError:
        with _guilds_lock:
            g = _guilds[guild_id]
            g["state"] = "stopped"
            g["ended_at"] = _now_iso()
        _log(f"[{guild_id}] stopped by user", guild=guild_id)
    except Exception as exc:  # noqa: BLE001 - surface any bot failure per guild
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
        try:
            loop.close()
        except Exception:
            pass


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

    # ---- check the bot imports before starting anything ----
    try:
        get_bot()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"bot failed to load: {exc}"}), 500

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
                "loop": None,
                "task": None,
            }
            started.append(gid)

    for gid in started:
        t = threading.Thread(target=_run_guild, args=(gid, team_size, region),
                             name=f"bot-{gid}", daemon=True)
        with _guilds_lock:
            _guilds[gid]["thread"] = t
        t.start()

    return jsonify({"ok": True, "started": started, "skipped_already_running": skipped})


@app.route("/api/status")
def api_status():
    with _guilds_lock:
        out = []
        for gid, g in _guilds.items():
            out.append({
                "guild_id": gid,
                "state": g["state"],
                "created": g["created"],
                "total": g["total"],
                "started_at": g["started_at"],
                "ended_at": g["ended_at"],
                "error": g["error"],
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
        loop = g.get("loop")
        task = g.get("task")
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
                stopped.append(g["guild_id"])
            except Exception:
                pass
    return jsonify({"ok": True, "stopped": stopped})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
