"""Deterministic pre-Critic FASTEXPR checks and LLM-only repair diagnostics.

No source of Alpha expressions. This module cannot generate any candidate,
operator replacement or parameter perturbation. Every repair must come from
a new, audited Worker tool response and be re-checked by Validator.
"""
from __future__ import annotations

import ast
import hashlib
import re

from .common import ResearchError
from .formula_quality import validate_units
from .validator import InvalidExpression, OPERATORS


def validate_formula(engine, expression, hypothesis):
    """Returns host-verified AST/semantic unit result or throws ResearchError."""
    result=engine.validator.validate(expression)
    allowed=set(hypothesis["required_fields"])
    if not result.fields or not set(result.fields).issubset(allowed):
        raise InvalidExpression("公式字段必须属于当前假设经 Explorer 声明的授权集合")
    unit=validate_units(expression,engine.config["catalog_data"])
    return result,unit


def formula_feedback(expression, error, catalog):
    """Only report locations/facts already deterministically derived."""
    if not isinstance(expression,str):
        expression=""
    payload={
        "error_type":type(error).__name__,
        "message":str(error)[:500],
        "expression_sha256":hashlib.sha256(expression.encode("utf-8")).hexdigest(),
        "location":None,"illegal_operator":None,
        "unapproved_fields":[],
        "approved_operators":sorted(catalog["operators"]),
        "no_formula_example_or_seed":True}
    try:
        parsed=ast.parse(expression,mode="eval")
    except SyntaxError as exc:
        payload["location"]={"line":exc.lineno,"column":exc.offset}
        return payload
    except (ValueError,RecursionError):
        return payload
    for n in ast.walk(parsed):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name):
            if n.func.id not in OPERATORS or n.func.id not in catalog["operators"]:
                payload["illegal_operator"]=n.func.id
                payload["location"]={"line":n.lineno,"column":n.col_offset}
                break
    referenced={
        n.id for n in ast.walk(parsed) if isinstance(n,ast.Name)}
    operators={
        n.func.id for n in ast.walk(parsed) if isinstance(n,ast.Call)
        and isinstance(n.func,ast.Name)}
    payload["unapproved_fields"]=sorted(
        x for x in referenced-operators if x not in catalog["fields"])
    if not payload["location"]:
        for node in ast.walk(parsed):
            if isinstance(node,ast.Name) and node.id in payload["unapproved_fields"]:
                payload["location"]={"line":node.lineno,"column":node.col_offset}
                break
    return payload
