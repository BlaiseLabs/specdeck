"""Does specdeck help an agent understand the business purpose behind a codebase's tests?

Three copies of specdeck's own repo (see build.py): plain, separate_doc, specdeck. The same
agent (navigate/agent.py, read-only shell) answers the same ten questions in each.

Budget: a hard cap on this experiment's own ledger. A session only starts if the cap still
has room for the largest session allowed, and a session that grows past SESSION_CAP is
aborted (its tokens still count) — agent.py has no budget of its own.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "navigate"))
import agent  # noqa: E402
import build  # noqa: E402
sys.path.insert(0, str(HERE))       # this directory's questions.py, not navigate's
sys.modules.pop("questions", None)
from questions import QUESTIONS  # noqa: E402
assert len(QUESTIONS[0]) == 4 and "test_partial_run" in QUESTIONS[0][1]

VARIANT = os.environ.get("PURPOSE_VARIANT", "v1")
MODEL = os.environ.get("PURPOSE_MODEL", "gpt-5.6-terra")
if VARIANT == "v1":
    LOG = HERE / "trials.jsonl"
    CAP = 450_000
    SESSION_CAP = 110_000   # abort a session that passes this (calibration seed 0 only)
    SOFT_CAP = int(os.environ.get("PURPOSE_SOFT_CAP", "0"))   # past this, the next call must answer
    HEADROOM = int(os.environ.get("PURPOSE_HEADROOM", "130000"))  # room needed before a session starts
else:
    # v2: no comments, docstrings or prose docs in any copy (build.strip_everything). One limit
    # for every copy: past SOFT_CAP the next call must answer; past SESSION_CAP the session is
    # aborted (a backstop: the forced call ends the session first). The call that crosses
    # SOFT_CAP and the forced answer can each be a full context, so HEADROOM covers both.
    # v3: the v2 copies, with specdeck's context fix (a requirements section, and `--task why`
    # for a document of the requirements alone). Built from PURPOSE_SRC, a snapshot of the
    # fixed code with the original requirements.yaml and tests, so the subject is unchanged.
    LOG = HERE / {"v2": "trials_v2.jsonl", "v2_prompt": "trials_v2_prompt.jsonl",
                  "v2_doc_prompt": "trials_v2_doc_prompt.jsonl", "v3": "trials_v3.jsonl"}[VARIANT]
    CAP = {"v2": 500_000, "v2_prompt": 150_000, "v2_doc_prompt": 120_000, "v3": 400_000}[VARIANT]
    SOFT_CAP = 40_000
    SESSION_CAP = 60_000
    HEADROOM = int(os.environ.get("PURPOSE_HEADROOM", "95000"))
_lock = threading.Lock()


class SessionOver(Exception):
    pass


class Metered:
    """Wraps the client so every call's usage is counted the moment it returns."""

    def __init__(self, client, preface: str = "", left: int = 10**9):
        self._client, self.tokens, self.calls = client, 0, []
        self.left = left            # what the ledger cap still allows; never cross it
        self.next_est = 0
        self.preface = preface      # put in front of the first user message (specdeck_prompt)
        self.responses = self

    forced = False

    def create(self, **kw):
        if SOFT_CAP and self.tokens > SOFT_CAP and "tools" in kw:
            # the same move agent.py makes when its tool budget runs out: no tools, answer now
            kw.pop("tools")
            kw["input"] = list(kw["input"]) + [{"role": "user", "content":
                                                "Tool budget used up. Answer now with the JSON object."}]
            self.forced = True
        elif (VARIANT != "v1" or not SOFT_CAP) and self.tokens > SESSION_CAP:
            raise SessionOver(self.tokens)
        if self.tokens + self.next_est > self.left:
            raise SessionOver(self.tokens)   # the next call could cross the experiment's cap
        if self.preface and "previous_response_id" not in kw:
            kw["input"] = [dict(m) for m in kw["input"]]
            user = next(m for m in kw["input"] if m.get("role") == "user")
            user["content"] = self.preface + "\n\n" + user["content"]
        r = self._client.responses.create(**kw)
        self.tokens += r.usage.total_tokens
        self.next_est = r.usage.total_tokens + 10_000   # next call re-reads this one, plus tool output
        self.calls.append(r.usage.total_tokens)
        return r


