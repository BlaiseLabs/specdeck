"""Run history for the specdeck viewer: one SQLite file shared by every project.

The pytest process writes (project, run, one row per scenario, an event per
state change); the viewer server only reads, plus polling ``events`` to push
live updates. WAL mode lets both happen at once.

The *board* is what the viewer shows as "now": the latest known result of every
scenario, folded over the last complete run and every run after it, so running
one test from the UI updates that test without forgetting the others.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

FINAL = ("passed", "failed", "error", "blocked", "skipped")
BAD = ("failed", "error")
STATUS_ORDER = ("passed", "failed", "error", "blocked", "skipped", "notrun")

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  root TEXT UNIQUE NOT NULL,
  description TEXT,
  python TEXT,
  invocation_dir TEXT,
  full_args TEXT,
  created REAL,
  updated REAL
);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL,
  started REAL, finished REAL,
  status TEXT,
  pid INTEGER, trigger TEXT, args TEXT, full INTEGER,
  git_branch TEXT, git_sha TEXT,
  total INTEGER DEFAULT 0, passed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
  error INTEGER DEFAULT 0, blocked INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0,
  duration REAL
);
CREATE INDEX IF NOT EXISTS runs_by_project ON runs(project_id, id);
CREATE TABLE IF NOT EXISTS results(
  run_id INTEGER NOT NULL,
  scenario_id TEXT NOT NULL,
  seq INTEGER,
  nodeid TEXT,
  meta TEXT,
  status TEXT,
  started REAL,
  duration REAL,
  message TEXT,
  steps TEXT,
  PRIMARY KEY(run_id, scenario_id)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER, run_id INTEGER, kind TEXT, scenario_id TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS events_by_project ON events(project_id, id);
"""


def home() -> Path:
    return Path(os.environ.get("SPECDECK_HOME") or (Path.home() / ".specdeck"))


def db_path() -> Path:
    return home() / "history.db"


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s or "project"


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


