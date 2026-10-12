"""Pick the functions for the swarm check: directly tested, 3-40 body lines."""
import ast, json, random
from pathlib import Path

ROOT = Path("/workspaces/python")
rows = []
for repo in ["tapedeck", "proofdeck", "specdeck"]:
    d = json.loads((Path(__file__).parent / f"direct_{repo}.json").read_text())
    for key, tests in d.items():
        fn, first, name = key.rsplit(":", 2)
        tree = ast.parse(Path(fn).read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name and \
                    min([x.lineno for x in node.decorator_list] + [node.lineno]) == int(first):
                body = node.body
                doc = ast.get_docstring(node)
                if doc:
                    body = body[1:]
                if not body:
                    continue
                n = body[-1].end_lineno - body[0].lineno + 1
                rows.append({"repo": repo, "file": str(Path(fn).relative_to(ROOT / repo)), "name": name,
                             "line": node.lineno, "body_lines": n, "doc": bool(doc),
                             "tests": sorted(set(tests))})
ok = [r for r in rows if 3 <= r["body_lines"] <= 40 and r["name"] != "__init__"]
print(len(rows), "traced,", len(ok), "eligible")
random.seed(7)
pick = []
for repo, k in [("tapedeck", 8), ("proofdeck", 7), ("specdeck", 9)]:
    pool = [r for r in ok if r["repo"] == repo]
    pick += random.sample(pool, min(k, len(pool)))
for r in pick:
    print(f"{r['repo']:9} {r['file']:38} {r['name']:28} {r['body_lines']:3} doc={r['doc']} tests={len(r['tests'])}")
(Path(__file__).parent / "functions.json").write_text(json.dumps(pick, indent=1))
