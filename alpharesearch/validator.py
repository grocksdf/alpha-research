"""A deliberately small audited FASTEXPR subset; never eval/exec model text."""
from __future__ import annotations

import ast
import math
from dataclasses import dataclass

from .common import ResearchError, digest


class InvalidExpression(ResearchError):
    pass


# Exact positional arity and temporal argument index. Extend by code review + fixtures.
OPERATORS = {
    "rank": (1, None), "abs": (1, None), "sign": (1, None),
    "log": (1, None), "sqrt": (1, None), "inverse": (1, None),
    "ts_mean": (2, 1), "ts_sum": (2, 1), "ts_std_dev": (2, 1),
    "ts_rank": (2, 1), "ts_delta": (2, 1), "ts_delay": (2, 1),
    "ts_decay_linear": (2, 1), "ts_zscore": (2, 1),
    "ts_corr": (3, 2), "ts_covariance": (3, 2),
    "group_rank": (2, None), "group_neutralize": (2, None),
    "signed_power": (2, None),
}
BINOPS = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


@dataclass(frozen=True)
class Validated:
    expression: str
    canonical: str
    fingerprint: str
    family: str
    fields: tuple[str, ...]
    nodes: int


def literal_number(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        if math.isfinite(node.value):
            return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = literal_number(node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    raise InvalidExpression("参数必须是有限数值字面量")


class Validator:
    def __init__(self, catalog, scope, rules):
        self.catalog, self.scope, self.rules = catalog, scope, rules

    def validate(self, expression):
        if not isinstance(expression, str) or len(expression) > self.rules["max_expression_chars"]:
            raise InvalidExpression("公式为空或超过长度限制")
        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except (SyntaxError, ValueError, RecursionError) as e:
            raise InvalidExpression("仅允许单个表达式，不支持赋值、语句、字符串或属性访问") from e
        total = sum(1 for _ in ast.walk(tree))
        if total > self.rules["max_ast_nodes"]:
            raise InvalidExpression("公式复杂度超过节点上限")
        fields = set()

        def walk(node, depth=0, family=False):
            if depth > self.rules["max_ast_depth"]:
                raise InvalidExpression("嵌套层数超过上限")
            if isinstance(node, ast.Name):
                field = self.catalog["fields"].get(node.id)
                if not field:
                    raise InvalidExpression(f"未知字段: {node.id}")
                if field.get("point_in_time") is not True:
                    raise InvalidExpression(f"字段时点语义未经审核: {node.id}")
                if field.get("min_delay", 999999) > self.scope["delay"]:
                    raise InvalidExpression(f"字段可用延迟不足: {node.id}")
                if field.get("kind") not in ("MATRIX", "GROUP"):
                    raise InvalidExpression(f"暂不支持的字段类型: {node.id}")
                fields.add(node.id)
                return node.id
            if isinstance(node, (ast.Constant, ast.UnaryOp)):
                if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
                    try:
                        value = literal_number(node)
                    except InvalidExpression:
                        return ("-" if isinstance(node.op, ast.USub) else "+") + "(" + walk(node.operand, depth+1, family) + ")"
                else:
                    value = literal_number(node)
                if abs(value) > 1e9:
                    raise InvalidExpression("数值常量过大")
                return "#" if family else str(int(value)) if float(value).is_integer() else repr(value)
            if isinstance(node, ast.BinOp) and type(node.op) in BINOPS:
                left, right = walk(node.left, depth+1, family), walk(node.right, depth+1, family)
                if isinstance(node.op, ast.Div):
                    try:
                        if literal_number(node.right) == 0:
                            raise InvalidExpression("常量除零")
                    except InvalidExpression as e:
                        if str(e) == "常量除零":
                            raise
                if isinstance(node.op, (ast.Add, ast.Mult)):
                    left, right = sorted((left, right))
                return f"({left}{BINOPS[type(node.op)]}{right})"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
                if name not in OPERATORS or name not in self.catalog["operators"]:
                    raise InvalidExpression(f"算子不在审计白名单: {name}")
                arity, window = OPERATORS[name]
                if node.keywords or len(node.args) != arity:
                    raise InvalidExpression(f"{name} 只支持 {arity} 个位置参数")
                if window is not None:
                    value = literal_number(node.args[window])
                    if type(value) is not int or not 1 <= value <= self.rules["max_window"]:
                        raise InvalidExpression(f"{name} 窗口/延迟必须为正整数字面量，禁止未来引用")
                if name.startswith("group_"):
                    group = node.args[1]
                    if not isinstance(group, ast.Name) or self.catalog["fields"].get(group.id, {}).get("kind") != "GROUP":
                        raise InvalidExpression("分组参数必须为 GROUP 字段")
                if name == "signed_power" and abs(literal_number(node.args[1])) > 8:
                    raise InvalidExpression("幂次超出审计范围")
                return f"{name}(" + ",".join(walk(a, depth+1, family) for a in node.args) + ")"
            raise InvalidExpression(f"不支持的 DSL 结构: {type(node).__name__}")

        canonical = walk(tree.body)
        shape = walk(tree.body, family=True)
        if not fields:
            raise InvalidExpression("公式必须引用数据字段")
        # GROUP 字段只可出现在 group_* 的第二个参数，其他输入只允许 MATRIX。
        def check_types(node, allow_group=False):
            if isinstance(node, ast.Name) and self.catalog["fields"][node.id]["kind"] == "GROUP" and not allow_group:
                raise InvalidExpression("GROUP 字段不能参与数值运算")
            if isinstance(node, ast.Call):
                for i, arg in enumerate(node.args):
                    check_types(arg, node.func.id.startswith("group_") and i == 1)
            else:
                for child in ast.iter_child_nodes(node):
                    check_types(child)
        check_types(tree.body)
        return Validated(expression.strip(), canonical, digest(canonical), digest(shape), tuple(sorted(fields)), total)

    def neighbors(self, expression):
        """Change one temporal argument, ±20%; no LLM-chosen favorable perturbation."""
        tree = ast.parse(expression, mode="eval")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                spec = OPERATORS.get(node.func.id)
                if spec and spec[1] is not None:
                    old = node.args[spec[1]].value
                    result = []
                    for factor in (1-self.rules["sensitivity_fraction"], 1+self.rules["sensitivity_fraction"]):
                        value = max(1, min(self.rules["max_window"], round(old*factor)))
                        if value != old:
                            node.args[spec[1]] = ast.Constant(value=value)
                            result.append(ast.unparse(tree))
                    return list(dict.fromkeys(result))[:self.rules["sensitivity_variants"]]
        return []