class History:
    def __init__(self, path: Optional[os.PathLike] = None) -> None:
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=15, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    # -- plumbing ---------------------------------------------------------- #
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "History":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def _tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    def _all(self, sql: str, args: Iterable = ()) -> list:
        return [dict(r) for r in self.conn.execute(sql, tuple(args)).fetchall()]

    def _one(self, sql: str, args: Iterable = ()) -> Optional[dict]:
        r = self.conn.execute(sql, tuple(args)).fetchone()
        return dict(r) if r else None

    def _event(self, project_id: int, run_id: Optional[int], kind: str, scenario_id: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO events(project_id, run_id, kind, scenario_id, ts) VALUES (?,?,?,?,?)",
            (project_id, run_id, kind, scenario_id, time.time()),
        )

    # -- writes (pytest side) ---------------------------------------------- #
    def register_project(self, root, name: str, description: str = "", python: str = "",
                         invocation_dir: str = "") -> dict:
        root = str(Path(root).resolve())
        now = time.time()
        with self._tx():
            row = self._one("SELECT * FROM projects WHERE root=?", (root,))
            if row:
                self.conn.execute(
                    "UPDATE projects SET name=?, description=?, python=?, updated=? WHERE id=?",
                    (name, description, python, now, row["id"]),
                )
            else:
                base = _slug(name)
                slug, n = base, 2
                while self._one("SELECT 1 FROM projects WHERE slug=?", (slug,)):
                    slug, n = f"{base}-{n}", n + 1
                self.conn.execute(
                    "INSERT INTO projects(slug, name, root, description, python, invocation_dir, full_args, created, updated)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (slug, name, root, description, python, invocation_dir, "[]", now, now),
                )
        return self._one("SELECT * FROM projects WHERE root=?", (root,))

    def start_run(self, project_id: int, scenarios: list, *, args: list = (), full: bool = True,
                  trigger: str = "terminal", pid: Optional[int] = None, invocation_dir: str = "",
                  git_branch: str = "", git_sha: str = "") -> int:
        """``scenarios``: ``[{"id", "nodeid", "meta"}]`` in execution order, all queued."""
        now = time.time()
        with self._tx():
            cur = self.conn.execute(
                "INSERT INTO runs(project_id, started, status, pid, trigger, args, full, git_branch, git_sha, total)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project_id, now, "running", pid, trigger, json.dumps(list(args)), 1 if full else 0,
                 git_branch, git_sha, len(scenarios)),
            )
            run_id = cur.lastrowid
            self.conn.executemany(
                "INSERT OR REPLACE INTO results(run_id, scenario_id, seq, nodeid, meta, status) VALUES (?,?,?,?,?,?)",
                [(run_id, s["id"], i, s["nodeid"], json.dumps(s.get("meta") or {}), "queued")
                 for i, s in enumerate(scenarios)],
            )
            if full:
                self.conn.execute(
                    "UPDATE projects SET full_args=?, invocation_dir=? WHERE id=?",
                    (json.dumps(list(args)), invocation_dir, project_id),
                )
            self._event(project_id, run_id, "run_start")
            self.conn.execute(
                "DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id), 0) - 20000 FROM events)"
            )
        return run_id

    def set_running(self, project_id: int, run_id: int, scenario_id: str) -> None:
        with self._tx():
            self.conn.execute(
                "UPDATE results SET status='running', started=? WHERE run_id=? AND scenario_id=?",
                (time.time(), run_id, scenario_id),
            )
            self._event(project_id, run_id, "test_start", scenario_id)

    def set_result(self, project_id: int, run_id: int, scenario_id: str, status: str,
                   duration: float = 0.0, message: Optional[str] = None, steps: Optional[list] = None) -> None:
        with self._tx():
            self.conn.execute(
                "UPDATE results SET status=?, duration=?, message=?, steps=? WHERE run_id=? AND scenario_id=?",
                (status, duration, message, json.dumps(steps or []), run_id, scenario_id),
            )
            self._event(project_id, run_id, "test_end", scenario_id)

    def finish_run(self, project_id: int, run_id: int, status: Optional[str] = None) -> dict:
        now = time.time()
        with self._tx():
            counts = {r["status"]: r["n"] for r in self._all(
                "SELECT status, COUNT(*) n FROM results WHERE run_id=? GROUP BY status", (run_id,))}
            if status is None:
                status = "failed" if counts.get("failed") or counts.get("error") else "passed"
            run = self._one("SELECT started FROM runs WHERE id=?", (run_id,))
            self.conn.execute(
                "UPDATE runs SET finished=?, status=?, passed=?, failed=?, error=?, blocked=?, skipped=?, duration=?"
                " WHERE id=?",
                (now, status, counts.get("passed", 0), counts.get("failed", 0), counts.get("error", 0),
                 counts.get("blocked", 0), counts.get("skipped", 0), now - (run["started"] if run else now), run_id),
            )
            self._event(project_id, run_id, "run_end")
        return self._one("SELECT * FROM runs WHERE id=?", (run_id,))

    # -- reads (viewer side) ----------------------------------------------- #
    def projects(self) -> list:
        return self._all("SELECT * FROM projects ORDER BY updated DESC")

    def project(self, slug: str) -> Optional[dict]:
        return self._one("SELECT * FROM projects WHERE slug=?", (slug,))

    def last_event_id(self, project_id: int) -> int:
        row = self._one("SELECT MAX(id) m FROM events WHERE project_id=?", (project_id,))
        return int(row["m"] or 0) if row else 0

    def events_after(self, project_id: int, after: int) -> list:
        return self._all("SELECT * FROM events WHERE project_id=? AND id>? ORDER BY id LIMIT 500", (project_id, after))

    def reap(self, project_id: int) -> None:
        """Runs whose pytest process died without finishing become 'abandoned'."""
        for run in self._all("SELECT id, pid FROM runs WHERE project_id=? AND status='running'", (project_id,)):
            if not _alive(run["pid"]):
                self.finish_run(project_id, run["id"], status="abandoned")

    def _ordinals(self, project_id: int) -> dict:
        """Run id -> its number within the project (1, 2, 3...), which is what people see."""
        ids = [r["id"] for r in self._all("SELECT id FROM runs WHERE project_id=? ORDER BY id", (project_id,))]
        return {rid: i + 1 for i, rid in enumerate(ids)}

    def runs(self, project_id: int, limit: int = 100) -> list:
        runs = self._all("SELECT * FROM runs WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, limit))
        deltas = self._deltas(project_id)
        n = self._ordinals(project_id)
        for r in runs:
            r["n"] = n.get(r["id"])
            r["args"] = json.loads(r["args"] or "[]")
            d = deltas.get(r["id"], {})
            r["fixed"] = d.get("fixed", [])
            r["regressed"] = d.get("regressed", [])
            r["new"] = d.get("new", [])
            if r["status"] == "running":
                r["done"] = self._one(
                    "SELECT COUNT(*) n FROM results WHERE run_id=? AND status NOT IN ('queued','running')",
                    (r["id"],))["n"]
        return runs

    def scenario_history(self, project_id: int, scenario_id: str, limit: int = 40) -> list:
        n = self._ordinals(project_id)
        rows = self._all(
            "SELECT r.run_id, r.status, r.duration, r.message, u.started, u.trigger, u.full FROM results r"
            " JOIN runs u ON u.id = r.run_id"
            " WHERE u.project_id=? AND r.scenario_id=? AND r.status IN ('passed','failed','error','blocked','skipped')"
            " ORDER BY r.run_id DESC LIMIT ?",
            (project_id, scenario_id, limit),
        )[::-1]
        for r in rows:
            r["n"] = n.get(r["run_id"])
        return rows

    def _deltas(self, project_id: int, max_runs: int = 300) -> dict:
        """Per run: scenarios that started passing, stopped passing, or appeared."""
        floor = self._one(
            "SELECT MIN(id) m FROM (SELECT id FROM runs WHERE project_id=? ORDER BY id DESC LIMIT ?)",
            (project_id, max_runs),
        )["m"] or 0
        rows = self._all(
            "SELECT r.run_id, r.scenario_id, r.status FROM results r JOIN runs u ON u.id=r.run_id"
            " WHERE u.project_id=? AND u.id>=? AND r.status IN ('passed','failed','error','blocked','skipped')"
            " ORDER BY r.run_id, r.seq",
            (project_id, floor),
        )
        last: dict = {}
        out: dict = {}
        for row in rows:
            d = out.setdefault(row["run_id"], {"fixed": [], "regressed": [], "new": []})
            prev = last.get(row["scenario_id"])
            st = row["status"]
            if prev is None:
                if row["run_id"] != rows[0]["run_id"]:
                    d["new"].append(row["scenario_id"])
            elif prev != "passed" and st == "passed":
                d["fixed"].append(row["scenario_id"])
            elif prev == "passed" and st in ("failed", "error", "blocked"):
                d["regressed"].append(row["scenario_id"])
            last[row["scenario_id"]] = st
        return out

    def board(self, project_id: int, run_id: Optional[int] = None) -> dict:
        self.reap(project_id)
        project = self._one("SELECT * FROM projects WHERE id=?", (project_id,))
        runs_by_id = {r["id"]: r for r in self._all("SELECT * FROM runs WHERE project_id=?", (project_id,))}
        if run_id is not None:
            if run_id not in runs_by_id:
                raise KeyError(run_id)
            rows = self._all("SELECT * FROM results WHERE run_id=? ORDER BY seq", (run_id,))
        else:
            base = self._one(
                "SELECT MAX(id) m FROM runs WHERE project_id=? AND full=1 AND status IN ('passed','failed')",
                (project_id,),
            )["m"] or 0
            rows = self._all(
                "SELECT r.* FROM results r JOIN runs u ON u.id=r.run_id"
                " WHERE u.project_id=? AND u.id>=? ORDER BY r.run_id, r.seq",
                (project_id, base),
            )

        entries: dict = {}
        order: list = []
        for row in rows:
            sid = row["scenario_id"]
            run = runs_by_id.get(row["run_id"], {})
            active = run.get("status") == "running"
            meta = json.loads(row["meta"] or "{}")
            prev = entries.get(sid)
            if row["status"] in ("queued", "running"):
                if active:
                    e = dict(prev) if prev else _entry(sid, row, meta, "notrun")
                    e.update(_meta_fields(meta, row))
                    e["live"] = row["status"]
                    entries[sid] = e
                elif prev is None:  # an interrupted run never reached it
                    entries[sid] = _entry(sid, row, meta, "notrun")
            else:
                entries[sid] = _entry(sid, row, meta, row["status"])
            if sid not in order:
                order.append(sid)

        scenarios = [entries[s] for s in order]
        analysis = analyze(scenarios)
        counts = {k: 0 for k in STATUS_ORDER}
        for s in scenarios:
            counts[s["status"]] = counts.get(s["status"], 0) + 1
        counts["total"] = len(scenarios)

        groups: dict = {}
        for s in scenarios:
            g = groups.setdefault(s["group"], {"id": s["group"], "name": s["group_name"] or s["group"],
                                               "total": 0, **{k: 0 for k in STATUS_ORDER}})
            g["total"] += 1
            g[s["status"]] += 1

        active_run = next((r for r in sorted(runs_by_id.values(), key=lambda r: -r["id"])
                           if r["status"] == "running"), None)
        if active_run:
            active_run = dict(active_run)
            active_run["done"] = self._one(
                "SELECT COUNT(*) n FROM results WHERE run_id=? AND status NOT IN ('queued','running')",
                (active_run["id"],))["n"]
        finished = [r for r in runs_by_id.values() if r["status"] != "running"]
        last_run = dict(max(finished, key=lambda r: r["id"])) if finished else None
        viewed = dict(runs_by_id[run_id]) if run_id is not None else None
        deltas = self._deltas(project_id)
        delta_run = viewed or last_run
        delta = deltas.get(delta_run["id"], {}) if delta_run else {}
        trend = [
            {k: r[k] for k in ("id", "started", "finished", "status", "total", "passed", "failed",
                               "error", "blocked", "skipped", "duration")}
            for r in sorted(finished, key=lambda r: r["id"]) if r["full"]
        ][-40:]

        n = self._ordinals(project_id)
        for r in (viewed, active_run, last_run, *trend):
            if r is not None:
                r["n"] = n.get(r["id"])
        return {
            "project": {k: project[k] for k in ("slug", "name", "root", "description")},
            "view": {"run_id": run_id, "run": _run_public(viewed) if viewed else None},
            "active_run": _run_public(active_run) if active_run else None,
            "last_run": _run_public(last_run) if last_run else None,
            "counts": counts,
            "groups": list(groups.values()),
            "scenarios": scenarios,
            "analysis": analysis,
            "delta": {"run_id": delta_run["id"] if delta_run else None,
                      "run_n": n.get(delta_run["id"]) if delta_run else None, **{
                k: delta.get(k, []) for k in ("fixed", "regressed", "new")}},
            "trend": trend,
            "runs_total": len(runs_by_id),
        }

    def summary(self, project: dict) -> dict:
        b = self.board(project["id"])
        return {
            "slug": project["slug"], "name": project["name"], "root": project["root"],
            "description": project["description"], "counts": b["counts"],
            "active_run": b["active_run"], "last_run": b["last_run"], "updated": project["updated"],
        }


def _run_public(r: dict) -> dict:
    out = dict(r)
    out["args"] = json.loads(out.get("args") or "[]") if isinstance(out.get("args"), str) else out.get("args")
    return out


def _meta_fields(meta: dict, row: dict) -> dict:
    return {
        "name": meta.get("name") or row["scenario_id"],
        "group": meta.get("group") or "tests",
        "group_name": meta.get("group_name") or meta.get("group") or "tests",
        "description": meta.get("description"),
        "tags": meta.get("tags") or [],
        "depends_on": meta.get("depends_on") or [],
        "planned": meta.get("planned") or [],
        "file": meta.get("file"),
        "line": meta.get("line"),
        "function": meta.get("function"),
        "parametrized": bool(meta.get("parametrized")),
        "nodeid": row["nodeid"],
    }


def _entry(sid: str, row: dict, meta: dict, status: str) -> dict:
    e = {"id": sid, "status": status, "live": None}
    e.update(_meta_fields(meta, row))
    ran = status != "notrun"
    e["run_id"] = row["run_id"] if ran else None
    e["duration"] = row["duration"] if ran else None
    e["message"] = row["message"] if ran else None
    e["steps"] = json.loads(row["steps"] or "[]") if ran and row["steps"] else []
    return e


# --------------------------------------------------------------------------- #
# Analysis: where is it stuck, what depends on what
# --------------------------------------------------------------------------- #
def _closure(start: str, edges: dict) -> set:
    seen: set = set()
    stack = list(edges.get(start, ()))
    while stack:
        n = stack.pop()
        if n in seen or n == start:
            continue
        seen.add(n)
        stack.extend(edges.get(n, ()))
    return seen


def analyze(scenarios: list) -> dict:
    """Frontier (fix these first), load-bearing scenarios, and goal progress.

    - frontier: failing scenarios with no failing or blocked prerequisite, i.e.
      the root causes; ranked by how much downstream work they hold up.
    - critical: scenarios the most other scenarios depend on, transitively.
    - goals: every scenario nothing depends on but which depends on something —
      an end of a branch — with progress over itself plus all its prerequisites.
    """
    by = {s["id"]: s for s in scenarios}
    order = [s["id"] for s in scenarios]
    deps = {i: [d for d in by[i]["depends_on"] if d in by and d != i] for i in order}
    dependents: dict = {i: [] for i in order}
    for i in order:
        for d in deps[i]:
            dependents[d].append(i)
    down = {i: _closure(i, dependents) for i in order}
    up = {i: _closure(i, deps) for i in order}
    bad = set(BAD)
    stuckish = bad | {"blocked"}

    for i in order:
        s = by[i]
        s["dependents"] = list(dependents[i])
        s["downstream"] = len(down[i])
        s["holding_up"] = sum(1 for j in down[i] if by[j]["status"] != "passed") if s["status"] in bad else 0
        s["blocked_by"] = [a for a in up[i] if by[a]["status"] in bad] if s["status"] == "blocked" else []

    frontier = [i for i in order
                if by[i]["status"] in bad and not any(by[a]["status"] in stuckish for a in up[i])]
    frontier.sort(key=lambda i: (-by[i]["holding_up"], -by[i]["downstream"], order.index(i)))
    for i in order:  # blocked scenarios point at their root causes
        if by[i]["status"] == "blocked":
            roots = [f for f in frontier if f in up[i]]
            by[i]["blocked_by"] = roots or by[i]["blocked_by"]

    critical = sorted((i for i in order if down[i]), key=lambda i: (-len(down[i]), order.index(i)))[:8]

    goals = []
    for i in order:
        if dependents[i] or not deps[i]:
            continue
        closure = up[i] | {i}
        counts = {k: 0 for k in STATUS_ORDER}
        for j in closure:
            counts[by[j]["status"]] += 1
        if counts["passed"] == len(closure):
            state = "complete"
        elif any(by[j]["status"] in stuckish for j in closure):
            state = "stuck"
        else:
            state = "open"
        goals.append({
            "id": i, "total": len(closure), "counts": counts, "state": state,
            "stuck_at": [f for f in frontier if f in closure],
        })
    rank = {"stuck": 0, "open": 1, "complete": 2}
    goals.sort(key=lambda g: (rank[g["state"]], -g["total"], order.index(g["id"])))
    return {"frontier": frontier, "critical": critical, "goals": goals}
