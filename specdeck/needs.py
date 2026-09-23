"""Minimal sphinx-needs ``needs.json`` emitter.

Why: the requirements/traceability ecosystem (sphinx-needs ``needimport`` /
``needs_external_needs``, StrictDoc, the ubCode MCP server that exposes a needs
graph to coding agents) already consumes this shape. Emitting it alongside the
YAML makes a generated spec reachable by those tools without adopting Sphinx.

The schema is intentionally the small common subset: id, type, title, status,
tags, links, description, docname. Import tools tolerate missing optional keys.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


def write_needs(path: Path, feature: str, records: Iterable, created: str) -> None:
    version = "generated"
    needs = {}
    for r in records:
        needs[r.id] = {
            "id": r.id,
            "type": "scenario",
            "title": r.name,
            "status": r.status,
            "tags": list(dict.fromkeys([*r.tags, r.group])),
            "links": list(r.depends_on),  # prerequisite scenarios
            "description": r.description or "",
            "docname": r.nodeid.split("::")[0],
            "lineno": None,
            "section_name": r.group_name,
            "duration": r.duration,
            "message": r.message,
            "is_need": True,
            "is_part": False,
        }
    doc = {
        "project": feature,
        "current_version": version,
        "created": created,
        "versions": {version: {"created": created, "needs": needs, "needs_amount": len(needs)}},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
