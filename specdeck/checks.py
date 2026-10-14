"""Rule checks: how a rule is checked, in a form a machine can run (docs/RULE-CHECKS.md).

A requirement's ``check:`` is one primitive (a key of PRIMITIVES, plus options) or a composition
(``all`` / ``any`` / ``not``, or a list meaning all). It is evaluated against the change (what
differs from a base revision), the files as they are, and the last test run, and either holds,
is broken (with evidence) or ends in an error, which is reported and never counted as a break.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

PRIMITIVES = ("forbid_pattern", "forbid_import", "protect", "append_only", "co_change", "no_new_dependencies", "ruff",
              "test", "command")
COMPOSITIONS = ("all", "any", "not")
OPTIONS = ("paths", "except", "scope", "when")
NEEDS_CHANGE = ("protect", "append_only", "co_change", "no_new_dependencies")
MAX_EVIDENCE = 10
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".specdeck", ".tox", ".mypy_cache", "build",
             "dist"}


# --------------------------------------------------------------------------- globs
_GLOB_CACHE: dict = {}


def _glob_re(pattern: str):
    """A compiled regex for a glob relative to the project root; ``**`` crosses directories."""
    if pattern not in _GLOB_CACHE:
        out, i = "", 0
        while i < len(pattern):
            c = pattern[i]
            if pattern.startswith("**/", i):
                out, i = out + "(?:.*/)?", i + 3
            elif pattern.startswith("**", i):
                out, i = out + ".*", i + 2
            elif c == "*":
                out, i = out + "[^/]*", i + 1
            elif c == "?":
                out, i = out + "[^/]", i + 1
            else:
                out, i = out + re.escape(c), i + 1
        _GLOB_CACHE[pattern] = re.compile(out + r"\Z")
    return _GLOB_CACHE[pattern]


def matches(path: str, globs) -> bool:
    return any(_glob_re(g.strip("/")).match(path) for g in ([globs] if isinstance(globs, str) else globs or []))


def _in_scope(path: str, node: dict, default=("**",)) -> bool:
    return matches(path, node.get("paths") or list(default)) and not matches(path, node.get("except") or [])


# --------------------------------------------------------------------------- the change
class Change:
    """What differs between ``base`` and the working tree, from git. Untracked files count as added."""

    def __init__(self, root, base: str = "HEAD"):
        self.root, self.base = Path(root), base
        self.error: Optional[str] = None
        self.status: dict = {}            # path (relative to root) -> A, M, D, R
        self._added: dict = {}
        self._untracked: set = set()
        self._prefix = ""
        try:
            self._prefix = self._git("rev-parse", "--show-prefix").strip()
            for line in self._git("diff", "--name-status", "--relative", "--no-renames", base).splitlines():
                st, _, path = line.partition("\t")
                if path:
                    self.status[path] = st[:1]
            self._untracked = {p for p in self._git("ls-files", "--others", "--exclude-standard").splitlines() if p}
            for p in self._untracked:
                self.status[p] = "A"
        except (OSError, subprocess.CalledProcessError) as exc:
            msg = getattr(exc, "stderr", "") or str(exc)
            self.error = f"no change to check against {base}: {' '.join(str(msg).split())[:200]}"

    @property
    def available(self) -> bool:
        return self.error is None

    def _git(self, *args) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True,
                              timeout=60).stdout

    @property
    def changed(self) -> list:
        return sorted(self.status)

    def added_lines(self, path: str) -> list:
        """[(line number, text)] the change added to ``path``."""
        if path not in self._added:
            if path in self._untracked:
                try:
                    lines = (self.root / path).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = []
                self._added[path] = list(enumerate(lines, 1))
            else:
                out, n = [], 0
                try:
                    diff = self._git("diff", "-U0", "--relative", self.base, "--", path)
                except subprocess.CalledProcessError:
                    diff = ""
                for line in diff.splitlines():
                    m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                    if m:
                        n = int(m.group(1))
                    elif line.startswith("+") and not line.startswith("+++"):
                        out.append((n, line[1:]))
                        n += 1
                self._added[path] = out
        return self._added[path]

    def base_text(self, path: str) -> Optional[str]:
        try:
            return self._git("show", f"{self.base}:{self._prefix}{path}")
        except subprocess.CalledProcessError:
            return None


@dataclass
class Context:
    root: Path
    change: Change
    tests: Optional[dict] = None          # node id or scenario id -> outcome of the last run
    _files: Optional[list] = None

    def files(self) -> list:
        """Every file of the project as it is now, relative to the root."""
        if self._files is None:
            try:
                out = subprocess.run(["git", "-C", str(self.root), "ls-files", "--cached", "--others",
                                      "--exclude-standard"], capture_output=True, text=True, check=True,
                                     timeout=60).stdout.splitlines()
                self._files = sorted(p for p in set(out) if (self.root / p).is_file())
            except (OSError, subprocess.CalledProcessError):
                self._files = sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*")
                                     if p.is_file() and not SKIP_DIRS & set(p.relative_to(self.root).parts))
        return self._files

    def read(self, path: str) -> Optional[str]:
        p = self.root / path
        try:
            if p.stat().st_size > 2_000_000:
                return None
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None


# --------------------------------------------------------------------------- results
@dataclass
class Result:
    state: str                        # holds | broken | error
    evidence: list = field(default_factory=list)
    error: Optional[str] = None
    applicable: bool = True

    def to_dict(self) -> dict:
        d = {"state": self.state, "applicable": self.applicable}
        if self.evidence:
            d["evidence"] = self.evidence[:MAX_EVIDENCE]
        if self.error:
            d["error"] = self.error
        return d


def _holds() -> Result:
    return Result("holds")


def _broken(evidence) -> Result:
    return Result("broken", list(evidence)[:MAX_EVIDENCE])


def _error(msg: str) -> Result:
    return Result("error", error=msg)


# --------------------------------------------------------------------------- validation
def validate(node, where: str = "check") -> list:
    """Problems with a check, as sentences; empty when it is well formed."""
    if isinstance(node, list):
        return [p for i, n in enumerate(node) for p in validate(n, f"{where}[{i}]")] if node else \
            [f"{where} is an empty list"]
    if not isinstance(node, dict):
        return [f"{where} should be a mapping or a list of checks"]
    kinds = [k for k in node if k in PRIMITIVES or k in COMPOSITIONS]
    unknown = [k for k in node if k not in PRIMITIVES and k not in COMPOSITIONS and k not in OPTIONS]
    out = [f"{where} has unknown key(s) {', '.join(map(str, unknown))}; checks are {', '.join(PRIMITIVES)}, "
           f"combined with all, any, not; options are {', '.join(OPTIONS)}"] if unknown else []
    if len(kinds) != 1:
        return out + [f"{where} needs exactly one of {', '.join(PRIMITIVES + COMPOSITIONS)}"]
    kind, v = kinds[0], node[kinds[0]]
    if kind in ("all", "any"):
        out += validate(v if isinstance(v, list) else [v], f"{where}.{kind}") if isinstance(v, list) else \
            [f"{where}.{kind} should be a list of checks"]
    elif kind == "not":
        out += validate(v, f"{where}.not")
    elif kind == "forbid_pattern":
        if not isinstance(v, str) or not v:
            out.append(f"{where}.forbid_pattern should be a regular expression")
        else:
            try:
                re.compile(v)
            except re.error as exc:
                out.append(f"{where}.forbid_pattern does not compile: {exc}")
    elif kind in ("forbid_import", "protect", "append_only", "ruff", "test"):
        if not (isinstance(v, list) and v and all(isinstance(x, str) and x for x in v)):
            out.append(f"{where}.{kind} should be a list of names")
    elif kind == "co_change":
        if not (isinstance(v, dict) and v.get("changed") and v.get("requires")):
            out.append(f"{where}.co_change needs `changed:` and `requires:` lists of globs")
    elif kind == "no_new_dependencies":
        if not (v is True or (isinstance(v, dict) and isinstance(v.get("allow", []), list))):
            out.append(f"{where}.no_new_dependencies should be true or {{allow: [names]}}")
    elif kind == "command":
        if not isinstance(v, str) or not v.strip():
            out.append(f"{where}.command should be a shell command")
    if "scope" in node and node["scope"] not in ("added", "files"):
        out.append(f"{where}.scope should be `added` or `files`")
    for opt in ("paths", "except"):
        if opt in node and not (isinstance(node[opt], list) and all(isinstance(x, str) for x in node[opt])):
            out.append(f"{where}.{opt} should be a list of globs")
    if "when" in node and not (isinstance(node["when"], dict) and isinstance(node["when"].get("changed"), list)):
        out.append(f"{where}.when should be {{changed: [globs]}}")
    return out


# --------------------------------------------------------------------------- evaluation
def evaluate(node, ctx: Context) -> Result:
    """Evaluate a validated check. The top-level result says whether the rule applied (``when``)."""
    if isinstance(node, list):
        node = {"all": node}
    if "when" in node and ctx.change.available:
        if not any(matches(p, node["when"]["changed"]) for p in ctx.change.changed):
            return Result("holds", applicable=False)
    kind = next(k for k in node if k in PRIMITIVES or k in COMPOSITIONS)
    if kind in NEEDS_CHANGE or (kind in ("forbid_pattern", "ruff") and node.get("scope", "added") == "added") \
            or (kind == "forbid_import" and node.get("scope") == "added"):
        if not ctx.change.available:
            return _error(ctx.change.error)
    try:
        return globals()[f"_{kind}"](node, ctx)
    except Exception as exc:  # noqa: BLE001 - a check that crashes is an error, never a break
        return _error(f"{kind} failed: {exc!r}"[:300])


def _all(node, ctx) -> Result:
    rs = [evaluate(n, ctx) for n in node["all"]]
    if any(r.state == "broken" for r in rs):
        return _broken(e for r in rs if r.state == "broken" for e in r.evidence)
    errs = [r.error for r in rs if r.state == "error"]
    return _error("; ".join(errs)) if errs else _holds()


def _any(node, ctx) -> Result:
    rs = [evaluate(n, ctx) for n in node["any"]]
    if any(r.state == "holds" for r in rs):
        return _holds()
    errs = [r.error for r in rs if r.state == "error"]
    return _error("; ".join(errs)) if errs else _broken(e for r in rs for e in r.evidence)


def _not(node, ctx) -> Result:
    r = evaluate(node["not"], ctx)
    if r.state == "error":
        return r
    return _holds() if r.state == "broken" else _broken([f"{describe(node['not'])} held"])


def _forbid_pattern(node, ctx) -> Result:
    rx = re.compile(node["forbid_pattern"])
    hits = []
    if node.get("scope", "added") == "added":
        for p in ctx.change.changed:
            if ctx.change.status.get(p) != "D" and _in_scope(p, node):
                hits += [f"{p}:{n}: {t.strip()[:100]}" for n, t in ctx.change.added_lines(p) if rx.search(t)]
    else:
        for p in (f for f in ctx.files() if _in_scope(f, node)):
            text = ctx.read(p)
            if text is not None:
                hits += [f"{p}:{n}: {t.strip()[:100]}" for n, t in enumerate(text.splitlines(), 1) if rx.search(t)]
    return _broken(hits) if hits else _holds()


def _imported(tree) -> list:
    """[(line, module)] for import statements and importlib.import_module("x") / __import__("x") calls."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(n.lineno, a.name) for a in n.names]
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
            out.append((n.lineno, n.module))
        elif isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name in ("import_module", "__import__"):
                out.append((n.lineno, n.args[0].value))
    return out


