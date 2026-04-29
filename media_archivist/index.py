"""High-level read-side SDK over a media_archivist DB file.

``Index`` opens the on-disk envelope read-only and exposes the entries
through the canonical :class:`MediaEntry` view. Filtering uses a
sandboxed ``--where`` expression evaluator: identifiers refer to fields
of the entry; ``len``, ``in``, comparisons and basic boolean operators
are allowed.

Example::

    from media_archivist import Index

    idx = Index("./talks.json")
    for e in idx.view(where='artist=="Foo" and duration>180'):
        print(e.url)
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional

from media_archivist.models.canonical import MediaEntry
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.views import to_media_entry

# Operators allowed in ``--where`` expressions. Anything else raises.
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv)
_ALLOWED_BOOLOPS = (ast.And, ast.Or)
_ALLOWED_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                   ast.In, ast.NotIn)
_ALLOWED_FUNCS = {"len": len, "lower": str.lower, "upper": str.upper}


class WhereError(ValueError):
    """Raised when a ``--where`` expression is invalid or uses denied syntax."""


def _eval_node(node: ast.AST, ctx: dict) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        if node.id in _ALLOWED_FUNCS:
            return _ALLOWED_FUNCS[node.id]
        raise WhereError(f"unknown name: {node.id}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
        v = _eval_node(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return not v
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BoolOp) and isinstance(node.op, _ALLOWED_BOOLOPS):
        vals = [_eval_node(v, ctx) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
        a, b = _eval_node(node.left, ctx), _eval_node(node.right, ctx)
        ops = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
               ast.Mod: "%", ast.FloorDiv: "//"}
        return eval(f"a {ops[type(node.op)]} b", {"a": a, "b": b})
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, _ALLOWED_CMPOPS):
                raise WhereError(f"comparator not allowed: {type(op).__name__}")
            right = _eval_node(comparator, ctx)
            # Ordering comparators with None on either side fail closed.
            if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) and (
                left is None or right is None
            ):
                return False
            if isinstance(op, ast.Eq) and not (left == right):
                return False
            if isinstance(op, ast.NotEq) and not (left != right):
                return False
            if isinstance(op, ast.Lt) and not (left < right):
                return False
            if isinstance(op, ast.LtE) and not (left <= right):
                return False
            if isinstance(op, ast.Gt) and not (left > right):
                return False
            if isinstance(op, ast.GtE) and not (left >= right):
                return False
            if isinstance(op, ast.In) and not (left in (right or [])):
                return False
            if isinstance(op, ast.NotIn) and not (left not in (right or [])):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        func = _eval_node(node.func, ctx)
        args = [_eval_node(a, ctx) for a in node.args]
        if func not in _ALLOWED_FUNCS.values():
            raise WhereError("only len/lower/upper are allowed in --where")
        return func(*args)
    if isinstance(node, ast.Attribute):
        # support entry.field via flat ctx — disallow attribute access
        raise WhereError("attribute access not allowed; use bare field names")
    raise WhereError(f"unsupported syntax: {type(node).__name__}")


def evaluate_where(expr: str, entry: MediaEntry) -> bool:
    """Evaluate a sandboxed ``--where`` expression against ``entry``."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise WhereError(f"invalid expression: {e.msg}") from e
    ctx = entry.model_dump(mode="python")
    return bool(_eval_node(tree.body, ctx))


class Index:
    """Read-side SDK over a media_archivist DB file."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._db = EnvelopeJsonStorage(self.path)

    def __len__(self) -> int:
        return len(self._db)

    @property
    def meta(self):
        return self._db.meta

    def raw_entries(self) -> Iterator[dict]:
        yield from self._db.values()

    def view(self, *, where: Optional[str] = None,
             source: Optional[str] = None,
             has_stream: Optional[bool] = None,
             explicit: Optional[bool] = None,
             grep: Optional[str] = None,
             limit: int = 0) -> Iterator[MediaEntry]:
        """Yield :class:`MediaEntry` rows matching the given filters."""
        n = 0
        needle = grep.lower() if grep else None
        for raw in self._db.values():
            try:
                entry = to_media_entry(raw)
            except Exception:
                continue
            if source is not None and entry.source.value != source:
                continue
            if has_stream is True and not entry.stream:
                continue
            if has_stream is False and entry.stream:
                continue
            if explicit is True and not entry.explicit:
                continue
            if explicit is False and entry.explicit:
                continue
            if needle and needle not in (entry.title or "").lower():
                continue
            if where and not evaluate_where(where, entry):
                continue
            yield entry
            n += 1
            if limit and n >= limit:
                return

    def to_list(self, **filters) -> List[MediaEntry]:
        return list(self.view(**filters))
