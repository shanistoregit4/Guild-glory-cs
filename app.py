"""
SHANI VIP account generator — Railway web dashboard.

- Serves a dashboard at / with a form (team size, guild IDs, region).
- POST /api/start launches one generator task per guild ID, in parallel
  (one thread per guild; each guild uses a ThreadPoolExecutor with 70
  workers calling our own generate_single_account).
- GET /api/status returns per-guild progress.
- GET /api/logs returns the last 100 log lines; GET /api/logs/stream is a
  Server-Sent Events stream that pushes new lines live.
- POST /api/stop cancels all running guild tasks.
- POST /api/guilds/<guild_id>/stop stops/removes ONE guild only; other
  guilds keep running.

The generator is OUR code (our_gen.py, from fast_gen.py): Garena token
grant + MajorLogin + GetLoginData directly, no author server, no Vercel
reporting. After an account is generated, it is logged in FRESH via
jwt.py (token grant with the guest uid -> MajorLogin -> parse) to get the
real in-game account_id, a fresh JWT, and the account's own clientbp URL;
guild join uses our RequestJoinClan logic (jwt.py AES key/IV) against that
clientbp URL. The guest uid is only ever a login credential — it is never
displayed or tracked; log lines and the status API use the account_id.
Importing our_gen/jwt does NOT start anything (both are __main__-guarded);
our_gen only defines its Flask app object.
"""

import contextlib
import importlib.util
import io
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

import requests
from Crypto.Cipher import AES
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

APP_DIR = os.path.dirname(os.path.abspath(__file__))
GEN_PATH = os.path.join(APP_DIR, "our_gen.py")
JWT_PATH = os.path.join(APP_DIR, "jwt.py")

# Our generator's fixed name/password prefixes (fast_gen defaults).
NAME_PREFIX = "shani"
PASSWORD_PREFIX = "pak"
# 70 workers per guild, same concurrency as the old dashboard.
GEN_WORKERS = 70
# Stop a guild after this many consecutive generation failures.
MAX_CONSEC_FAILURES = 10

# Guild join (RequestJoinClan) — proven logic from guild_join_from_file.py.
# The generation-time jwt_token is NOT reused: after an account is created
# we do a FRESH login (token grant with the guest uid -> MajorLogin via
# jwt.py -> parse_major_login_response), which yields the real in-game
# account_id, a fresh JWT, and the account's own clientbp URL. The join
# POSTs to that clientbp URL with Bearer JWT. HTTP 400 = already in
# guild (harmless). The hardcoded clientbp URL is only a fallback when
# MajorLogin returns no URL.
CLIENTBP_FALLBACK_URL = "https://clientbp.ppmainecoonghj.com"

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
# stdout capture: the generator prints via print(); capture it into the log
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
# Generator modules (lazy import — importing our_gen builds its IP/device/
# UA pools, which prints a few lines and takes a few seconds)
# ---------------------------------------------------------------------------

_gen = None        # (our_gen module, jwt module)
_gen_lock = threading.Lock()
_gen_error = None


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_gen():
    """Return (our_gen, jwtmod), importing once. Safe: neither module runs
    anything on import (both are __main__-guarded)."""
    global _gen, _gen_error
    if _gen is not None:
        return _gen
    if _gen_error is not None:
        raise RuntimeError(f"generator failed to import earlier: {_gen_error}")
    with _gen_lock:
        if _gen is not None:
            return _gen
        try:
            _log("[dashboard] importing generator modules (this takes a few seconds)...")
            jwtmod = _load_module("dashboard_jwt", JWT_PATH)
            our_gen = _load_module("our_gen", GEN_PATH)
            if not hasattr(our_gen, "generate_single_account"):
                raise RuntimeError("our_gen has no generate_single_account")
            if not hasattr(jwtmod, "AES_KEY") or not hasattr(jwtmod, "AES_IV"):
                raise RuntimeError("jwt module has no AES_KEY/AES_IV")
            _gen = (our_gen, jwtmod)
            _log("[dashboard] generator modules imported OK")
            return _gen
        except Exception as exc:  # noqa: BLE001 - remember the failure
            _gen_error = exc
            raise


# ---------------------------------------------------------------------------
# Guild join (RequestJoinClan) — copied logic from guild_join_from_file.py.
# The generated account's jwt_token is the Bearer token; protobuf is
# {f1=guild_id, f2="PK"}, AES-CBC with jwt.py's key/IV.
# ---------------------------------------------------------------------------

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _pad(data):
    pad_len = 16 - (len(data) % 16)
    return data + bytes([pad_len]) * pad_len