def _forbid_import(node, ctx) -> Result:
    banned = node["forbid_import"]
    added_only = node.get("scope") == "added"
    files = [p for p in (ctx.change.changed if added_only else ctx.files())
             if p.endswith(".py") and _in_scope(p, node, ("**/*.py",)) and ctx.change.status.get(p) != "D"]
    hits = []
    for p in files:
        text = ctx.read(p)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        lines = {n for n, _ in ctx.change.added_lines(p)} if added_only else None
        for line, mod in _imported(tree):
            if (lines is None or line in lines) and any(mod == b or mod.startswith(b + ".") for b in banned):
                hits.append(f"{p}:{line}: imports {mod}")
    return _broken(hits) if hits else _holds()


def _protect(node, ctx) -> Result:
    hits = [f"{p} {'deleted' if ctx.change.status[p] == 'D' else 'changed'}" for p in ctx.change.changed
            if matches(p, node["protect"])]
    return _broken(hits) if hits else _holds()


def _append_only(node, ctx) -> Result:
    """Files under these globs may be added, never changed or deleted: an archive."""
    hits = [f"{p} {'deleted' if ctx.change.status[p] == 'D' else 'changed'}" for p in ctx.change.changed
            if matches(p, node["append_only"]) and ctx.change.status[p] != "A"]
    return _broken(hits) if hits else _holds()


