"""Safe boolean expression evaluator used by the flow validator and the voice runtime.

Parses with :mod:`ast` and walks a strict whitelist of node types. Never calls ``eval``/``exec``.

Supported:
  * literals (str, int, float, bool, None) and list/tuple/set literals
  * variable names (unknown -> None); ``true``/``false``/``null``/``none`` aliases
  * ``a.b`` / ``a['b']`` / ``a[0]`` -> key/index lookup on dict/list values only (never getattr)
  * comparisons ``== != < <= > >= in not in is is not`` (chained allowed)
  * ``and`` / ``or`` / ``not``, unary ``-``/``+``
  * arithmetic ``+ - * / // %``
  * ternary ``x if cond else y``
  * functions: len, lower, upper, strip, int, float, str, abs, min, max, bool;
    string methods ``.lower() .upper() .strip()``

Semantics: a comparison involving a ``None`` operand is False (unless comparing against a
literal ``None``). Runtime type errors make :func:`evaluate` return False; unsupported syntax
raises :class:`ExpressionError`.
"""

from __future__ import annotations

import ast
import operator
from functools import lru_cache
from typing import Any

__all__ = [
    "ExpressionError",
    "evaluate",
    "evaluate_value",
    "parse_names",
    "check_expression",
]

MAX_EXPR_LEN = 1000
MAX_DEPTH = 40
MAX_SEQ_LEN = 10_000

_ALIASES: dict[str, Any] = {"true": True, "false": False, "null": None, "none": None}


class ExpressionError(ValueError):
    """Raised for syntax errors or disallowed constructs."""


class _EvalError(Exception):
    """Internal: runtime type problems -> expression evaluates to False."""


def _to_str(v: Any) -> str | None:
    return None if v is None else str(v)


def _lower(v: Any) -> Any:
    return None if v is None else str(v).lower()


def _upper(v: Any) -> Any:
    return None if v is None else str(v).upper()


def _strip(v: Any) -> Any:
    return None if v is None else str(v).strip()


def _len(v: Any) -> Any:
    if v is None:
        return 0
    if isinstance(v, (str, list, tuple, dict, set)):
        return len(v)
    return len(str(v))


def _num(fn):
    def inner(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.replace(",", "").strip()
        try:
            return fn(float(v)) if fn is int else fn(v)
        except (TypeError, ValueError) as e:
            raise _EvalError(str(e)) from e

    return inner


def _min_max(fn):
    def inner(*args: Any) -> Any:
        vals = [a for a in (args[0] if len(args) == 1 and isinstance(args[0], (list, tuple)) else args)
                if a is not None]
        if not vals:
            return None
        try:
            return fn(vals)
        except TypeError as e:
            raise _EvalError(str(e)) from e

    return inner


_FUNCS: dict[str, Any] = {
    "len": _len,
    "lower": _lower,
    "upper": _upper,
    "strip": _strip,
    "int": _num(int),
    "float": _num(float),
    "str": _to_str,
    "abs": lambda v: None if v is None else abs(v),
    "min": _min_max(min),
    "max": _min_max(max),
    "bool": lambda v: bool(v),
}
_METHODS: dict[str, Any] = {"lower": _lower, "upper": _upper, "strip": _strip}

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: lambda a, b: a is b,
    ast.IsNot: lambda a, b: a is not b,
}


# --------------------------------------------------------------------------- parsing