def _clean_body_snippet(raw, limit=200):
    """First `limit` chars of a response body, printable only, one line."""
    if raw is None:
        return ""
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8", errors="replace")
    else:
        text = str(raw)
    cleaned = "".join(ch if ch.isprintable() or ch in "\n\t" else " "
                      for ch in text)
    return " ".join(cleaned.split())[:limit]


def login_account(jwtmod, our_gen, uid, password, region, guild_id, name=""):
    """Log one account in (guest uid -> token grant -> MajorLogin).

    Proven flow copied from guild_join_from_file.py: token grant with the
    GUEST uid (not the in-game account_id), then build_major_login_payload
    + major_login, then parse_major_login_response, which returns a
    _MajorLoginResAdapter OBJECT (.token = JWT, .account_id, .url).
    This is the SECOND login — exactly like the emote bot, on the
    original ppmainecoonghj.com endpoint. No ChooseRegion here: the
    region bind already happened once, in the gen, during account
    creation (first login, mainecoonnodex.com).
    Returns (info, error): info is the dict (account_id, jwt,
    clientbp_url, ...) or None; error names the failed step
    ("" on success). The guest uid/password live only inside the returned
    info dict — they are never logged or returned by the API.
    """
    try:
        dev_id = f"02-{uuid.uuid4()}"
        grant_body = {
            "client_id": jwtmod.APP_ID,
            "client_secret": jwtmod.CLIENT_SECRET,
            "client_type": 2,
            "device_id": dev_id,
            "password": str(password),
            "response_type": "token",
            "uid": int(uid),
        }
        try:
            r = requests.post(jwtmod.URL_TOKEN_GRANT,
                              headers=jwtmod.HEADERS_MSDK,
                              json=grant_body, verify=False, timeout=15)
        except Exception:
            time.sleep(3)
            try:
                r = requests.post(jwtmod.URL_TOKEN_GRANT,
                                  headers=jwtmod.HEADERS_MSDK,
                                  json=grant_body, verify=False, timeout=15)
            except Exception as e2:
                return None, f"token-grant network error: {e2}"
        if r.status_code != 200:
            return None, f"token-grant HTTP {r.status_code}"
        try:
            gd = r.json().get("data", {})
        except Exception:
            return None, "token-grant bad JSON"
        access_token = gd.get("access_token")
        open_id = gd.get("open_id")
        if not access_token or not open_id:
            return None, "token-grant missing tokens"
        try:
            enc_login = jwtmod.build_major_login_payload(
                access_token, open_id, f"Google|{uuid.uuid4()}")
        except Exception as e:
            return None, f"build MajorLogin payload: {e}"
        try:
            login_bytes = jwtmod.major_login(enc_login, access_token)
        except Exception as e:
            return None, f"MajorLogin request: {e}"
        if not login_bytes:
            return None, "MajorLogin empty reply"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                proto = jwtmod.parse_major_login_response(login_bytes)
        except Exception as e:
            return None, f"parse MajorLogin: {e}"
        # parse_major_login_response returns a _MajorLoginResAdapter
        # OBJECT (not a dict): .token = JWT, .account_id, .url,
        # .aes_ak = TCP key, .iv_i = TCP IV.
        if not proto or not getattr(proto, "token", None):
            return None, "MajorLogin no JWT"
        info = {
            "uid": str(uid),
            "password": str(password),
            "name": name,
            "account_id": getattr(proto, "account_id", None),
            "jwt": proto.token,
            "tcp_key": getattr(proto, "aes_ak", None),
            "tcp_iv": getattr(proto, "iv_i", None),
            "clientbp_url": getattr(proto, "url", None)
                            or CLIENTBP_FALLBACK_URL,
        }
        return info, ""
    except Exception as e:
        return None, f"unexpected: {e}"


