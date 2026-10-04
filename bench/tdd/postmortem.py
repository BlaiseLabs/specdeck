"""One captured run: keep the code it wrote and the outcome of every individual test."""
import json, os, sys
os.environ["TDD_CAPTURE"] = "1"
import trial
ledger = []
res = trial.run(sys.argv[1] if len(sys.argv) > 1 else "mono", 99, ledger)
open("postmortem.json", "w").write(json.dumps(res, indent=1))
o = res["post_mortem"]["outcomes"]
from collections import Counter
print("outcomes:", dict(Counter(o.values())), "| tokens", f"{res['tokens']:,}")
