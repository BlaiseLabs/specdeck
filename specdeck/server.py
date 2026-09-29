"""The specdeck viewer: a small local web app over the run history.

Standard library only. It binds 127.0.0.1 and nothing else, and it rejects any
request whose Host header isn't a loopback name (so a web page can't reach it by
DNS rebinding). Writes (editing a test, starting a run) also need a per-process
token that is only embedded in the page the server itself serves.

One server serves every project that has recorded a run: /p/<project>.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

from . import editing
from .history import History, db_path, home
from .recorder import child_env

WEB = Path(__file__).parent / "web"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
VERSION = "0.2.0"


class Launcher:
    """pytest runs started from the viewer, at most one per project."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.procs: dict = {}  # slug -> dict(proc, started, log, cmd, scope)
        self.version = 0

    def _bump(self) -> None:
        with self.lock:
            self.version += 1

    def status(self, slug: str) -> Optional[dict]:
        with self.lock:
            info = self.procs.get(slug)
            if info is None:
                return None
            rc = info["proc"].poll()
            out = {"pid": info["proc"].pid, "started": info["started"], "scope": info["scope"],
                   "running": rc is None, "returncode": rc, "log": str(info["log"])}
        if rc not in (None, 0, 1, 2):  # 0 pass, 1 test failures, 2 interrupted
            try:
                out["log_tail"] = Path(info["log"]).read_text(errors="replace")[-2000:]
            except OSError:
                pass
        return out

    def start(self, slug: str, cmd: list, cwd: str, scope: str) -> dict:
        with self.lock:
            cur = self.procs.get(slug)
            if cur is not None and cur["proc"].poll() is None:
                raise editing.Conflict("A run started from the viewer is still going. Stop it or wait for it.")
            log_dir = home() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log = log_dir / f"run-{slug}.log"
            fh = open(log, "wb")
            env = child_env(SPECDECK_TRIGGER="ui", PYTHONUNBUFFERED="1")
            try:
                proc = subprocess.Popen(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            except OSError as exc:
                raise editing.EditError(f"could not start pytest: {exc}") from exc
            finally:
                fh.close()
            self.procs[slug] = {"proc": proc, "started": time.time(), "log": log, "cmd": cmd, "scope": scope}
            self.version += 1
        threading.Thread(target=self._watch, args=(proc,), daemon=True).start()
        return {"pid": proc.pid, "cmd": cmd}

    def _watch(self, proc: subprocess.Popen) -> None:
        proc.wait()
        self._bump()

    def stop(self, slug: str) -> bool:
        with self.lock:
            info = self.procs.get(slug)
            if info is None or info["proc"].poll() is not None:
                return False
            try:
                os.killpg(info["proc"].pid, signal.SIGINT)  # pytest finishes the session as "interrupted"
            except OSError:
                info["proc"].send_signal(signal.SIGINT)
        return True


class App:
    def __init__(self, port: int, allow_hosts: tuple = ()) -> None:
        self.port = port
        self.token = secrets.token_urlsafe(18)
        self.allow = LOOPBACK | {h.lower() for h in allow_hosts}
        self.launcher = Launcher()

    def history(self) -> History:
        return History(db_path())


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _project(h: History, slug: str) -> dict:
    p = h.project(slug)
    if p is None:
        raise HttpError(404, f"no project called {slug!r} has recorded a run yet")
    return p


def _scenario(board: dict, sid: str) -> dict:
    for s in board["scenarios"]:
        if s["id"] == sid:
            return s
    raise HttpError(404, f"scenario {sid!r} is not on the board")


def _test_file(project: dict, scenario: dict) -> Path:
    root = Path(project["root"]).resolve()
    rel = scenario.get("file")
    if not rel or not scenario.get("function"):
        raise HttpError(400, "this scenario has no source location recorded; run it once with --specdeck-live")
    path = (root / rel).resolve()
    if root not in path.parents:
        raise HttpError(403, "the test file is outside the project root")
    if not path.is_file():
        raise HttpError(404, f"{rel} does not exist any more")
    return path


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"specdeck/{VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        # -- plumbing -------------------------------------------------------- #
        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip().lower()
            if host.startswith("["):
                name = host[1:].split("]", 1)[0]
            else:
                name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
            return name in app.allow

        def _send(self, status: int, body, ctype: str = "application/json; charset=utf-8",
                  headers: Optional[dict] = None) -> None:
            data = body if isinstance(body, bytes) else (
                body.encode("utf-8") if isinstance(body, str) else json.dumps(body, default=str).encode("utf-8"))
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _body(self) -> dict:
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                raise HttpError(415, "send JSON")
            if not secrets.compare_digest(self.headers.get("X-Specdeck-Token") or "", app.token):
                raise HttpError(403, "missing or stale token; reload the page")
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                raise HttpError(413, "request too large")
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                raise HttpError(400, "invalid JSON")

        def do_GET(self):
            self._dispatch("GET")

        def do_HEAD(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                return self._send(403, {"error": "host not allowed"})
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                for m, rx, name in ROUTES:
                    if m == method:
                        mt = rx.fullmatch(url.path)
                        if mt:
                            return getattr(self, name)(q, *[unquote(g) for g in mt.groups()])
                raise HttpError(404, "not found")
            except HttpError as e:
                self._send(e.status, {"error": str(e)})
            except editing.EditError as e:
                self._send(e.status, {"error": str(e)})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:  # pragma: no cover - last resort
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        # -- pages ------------------------------------------------------------ #
        def page(self, q, *_):
            html = (WEB / "index.html").read_text(encoding="utf-8").replace("{{TOKEN}}", app.token)
            self._send(200, html, "text/html; charset=utf-8",
                       {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "img-src 'self' data:; frame-ancestors 'none'"})

        def static(self, q, name):
            path = (WEB / name).resolve()
            if WEB.resolve() not in path.parents or not path.is_file():
                raise HttpError(404, "not found")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, path.read_bytes(), ctype)

        # -- api -------------------------------------------------------------- #
        def health(self, q):
            self._send(200, {"app": "specdeck", "version": VERSION, "db": str(db_path()), "pid": os.getpid()})

        def projects(self, q):
            with app.history() as h:
                out = []
                for p in h.projects():
                    s = h.summary(p)
                    s["launch"] = app.launcher.status(p["slug"])
                    out.append(s)
            self._send(200, {"projects": out})

        def board(self, q, slug):
            with app.history() as h:
                p = _project(h, slug)
                run = q.get("run")
                try:
                    b = h.board(p["id"], int(run) if run else None)
                except (KeyError, ValueError):
                    raise HttpError(404, f"run {run} not found")
            b["launch"] = app.launcher.status(slug)
            self._send(200, b)

        def runs(self, q, slug):
            with app.history() as h:
                p = _project(h, slug)
                self._send(200, {"runs": h.runs(p["id"], limit=int(q.get("limit", 100)))})

        def scenario_history(self, q, slug):
            with app.history() as h:
                p = _project(h, slug)
                self._send(200, {"history": h.scenario_history(p["id"], q.get("id", ""))})

        def source(self, q, slug):
            with app.history() as h:
                p = _project(h, slug)
                s = _scenario(h.board(p["id"]), q.get("id", ""))
            path = _test_file(p, s)
            model = editing.load(path, s["function"], s.get("line"))
            model["file"] = s["file"]
            self._send(200, model)

        def edit(self, q, slug):
            body = self._body()
            with app.history() as h:
                p = _project(h, slug)
                s = _scenario(h.board(p["id"]), str(body.get("id", "")))
            path = _test_file(p, s)
            res = editing.apply(path, s["function"], str(body.get("fingerprint", "")), body.get("edits") or {},
                                line=s.get("line"), dry_run=bool(body.get("dry_run")), display_name=s["file"])
            if res.get("model"):
                res["model"]["file"] = s["file"]
            self._send(200, res)

        def run(self, q, slug):
            body = self._body()
            scope = body.get("scope", "all")
            with app.history() as h:
                p = _project(h, slug)
                board = h.board(p["id"])
            python = p["python"] or sys.executable
            if scope == "all":
                args = json.loads(p["full_args"] or "[]")
                cwd = p["invocation_dir"] or p["root"]
            else:
                if scope == "failing":
                    ids = {s["id"] for s in board["scenarios"] if s["status"] in ("failed", "error", "blocked")}
                else:
                    ids = set(body.get("ids") or [])
                nodeids = [s["nodeid"] for s in board["scenarios"] if s["id"] in ids and s.get("nodeid")]
                if not nodeids:
                    raise HttpError(400, "nothing to run")
                args, cwd = nodeids, p["root"]
            if not Path(cwd).is_dir():
                raise HttpError(400, f"{cwd} does not exist any more")
            cmd = [python, "-m", "pytest", *args, "--specdeck-live", "--specdeck-port", str(app.port)]
            info = app.launcher.start(slug, cmd, cwd, scope)
            self._send(200, {"started": True, **info})

        def stop(self, q, slug):
            self._body()
            self._send(200, {"stopped": app.launcher.stop(slug)})

        def stream(self, q, slug):
            with app.history() as h:
                p = _project(h, slug)
                last = h.last_event_id(p["id"])
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(b"retry: 1500\n\n")
                self.wfile.flush()
                seen_launch = app.launcher.version
                ping = time.time()
                while True:
                    time.sleep(0.2)
                    rows = h.events_after(p["id"], last)
                    lv = app.launcher.version
                    if rows or lv != seen_launch:
                        if rows:
                            last = rows[-1]["id"]
                        seen_launch = lv
                        payload = {"last": last, "kinds": sorted({r["kind"] for r in rows}) or ["launch"]}
                        self.wfile.write(f"event: change\ndata: {json.dumps(payload)}\n\n".encode())
                        self.wfile.flush()
                        ping = time.time()
                    elif time.time() - ping > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        ping = time.time()

    ROUTES = [
        ("GET", re.compile(r"/"), "page"),
        ("GET", re.compile(r"/p/([^/]+)/?"), "page"),
        ("GET", re.compile(r"/static/([\w.-]+)"), "static"),
        ("GET", re.compile(r"/api/health"), "health"),
        ("GET", re.compile(r"/api/projects"), "projects"),
        ("GET", re.compile(r"/api/p/([^/]+)/board"), "board"),
        ("GET", re.compile(r"/api/p/([^/]+)/runs"), "runs"),
        ("GET", re.compile(r"/api/p/([^/]+)/history"), "scenario_history"),
        ("GET", re.compile(r"/api/p/([^/]+)/source"), "source"),
        ("GET", re.compile(r"/api/p/([^/]+)/stream"), "stream"),
        ("POST", re.compile(r"/api/p/([^/]+)/edit"), "edit"),
        ("POST", re.compile(r"/api/p/([^/]+)/run"), "run"),
        ("POST", re.compile(r"/api/p/([^/]+)/stop"), "stop"),
    ]
    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_server(port: int, allow_hosts: tuple = ()) -> tuple:
    app = App(port, allow_hosts)
    server = _Server(("127.0.0.1", port), make_handler(app))
    app.port = server.server_address[1]
    return server, app


def serve(port: int, allow_hosts: tuple = ()) -> int:
    try:
        server, app = make_server(port, allow_hosts)
    except OSError as exc:
        print(f"specdeck: cannot listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1
    print(f"specdeck viewer: http://127.0.0.1:{app.port}/  (history: {db_path()})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
