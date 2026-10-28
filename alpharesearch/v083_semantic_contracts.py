"""One declarative source for v0.8 research-role semantic contracts.

The tool schema defines shape; these context-sensitive contracts describe AND
validate relationships which JSON Schema alone cannot express. They restrict
field usage, evidence references and index bookkeeping, never Alpha mechanisms,
formula families or candidate creation. No model calls or side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .common import ResearchError

MEMORY_EXPLANATION_MIN_CHARS = 24


@dataclass(frozen=True)
class SemanticRule:
    phase: str
    name: str
    guidance: str
    validate: Callable[[dict, dict], None]


def _explorer_fields(response, context):
    fields=context.get('approved_fields')
    if not isinstance(fields, dict):
        return  # The later host catalog gate remains authoritative.
    for item in response['hypotheses']:
        chosen=item['required_fields']
        if not chosen or not set(chosen).issubset(fields):
            raise ResearchError('Explorer 选择了未经授权的字段')
        if not item['sources']:
            raise ResearchError('Explorer 缺少研究依据来源')


def _strategist_memories(response, context):
    hypotheses=context.get('hypotheses')
    retrieved=context.get('playbook')
    known={m['id'] for m in retrieved if isinstance(m,dict) and isinstance(m.get('id'),str)} if isinstance(retrieved,list) else None
    for item in response['decisions']:
        index=item['hypothesis_index']
        if index<0 or (isinstance(hypotheses,list) and index>=len(hypotheses)):
            raise ResearchError('Strategist 引用了不存在的假设索引')
        ids=item['memory_ids_used']
        applications=[a['memory_id'] for a in item['memory_application']]
        if (len(ids)!=len(set(ids)) or len(applications)!=len(set(applications))
                or set(ids)!=set(applications)):
            raise ResearchError('已引用记忆与具体决策应用说明必须逐条对应')
        if known is not None and not set(ids).issubset(known):
            raise ResearchError('Strategist 引用了未检索的记忆 ID')
        # Non-empty memory_effect is enforced by the common schema validator,
        # whether memory_ids_used is empty or nonempty.


def _worker_choices(response, context):
    selected=context.get('selected_indices')
    if not isinstance(selected,list):
        return
    for item in response['proposals']:
        if item['hypothesis_index'] not in selected:
            raise ResearchError('Worker 公式引用未经 Strategist 批准的假设')
        if item['parent_revision_id'] is not None and not item['difference_from_parent'].strip():
            raise ResearchError('修订候选缺少相对原公式的变更说明')


def _critic_coverage(response, context):
    proposals=context.get('formula_proposals')
    if not isinstance(proposals,list):
        return
    indexes=[x['proposal_index'] for x in response['reviews']]
    if len(indexes)!=len(proposals) or len(set(indexes))!=len(indexes) or set(indexes)!=set(range(len(proposals))):
        raise ResearchError('Critic 必须逐条提供对应的经济审核结果')


class SemanticContractError(ResearchError):
    """Safe, source-independent rule identifier for audit and diagnostics."""

    def __init__(self, rule: SemanticRule, message: str):
        self.rule_id=rule.name
        self.phase=rule.phase
        super().__init__(message)


# Each rule is used for both generation guidance and the executable gate.
SEMANTIC_RULES=(
    SemanticRule('explorer','authorized_data_fields',
        '`hypotheses[].required_fields` must contain only exact approved data-field identifiers; never formulas or operators. Every hypothesis must cite a nonempty `sources` list.',
        _explorer_fields),
    SemanticRule('strategist','evidence_bound_decisions',
        'Every decision index must address a supplied hypothesis. Each `memory_ids_used` entry must have exactly one same-ID `memory_application` with a decision change and evidence explanation, each at least {MEMORY_EXPLANATION_MIN_CHARS} characters; both arrays are empty when nothing is cited. `memory_effect` must be nonblank even if no memory is used.',
        _strategist_memories),
    SemanticRule('worker','approved_selection',
        'Every `hypothesis_index` must be selected by the strategist. For a `parent_revision_id`, explain the concrete `difference_from_parent`. Use only authorized catalog syntax.',
        _worker_choices),
    SemanticRule('critic','complete_review_coverage',
        '`reviews` must contain exactly one review per supplied `formula_proposals` item, and `proposal_index` must cover every index 0..N-1 once, without duplicates or gaps.',
        _critic_coverage),
)


def _schema_outline(schema):
    """Summarize *the actual tool schema*, not another hand-copied schema."""
    if not isinstance(schema,dict) or schema.get('type')!='object':
        raise ResearchError('角色工具结构必须为对象')
    constraints=[]
    for field, spec in schema.get('properties',{}).items():
        if field not in schema.get('required',[]):
            continue
        if spec.get('type')=='array':
            inner=spec.get('items',{})
            count=spec.get('maxItems')
            if inner.get('type')=='object':
                required=inner.get('required',[])
                constraints.append(f'{field}: array up to {count} records; each record requires '+', '.join(required))
            else:
                constraints.append(f'{field}: array up to {count} elements')
        elif spec.get('type')=='string':
            constraints.append(f'{field}: nonblank text, up to {spec.get("maxLength", "schema") } characters')
        else:
            constraints.append(f'{field}: required by tool schema')
    return constraints


def describe_contract(phase,schema,context):
    """System-only guidance derived from the same schema/rules as validation."""
    lines=['Structured tool contract: include every required field with its specified JSON type, do not add keys; text values must be nonblank.']
    lines.extend(_schema_outline(schema))
    lines.extend(f'{rule.name}: {rule.guidance.format(MEMORY_EXPLANATION_MIN_CHARS=MEMORY_EXPLANATION_MIN_CHARS)}' for rule in SEMANTIC_RULES if rule.phase==phase)
    # No value copied from untrusted articles, memory text or field descriptions.
    if phase=='explorer' and isinstance(context.get('approved_fields'),dict):
        lines.append('Authorized field identifiers are exactly the keys of approved_fields in the user data; they are not example Alpha expressions.')
    return '\n'.join(lines)


def validate_contract(phase,response,context):
    for rule in SEMANTIC_RULES:
        if rule.phase==phase:
            try:
                rule.validate(response,context)
            except ResearchError as err:
                raise SemanticContractError(rule,str(err)) from err
    return True