def spent() -> int:
    rows = [json.loads(l) for l in LOG.read_text().splitlines()] if LOG.exists() else []
    return sum(r["tokens"] for r in rows)


def session(client, arm: str, seed: int, max_calls: int) -> dict:
    # a neutral directory name: `pwd` must not tell the agent which arm it is in
    tag = hashlib.md5(f"{arm}{seed}".encode()).hexdigest()[:6]
    build_arm = arm.removesuffix("_prompt")
    repo = build.build(build_arm, Path(f"/tmp/pr_{tag}{VARIANT}/specdeck"),
                       "v2" if VARIANT.startswith(("v2", "v3")) else VARIANT)
    if VARIANT == "v3" and arm == "specdeck_prompt":   # the document written for "what is this for?"
        env = {**os.environ, "PYTHONPATH": str(repo)}
        import subprocess
        subprocess.run([sys.executable, "-m", "specdeck", "context", "--task", "why", "--out", str(repo / "SPEC.md")],
                       cwd=repo, env=env, check=True, capture_output=True)
    preface = ""
    if arm == "specdeck_prompt":   # the same copy; the only change is SPEC.md in the prompt
        preface = ("The suite's spec (SPEC.md, generated by specdeck) follows.\n\n"
                   + (repo / "SPEC.md").read_text())
    if arm == "separate_doc_prompt":   # mirrors specdeck_prompt exactly
        preface = ("The project's requirements document (REQUIREMENTS.md) follows.\n\n"
                   + (repo / "REQUIREMENTS.md").read_text())
    m = Metered(client, preface, left=CAP - spent())
    qs = [(qid, q, chk) for qid, q, chk, _ in QUESTIONS]
    try:
        res = agent.answer(m, MODEL, repo, qs, max_calls=max_calls)
    except SessionOver:
        res = {"answers": {}, "tool_calls": None, "commands": [], "ledger": [], "aborted": True,
               "seconds": None}
    res["tokens"] = m.tokens      # what the API reported, including an aborted session's calls
    res["forced"] = m.forced
    graded = {qid: bool(chk(str(res["answers"].get(qid, "")))) for qid, _, chk, _ in QUESTIONS}
    cmds = " ".join(res["commands"])
    read = {name: name in cmds for name in ("SPEC.md", "requirements.yaml", ".specdeck", "AGENTS.md",
                                            "REQUIREMENTS.md", "covers")}
    row = {"arm": arm, "seed": seed, "model": MODEL, "variant": VARIANT, "soft_cap": SOFT_CAP, "correct": sum(graded.values()), "graded": graded,
           "read": read, **res}
    with _lock:
        with LOG.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
    return row


def show(row, total):
    read = ",".join(k for k, v in row["read"].items() if v) or "-"
    print(f"  {row['arm']:12} seed{row['seed']}  {row['correct']:>2}/10  {row['tool_calls'] or 0:>2} commands  "
          f"{row['tokens']:>7,} tok  read: {read}" + ("  ABORTED" if row.get("aborted") else "")
          + f"  | spent {total:,}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="plain,separate_doc,specdeck")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--parallel", action="store_true", help="run the arms of one seed at the same time")
    ap.add_argument("--max-calls", type=int, default=25)
    args = ap.parse_args()
    load_dotenv("/workspaces/python/.env")
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    arms = [a for a in args.arms.split(",")]
    done = {(json.loads(l)["arm"], json.loads(l)["seed"]) for l in LOG.read_text().splitlines()} if LOG.exists() else set()
    arms = [a for a in arms if (a, args.seed) not in done]
    print(f"spent {spent():,} of {CAP:,}", flush=True)
    if args.parallel:
        if spent() + HEADROOM * len(arms) > CAP:
            print("STOP: not enough budget to run these in parallel", flush=True)
            return 3
        with ThreadPoolExecutor(len(arms)) as ex:
            for row in ex.map(lambda a: session(client, a, args.seed, args.max_calls), arms):
                show(row, spent())
        return 0
    for arm in arms:
        if spent() + HEADROOM > CAP:
            print("STOP: budget", flush=True)
            return 3
        show(session(client, arm, args.seed, args.max_calls), spent())
    return 0


if __name__ == "__main__":
    sys.exit(main())