def _co_change(node, ctx) -> Result:
    spec = node["co_change"]
    touched = [p for p in ctx.change.changed if matches(p, spec["changed"])]
    if touched and not any(matches(p, spec["requires"]) for p in ctx.change.changed):
        return _broken([f"{p} changed, but nothing in {', '.join(spec['requires'])}" for p in touched])
    return _holds()


_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dependencies(path: str, text: Optional[str]) -> set:
    if not text:
        return set()
    name = path.rsplit("/", 1)[-1]
    out: set = set()
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return set()
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            out |= set((data.get(key) or {}).keys())
        return out
    if name == "pyproject.toml":
        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]
        data = tomllib.loads(text)
        project = data.get("project") or {}
        reqs = list(project.get("dependencies") or [])
        for group in (project.get("optional-dependencies") or {}).values():
            reqs += list(group)
        for group in (data.get("dependency-groups") or {}).values():
            reqs += [r for r in group if isinstance(r, str)]
        poetry = (data.get("tool") or {}).get("poetry") or {}
        out |= {k for k in (poetry.get("dependencies") or {}) if k != "python"}
        for group in (poetry.get("group") or {}).values():
            out |= set((group.get("dependencies") or {}).keys())
        out |= {m.group(1) for m in map(_NAME.match, reqs) if m}
        return {_norm(n) for n in out}
    for line in text.splitlines():           # requirements*.txt
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            m = _NAME.match(line)
            if m:
                out.add(_norm(m.group(1)))
    return out


