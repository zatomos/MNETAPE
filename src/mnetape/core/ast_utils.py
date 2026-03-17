"""Shared AST utility helpers used by the action builder and code generator."""

from __future__ import annotations

import ast

def value_to_ast(value: object) -> ast.expr:
    """Convert a Python value to an AST expression node."""
    if value is None:
        return ast.Constant(value=None)
    if isinstance(value, bool):
        return ast.Constant(value=value)
    if isinstance(value, (int, float, str)):
        return ast.Constant(value=value)
    if isinstance(value, list):
        return ast.List(elts=[value_to_ast(v) for v in value], ctx=ast.Load())
    if isinstance(value, dict):
        return ast.Dict(
            keys=[ast.Constant(value=k) for k in value.keys()],
            values=[value_to_ast(v) for v in value.values()],
        )
    return ast.Constant(value=str(value))

def get_dotted_name(node: ast.expr) -> str | None:
    """Return 'a.b.c' string for an AST Name or Attribute chain, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = get_dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None