def guild_join(jwtmod, info, guild_id):
    """Send RequestJoinClan for one freshly logged-in account.

    info: the dict from login_account() above (has jwt, clientbp_url).
    Returns (ok, status, detail): ok True ONLY on HTTP 200; status is the
    HTTP code or an error string; detail is a cleaned snippet of the
    response body (decrypted when possible) so the real reason for a
    failure is visible. A 400 is NOT assumed to mean "already in guild"
    — the caller checks `detail` for an already-in-guild marker first.
    """
    if not info or not info.get("jwt"):
        return False, "no jwt", ""
    proto = b"\x08" + _varint(int(guild_id)) + b"\x12\x02PK"
    body = AES.new(jwtmod.AES_KEY, AES.MODE_CBC, jwtmod.AES_IV).encrypt(
        _pad(proto))
    headers = {
        "Authorization": "Bearer " + info["jwt"],
        "Content-Type": "application/x-www-form-urlencoded",
        "ReleaseVersion": "OB55",
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "X-GA": "v1 1",
        "X-GA-SV": str(int(time.time())),
        "X-Unity-Version": "2018.4.12f1",
    }
    try:
        r = requests.post(info["clientbp_url"] + "/RequestJoinClan",
                          data=body, headers=headers, timeout=20)
        if r.status_code == 200:
            return True, 200, ""
        detail = _clean_body_snippet(r.content)
        # The error body may be AES-encrypted protobuf — try decrypting
        # it with the static key/IV to expose any readable error text.
        try:
            dec = jwtmod.dec_aes(r.content)
            dec_text = _clean_body_snippet(dec)
            if dec_text and dec_text not in detail:
                detail = (f"{detail} | decrypted: {dec_text}"
                          if detail else dec_text)
        except Exception:
            pass
        return False, r.status_code, detail or "(empty body)"
    except Exception as e:  # noqa: BLE001 - report as a join failure
        return False, str(e), ""


class _GuildFailed(Exception):
    """Raised when a guild run must stop with an error state."""


# ---------------------------------------------------------------------------
# Per-guild run state
# ---------------------------------------------------------------------------

# guild_id -> {
#   "guild_id", "state": "running"|"done"|"error"|"stopped",
#   "created": int, "total": int, "started_at": str, "ended_at": str|None,
#   "error": str|None, "thread": Thread|None, "stop_event": Event|None,
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
    """Generates team_size accounts for one guild with 70 workers, then
    joins each one to the guild. Runs in its own thread; one thread per
    guild, so multiple guilds run in parallel."""
    with _thread_guild_lock:
        _thread_guild[threading.get_ident()] = guild_id
    stop_event = threading.Event()
    with _guilds_lock:
        _guilds[guild_id]["stop_event"] = stop_event
    created = 0
    consecutive_failures = 0
    try:
        our_gen, jwtmod = get_gen()
        _log(f"[{guild_id}] starting: {team_size} accounts, region {region} "
             f"(our generator, 70 workers)", guild=guild_id)
        # Work in waves: each wave submits up to 70 generation attempts
        # (never more than the accounts still needed), waits for them all,
        # counts the successes, and retries the failures in the next wave.
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
                    # Fresh login (proven flow): yields the real in-game
                    # account_id, a fresh JWT and the account's own
                    # clientbp URL. The guest uid is only the login
                    # credential passed to login_account — it is never
                    # displayed, logged, or tracked anywhere.
                    gen_account_id = acc.get("account_id", "?")
                    info, login_err = login_account(
                        jwtmod, our_gen, acc.get("uid"), acc.get("password"),
                        region, guild_id)
                    if not info:
                        _log(f"[CREATED] Bot {created}/{team_size} | "
                             f"ID: {gen_account_id}", guild=guild_id)
                        _log(f"[LOGIN-FAIL] ID {gen_account_id} -> guild "
                             f"{guild_id}: {login_err}", guild=guild_id)
                        continue
                    account_id = info.get("account_id") or gen_account_id
                    _log(f"[CREATED] Bot {created}/{team_size} | "
                         f"ID: {account_id}", guild=guild_id)
                    # Join this account to the guild. A join failure does
                    # NOT stop generation of the remaining accounts.
                    # A 400 is only "already in guild" when the response
                    # body actually says so — a brand-new account cannot
                    # already be in the guild, so any other 400 is a real
                    # failure and its body is shown instead of a lie.
                    ok, status, detail = guild_join(jwtmod, info, guild_id)
                    if ok:
                        _log(f"[JOINED] ID {account_id} -> guild {guild_id}",
                             guild=guild_id)
                    elif status == 400 and "already" in str(detail).lower():
                        _log(f"[JOINED] ID {account_id} -> guild {guild_id} "
                             f"(already in guild)", guild=guild_id)
                    elif status == 400:
                        _log(f"[JOIN-FAIL] ID {account_id} -> guild "
                             f"{guild_id}: HTTP 400: {detail}", guild=guild_id)
                    else:
                        suffix = f": {detail}" if detail else ""
                        _log(f"[JOIN-FAIL] ID {account_id} -> guild "
                             f"{guild_id}: {status}{suffix}", guild=guild_id)
            finally:
                # On stop, drop queued futures immediately; running attempts
                # finish in the background and their results are discarded.
                ex.shutdown(wait=False, cancel_futures=True)
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

    # ---- check the generator imports before starting anything ----
    try:
        get_gen()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"generator failed to load: {exc}"}), 500

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
    thread notices within one wave and exits). In all cases the entry is
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