def _is_dependency_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in ("pyproject.toml", "package.json") or bool(re.match(r"requirements.*\.txt$", name))


def _no_new_dependencies(node, ctx) -> Result:
    v = node["no_new_dependencies"]
    allow = {_norm(a) for a in (v.get("allow") or [])} if isinstance(v, dict) else set()
    hits = []
    for p in ctx.change.changed:
        if _is_dependency_file(p) and ctx.change.status.get(p) != "D":
            new = _dependencies(p, ctx.read(p)) - _dependencies(p, ctx.change.base_text(p)) - allow
            hits += [f"{p}: adds {n}" for n in sorted(new)]
    return _broken(hits) if hits else _holds()


def _ruff(node, ctx) -> Result:
    exe = shutil.which("ruff")
    cmd = [exe] if exe else [sys.executable, "-m", "ruff"]
    added_only = node.get("scope", "added") == "added"
    files = [p for p in (ctx.change.changed if added_only else ctx.files())
             if p.endswith(".py") and _in_scope(p, node, ("**/*.py",)) and ctx.change.status.get(p) != "D"
             and (ctx.root / p).is_file()]
    if not files:
        return _holds()
    try:
        out = subprocess.run([*cmd, "check", "--isolated", "--no-cache", "--output-format", "json", "--select",
                              ",".join(node["ruff"]), *files], cwd=ctx.root, capture_output=True, text=True,
                             timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _error(f"ruff could not run: {exc}")
    try:
        findings = json.loads(out.stdout or "[]")
    except ValueError:
        return _error("ruff is not installed" if "No module named ruff" in out.stderr else
                      f"ruff gave no JSON: {out.stderr.strip()[:200]}")
    hits = []
    for f in findings:
        p = Path(f["filename"]).resolve().relative_to(ctx.root.resolve()).as_posix()
        line = (f.get("location") or {}).get("row")
        if added_only and line not in {n for n, _ in ctx.change.added_lines(p)}:
            continue
        hits.append(f"{p}:{line}: {f.get('code')} {f.get('message', '')[:80]}")
    return _broken(hits) if hits else _holds()


def _test(node, ctx) -> Result:
    if ctx.tests is None:
        return _error("no test results to check against; run pytest first")
    bad, missing = [], []
    for t in node["test"]:
        status = ctx.tests.get(t)
        if status is None or status in ("notrun", "not run", "skipped"):
            missing.append(t)
        elif status != "passed":
            bad.append(f"{t} {status}")
    if bad:
        return _broken(bad)
    return _error(f"not run: {', '.join(missing)}") if missing else _holds()


def _command(node, ctx) -> Result:
    try:
        p = subprocess.run(node["command"], shell=True, cwd=ctx.root, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return _error("command timed out")
    if p.returncode == 0:
        return _holds()
    tail = [line for line in (p.stdout + p.stderr).strip().splitlines() if line.strip()][-3:]
    return _broken([f"`{node['command']}` exited {p.returncode}"] + [t[:120] for t in tail])


# --------------------------------------------------------------------------- describing a check
def describe(node) -> str:
    """A short description of a check, for people and agents reading AGENTS.md."""
    if isinstance(node, list):
        return " and ".join(describe(n) for n in node)
    kind = next((k for k in node if k in PRIMITIVES or k in COMPOSITIONS), None)
    where = ""
    if node.get("paths"):
        where += f" in {', '.join(node['paths'])}"
    if node.get("except"):
        where += f" (except {', '.join(node['except'])})"
    scope = node.get("scope", "files" if kind == "forbid_import" else "added")
    lines = "added lines" if scope == "added" else "files"
    v = node.get(kind)
    text = {
        "all": lambda: " and ".join(describe(n) for n in v),
        "any": lambda: "one of: " + "; ".join(describe(n) for n in v),
        "not": lambda: f"not ({describe(v)})",
        "forbid_pattern": lambda: f"no match for `{v}` in {lines}{where}",
        "forbid_import": lambda: f"no import of {', '.join(v)}{where}",
        "protect": lambda: f"{', '.join(v)} left unchanged",
        "append_only": lambda: f"files in {', '.join(v)} only added, never changed or deleted",
        "co_change": lambda: f"a change to {', '.join(v['changed'])} also changes {', '.join(v['requires'])}",
        "no_new_dependencies": lambda: "no new dependencies" + (
            f" (except {', '.join(v['allow'])})" if isinstance(v, dict) and v.get("allow") else ""),
        "ruff": lambda: f"ruff {', '.join(v)} clean in {lines}{where}",
        "test": lambda: f"test {', '.join(v)} passes",
        "command": lambda: f"`{v}` succeeds",
    }.get(kind, lambda: "?")()
    if node.get("when"):
        text += f", when {', '.join(node['when']['changed'])} change"
    return text


# --------------------------------------------------------------------------- whole-project helpers
def run_all(reqs, root, base: str = "HEAD", tests: Optional[dict] = None) -> dict:
    """{requirement id: Result} for every requirement with a well-formed check."""
    checked = [r for r in reqs.items.values() if r.check is not None and not validate(r.check)]
    if not checked:
        return {}
    ctx = Context(Path(root), Change(root, base), tests)
    return {r.id: evaluate(r.check, ctx) for r in checked}


def merge(rep: dict, results: dict) -> dict:
    """Fold check results into a requirements report: a broken check makes the requirement failing;
    a holding check verifies a requirement no test covers."""
    for d in rep.get("requirements", []):
        r = results.get(d["id"])
        if r is None:
            continue
        d["check_result"] = r.to_dict()
        if r.state == "broken":
            d["status"] = "failing"
        elif r.state == "holds" and d.get("status") in ("untested", "not-run"):
            d["status"] = "met"
    counts = {k: 0 for k in ("met", "failing", "blocked", "not-run", "untested")}
    for d in rep.get("requirements", []):
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    rep["counts"] = {**counts, "total": len(rep.get("requirements", []))}
    return rep


def record(path: Path, rep: dict, *, base: str, root, full: bool) -> None:
    """Append this run to the breaks log: per requirement, whether it applied and held."""
    sha = branch = ""
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True,
                             text=True, timeout=10).stdout.strip()
        branch = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    from datetime import datetime, timezone

    rows = {}
    for d in rep.get("requirements", []):
        cr = d.get("check_result") or {}
        if d["status"] not in ("met", "failing") and not cr:
            continue
        rows[d["id"]] = {"status": d["status"], "applicable": cr.get("applicable", True),
                         **({"check": cr["state"]} if cr else {}),
                         **({"evidence": cr["evidence"][:3]} if cr.get("evidence") else {})}
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sha": sha, "branch": branch,
            "base": base, "full": full, "requirements": rows}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def summarize(path: Path) -> list:
    """Per requirement from the breaks log: runs it applied to, runs broken, commits broken, last break."""
    stats: dict = {}
    if not path.exists():
        return []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            run = json.loads(raw)
        except ValueError:
            continue
        for rid, r in (run.get("requirements") or {}).items():
            s = stats.setdefault(rid, {"id": rid, "runs": 0, "applied": 0, "broken": 0, "commits": set(),
                                       "last_broken": None, "last_evidence": [], "checked": False})
            s["runs"] += 1
            s["checked"] = s["checked"] or "check" in r
            if not r.get("applicable", True):
                continue
            s["applied"] += 1
            if r.get("status") == "failing":
                s["broken"] += 1
                s["commits"].add(run.get("sha") or "?")
                s["last_broken"] = {"ts": run.get("ts"), "sha": run.get("sha")}
                s["last_evidence"] = r.get("evidence") or s["last_evidence"]
    out = []
    for s in stats.values():
        s["commits"] = len(s["commits"])
        s["rate"] = round(s["broken"] / s["applied"], 3) if s["applied"] else None
        out.append(s)
    return sorted(out, key=lambda s: (-(s["rate"] or 0), -s["broken"], s["id"]))
