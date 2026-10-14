"""A rule registry: shared rules, each with its reason and the check that says when it breaks.

A registry is a git repository of rule cards, one YAML file per rule under ``rules/``:

    id: money.decimal
    statement: Money amounts shall be Decimal values, with no float in the billing code.
    rationale: A float refund was one cent short and the accounts would not reconcile.
    type: design-constraint
    check: {forbid_pattern: '\\bfloat\\(', paths: ['**/*.py']}
    tags: [python, money]

Self-hosting it is hosting a git repository. specdeck reads a registry from a local directory, or
clones it from any git URL into its cache (``$SPECDECK_HOME/registries``) and pulls it on use.

Only checkable rules are shared: a card needs a rationale and a well-formed check, and never a
``command`` check, so taking a rule from a registry never means running someone else's code.
Card text ends up in AGENTS.md, so ``rules add`` shows the card for review before adding it.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from .checks import describe, validate

CARD_KEYS = ("id", "statement", "rationale", "type", "check", "tags")
README = """# Rule registry

Rules for coding agents, each with its reason and the check that says when it breaks. Read by
specdeck: `specdeck rules search`, `specdeck rules add ID`, `specdeck rules publish ID`. One rule
per file in `rules/`. Self-hosted: clone it, add rules, push.
"""


class RegistryError(Exception):
    pass


def _git(root, *args, check=True) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=check,
                          timeout=120).stdout


def _is_url(spec: str) -> bool:
    return "://" in spec or spec.endswith(".git") or bool(re.match(r"^[\w.-]+@[\w.-]+:", spec))


@dataclass
class Registry:
    spec: str              # what the user gave: a directory or a git URL
    root: Path             # where the cards are read from
    revision: str          # the registry's commit, for provenance

    @property
    def name(self) -> str:
        return self.spec.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or self.spec

    def cards(self) -> list:
        out = []
        for p in sorted((self.root / "rules").glob("*.yaml")):
            try:
                card = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            if isinstance(card, dict) and card.get("id"):
                out.append(card)
        return out

    def card(self, rid: str) -> dict:
        found = next((c for c in self.cards() if c["id"] == rid), None)
        if found is None:
            raise RegistryError(f"no rule {rid!r} in {self.spec}")
        return found

    def search(self, query: str = "") -> list:
        words = [w for w in query.lower().split() if w]
        scored = []
        for c in self.cards():
            text = " ".join(str(c.get(k) or "") for k in ("id", "statement", "rationale", "type")) + " " + \
                " ".join(map(str, c.get("tags") or []))
            text = text.lower()
            score = sum(text.count(w) for w in words) if words else 1
            if score and all(w in text for w in words):
                scored.append((-score, c["id"], c))
        return [c for _, _, c in sorted(scored, key=lambda x: (x[0], x[1]))]


def open_registry(spec: str, cache: Optional[Path] = None) -> Registry:
    """A registry from a local directory, or cloned (and pulled) from a git URL into the cache."""
    path = Path(spec).expanduser()
    if not _is_url(spec) and path.is_dir():
        root = path
    elif _is_url(spec):
        from .history import home
        cache = cache or home() / "registries"
        root = cache / (re.sub(r"[^\w.-]+", "_", spec)[-60:] + "-" + hashlib.sha1(spec.encode()).hexdigest()[:8])
        try:
            if (root / ".git").exists():
                _git(root, "pull", "-q", "--ff-only")
            else:
                cache.mkdir(parents=True, exist_ok=True)
                subprocess.run(["git", "clone", "-q", "--depth", "1", spec, str(root)], capture_output=True,
                               text=True, check=True, timeout=300)
        except subprocess.CalledProcessError as exc:
            raise RegistryError(f"could not fetch {spec}: {' '.join((exc.stderr or '').split())[:200]}") from exc
    else:
        raise RegistryError(f"no registry at {spec}: give a directory or a git URL")
    if not (root / "rules").is_dir():
        raise RegistryError(f"{spec} is not a rule registry (no rules/ directory); `specdeck rules init` makes one")
    revision = _git(root, "rev-parse", "--short", "HEAD", check=False).strip() if (root / ".git").exists() else ""
    return Registry(spec, root, revision)


def init(path: Path) -> Path:
    """Create an empty registry: a git repository with a README and rules/."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "rules").mkdir(exist_ok=True)
    (path / "rules" / ".gitkeep").touch()
    if not (path / "README.md").exists():
        (path / "README.md").write_text(README, encoding="utf-8")
    if not (path / ".git").exists():
        _git(path, "init", "-q")
    _git(path, "add", "-A")
    if _git(path, "status", "--porcelain").strip():
        _git(path, "-c", "user.name=specdeck", "-c", "user.email=specdeck@localhost", "commit", "-q", "-m",
             "Rule registry")
    return path


