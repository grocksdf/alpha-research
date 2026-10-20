from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .models import ValidationIssue, ValidationResult


class ParseError(ValueError):
    def __init__(self, message: str, position: int | None = None):
        super().__init__(message)
        self.position = position


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: str
    position: int


TOKEN_RE = re.compile(
    r"""
    (?P<WS>\s+)
  | (?P<NUMBER>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_.$]*)
  | (?P<OP><=|>=|==|!=|\|\||&&|[+\-*/%^<>()?,:])
  | (?P<BAD>.)
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> Iterator[Token]:
    for match in TOKEN_RE.finditer(text):
        kind = match.lastgroup or "BAD"
        if kind == "WS":
            continue
        if kind == "BAD":
            raise ParseError(f"unexpected character {match.group()!r}", match.start())
        value = match.group()
        if kind == "OP":
            kind = value
        yield Token(kind, value, match.start())
    yield Token("EOF", "", len(text))


class Node:
    def canonical(self) -> str:
        raise NotImplementedError

    def children(self) -> Iterable[Node]:
        return ()


@dataclass(frozen=True, slots=True)
class Number(Node):
    value: float

    def canonical(self) -> str:
        if self.value.is_integer():
            return str(int(self.value))
        return format(self.value, ".12g")


@dataclass(frozen=True, slots=True)
class Name(Node):
    value: str

    def canonical(self) -> str:
        return self.value.lower()


@dataclass(frozen=True, slots=True)
class Unary(Node):
    op: str
    operand: Node

    def canonical(self) -> str:
        return f"{self.op}{self.operand.canonical()}"

    def children(self) -> Iterable[Node]:
        return (self.operand,)


@dataclass(frozen=True, slots=True)
class Binary(Node):
    op: str
    left: Node
    right: Node

    def canonical(self) -> str:
        left = self.left.canonical()
        right = self.right.canonical()
        if self.op in {"+", "*", "==", "!="} and right < left:
            left, right = right, left
        return f"({left}{self.op}{right})"

    def children(self) -> Iterable[Node]:
        return (self.left, self.right)


@dataclass(frozen=True, slots=True)
class Conditional(Node):
    condition: Node
    yes: Node
    no: Node

    def canonical(self) -> str:
        return f"({self.condition.canonical()}?{self.yes.canonical()}:{self.no.canonical()})"

    def children(self) -> Iterable[Node]:
        return (self.condition, self.yes, self.no)


@dataclass(frozen=True, slots=True)
class Call(Node):
    function: str
    args: tuple[Node, ...]

    def canonical(self) -> str:
        args = ",".join(arg.canonical() for arg in self.args)
        return f"{self.function.lower()}({args})"

    def children(self) -> Iterable[Node]:
        return self.args


PRECEDENCE = {
    "||": 10,
    "&&": 20,
    "==": 30,
    "!=": 30,
    "<": 40,
    "<=": 40,
    ">": 40,
    ">=": 40,
    "+": 50,
    "-": 50,
    "*": 60,
    "/": 60,
    "%": 60,
    "^": 70,
}


class Parser:
    def __init__(self, text: str):
        self.tokens = list(tokenize(text))
        self.index = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def take(self, kind: str | None = None) -> Token:
        token = self.current
        if kind is not None and token.kind != kind:
            raise ParseError(f"expected {kind!r}, got {token.value!r}", token.position)
        self.index += 1
        return token

    def parse(self) -> Node:
        if self.current.kind == "EOF":
            raise ParseError("expression is empty", 0)
        node = self.expression(0)
        if self.current.kind != "EOF":
            raise ParseError(f"unexpected token {self.current.value!r}", self.current.position)
        return node

    def expression(self, minimum: int) -> Node:
        token = self.take()
        if token.kind == "NUMBER":
            left: Node = Number(float(token.value))
        elif token.kind == "IDENT":
            if self.current.kind == "(":
                self.take("(")
                args: list[Node] = []
                if self.current.kind != ")":
                    while True:
                        args.append(self.expression(0))
                        if self.current.kind != ",":
                            break
                        self.take(",")
                self.take(")")
                left = Call(token.value, tuple(args))
            else:
                left = Name(token.value)
        elif token.kind in {"+", "-"}:
            left = Unary(token.value, self.expression(80))
        elif token.kind == "(":
            left = self.expression(0)
            self.take(")")
        else:
            raise ParseError(f"unexpected token {token.value!r}", token.position)

        while True:
            token = self.current
            if token.kind == "?" and minimum <= 5:
                self.take("?")
                yes = self.expression(0)
                self.take(":")
                no = self.expression(5)
                left = Conditional(left, yes, no)
                continue
            precedence = PRECEDENCE.get(token.kind)
            if precedence is None or precedence < minimum:
                break
            self.take()
            right_minimum = precedence if token.kind == "^" else precedence + 1
            right = self.expression(right_minimum)
            left = Binary(token.kind, left, right)
        return left


DEFAULT_FIELDS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "returns",
    "cap",
    "adv20",
    "adv60",
}

FIELD_LITERALS = {
    "true",
    "false",
    "nan",
    "inf",
    "market",
    "sector",
    "industry",
    "subindustry",
    "exchange",
    "country",
}

# Arity is deliberately conservative. The live operator catalog can extend it.
DEFAULT_OPERATORS: dict[str, dict[str, Any]] = {
    "abs": {"min": 1, "max": 1},
    "add": {"min": 2, "max": 3},
    "divide": {"min": 2, "max": 2},
    "inverse": {"min": 1, "max": 1},
    "log": {"min": 1, "max": 1},
    "max": {"min": 2, "max": 2},
    "min": {"min": 2, "max": 2},
    "multiply": {"min": 2, "max": 3},
    "power": {"min": 2, "max": 2},
    "rank": {"min": 1, "max": 2},
    "signed_power": {"min": 2, "max": 2},
    "subtract": {"min": 2, "max": 3},
    "zscore": {"min": 1, "max": 1},
    "group_neutralize": {"min": 2, "max": 2},
    "group_rank": {"min": 2, "max": 2},
    "trade_when": {"min": 3, "max": 3},
    "if_else": {"min": 3, "max": 3},
    "ts_arg_max": {"min": 2, "max": 2, "window_args": [1]},
    "ts_arg_min": {"min": 2, "max": 2, "window_args": [1]},
    "ts_av_diff": {"min": 2, "max": 2, "window_args": [1]},
    "ts_corr": {"min": 3, "max": 3, "window_args": [2]},
    "ts_count_nans": {"min": 2, "max": 2, "window_args": [1]},
    "ts_covariance": {"min": 3, "max": 3, "window_args": [2]},
    "ts_decay_linear": {"min": 2, "max": 3, "window_args": [1]},
    "ts_delay": {"min": 2, "max": 2, "window_args": [1]},
    "ts_delta": {"min": 2, "max": 2, "window_args": [1]},
    "ts_max": {"min": 2, "max": 2, "window_args": [1]},
    "ts_mean": {"min": 2, "max": 2, "window_args": [1]},
    "ts_min": {"min": 2, "max": 2, "window_args": [1]},
    "ts_product": {"min": 2, "max": 2, "window_args": [1]},
    "ts_rank": {"min": 2, "max": 3, "window_args": [1]},
    "ts_regression": {"min": 3, "max": 6, "window_args": [2]},
    "ts_scale": {"min": 2, "max": 3, "window_args": [1]},
    "ts_std_dev": {"min": 2, "max": 2, "window_args": [1]},
    "ts_sum": {"min": 2, "max": 2, "window_args": [1]},
    "ts_zscore": {"min": 2, "max": 2, "window_args": [1]},
    "winsorize": {"min": 1, "max": 2},
}


def walk(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children():
        yield from walk(child)


def tree_depth(node: Node) -> int:
    children = list(node.children())
    return 1 if not children else 1 + max(tree_depth(child) for child in children)


def _number(node: Node) -> float | None:
    if isinstance(node, Number):
        return node.value
    if isinstance(node, Unary) and node.op == "-" and isinstance(node.operand, Number):
        return -node.operand.value
    return None


def operator_catalog_from_api(payload: Any) -> dict[str, dict[str, Any]]:
    catalog = dict(DEFAULT_OPERATORS)
    if isinstance(payload, dict):
        values = payload.get("results", payload.get("operators", payload.get("data", payload)))
    else:
        values = payload
    if not isinstance(values, list):
        return catalog
    for item in values:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("id")
        if not name:
            continue
        spec: dict[str, Any] = {}
        count = item.get("numberOfArguments") or item.get("arity")
        if isinstance(count, int):
            spec.update({"min": count, "max": count})
        catalog[str(name).lower()] = spec
    return catalog


class ExpressionValidator:
    def __init__(
        self,
        fields: Iterable[str] | None = None,
        operators: dict[str, dict[str, Any]] | None = None,
        rules: dict[str, Any] | None = None,
    ):
        self.fields = {field.lower() for field in (fields or DEFAULT_FIELDS)}
        self.operators = {
            name.lower(): spec for name, spec in (operators or DEFAULT_OPERATORS).items()
        }
        self.rules = {
            "max_nodes": 80,
            "max_depth": 12,
            "max_duplicate_subtree": 3,
            "max_window": 756,
            "forbidden_operators": ["lead", "ts_lead", "future_return"],
            **(rules or {}),
        }

    def validate(self, expression: str) -> ValidationResult:
        issues: list[ValidationIssue] = []
        try:
            root = Parser(expression).parse()
        except ParseError as exc:
            issues.append(ValidationIssue("SYNTAX", str(exc), position=exc.position))
            return ValidationResult(False, None, [], [], 0, 0, None, issues)

        nodes = list(walk(root))
        names = sorted({n.value.lower() for n in nodes if isinstance(n, Name)})
        calls = [n for n in nodes if isinstance(n, Call)]
        operators = sorted({call.function.lower() for call in calls})

        for name in names:
            if name not in self.fields and name not in FIELD_LITERALS:
                issues.append(ValidationIssue("UNKNOWN_FIELD", f"unknown field: {name}"))

        forbidden = {value.lower() for value in self.rules["forbidden_operators"]}
        for call in calls:
            function = call.function.lower()
            if function in forbidden or "future" in function or function.endswith("_lead"):
                issues.append(
                    ValidationIssue("LOOKAHEAD_OPERATOR", f"forbidden operator: {function}")
                )
            spec = self.operators.get(function)
            if spec is None:
                issues.append(ValidationIssue("UNKNOWN_OPERATOR", f"unknown operator: {function}"))
                continue
            minimum = spec.get("min")
            maximum = spec.get("max")
            if minimum is not None and len(call.args) < minimum:
                issues.append(
                    ValidationIssue("ARITY", f"{function} requires at least {minimum} arguments")
                )
            if maximum is not None and len(call.args) > maximum:
                issues.append(
                    ValidationIssue("ARITY", f"{function} accepts at most {maximum} arguments")
                )
            for index in spec.get("window_args", []):
                if index >= len(call.args):
                    continue
                value = _number(call.args[index])
                if value is None:
                    issues.append(
                        ValidationIssue(
                            "DYNAMIC_WINDOW",
                            f"{function} window argument {index + 1} must be numeric",
                        )
                    )
                elif value <= 0:
                    code = "LOOKAHEAD_DELAY" if function == "ts_delay" else "INVALID_WINDOW"
                    issues.append(ValidationIssue(code, f"{function} window must be positive"))
                elif value > float(self.rules["max_window"]):
                    issues.append(
                        ValidationIssue(
                            "WINDOW_TOO_LARGE",
                            f"{function} window {value:g} exceeds {self.rules['max_window']}",
                        )
                    )

        depth = tree_depth(root)
        if len(nodes) > int(self.rules["max_nodes"]):
            issues.append(ValidationIssue("COMPLEXITY", f"node count {len(nodes)} is too high"))
        if depth > int(self.rules["max_depth"]):
            issues.append(ValidationIssue("COMPLEXITY", f"tree depth {depth} is too high"))

        subtrees = Counter(node.canonical() for node in nodes)
        duplicate_limit = int(self.rules["max_duplicate_subtree"])
        repeated = [
            value for value, count in subtrees.items() if count > duplicate_limit and len(value) > 3
        ]
        if repeated:
            issues.append(
                ValidationIssue(
                    "REPEATED_STRUCTURE",
                    f"subtree repeated more than {duplicate_limit} times: {repeated[0][:80]}",
                    severity="warning",
                )
            )

        canonical = root.canonical()
        skeleton = re.sub(r"(?<![A-Za-z_])[-+]?\d+(?:\.\d+)?", "#", canonical)
        family_hash = hashlib.sha256(skeleton.encode()).hexdigest()[:16]
        valid = not any(issue.severity == "error" for issue in issues)
        return ValidationResult(
            valid=valid,
            canonical=canonical,
            fields=names,
            operators=operators,
            node_count=len(nodes),
            depth=depth,
            family_hash=family_hash,
            issues=issues,
        )

    def describe(self) -> str:
        return json.dumps(
            {
                "fields": sorted(self.fields),
                "operators": sorted(self.operators),
                "rules": self.rules,
            },
            ensure_ascii=False,
        )
