#!/usr/bin/env python3
"""Safe arithmetic calculator for deterministic extraction math.

This strict skill variant requires the helper to run inside worker_sandbox.py.
"""

from __future__ import annotations

import argparse
import ast
import operator
import os
import sys
from typing import Any


def _assert_sandbox() -> None:
    if os.environ.get("CFST_SANDBOX") != "1":
        print("[FAIL] This script must run inside worker_sandbox.py (CFST_SANDBOX=1 not set).", file=sys.stderr)
        raise SystemExit(1)


ALLOWED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

ALLOWED_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _parse_vars(items: list[str]) -> dict[str, float]:
    variables: dict[str, float] = {}
    for raw in items:
        if "=" not in raw:
            raise ValueError(f"Invalid --var '{raw}', expected key=value.")
        key, value = raw.split("=", 1)
        name = key.strip()
        if not name.isidentifier():
            raise ValueError(f"Invalid variable name '{name}'.")
        try:
            variables[name] = float(value.strip())
        except ValueError as exc:
            raise ValueError(f"Variable '{name}' value must be numeric.") from exc
    return variables


def _eval_node(node: ast.AST, variables: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"Unsupported constant type: {type(node.value).__name__}")

    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ValueError(f"Unknown variable: {node.id}")
        return float(variables[node.id])

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in ALLOWED_BIN_OPS:
            raise ValueError(f"Unsupported operator: {op_type.__name__}")
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        return float(ALLOWED_BIN_OPS[op_type](left, right))

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in ALLOWED_UNARY_OPS:
            raise ValueError(f"Unsupported unary operator: {op_type.__name__}")
        operand = _eval_node(node.operand, variables)
        return float(ALLOWED_UNARY_OPS[op_type](operand))

    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def safe_eval(expression: str, variables: dict[str, float]) -> float:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid expression syntax: {exc}") from exc
    return _eval_node(tree.body, variables)


def main() -> int:
    _assert_sandbox()
    parser = argparse.ArgumentParser(
        description="Evaluate arithmetic expressions safely for CFST extraction math. Requires CFST_SANDBOX=1.",
    )
    parser.add_argument("expression", help='Expression to evaluate, for example "141.4 / 2".')
    parser.add_argument(
        "--var",
        action="append",
        default=[],
        help="Variable assignment in key=value form, repeatable.",
    )
    parser.add_argument(
        "--round",
        type=int,
        default=None,
        dest="round_digits",
        help="Optional decimal rounding digits, for example --round 3.",
    )
    args = parser.parse_args()

    try:
        variables = _parse_vars(args.var)
        result = safe_eval(args.expression, variables)
        if args.round_digits is not None:
            result = round(result, args.round_digits)
        print(result)
        return 0
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
