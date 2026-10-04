"""Is every rule actually recoverable from the material, and is none of it handed over clean?

Two failure modes would invalidate the experiment: a rule that never made it into the
documents (every arm fails it, for reasons unrelated to the handoff format), and a tidy
summary of the rules somewhere (no synthesis left for a spec to do).
"""
import re
from pathlib import Path

M = Path(__file__).with_name(__import__("os").environ.get("MESSY_MATERIAL","material"))
text = {p.name: p.read_text(encoding="utf-8") for p in sorted(M.iterdir()) if p.is_file()}
blob = "\n".join(text.values()).lower()

# evidence for each rule: patterns any of which should appear somewhere in the material
EVIDENCE = {
    "R1":  [r"quantity|qty", r"unit price|price per|each"],
    "R2":  [r"100 or more|100 itself|exactly 100|100 counts|includes 100|100 qualif"],
    "R3":  [r"clearance"],
    "R4":  [r"5\s?%|five percent", r"loyalty|member"],
    "R5":  [r"10\s?%|ten percent", r"july|2026-07-01|07/01"],
    "R6":  [r"gift card"],
    "R7":  [r"after discount|what they (actually )?pay|discounted"],
    "R8":  [r"5\.99", r"\b50\b|fifty"],
    "R9":  [r"shipping (is )?(never|not|isn.?t) tax|not tax\w* on (shipping|the delivery)|tax on the delivery"],
    "R10": [r"digital"],
    "R11": [r"half[- ]?up|round\w* up|to the cent|nearest cent"],
    "R12": [r"gift card"],
}
print("rule  evidence found in")
for rule, pats in EVIDENCE.items():
    where = [n for n, t in text.items() if all(re.search(p, t.lower()) for p in pats)]
    anywhere = all(re.search(p, blob) for p in pats)
    print(f"  {rule:4} {'OK ' if anywhere else 'MISSING'}  {', '.join(where) or '(spread across files)' if anywhere else ''}")
print()
bad = [w for w in ("requirement", "specification", "acceptance test", "test case") if w in blob]
print("forbidden words present:", bad or "none")
lists = [n for n, t in text.items() if len(re.findall(r"^\s*[-*\d]+[.)]?\s.*(discount|tax|shipping)", t, re.M | re.I)) >= 4]
print("files that look like a rules list:", lists or "none")
print("R10 stated in prose anywhere?", any("digital" in t.lower() and "ship" in t.lower()
                                           for n, t in text.items() if not n.endswith(".csv")))
total = sum(len(t.split()) for t in text.values())
print(f"total words: {total:,}")
