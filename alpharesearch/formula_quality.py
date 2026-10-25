"""Safe expression quality preflight without changing legacy AST fingerprints.

The unit algebra is conservative. Unknown live units are distinct symbolic
quantities, never guessed from field names. Structural similarity is advisory;
only exact normalized equivalence can be flagged for dedup, never forbidden
just because a factor uses a well-known operator family.
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections import Counter

from .common import ResearchError
from .validator import InvalidExpression, literal_number


class UnitError(InvalidExpression):
    pass


def normalized_signature(expression):
    """Conservative associative/commutative AST screen, not a trial fingerprint.

    Numeric windows and field identities remain significant. No unsafe algebra
    such as canceling x/x, zero multiplication, reassociating division or
    simplifying log/NaN behavior is performed.
    """
    tree = ast.parse(expression.strip(), mode='eval')

    def normalize(node):
        if isinstance(node, ast.Name):
            return ('name', node.id)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return ('num', repr(float(node.value)))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
            return normalize(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return ('neg', normalize(node.operand))
        if isinstance(node, ast.BinOp):
            if isinstance(node.op, (ast.Add, ast.Mult)):
                op = 'add' if isinstance(node.op, ast.Add) else 'mul'
                def collect(n):
                    if isinstance(n, ast.BinOp) and type(n.op) is type(node.op):
                        return collect(n.left) + collect(n.right)
                    return [normalize(n)]
                args = collect(node)
                return (op, tuple(sorted(args, key=repr)))
            ops = {ast.Sub: 'sub', ast.Div: 'div'}
            if type(node.op) not in ops:
                raise InvalidExpression('Unsupported AST operation for quality check')
            return (ops[type(node.op)], normalize(node.left), normalize(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            return ('call', node.func.id, tuple(normalize(arg) for arg in node.args))
        raise InvalidExpression('Unsupported AST expression for quality check')

    return hashlib.sha256(repr(normalize(tree.body)).encode('utf-8')).hexdigest()


def structural_similarity(left, right):
    """Multiset weighted subtree overlap, not observed return correlation."""
    def profile(expression):
        root = ast.parse(expression, mode='eval')
        counter = Counter()
        for node in ast.walk(root):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                counter['op:'+node.func.id] += 2
                fields = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)
                                 and n is not node.func})
                counter['context:'+node.func.id+':'+','.join(fields)] += 1
            elif isinstance(node, ast.Name):
                if not isinstance(node.ctx, ast.Load):
                    continue
                counter['field-or-op:'+node.id] += 1
        return counter
    a, b = profile(left), profile(right)
    union = sum((a | b).values())
    return sum((a & b).values()) / union if union else 1.0


def find_equivalent(memory, context, expression, fingerprint, *, additional=()):
    """Read-only scan of existing same-context formulas; no schema changes."""
    wanted = normalized_signature(expression)
    for prior in additional:
        if isinstance(prior, dict) and prior.get('expression') and prior.get('fingerprint') != fingerprint:
            try:
                if normalized_signature(prior['expression']) == wanted:
                    return {'existing': prior.get('id'), 'scope': 'queued',
                            'reason': 'normalized_equivalent_not_return_correlation'}
            except (InvalidExpression, SyntaxError, ValueError):
                continue
    rows = memory.db.execute('SELECT id,expression,fingerprint FROM trials WHERE context=?', (context,))
    for item in rows:
        if item['fingerprint'] == fingerprint:
            continue  # Distinct hypotheses can reuse identical trial via existing memory path.
        try:
            if normalized_signature(item['expression']) == wanted:
                return {'existing': item['id'], 'scope': 'prior_trial',
                        'reason': 'normalized_equivalent_requires_manual_reuse_review'}
        except (InvalidExpression, SyntaxError, ValueError):
            continue
    return None


# Unit dimensions are evidence, not guesses from financial field names.
# Explicitly reviewed metadata supports hard rejection of *proven* mistakes.
# Missing/conflicting evidence is propagated as a separate diagnostic instead
# of permanently rejecting plausible formulas before their first Simulation.
DIMENSIONLESS = ()
UNKNOWN_PREFIX = 'UNREVIEWED_FIELD:'
UNRESOLVED_UNIT = (('UNRESOLVED_UNITS', 1.0),)


def _unit_of_field(name, meta):
    raw = meta.get('unit', meta.get('unit_type', meta.get('units')))
    if meta.get('unit_reviewed') is True and isinstance(raw, str) and 0 < len(raw.strip()) <= 80:
        token = raw.strip().upper()
        if token in ('1', 'DIMENSIONLESS', 'RATIO', 'PERCENT', 'RETURN', 'FRACTION'):
            return DIMENSIONLESS
        return ((token, 1.0),)
    # The field name must never become a source of inferred units.
    return ((UNKNOWN_PREFIX + name, 1.0),)


def _combine(a, b, sign):
    if a == UNRESOLVED_UNIT or b == UNRESOLVED_UNIT:
        return UNRESOLVED_UNIT
    result = dict(a)
    for key, exponent in b:
        result[key] = result.get(key, 0.0) + sign*exponent
    return tuple(sorted((key, round(value, 8)) for key, value in result.items()
                        if abs(value) > 1e-8))


def _power(unit, exponent):
    if unit == UNRESOLVED_UNIT:
        return UNRESOLVED_UNIT
    return tuple(sorted((key, round(value*exponent, 8)) for key,value in unit
                        if abs(value*exponent) > 1e-8))


def _uncertain(unit):
    return any(key.startswith(UNKNOWN_PREFIX) or key == 'UNRESOLVED_UNITS'
               for key, _ in unit)


def validate_units(expression, catalog):
    """Three-valued unit check: verified, insufficient evidence, or error.

    A dimensional contradiction using two *reviewed* field units is an
    InvalidExpression/UnitError. An unreviewed field's dimensions are unknown;
    conservative semantics record the uncertainty without treating it as a
    proven mathematical error. Caller receives diagnostics; no secret metadata,
    guessed price units, or unreviewed unit equivalence is introduced.

    This runs AFTER the original AST/operator/PIT gate; its output cannot
    replace the original validator, simulation budget or review contracts.
    """
    root = ast.parse(expression, mode='eval')
    fields = catalog['fields']
    warnings = []
    warning_keys = set()

    def warn(code, detail):
        key = (code, detail)
        if key not in warning_keys:
            warning_keys.add(key)
            warnings.append({'code': code, 'detail': detail})

    def check(node):
        if isinstance(node, ast.Name):
            meta = fields.get(node.id)
            if not isinstance(meta, dict):
                raise UnitError('unit field is not in approved catalog')
            unit = _unit_of_field(node.id, meta)
            if _uncertain(unit):
                warn('field_unit_unreviewed', node.id)
            return unit
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return DIMENSIONLESS
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return check(node.operand)
        if isinstance(node, ast.BinOp):
            left, right = check(node.left), check(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                # Addition/subtraction of zero is dimension-neutral.
                if isinstance(node.right, ast.Constant) and node.right.value == 0:
                    return left
                if isinstance(node.left, ast.Constant) and node.left.value == 0:
                    return right
                if left == right:
                    return left
                # A scalar constant may be a scale-specific pseudocount
                # (e.g. volume+1 shares). The DSL does not annotate literal
                # units; treat this as insufficient evidence, NOT a proved
                # dimensional conflict. Never silently assume a conversion.
                if (isinstance(node.left, ast.Constant) or
                        isinstance(node.right, ast.Constant)):
                    warn('literal_unit_unspecified', 'nonzero scalar offset')
                    return UNRESOLVED_UNIT
                if _uncertain(left) or _uncertain(right):
                    warn('additive_units_unverified', 'operand dimensions cannot be established')
                    return UNRESOLVED_UNIT
                raise UnitError('已审核字段单位冲突：加减法要求相同量纲')
            if isinstance(node.op, ast.Mult):
                return _combine(left, right, 1)
            if isinstance(node.op, ast.Div):
                return _combine(left, right, -1)
            raise UnitError('unsupported arithmetic operator')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            args = [check(arg) if not (name.startswith('group_') and i == 1)
                    else DIMENSIONLESS for i, arg in enumerate(node.args)]
            if name in ('rank', 'ts_rank', 'ts_zscore', 'ts_corr', 'sign', 'group_rank'):
                return DIMENSIONLESS
            if name == 'ts_covariance':
                return _combine(args[0], args[1], 1)
            if name == 'log':
                if args[0] != DIMENSIONLESS:
                    if _uncertain(args[0]):
                        warn('dimensionless_requirement_unverified', 'log argument')
                    else:
                        raise UnitError('log() 已证明输入有量纲；必须为无量纲')
                return DIMENSIONLESS
            if name == 'sqrt':
                if _uncertain(args[0]):
                    warn('power_units_unverified', 'sqrt argument')
                return _power(args[0], 0.5)
            if name == 'inverse':
                return _power(args[0], -1)
            if name == 'signed_power':
                exponent = literal_number(node.args[1])
                if _uncertain(args[0]) and exponent != 1:
                    warn('power_units_unverified', 'signed_power argument')
                return _power(args[0], exponent)
            if name in ('abs', 'ts_mean', 'ts_sum', 'ts_std_dev', 'ts_delta',
                        'ts_delay', 'ts_decay_linear', 'group_neutralize'):
                return args[0]
            raise UnitError('算子缺少语义审查: '+name)
        raise UnitError('不合法表达式 AST 节点')

    output = check(root.body)
    return {'output_unit': output,
            'unit_status': 'insufficient_metadata' if warnings else 'verified',
            'warnings': warnings,
            'unit_review_note': 'missing unit evidence is recorded, not inferred from field names',
            'independent_performance_proof': False}
