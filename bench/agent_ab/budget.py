"""Every model call is written down; nothing runs past the budget."""
from __future__ import annotations

import json
import time
from pathlib import Path

import tiktoken

ENC = tiktoken.get_encoding("o200k_base")


def count(text: str) -> int:
    return len(ENC.encode(text))


class Ledger:
    def __init__(self, path: Path, budget: int) -> None:
        self.path, self.budget = Path(path), int(budget)
        self.calls = [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()] if self.path.exists() else []

    def spent(self) -> int:
        return sum(c["usage"]["total_tokens"] for c in self.calls)

    def done(self) -> set:
        return {(c["task"], c["arm"], c["batch"]) for c in self.calls}

    def can_afford(self, estimate: int) -> bool:
        return self.spent() + estimate <= self.budget

    def log(self, **call) -> None:
        call["at"] = time.time()
        self.calls.append(call)
        with self.path.open("a") as fh:
            fh.write(json.dumps(call) + "\n")

    def totals(self) -> dict:
        out: dict = {}
        for c in self.calls:
            u = c["usage"]
            t = out.setdefault(c["arm"], {"calls": 0, "prompt": 0, "cached": 0, "completion": 0, "reasoning": 0, "total": 0})
            t["calls"] += 1
            t["prompt"] += u["prompt_tokens"]
            t["cached"] += u.get("cached_tokens", 0)
            t["completion"] += u["completion_tokens"]
            t["reasoning"] += u.get("reasoning_tokens", 0)
            t["total"] += u["total_tokens"]
        out["all"] = {k: sum(t[k] for a, t in out.items()) for k in ("calls", "prompt", "cached", "completion", "reasoning", "total")}
        return out
