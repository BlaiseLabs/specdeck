"""Assemble a program from a worker's functions (pure from the base run, services from the style-2
rerun when present) and run the acceptance tests: worker-only, and with terra for the failures."""
import json, sys
from pathlib import Path
import designed

def rows(prog, model):
    base = designed.load(prog, f"{model}.json")
    s2p = designed.pdir(prog) / f"{model}_s2.json"
    s2 = json.loads(s2p.read_text()) if s2p.exists() else {"rows": {}, "impl": {}}
    out = {}
    for n, r in base["rows"].items():
        src = s2 if n in s2["rows"] else base
        out[n] = (src["rows"][n], src["impl"][n])
    return out

for prog in ["srs", "ledger"]:
    d = designed.load(prog, "design.json"); t = designed.load(prog, "tests.json"); ref = designed.load(prog, "ref.json")["impl"]
    for model in sys.argv[1:]:
        try:
            R = rows(prog, model)
        except FileNotFoundError:
            continue
        if len(R) < len(d["functions"]):
            print(prog, model, f"incomplete ({len(R)})"); continue
        ok = {n for n, (r, _) in R.items() if r["ok"]}
        only = {n: s for n, (_, s) in R.items()}
        esc = {n: (s if n in ok else ref[n]) for n, (_, s) in R.items()}
        a1 = designed.run_tests(d, only, t["__acceptance__"]); a2 = designed.run_tests(d, esc, t["__acceptance__"])
        pure = [n for n in R if R[n][0]["kind"] == "pure"]
        print(f"{prog:7} {model:12} functions {len(ok)}/{len(R)} (pure {len([n for n in pure if n in ok])}/{len(pure)}, "
              f"first-try {sum(r['first'] for r, _ in R.values())})  acceptance worker-only {a1[1]}/{a1[1]+a1[2]}  "
              f"with terra for the {len(R)-len(ok)} failures {a2[1]}/{a2[1]+a2[2]}")