def shareable(card: dict) -> list:
    """Why a rule can't be shared, as sentences; empty when it can."""
    out = []
    if not card.get("rationale"):
        out.append("it has no rationale; a shared rule says why it exists")
    if card.get("check") is None:
        out.append("it has no `check:`; a shared rule says how it is checked (docs/RULE-CHECKS.md)")
    else:
        out += [f"its {p}" for p in validate(card["check"])]
        if _has_command(card["check"]):
            out.append("its check runs a `command`, which a registry never shares; use data checks or `test`")
    return out


def _has_command(node) -> bool:
    if isinstance(node, list):
        return any(_has_command(n) for n in node)
    if isinstance(node, dict):
        return "command" in node or any(_has_command(v) for k, v in node.items() if k in ("all", "any", "not"))
    return False


def card_from(requirement: dict) -> dict:
    return {k: requirement[k] for k in CARD_KEYS if requirement.get(k) not in (None, [], "")}


def publish(registry_path: Path, card: dict, message: str = "") -> Path:
    """Write ``card`` into a registry checkout and commit it. Pushing is left to the publisher."""
    problems = shareable(card)
    if problems:
        raise RegistryError(f"{card.get('id')} can't be shared: " + "; ".join(problems))
    reg = open_registry(str(registry_path))
    if _is_url(reg.spec):
        raise RegistryError("publish writes to a checkout of the registry; give its directory, then push")
    out = reg.root / "rules" / f"{card['id']}.yaml"
    out.write_text(yaml.safe_dump(card, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    if (reg.root / ".git").exists():
        _git(reg.root, "add", str(out.relative_to(reg.root)))
        if _git(reg.root, "status", "--porcelain").strip():
            _git(reg.root, "-c", "user.name=specdeck", "-c", "user.email=specdeck@localhost", "commit", "-q", "-m",
                 message or f"Rule {card['id']}")
    return out


def add(requirements_path: Path, card: dict, source: str) -> str:
    """Append a registry card to a requirements file as a requirement, keeping the file's formatting.
    Returns the entry as written."""
    problems = shareable(card)
    if problems:
        raise RegistryError(f"{card.get('id')} won't be added: " + "; ".join(problems))
    text = requirements_path.read_text(encoding="utf-8") if requirements_path.exists() else "requirements:\n"
    data = yaml.safe_load(text) or {}
    existing = data.get("requirements") if isinstance(data, dict) else data
    if any(isinstance(e, dict) and e.get("id") == card["id"] for e in existing or []):
        raise RegistryError(f"{requirements_path.name} already has {card['id']}")
    entry = dict(card_from(card), source=source)
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(r"^requirements:\s*(#.*)?$", line)), None)
    if start is None:
        lines += ["", "requirements:"]
        start = len(lines) - 1
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[^\s#-]", lines[i])), len(lines))
    item = next((re.match(r"^(\s*)- ", lines[i]).group(1) for i in range(start + 1, end)
                 if re.match(r"^\s*- ", lines[i])), "  ")
    dumped = yaml.safe_dump([entry], sort_keys=False, allow_unicode=True, width=100).rstrip("\n").splitlines()
    block = [""] + [item + line for line in dumped]
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines[end:end] = block
    new = "\n".join(lines) + "\n"
    check = yaml.safe_load(new)
    if not any(e.get("id") == card["id"] for e in (check.get("requirements") or [])):
        raise RegistryError("could not place the rule in the requirements file; add it by hand")
    requirements_path.write_text(new, encoding="utf-8")
    return "\n".join(dumped)


def summary(card: dict) -> str:
    return f"{card['id']:<28} {' '.join(str(card.get('statement', '')).split())[:90]}\n" \
           f"{'':<30}checked by: {describe(card['check']) if card.get('check') and not validate(card['check']) else '—'}"