def _check(node: ast.AST, depth: int = 0) -> None:
    """Validate that ``node`` only uses whitelisted constructs."""
    if depth > MAX_DEPTH:
        raise ExpressionError("expression is nested too deeply")
    if isinstance(node, ast.Expression):
        _check(node.body, depth + 1)
    elif isinstance(node, ast.Constant):
        if not isinstance(node.value, (str, int, float, bool, type(None))):
            raise ExpressionError(f"unsupported literal: {node.value!r}")
    elif isinstance(node, ast.Name):
        if node.id.startswith("_"):
            raise ExpressionError(f"names starting with '_' are not allowed: {node.id}")
        if not isinstance(node.ctx, ast.Load):
            raise ExpressionError("assignment is not allowed")
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for e in node.elts:
            if isinstance(e, ast.Starred):
                raise ExpressionError("starred expressions are not allowed")
            _check(e, depth + 1)
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            _check(v, depth + 1)
    elif isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.Not, ast.USub, ast.UAdd)):
            raise ExpressionError(f"unsupported operator: {type(node.op).__name__}")
        _check(node.operand, depth + 1)
    elif isinstance(node, ast.BinOp):
        if type(node.op) not in _BINOPS:
            raise ExpressionError(f"unsupported operator: {type(node.op).__name__}")
        _check(node.left, depth + 1)
        _check(node.right, depth + 1)
    elif isinstance(node, ast.Compare):
        for op in node.ops:
            if type(op) not in _CMPOPS:
                raise ExpressionError(f"unsupported comparison: {type(op).__name__}")
        _check(node.left, depth + 1)
        for c in node.comparators:
            _check(c, depth + 1)
    elif isinstance(node, ast.IfExp):
        _check(node.test, depth + 1)
        _check(node.body, depth + 1)
        _check(node.orelse, depth + 1)
    elif isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExpressionError(f"attribute '{node.attr}' is not allowed")
        _check(node.value, depth + 1)
    elif isinstance(node, ast.Subscript):
        if isinstance(node.slice, ast.Slice):
            raise ExpressionError("slices are not allowed")
        _check(node.value, depth + 1)
        _check(node.slice, depth + 1)
    elif isinstance(node, ast.Call):
        if node.keywords:
            raise ExpressionError("keyword arguments are not allowed")
        for a in node.args:
            if isinstance(a, ast.Starred):
                raise ExpressionError("starred arguments are not allowed")
            _check(a, depth + 1)
        func = node.func
        if isinstance(func, ast.Name):
            if func.id not in _FUNCS:
                raise ExpressionError(f"function '{func.id}' is not allowed")
        elif isinstance(func, ast.Attribute):
            if func.attr not in _METHODS or node.args:
                raise ExpressionError(f"method '{func.attr}' is not allowed")
            _check(func.value, depth + 1)
        else:
            raise ExpressionError("unsupported call")
    else:
        raise ExpressionError(f"unsupported syntax: {type(node).__name__}")


@lru_cache(maxsize=1024)
def _parse(expr: str) -> ast.Expression:
    if not isinstance(expr, str):
        raise ExpressionError("expression must be a string")
    src = expr.strip()
    if not src:
        raise ExpressionError("expression is empty")
    if len(src) > MAX_EXPR_LEN:
        raise ExpressionError(f"expression longer than {MAX_EXPR_LEN} characters")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"syntax error: {e.msg}") from e
    _check(tree)
    return tree


def check_expression(expr: str) -> str | None:
    """Return an error message if ``expr`` is invalid, else None."""
    try:
        _parse(expr)
    except ExpressionError as e:
        return str(e)
    return None


def parse_names(expr: str) -> set[str]:
    """Return the variable names referenced by ``expr`` (root names only, e.g. ``dealer`` for
    ``dealer.city``). Function names and aliases (true/false/null) are excluded.
    Raises :class:`ExpressionError` on invalid expressions."""
    tree = _parse(expr)
    names: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                visit(node.func.value)
            for a in node.args:
                visit(a)
            return
        if isinstance(node, ast.Name):
            if node.id not in _ALIASES:
                names.add(node.id)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return names


# --------------------------------------------------------------------------- evaluation


def _coerce_pair(a: Any, b: Any) -> tuple[Any, Any]:
    """Allow comparing numbers with numeric strings ("500000" vs 500000)."""
    num = (int, float)
    if isinstance(a, num) and not isinstance(a, bool) and isinstance(b, str):
        try:
            return a, float(b.replace(",", ""))
        except ValueError:
            return a, b
    if isinstance(b, num) and not isinstance(b, bool) and isinstance(a, str):
        try:
            return float(a.replace(",", "")), b
        except ValueError:
            return a, b
    return a, b


def _is_none_literal(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and node.value is None) or (
        isinstance(node, ast.Name) and node.id in ("null", "none")
    )


