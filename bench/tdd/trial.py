"""One trial: build a fresh subject, let an arm attempt it twice, grade it honestly."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

import arms
import guards
import subject
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv("/workspaces/python/.env")
MODEL = os.environ.get("TDD_MODEL", "gpt-5.6-terra")
SYSTEM = ("You are a senior Python engineer. You write complete, working modules. "
          "You never weaken a test to make it pass.")
BLOCK = re.compile(r"```(?:python)?\s*\n#\s*FILE:\s*(\S+)\s*\n(.*?)```", re.S)


OLLAMA_MODEL = os.environ.get("TDD_OLLAMA")      # e.g. qwen3:8b — runs on the Mac over ssh
OLLAMA_HOST = os.environ.get("TDD_OLLAMA_HOST", "macmini-tunnel")
OLLAMA_CTX = int(os.environ.get("TDD_OLLAMA_CTX", "32768"))


def _ollama(prompt: str, ledger: list) -> dict:
    """Generate on the Mac. The port forward would not bind, so the request is shipped over
    ssh: 205 tok/s reading and 17 tok/s writing dwarf the round trips."""
    import subprocess as sp
    import tempfile

    body = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "think": False,
            "options": {"num_predict": 8192, "temperature": 0, "num_ctx": OLLAMA_CTX}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(body, fh)
        local = fh.name
    remote = f"/tmp/tdd_req_{os.getpid()}.json"
    out = f"/tmp/tdd_res_{os.getpid()}.json"
    t0 = time.time()
    sp.run(["scp", "-q", "-o", "BatchMode=yes", local, f"{OLLAMA_HOST}:{remote}"], check=True, timeout=120)
    sp.run(["ssh", "-o", "BatchMode=yes", OLLAMA_HOST,
            f"curl -s --max-time 3000 http://127.0.0.1:11434/api/generate "
            f"-H 'Content-Type: application/json' --data-binary @{remote} -o {out}"],
           check=True, timeout=3300)
    got = sp.run(["ssh", "-o", "BatchMode=yes", OLLAMA_HOST, f"cat {out}"],
                 check=True, capture_output=True, text=True, timeout=300)
    d = json.loads(got.stdout)
    ledger.append({"prompt": d.get("prompt_eval_count", 0), "completion": d.get("eval_count", 0),
                   "cached": 0, "reasoning": 0,
                   "total": d.get("prompt_eval_count", 0) + d.get("eval_count", 0),
                   "seconds": round(time.time() - t0, 1),
                   "read_tps": round(d.get("prompt_eval_count", 0) / max(d.get("prompt_eval_duration", 1) / 1e9, .01), 1),
                   "write_tps": round(d.get("eval_count", 0) / max(d.get("eval_duration", 1) / 1e9, .01), 1)})
    return {"text": d.get("response", "")}


def _call(client, prompt: str, ledger: list) -> dict:
    if OLLAMA_MODEL:
        return _ollama(prompt, ledger)
    t0 = time.time()
    r = client.chat.completions.create(model=MODEL, messages=[
        {"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
    u = r.usage
    ledger.append({"prompt": u.prompt_tokens, "completion": u.completion_tokens,
                   "cached": getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0,
                   "reasoning": getattr(u.completion_tokens_details, "reasoning_tokens", 0) or 0,
                   "total": u.total_tokens, "seconds": round(time.time() - t0, 1)})
    return {"text": r.choices[0].message.content or ""}


BARE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)


def _apply(tree: Path, text: str, allowed: list) -> dict:
    """Write back only the files this worker was allowed to write.

    Smaller models ignore the `# FILE:` header — qwen3:8b emitted a bare code block every
    time. When exactly one file was asked for, a bare block is unambiguous, so accept it
    rather than scoring an instruction-following failure as a coding failure.
    """
    written = {}
    found = BLOCK.findall(text)
    if not found and len(allowed) == 1:
        bare = BARE.findall(text)
        if bare:
            found = [(allowed[0], max(bare, key=len))]
    for rel, body in found:
        rel = rel.strip()
        if rel not in allowed:
            continue
        (tree / rel).write_text(body.rstrip() + "\n", encoding="utf-8")
        written[rel] = body
    return written


def run(arm: str, seed: int, ledger: list, say=print) -> dict:
    tree = Path(f"/tmp/tdd_run_{arm}_{seed}")
    pristine = subject.build(tree)
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    written: dict = {}
    attempts = []

    def grade(tag: str) -> dict:
        cheats = guards.tests_untouched(tree, pristine)
        subject.restore_tests(tree, pristine)            # before anything is measured
        vis = [f for f in arms.visible_tests(tree) if f not in subject.HELD_OUT]
        all_r = subject.run_tests(tree)
        vis_r = subject.run_tests(tree, only=vis)
        hid_r = subject.run_tests(tree, only=subject.HELD_OUT)
        reg_r = subject.run_tests(tree, only=subject.REGRESSION)
        banned = guards.scan(written)
        out = {"stage": tag, "all": all_r, "visible": vis_r, "held_out": hid_r, "regression": reg_r,
               "test_edits": cheats, "banned": banned}
        say(f"    {tag}: visible {vis_r['passed']}/{vis_r['total']} | held-out "
            f"{hid_r['passed']}/{hid_r['total']} | regression {reg_r['passed']}/{reg_r['total']}"
            + (f" | CHEATS {cheats + banned}" if (cheats or banned) else ""))
        return out

    import slicing

    live = [m for m in subject.TARGETS if m in pristine["stubs"]]
    if arm in ("fine", "finespec", "fineboth"):
        # one module per worker, in dependency order; each sees only what it imports
        plan = [([m], deps) for m, deps in slicing.chunks(live) if m in pristine["stubs"]]
    elif arm == "speconly":
        plan = [(live, [])]
    elif arm == "mono":
        plan = [(live, [])]
    else:
        plan = [(real, []) for stage in subject.STAGES
                for real in [[m for m in stage if m in pristine["stubs"]]] if real]

    def build_prompt(stage, deps, done_so_far):
        if arm == "mono":
            return arms.mono_prompt(tree)
        if arm == "speconly":
            return arms.spec_only_prompt(tree, stage, arms.document(stage))
        if arm in ("fine", "finespec", "fineboth"):
            m = stage[0]
            tests = slicing.test_source_for(tree, m) if arm in ("fine", "fineboth") else ""
            doc = arms.document([m]) if arm in ("finespec", "fineboth") else ""
            return arms.chunk_prompt(tree, m, [d for d in deps if d in done_so_far], tests, doc)
        return arms.stage_prompt(tree, stage, done_so_far,
                                 arms.document(stage) if arm == "spec" else "")

    done: list = []
    for i, (stage, deps) in enumerate(plan, 1):
        prompt = build_prompt(stage, deps, done)
        got = _call(client, prompt, ledger)
        written.update(_apply(tree, got["text"], stage))
        subject.restore_tests(tree, pristine)
        # the retry sees the failures of the tests that concern this chunk, not the whole suite
        only = None
        if arm in ("fine", "finespec", "fineboth"):
            ids = slicing.nodeids_for(stage[0])
            only = [n for n in ids if (tree / n.split("::")[0]).exists()] or None
        fail_text = subject.failures(tree) if only is None else subject.failures_of(tree, only)
        retry = (prompt + "\n\n## Your attempt produced these failures\n\n```\n" + fail_text
                 + "\n```\n\nRewrite the whole of your file(s), fixing them. Same format.")
        got2 = _call(client, retry, ledger)
        written.update(_apply(tree, got2["text"], stage))
        done += stage
        attempts.append(grade(f"{i}/{len(plan)} ({', '.join(Path(s).name for s in stage)})"))

    final = grade("final")
    # proofdeck: is the code it wrote actually checked by the tests it passes?
    post = subject.per_test(tree) if os.environ.get("TDD_CAPTURE") else {}
    result = {"arm": arm, "seed": seed, "model": MODEL, "detail": subject.DETAIL,
              "stages": attempts, "final": final, "post_mortem": post,
              "code": written if os.environ.get("TDD_CAPTURE") else {},
              "tokens": sum(c["total"] for c in ledger), "wrote": sorted(written)}
    shutil.rmtree(tree, ignore_errors=True)
    return result
