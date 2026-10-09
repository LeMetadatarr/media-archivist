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
import operator
from pathlib import Path
from typing import Any, Iterator, List, Optional

from media_archivist.models.canonical import MediaEntry
from media_archivist.storage import EnvelopeJsonStorage
from media_archivist.views import to_media_entry

# Operators allowed in ``--where`` expressions. Anything else raises.
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv)
_ALLOWED_BOOLOPS = (ast.And, ast.Or)
_ALLOWED_CMPOPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
                   ast.In, ast.NotIn)
_ALLOWED_FUNCS = {"len": len, "lower": str.lower, "upper": str.upper}
_BINOP_FUNCS = {ast.Add: operator.add, ast.Sub: operator.sub,
                 ast.Mult: operator.mul, ast.Div: operator.truediv,
                 ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv}

# Upper bound on the number of AST nodes a --where expression may contain.
# Guards against pathologically deep/wide expressions (parsed once per
# request, but evaluated once per row -- an expensive tree amplifies fast).
_MAX_DSL_NODES = 200


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
        if isinstance(node.op, ast.Mult):
            # String/bytes/list repetition (e.g. "a" * 10**9) has no
            # legitimate use in a filter predicate and lets a single
            # request force a giant allocation -- reject it outright.
            # Only plain numeric multiplication is allowed.
            if isinstance(a, (str, bytes, list)) or isinstance(b, (str, bytes, list)):
                raise WhereError("string/sequence repetition not allowed in --where")
        return _BINOP_FUNCS[type(node.op)](a, b)
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
        # Dotted access is allowed when the LHS is itself a dict-valued
        # field (e.g. relations.artist, external_ids.imdb). Method
        # access on strings (title.upper()) is still rejected because
        # the LHS resolves to a non-dict.
        target = _eval_node(node.value, ctx)
        if isinstance(target, dict):
            return target.get(node.attr)
        raise WhereError(
            f"attribute access on non-dict ({type(target).__name__})"
        )
    raise WhereError(f"unsupported syntax: {type(node).__name__}")


def evaluate_where(expr: str, entry: MediaEntry) -> bool:
    """Evaluate a sandboxed ``--where`` expression against ``entry``."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise WhereError(f"invalid expression: {e.msg}") from e
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAX_DSL_NODES:
        raise WhereError(
            f"expression too complex ({node_count} nodes > {_MAX_DSL_NODES} max)"
        )
    ctx = entry.model_dump(mode="python")
    return bool(_eval_node(tree.body, ctx))


class Index:
    """Read-side SDK over a media_archivist DB file."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._db = EnvelopeJsonStorage(self.path)
        self._canonical_index = self._load_canonical_index()
        self._entity_index = self._load_entity_index()
        self._id_index: Optional[dict[str, dict]] = None

    def _load_canonical_index(self):
        """Read ``<db>.canonical.json`` if present and build a lookup map."""
        from media_archivist.canonicalize import load_canonical
        try:
            sidecar = load_canonical(self.path)
        except Exception:
            return {}
        return {cid: rec for cid, rec in sidecar.records.items()}

    def _load_entity_index(self):
        """Read ``<db>.entities.json`` if present and build a lookup map."""
        from media_archivist.entities import load_entities
        try:
            sidecar = load_entities(self.path)
        except Exception:
            return {}
        return {eid: rec for eid, rec in sidecar.entities.items()}

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
             limit: int = 0,
             offset: int = 0) -> Iterator[MediaEntry]:
        """Yield :class:`MediaEntry` rows matching the given filters.

        ``offset`` skips the first ``offset`` matching rows (post-filter,
        pre-limit) before yielding — the standard offset/limit pagination
        contract. ``limit=0`` still means "no limit" (existing convention).
        """
        skipped = 0
        n = 0
        needle = grep.lower() if grep else None
        for raw in self._db.values():
            try:
                entry = to_media_entry(raw)
            except Exception:
                continue
            self._stamp_canonical(entry, raw)
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
            if skipped < offset:
                skipped += 1
                continue
            yield entry
            n += 1
            if limit and n >= limit:
                return

    def to_list(self, **filters) -> List[MediaEntry]:
        return list(self.view(**filters))

    def count(self, *, where: Optional[str] = None,
              source: Optional[str] = None,
              has_stream: Optional[bool] = None,
              explicit: Optional[bool] = None,
              grep: Optional[str] = None) -> int:
        """Count entries matching the given filters (no limit/offset).

        Applies the same predicate as :meth:`view` so callers can compute
        page totals, but skips MediaEntry construction is not possible in
        general (filters like ``where`` need the full entry), so this
        still builds each matching row -- it is a full scan, same cost
        class as ``view()`` without a limit.
        """
        n = 0
        for _ in self.view(where=where, source=source, has_stream=has_stream,
                            explicit=explicit, grep=grep, limit=0, offset=0):
            n += 1
        return n

    def _build_id_index(self) -> dict[str, dict]:
        """Build (and cache) a stable_id -> raw lookup map.

        The on-disk storage is keyed by URL, not by the derived entry id,
        so a keyed lookup still needs an id -> raw map. Computing that map
        only needs ``source``/``url`` (cheap), not a full MediaEntry
        conversion of every row, and is built once per Index instance
        rather than re-scanned per lookup.
        """
        from media_archivist.models.canonical import stable_id
        from media_archivist.models.raw import Source

        index: dict[str, dict] = {}
        for raw in self._db.values():
            try:
                sid = stable_id(Source(raw["source"]), raw["url"])
            except Exception:
                continue
            index[sid] = raw
        return index

    def get(self, entry_id: str) -> Optional[MediaEntry]:
        """Look up a single entry by id without a per-call full-table scan."""
        if self._id_index is None:
            self._id_index = self._build_id_index()
        raw = self._id_index.get(entry_id)
        if raw is None:
            return None
        try:
            entry = to_media_entry(raw)
        except Exception:
            return None
        self._stamp_canonical(entry, raw)
        return entry

    def _stamp_canonical(self, entry: MediaEntry, raw: dict) -> None:
        """Attach canonical_id / canonical_status / external_ids / relations from sidecars."""
        meta = raw.get("_meta") or {}
        cid = meta.get("canonical_id")
        status = meta.get("canonical_status")
        if cid:
            entry.canonical_id = cid
            rec = self._canonical_index.get(cid)
            if rec is not None:
                entry.external_ids = rec.external_ids
                # Resolve role → entity names; keep ids alongside.
                names: dict[str, list[str]] = {}
                ids: dict[str, list[str]] = {}
                for role, eids in (rec.relations or {}).items():
                    role_key = role.value if hasattr(role, "value") else str(role)
                    ids[role_key] = list(eids)
                    names[role_key] = [
                        self._entity_index[e].name for e in eids
                        if e in self._entity_index
                    ]
                entry.relations = names
                entry.relation_ids = ids
        if status:
            entry.canonical_status = status
