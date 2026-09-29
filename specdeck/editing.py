"""Light source edits from the viewer: title, details, tags, depends_on, step text and expected labels.

Only string literals are rewritten, in place, so the rest of the file keeps its
formatting. Every edit carries a fingerprint of the function's source; if the
test changed on disk since the form was opened, the edit is refused rather than
applied to code the user never saw.

This module is also the single definition of "which ``with`` calls are steps",
shared with the plugin's static step extraction so step indices always agree.
"""
from __future__ import annotations

import ast
import difflib
import hashlib
import re
from pathlib import Path
from typing import Optional

STEP_FUNCS = {"given": "Given", "when": "When", "then": "Then", "and_": "And"}


class EditError(Exception):
    status = 400


class Conflict(EditError):
    status = 409


# --------------------------------------------------------------------------- #
# Finding steps (shared with the plugin)
# --------------------------------------------------------------------------- #
def call_name(call: ast.Call) -> Optional[str]:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def step_calls(node: ast.AST) -> list:
    """``(keyword, call, text_node, expected_keyword_or_None)`` for every step, in source order."""
    out: list = []

    class _Visitor(ast.NodeVisitor):
        def _with(self, w) -> None:
            for it in w.items:
                c = it.context_expr
                if not isinstance(c, ast.Call):
                    continue
                name = call_name(c)
                args = list(c.args)
                if name in STEP_FUNCS:
                    keyword = STEP_FUNCS[name]
                elif name == "step" and args and isinstance(args[0], ast.Constant):
                    keyword = str(args.pop(0).value)
                else:
                    continue
                if not args:
                    continue
                expected = next((k for k in c.keywords if k.arg == "expected"), None)
                out.append((keyword, c, args[0], expected))
            self.generic_visit(w)

        visit_With = _with
        visit_AsyncWith = _with

    _Visitor().visit(node)
    return out


# --------------------------------------------------------------------------- #
# Source positions (ast columns are UTF-8 byte offsets)
# --------------------------------------------------------------------------- #
class _Source:
    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = text.splitlines(keepends=True) or [""]
        self.starts = []
        pos = 0
        for line in self.lines:
            self.starts.append(pos)
            pos += len(line)

    def offset(self, lineno: int, col: int) -> int:
        line = self.lines[lineno - 1]
        return self.starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8", errors="ignore"))

    def span(self, node: ast.AST) -> tuple:
        return (self.offset(node.lineno, node.col_offset), self.offset(node.end_lineno, node.end_col_offset))

    def segment(self, node: ast.AST) -> str:
        a, b = self.span(node)
        return self.text[a:b]

    def line_start(self, lineno: int) -> int:
        return self.starts[lineno - 1]

    def line_end(self, lineno: int) -> int:
        return self.starts[lineno - 1] + len(self.lines[lineno - 1])


def _first_line(fn: ast.AST) -> int:
    return min([fn.lineno] + [d.lineno for d in fn.decorator_list])


def _find_function(tree: ast.AST, name: str, line: Optional[int]) -> ast.AST:
    cands = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if not cands:
        raise EditError(f"test function {name!r} was not found in the file (was it renamed?)")
    if line:
        cands.sort(key=lambda n: abs(_first_line(n) - line))
    return cands[0]


def _fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _is_str(node: Optional[ast.AST]) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _str_list(node: Optional[ast.AST]) -> Optional[list]:
    if isinstance(node, (ast.List, ast.Tuple)) and all(_is_str(e) for e in node.elts):
        return [e.value for e in node.elts]
    return None


def _scenario_decorator(fn: ast.AST) -> Optional[ast.Call]:
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and call_name(d) == "scenario":
            return d
    return None


def _kw(call: Optional[ast.Call], name: str) -> Optional[ast.keyword]:
    if call is None:
        return None
    return next((k for k in call.keywords if k.arg == name), None)


def _docstring_node(fn: ast.AST) -> Optional[ast.Constant]:
    if fn.body and isinstance(fn.body[0], ast.Expr) and _is_str(fn.body[0].value):
        return fn.body[0].value
    return None


