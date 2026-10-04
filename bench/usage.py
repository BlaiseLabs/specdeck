"""OpenAI organisation usage — the provider's own count, as a check on ours.

    python usage.py snapshot <label>          record a time point
    python usage.py since <label|unix|iso>    usage from a time point until now
    python usage.py between <label> <label>   usage between two recorded time points

Totals come from /v1/organization/usage/completions with an admin key, grouped by model and
API key, so traffic from other projects on the same organisation can be told apart. The
provider's figures lag a few minutes; our own per-call ledger stays the primary record and
this is the secondary one. On 2026-10-03 it matched our ledger to the token.

It can only tell projects apart if they use different API keys. Everything on one key is one
line here — on 2026-10-03, ~6M tokens from other sessions shared the experiments' key, and
only the timeline told them apart.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv("/workspaces/python/.env")
SNAPSHOTS = Path(__file__).with_name("usage_snapshots.jsonl")
FIELDS = ("input_tokens", "output_tokens", "input_cached_tokens", "num_model_requests")


def _get(path: str, key: str, params: dict) -> dict:
    q = "&".join(f"{k}={v}" for k, v in params.items() if not isinstance(v, (list, tuple)))
    for k, v in params.items():
        if isinstance(v, (list, tuple)):
            q += "".join(f"&{k}[]={x}" for x in v)
    req = urllib.request.Request(f"https://api.openai.com/v1/organization/{path}?{q}",
                                 headers={"Authorization": f"Bearer {os.environ[key]}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def usage(start: int, end: int | None = None, key: str = "OPENAI_ADMIN_KEY") -> dict:
    """{(model, api_key_id): {input_tokens, output_tokens, input_cached_tokens, num_model_requests}}"""
    end = end or int(time.time())
    # The API reports whole-minute buckets. A snapshot taken mid-minute would otherwise drop
    # that minute's calls: on 2026-10-03 it silently excluded the first call of a run (11,208
    # tokens). Round the start down so the window always includes it.
    start -= start % 60
    out: dict = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    params = {"start_time": start, "end_time": end, "bucket_width": "1m", "limit": 1440,
              "group_by": ["model", "api_key_id"]}
    while True:
        d = _get("usage/completions", key, params)
        for bucket in d.get("data", []):
            for r in bucket.get("results", []):
                g = out[(r.get("model") or "?", r.get("api_key_id") or "?")]
                for f in FIELDS:
                    g[f] += r.get(f, 0) or 0
        if not d.get("has_more"):
            break
        params["page"] = d["next_page"]
    return dict(out)


def _when(arg: str) -> int:
    if arg.isdigit():
        return int(arg)
    for line in SNAPSHOTS.read_text().splitlines() if SNAPSHOTS.exists() else []:
        s = json.loads(line)
        if s["label"] == arg:
            return s["t"]
    return int(datetime.fromisoformat(arg).replace(tzinfo=timezone.utc).timestamp())


def report(u: dict, title: str) -> None:
    by_model: dict = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    for (model, _), v in u.items():
        for f in FIELDS:
            by_model[model][f] += v[f]
    print(title)
    print(f"  {'model':28}{'input':>12}{'cached':>11}{'output':>11}{'total':>12}{'requests':>10}")
    grand = 0
    for model, v in sorted(by_model.items(), key=lambda kv: -(kv[1]['input_tokens'] + kv[1]['output_tokens'])):
        t = v["input_tokens"] + v["output_tokens"]
        grand += t
        print(f"  {model:28}{v['input_tokens']:>12,}{v['input_cached_tokens']:>11,}{v['output_tokens']:>11,}"
              f"{t:>12,}{v['num_model_requests']:>10,}")
    print(f"  {'TOTAL':28}{'':>12}{'':>11}{'':>11}{grand:>12,}")
    keys = sorted({k for _, k in u})
    if len(keys) > 1:
        print(f"  ({len(keys)} API keys in this window — other projects share this organisation)")


def main(argv: list) -> int:
    if not argv:
        print(__doc__); return 2
    cmd = argv[0]
    if cmd == "snapshot":
        rec = {"label": argv[1], "t": int(time.time()), "iso": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with SNAPSHOTS.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"snapshot {rec['label']!r} at {rec['iso']}")
        return 0
    if cmd == "since":
        t0 = _when(argv[1])
        report(usage(t0), f"usage since {argv[1]} ({datetime.fromtimestamp(t0, timezone.utc):%Y-%m-%d %H:%M} UTC)")
        return 0
    if cmd == "between":
        a, b = _when(argv[1]), _when(argv[2])
        report(usage(a, b), f"usage between {argv[1]} and {argv[2]}")
        return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
