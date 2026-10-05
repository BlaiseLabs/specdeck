"""Mine real bugs from the workspace's git history.

A fix commit C that changes both source and tests gives a real bug: C's tests against C^'s
source. A case is kept when the tests C touched pass at C and some of them fail with the
source rolled back, without import or collection errors (a rolled-back source that simply
lacks a feature the new test imports is not a bug). Nothing here calls a model.

    python3 realbugs.py            # writes data/realbugs.json
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path("/workspaces/python")
REPOS = ["FluencyGen", "SubtitleMixerKB", "NihongoArcade", "SubtitleMixerStudio", "KaishaQuest", "SubtitleMixer",
         "SubtitleAcademy", "NihongoLab_v1", "tapedeck", "specdeck", "dx_opus_mock_lib", "proofdeck", "dx-spec-viewer"]
TEST = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$")
_FAIL = re.compile(r"^(FAILED|ERROR) (.+?)(?: - (.*))?$")
LOUD = ("ImportError", "ModuleNotFoundError", "AttributeError: module", "cannot import name", "SyntaxError",
        "fixture '")


def git(repo, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def candidates(repo: str) -> list:
    out = []
    for c in git(ROOT / repo, "log", "--format=%H", "-i", "-E", "--grep=fix|bug").split():
        files = [f for f in git(ROOT / repo, "show", "--name-only", "--format=", c).splitlines() if f]
        tests = [f for f in files if TEST.search(f) and f.endswith(".py")]
        src = [f for f in files if f.endswith(".py") and not TEST.search(f)]
        if tests and src:
            out.append((repo, c, src, tests))
    return out


def pytest(tree: Path, tests: list) -> dict:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(tree), str(tree / "src")])}
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rfE", "--tb=line", "-p", "no:cacheprovider",
                            "-p", "no:specdeck", "-p", "no:randomly", *tests],
                           cwd=tree, env=env, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        return {"rc": "timeout", "failed": [], "loud": True, "tail": "timeout"}
    failed, loud = [], False
    for line in p.stdout.splitlines():
        m = _FAIL.match(line)
        if m:
            failed.append(m.group(2))
            if m.group(1) == "ERROR" or any(x in (m.group(3) or "") for x in LOUD):
                loud = True
    if any(x in p.stdout for x in ("ImportError while importing", "errors during collection")):
        loud = True
    tail = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]
    return {"rc": p.returncode, "failed": failed, "loud": loud, "tail": tail}


def check(cand) -> dict:
    repo, c, src, tests = cand
    tmp = Path(tempfile.mkdtemp(prefix="realbug-"))
    tree = tmp / repo
    try:
        subprocess.run(["git", "-C", str(ROOT / repo), "worktree", "add", "-q", "--detach", str(tree), c],
                       check=True, capture_output=True)
        present = [t for t in tests if (tree / t).exists()]
        if not present:
            return {"repo": repo, "commit": c, "keep": False, "why": "tests deleted"}
        fixed = pytest(tree, present)
        if fixed["rc"] != 0:
            return {"repo": repo, "commit": c, "keep": False, "why": f"tests fail at the fix: {fixed['tail']}"}
        for f in src:   # roll the source back to before the fix
            try:
                (tree / f).write_text(git(ROOT / repo, "show", f"{c}^:{f}"))
            except subprocess.CalledProcessError:
                (tree / f).unlink(missing_ok=True)   # file added by the fix
        buggy = pytest(tree, present)
        keep = bool(buggy["failed"]) and not buggy["loud"]
        diff = git(ROOT / repo, "diff", f"{c}^", c, "--", *src)
        return {"repo": repo, "commit": c, "keep": keep, "src": src, "tests": present, "failing": buggy["failed"],
                "why": "ok" if keep else ("no failure" if not buggy["failed"] else f"loud: {buggy['tail']}"),
                "subject": git(ROOT / repo, "log", "-1", "--format=%s", c).strip(),
                "fix_lines": sum(1 for l in diff.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---"))}
    except Exception as exc:  # noqa: BLE001
        return {"repo": repo, "commit": c, "keep": False, "why": repr(exc)[:200]}
    finally:
        subprocess.run(["git", "-C", str(ROOT / repo), "worktree", "remove", "--force", str(tree)], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    cands = [c for r in REPOS if (ROOT / r / ".git").exists() for c in candidates(r)]
    print(f"{len(cands)} candidate fix commits", flush=True)
    with ProcessPoolExecutor(4) as ex:
        res = list(ex.map(check, cands))
    (HERE / "data" / "realbugs.json").write_text(json.dumps(res, indent=1))
    keep = [r for r in res if r["keep"]]
    print(f"{len(keep)} usable real bugs", flush=True)
    for r in keep:
        print(f"  {r['repo']:20} {r['commit'][:8]} {len(r['failing'])} failing, {r['fix_lines']} changed lines | {r['subject'][:60]}")
    from collections import Counter
    print(Counter(r["why"].split(":")[0] for r in res if not r["keep"]))


if __name__ == "__main__":
    main()