def _split_doc(doc: str) -> tuple:
    doc = doc.strip()
    parts = doc.split("\n\n", 1)
    title = " ".join(parts[0].split())
    details = parts[1].strip() if len(parts) > 1 else ""
    return title, details


def _humanize(name: str) -> str:
    s = re.sub(r"^test_", "", name).replace("_", " ").strip()
    return s[:1].upper() + s[1:]


# --------------------------------------------------------------------------- #
# Load: what the editor form shows
# --------------------------------------------------------------------------- #
def load(path, function: str, line: Optional[int] = None) -> dict:
    path = Path(path)
    text = _read(path)
    src = _Source(text)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise EditError(f"{path.name} does not parse right now: {exc.msg} (line {exc.lineno})") from exc
    fn = _find_function(tree, function, line)
    start, end = src.line_start(_first_line(fn)), src.line_end(fn.end_lineno)
    fn_source = text[start:end]
    deco = _scenario_decorator(fn)
    doc_node = _docstring_node(fn)
    raw_doc = ast.get_docstring(fn, clean=True) or ""
    title, details = _split_doc(raw_doc) if raw_doc else ("", "")
    name_kw = _kw(deco, "name")
    title_via = "docstring"
    if name_kw is not None:
        title_via = "name"
        title = name_kw.value.value if _is_str(name_kw.value) else ast.unparse(name_kw.value)
    can_add_doc = doc_node is not None or (fn.body and fn.body[0].lineno > fn.lineno)
    if name_kw is not None:
        title_editable = _is_str(name_kw.value)
    else:
        title_editable = bool(can_add_doc)

    def list_field(kwname: str) -> dict:
        kw = _kw(deco, kwname)
        if kw is None:
            return {"value": [], "editable": True}
        vals = _str_list(kw.value)
        return {"value": vals if vals is not None else [], "editable": vals is not None,
                "source": None if vals is not None else ast.unparse(kw.value)}

    steps = []
    for i, (keyword, _call, text_node, exp_kw) in enumerate(step_calls(fn)):
        steps.append({
            "index": i,
            "keyword": keyword,
            "text": text_node.value if _is_str(text_node) else src.segment(text_node),
            "text_editable": _is_str(text_node),
            "expected": (exp_kw.value.value if exp_kw is not None and _is_str(exp_kw.value)
                         else (src.segment(exp_kw.value) if exp_kw is not None else "")),
            "expected_editable": exp_kw is None or _is_str(exp_kw.value),
        })
    return {
        "function": function,
        "line": _first_line(fn),
        "source": fn_source,
        "fingerprint": _fingerprint(fn_source),
        "parametrized": any(
            isinstance(d, ast.Call) and call_name(d) == "parametrize" for d in fn.decorator_list
        ),
        "has_scenario": deco is not None,
        "title": {"value": title or _humanize(function), "editable": title_editable,
                  "via": title_via, "derived": not title},
        "details": {"value": details, "editable": bool(can_add_doc)},
        "tags": list_field("tags"),
        "depends_on": list_field("depends_on"),
        "steps": steps,
    }


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #
def _quote(s: str, like: Optional[str] = None) -> str:
    body_like = (like or "").lstrip("rRbBuU")
    q = "'" if body_like.startswith("'") and not body_like.startswith("'''") else '"'
    body = (s.replace("\\", "\\\\").replace(q, "\\" + q)
            .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return q + body + q


def _docstring_literal(title: str, details: str, indent: str) -> str:
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')

    body = esc(" ".join(title.split()))
    if details.strip():
        lines = [esc(line.rstrip()) for line in details.strip().splitlines()]
        body += "\n\n" + "\n".join((indent + line) if line else "" for line in lines) + "\n" + indent
    if body.endswith('"'):
        body = body[:-1] + '\\"'
    return '"""' + body + '"""'


def _list_literal(values: list) -> str:
    return "[" + ", ".join(_quote(v) for v in values) + "]"


class _Edits:
    def __init__(self, src: _Source) -> None:
        self.src = src
        self.ops: list = []  # (start, end, text)

    def replace(self, node: ast.AST, text: str) -> None:
        a, b = self.src.span(node)
        self.ops.append((a, b, text))

    def insert(self, at: int, text: str) -> None:
        self.ops.append((at, at, text))

    def remove(self, a: int, b: int) -> None:
        self.ops.append((a, b, ""))

    def result(self) -> str:
        ops = sorted(self.ops, key=lambda o: (o[0], o[1]))
        for (a1, b1, _), (a2, b2, _) in zip(ops, ops[1:]):
            if a2 < b1 or (a1 == a2 and b1 == b2 and a1 != b1):
                raise EditError("two edits touch the same code; apply them one at a time")
        text = self.src.text
        for a, b, new in sorted(ops, key=lambda o: (o[0], o[1]), reverse=True):
            text = text[:a] + new + text[b:]
        return text


def _call_items(call: ast.Call) -> list:
    items = list(call.args) + list(call.keywords)
    return sorted(items, key=lambda n: (n.lineno, n.col_offset))


def _kw_start(src: _Source, kw: ast.keyword) -> int:
    return src.offset(kw.lineno, kw.col_offset)


def _set_kwarg(ed: _Edits, call: ast.Call, name: str, value_text: Optional[str]) -> None:
    """Set (value_text) or remove (None) keyword ``name`` on ``call``."""
    src = ed.src
    kw = _kw(call, name)
    if kw is not None and value_text is not None:
        ed.replace(kw.value, value_text)
        return
    if kw is None and value_text is None:
        return
    items = _call_items(call)
    if kw is None:  # insert after the last argument, or right after "("
        if items:
            last = items[-1]
            end = src.offset(last.end_lineno, last.end_col_offset)
            ed.insert(end, f", {name}={value_text}")
        else:
            close = src.offset(call.end_lineno, call.end_col_offset) - 1
            ed.insert(close, f"{name}={value_text}")
        return
    # remove
    i = items.index(kw)
    kw_a = _kw_start(src, kw)
    kw_b = src.offset(kw.end_lineno, kw.end_col_offset)
    if i > 0:
        prev = items[i - 1]
        ed.remove(src.offset(prev.end_lineno, prev.end_col_offset), kw_b)
    elif i + 1 < len(items):
        nxt = items[i + 1]
        ed.remove(kw_a, src.offset(nxt.lineno, nxt.col_offset))
    else:
        m = re.match(r"\s*,", src.text[kw_b:])
        ed.remove(kw_a, kw_b + (m.end() if m else 0))


def _ensure_scenario_import(ed: _Edits, tree: ast.Module) -> str:
    """Name to call the decorator by, adding ``from specdeck import scenario`` if needed."""
    src = ed.src
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "specdeck":
            for a in node.names:
                if a.name == "scenario":
                    return a.asname or "scenario"
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "specdeck":
                    return f"{a.asname or 'specdeck'}.scenario"
    anchor = None
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            anchor = node
    if anchor is not None:
        ed.insert(src.line_end(anchor.end_lineno), "from specdeck import scenario\n")
    else:
        first = tree.body[0] if tree.body else None
        if first is not None and isinstance(first, ast.Expr) and _is_str(first.value):  # module docstring
            ed.insert(src.line_end(first.end_lineno), "from specdeck import scenario\n")
        else:
            ed.insert(0, "from specdeck import scenario\n")
    return "scenario"


def apply(path, function: str, fingerprint: str, edits: dict, *, line: Optional[int] = None,
          dry_run: bool = False, display_name: Optional[str] = None) -> dict:
    """Apply ``edits`` to the test function. Returns ``{"diff", "changed", "model"}``.

    ``edits`` keys (all optional): ``title``, ``details``, ``tags``, ``depends_on``,
    ``steps`` = ``[{"index", "text"?, "expected"?}]``.
    """
    path = Path(path)
    text = _read(path)
    src = _Source(text)
    tree = ast.parse(text)
    fn = _find_function(tree, function, line)
    start, end = src.line_start(_first_line(fn)), src.line_end(fn.end_lineno)
    if _fingerprint(text[start:end]) != fingerprint:
        raise Conflict("This test changed on disk since you opened the editor. Reopen it to edit the current version.")

    ed = _Edits(src)
    deco = _scenario_decorator(fn)
    body_indent = None

    # title / details
    if "title" in edits or "details" in edits:
        name_kw = _kw(deco, "name")
        doc_node = _docstring_node(fn)
        raw = ast.get_docstring(fn, clean=True) or ""
        cur_title, cur_details = _split_doc(raw) if raw else ("", "")
        title = edits.get("title")
        details = edits.get("details", cur_details)
        if name_kw is not None and title is not None:
            if not _is_str(name_kw.value):
                raise EditError("the title comes from computed code and can't be edited here")
            if not title.strip():
                raise EditError("the title can't be empty")
            ed.replace(name_kw.value, _quote(title.strip(), src.segment(name_kw.value)))
            title = None  # docstring title untouched
        doc_title = cur_title if title is None else title
        if (title is not None or "details" in edits) and (doc_title.strip() or details.strip()):
            if not doc_title.strip():
                raise EditError("the title can't be empty")
            first = fn.body[0]
            body_indent = src.lines[first.lineno - 1][: len(src.lines[first.lineno - 1]) - len(src.lines[first.lineno - 1].lstrip())]
            lit = _docstring_literal(doc_title, details, body_indent)
            if doc_node is not None:
                ed.replace(doc_node, lit)
            else:
                if first.lineno == fn.lineno:
                    raise EditError("the function body is on the same line as def; add a docstring by hand")
                ed.insert(src.line_start(first.lineno), body_indent + lit + "\n")

    # tags / depends_on
    lists = {f: list(dict.fromkeys(str(v).strip() for v in (edits[f] or []) if str(v).strip()))
             for f in ("tags", "depends_on") if f in edits}
    if deco is not None:
        for field, values in lists.items():
            kw = _kw(deco, field)
            if kw is not None and _str_list(kw.value) is None:
                raise EditError(f"{field} is computed in code and can't be edited here")
            if kw is not None and _str_list(kw.value) == values:
                continue
            _set_kwarg(ed, deco, field, _list_literal(values) if values else None)
    elif any(lists.values()):  # no @scenario yet: add one (and its import)
        name = _ensure_scenario_import(ed, tree)
        first_line = _first_line(fn)
        raw_line = src.lines[first_line - 1]
        indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        kws = ", ".join(f"{f}={_list_literal(v)}" for f, v in lists.items() if v)
        ed.insert(src.line_start(first_line), f"{indent}@{name}({kws})\n")

    # steps
    calls = step_calls(fn)
    for st in edits.get("steps") or []:
        try:
            i = int(st["index"])
            keyword, call, text_node, exp_kw = calls[i]
        except (KeyError, ValueError, IndexError):
            raise EditError(f"step {st.get('index')!r} does not exist any more; reopen the editor")
        if "text" in st:
            new = str(st["text"])
            if not _is_str(text_node):
                raise EditError(f"the text of step {i + 1} ({keyword}) is computed in code and can't be edited here")
            if new != text_node.value:
                if not new.strip():
                    raise EditError(f"step {i + 1} ({keyword}) needs some text")
                ed.replace(text_node, _quote(new, src.segment(text_node)))
        if "expected" in st:
            new = str(st["expected"] or "")
            if exp_kw is not None and not _is_str(exp_kw.value):
                raise EditError(f"the expected label of step {i + 1} is computed in code and can't be edited here")
            cur = exp_kw.value.value if exp_kw is not None else ""
            if new != cur:
                like = src.segment(exp_kw.value) if exp_kw is not None else src.segment(text_node)
                _set_kwarg(ed, call, "expected", _quote(new, like) if new else None)

    new_text = ed.result()
    try:
        ast.parse(new_text)
    except SyntaxError as exc:  # never write a file that doesn't parse
        raise EditError(f"the edit would break the file ({exc.msg}); nothing was written") from exc
    shown = display_name or path.name
    diff = "".join(difflib.unified_diff(
        text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"a/{shown}", tofile=f"b/{shown}", n=2,
    ))
    changed = new_text != text
    if changed and not dry_run:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new_text)
    model = None
    if not dry_run:
        model = load(path, function, line)
    return {"diff": diff, "changed": changed, "written": changed and not dry_run, "model": model}


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()