def _eval(node: ast.AST, vars: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body, vars)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in vars:
            return vars[node.id]
        return _ALIASES.get(node.id)
    if isinstance(node, ast.List):
        return [_eval(e, vars) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval(e, vars) for e in node.elts)
    if isinstance(node, ast.Set):
        try:
            return {_eval(e, vars) for e in node.elts}
        except TypeError as e:
            raise _EvalError(str(e)) from e
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for v in node.values:
                result = _eval(v, vars)
                if not result:
                    return result
            return result
        result = False
        for v in node.values:
            result = _eval(v, vars)
            if result:
                return result
        return result
    if isinstance(node, ast.UnaryOp):
        val = _eval(node.operand, vars)
        if isinstance(node.op, ast.Not):
            return not val
        if val is None:
            return None
        if not isinstance(val, (int, float)):
            val = _num(float)(val)
        return -val if isinstance(node.op, ast.USub) else +val
    if isinstance(node, ast.BinOp):
        left = _eval(node.left, vars)
        right = _eval(node.right, vars)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            for seq, n in ((left, right), (right, left)):
                if isinstance(seq, (str, list, tuple)) and isinstance(n, int):
                    if len(seq) * max(n, 0) > MAX_SEQ_LEN:
                        raise _EvalError("result too large")
        if not isinstance(node.op, ast.Add) or not (isinstance(left, str) and isinstance(right, str)):
            left, right = _coerce_pair(left, right)
        try:
            return _BINOPS[type(node.op)](left, right)
        except (TypeError, ValueError, ZeroDivisionError, OverflowError) as e:
            raise _EvalError(str(e)) from e
    if isinstance(node, ast.Compare):
        left_node = node.left
        left = _eval(left_node, vars)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, vars)
            if isinstance(op, (ast.Is, ast.IsNot)):
                ok = _CMPOPS[type(op)](left, right)
            elif (left is None or right is None) and not (
                _is_none_literal(left_node) or _is_none_literal(right_node)
            ):
                return False
            else:
                a, b = _coerce_pair(left, right)
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(a, str) and isinstance(b, (list, tuple, set)):
                    # case-insensitive membership for strings in literal lists
                    a = a.lower()
                    b = [x.lower() if isinstance(x, str) else x for x in b]
                try:
                    ok = _CMPOPS[type(op)](a, b)
                except TypeError as e:
                    raise _EvalError(str(e)) from e
            if not ok:
                return False
            left, left_node = right, right_node
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, vars) if _eval(node.test, vars) else _eval(node.orelse, vars)
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, vars)
        if isinstance(base, dict):
            return base.get(node.attr)
        return None
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, vars)
        key = _eval(node.slice, vars)
        if isinstance(base, dict):
            try:
                return base.get(key)
            except TypeError:
                return None
        if isinstance(base, (list, tuple)) and isinstance(key, int) and not isinstance(key, bool):
            return base[key] if -len(base) <= key < len(base) else None
        return None
    if isinstance(node, ast.Call):
        args = [_eval(a, vars) for a in node.args]
        if isinstance(node.func, ast.Name):
            fn = _FUNCS[node.func.id]
            try:
                return fn(*args)
            except TypeError as e:
                raise _EvalError(str(e)) from e
        assert isinstance(node.func, ast.Attribute)
        return _METHODS[node.func.attr](_eval(node.func.value, vars))
    raise ExpressionError(f"unsupported syntax: {type(node).__name__}")  # pragma: no cover


def evaluate_value(expr: str, vars: dict[str, Any] | None = None) -> Any:
    """Evaluate and return the raw value. Raises ExpressionError on invalid syntax and
    returns None on runtime type errors."""
    tree = _parse(expr)
    try:
        return _eval(tree, dict(vars or {}))
    except _EvalError:
        return None
    except RecursionError:
        return None


def evaluate(expr: str, vars: dict[str, Any] | None = None) -> bool:
    """Evaluate ``expr`` against ``vars`` and return a bool.

    Raises :class:`ExpressionError` for invalid/disallowed syntax. Runtime type errors
    (e.g. ``'abc' > 5``) evaluate to False.
    """
    tree = _parse(expr)
    try:
        return bool(_eval(tree, dict(vars or {})))
    except (_EvalError, RecursionError):
        return False
