"""An agent that explores a repository with read-only shell commands and answers questions."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

ALLOWED = {"ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "sed", "awk", "sort", "uniq",
           "tree", "file", "pwd", "echo", "nl", "cut", "tr", "basename", "dirname", "stat"}
OUT_LIMIT = 6000          # characters of command output returned to the model (~1.5k tokens)

TOOL = [{"type": "function", "name": "bash",
         "description": "Run a read-only shell command in the repository root. Allowed: "
                        + ", ".join(sorted(ALLOWED)) + ", combined with pipes. Output is truncated.",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                        "required": ["command"], "additionalProperties": False}}]


def safe(cmd: str) -> str | None:
    """None if the command may run, else the reason it may not. This runs on a real machine,
    so the bar is: reads only, inside the copy."""
    if re.search(r"[`]|\$\(|>|<\(|\bsudo\b", cmd):
        return "redirects, command substitution and sudo are not allowed"
    if re.search(r"-exec\b|-execdir\b|-delete\b|\bsystem\s*\(|\bsed\b[^|]*\s-i", cmd):
        return "find -exec/-delete, sed -i and awk system() are not allowed"
    for seg in re.split(r"\|\||&&|\||;", cmd):
        seg = seg.strip()
        if not seg:
            continue
        try:
            first = shlex.split(seg)[0]
        except ValueError:
            return "could not parse the command"
        if first not in ALLOWED:
            return f"`{first}` is not an allowed command"
    if re.search(r"(^|\s)/(?!dev/null)|\.\./", cmd):
        return "stay inside the repository (no absolute paths or ..)"
    return None


def run_cmd(cmd: str, cwd: Path) -> str:
    why = safe(cmd)
    if why:
        return f"refused: {why}"
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=15,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "LC_ALL": "C"})
        out = (p.stdout + p.stderr) or "(no output)"
    except subprocess.TimeoutExpired:
        out = "(timed out)"
    return out if len(out) <= OUT_LIMIT else out[:OUT_LIMIT] + f"\n... [truncated, {len(out)} chars total]"


SYSTEM = ("You are an engineer new to this Python repository. Explore it with the bash tool to answer "
          "the questions. Be efficient: look before you guess, and stop exploring once you can answer.")


def answer(client, model: str, repo: Path, questions: list, max_calls: int = 25) -> dict:
    """Responses API, not chat completions: this model refuses function tools on the chat
    endpoint while reasoning is on, and turning reasoning off would handicap the agent and
    change what is being measured. The conversation is chained server-side with
    previous_response_id, which also carries the model's reasoning between turns."""
    q_text = "\n".join(f"{qid}. {q}" for qid, q, _ in questions)
    first = [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": "Questions:\n" + q_text +
              "\n\nWhen you can answer, reply with ONLY a JSON object: "
              '{"answers": {"Q1": "...", "Q2": "...", ...}}'}]
    ledger, calls, commands = [], 0, []
    t0 = time.time()
    prev, pending = None, first
    while True:
        force = calls >= max_calls
        kw = {"previous_response_id": prev} if prev else {}
        if force:
            pending = pending + [{"role": "user", "content": "Tool budget used up. Answer now with the JSON object."}]
            r = client.responses.create(model=model, input=pending, **kw)
        else:
            r = client.responses.create(model=model, input=pending, tools=TOOL, **kw)
        u = r.usage
        ledger.append({"prompt": u.input_tokens, "completion": u.output_tokens, "total": u.total_tokens,
                       "cached": getattr(u.input_tokens_details, "cached_tokens", 0) or 0,
                       "reasoning": getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0})
        prev = r.id
        fcalls = [o for o in r.output if getattr(o, "type", "") == "function_call"]
        if fcalls and not force:
            pending = []
            for fc in fcalls:
                calls += 1
                try:
                    cmd = json.loads(fc.arguments).get("command", "")
                except json.JSONDecodeError:
                    cmd = ""
                commands.append(cmd)
                pending.append({"type": "function_call_output", "call_id": fc.call_id,
                                "output": run_cmd(cmd, repo)})
            continue
        text = r.output_text or ""
        m = re.search(r"\{.*\}", text, re.S)
        try:
            ans = json.loads(m.group(0)).get("answers", {}) if m else {}
        except json.JSONDecodeError:
            ans = {}
        return {"answers": ans, "tool_calls": calls, "commands": commands, "ledger": ledger,
                "tokens": sum(c["total"] for c in ledger), "seconds": round(time.time() - t0, 1)}
