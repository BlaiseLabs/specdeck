"""Does the instructor's own expectation do what the hand-written sentence did (probe2)?"""
import json, re
import atomic, run as base
from atomic import *
from bugs import fresh_copy, plant
from groundtruth import run_suite
from openai import OpenAI
client = OpenAI(); ledger = []
tree = fresh_copy(prefix="probe5-"); plant(tree)
cov = coverage_map()
out = base.pytest_output(tree, specdeck=False)
failing = run_suite(tree, specdeck=False)["failed"]
text = "Test output (pytest -q -ra --tb=short):\n" + out + "\n\n" + coverage_evidence(cov, failing)
srcs = [f"--- {t} ---\n{base.test_source(tree, t)}" for t in dict.fromkeys(re.sub(r"\[.*\]$", "", f) for f in failing)]
text += "\n\nThe failing tests' source:\n" + "\n\n".join(srcs)[:24000]
where = WHERE_C1.format(mf=MAX_FUNCS, nf=MAX_FILES_C0) + EXPECT_NOTE
first = INSTRUCT_C.format(mt=MAX_TESTS_PER_INV, where=where, mi=MAX_INVESTIGATIONS, files=base.file_list(tree),
                          agents="(none)", evidence=text, files_field=', "expectation": "..."')
plan = base.parse(base.call(client, base.INSTRUCTOR, [{"role": "user", "content": first}], ledger, "instructor"))
for inv in plan.get("investigations", []):
    print("INV", inv.get("id"), inv.get("tests")); print("   EXPECT:", inv.get("expectation"))
inv = next((i for i in plan.get("investigations", []) if any("switched_code" in t for t in i.get("tests", []))), None)
res = []
if inv:
    tests = [resolve(cov, t) for t in inv["tests"]][:MAX_TESTS_PER_INV]
    span = locate(tree, "proofdeck/schemata.py:switch_line")
    for i in range(3):
        r = fn_job(client, tree, tests, out, span, ledger, str(inv.get("expectation")), "")
        res.append(r); print("switch_line", i, r.get("verdict"), r.get("start"), r.get("end"), "|", str(r.get("why"))[:150], "|", repr(r.get("new"))[:80])
json.dump({"plan": plan, "res": res, "tokens": sum(c["total"] for c in ledger), "ledger": ledger}, open("probe5.json", "w"), indent=1)
print("tokens", sum(c["total"] for c in ledger))
