"""Append-only, privacy-limited evidence for autonomous LLM proposal batches.

No model request, filesystem/network operation, or database migration is made here.
Only a new Run's EXISTING hash-chained events table receives records.
Original vendor tool arguments are fingerprinted BEFORE redaction; event payloads
contain a redacted, capped structured representation, never auth headers.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from .common import ResearchError, digest, dumps

MAX_RAW_BYTES = 128_000
MAX_BATCH_IDEAS = 8
IDEA_LIMITS = {
    'title': 200, 'domain': 80, 'mechanism': 2000,
    'expected_sign': 32, 'horizon_days': 32,
    'falsification': 1000, 'expression': 2000,
    'source': 1000, 'family': 80, 'parent_idea_id': 100, 'change': 1000,
}
# Redaction is defense in depth, NOT proof that no personal or confidential
# information exists. Intentionally discard unexpected keys and nonstrings.
REDACTIONS = (
    (re.compile(r'(?i)\bBearer\s+[a-z0-9._~+/-]{8,}'), '[REDACTED_BEARER]'),
    (re.compile(r'(?i)(?:sk-|api[_-]?key\s*[:=]\s*|token\s*[:=]\s*)[a-z0-9_\-]{12,}'), '[REDACTED_CREDENTIAL]'),
    (re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'), '[REDACTED_EMAIL]'),
    (re.compile(r'(?i)https?://[^\s"\']+'), '[REDACTED_URL]'),
    (re.compile(r'(?i)\b(?:password|secret|authorization)\s*[:=]\s*[^\s,;]{5,}'), '[REDACTED_SECRET]'),
)
SAFE_VENDOR_ID = re.compile(r'^[A-Za-z0-9._:/@+\-]{1,160}$')


def _model_policy(memory, run_id):
    """Read alias approvals from the ORIGINAL frozen Run snapshot, not callers.

    Never derive authorization from a response's claimed model or mutable
    runtime config. An alias needs an explicit human review reference.
    """
    record = memory.run(run_id)
    frozen = json.loads(record['snapshot'])
    llm = frozen.get('llm')
    if not isinstance(llm, dict):
        raise ResearchError('冻结 Run 缺少模型身份，禁止确认 LLM 来源')
    known = set()
    for key in ('model', 'fast_model', 'reasoning_model'):
        value = llm.get(key)
        if value is not None:
            if not _safe_id(value):
                raise ResearchError('冻结模型标识无效: '+key)
            known.add(value)
    if not known:
        raise ResearchError('冻结计划没有批准的 LLM 模型身份')
    aliases = llm.get('response_model_aliases', {})
    if not isinstance(aliases, dict) or len(aliases) > 12:
        raise ResearchError('LLM 别名必须为显式审核映射，不能使用通配符')
    for requested, entries in aliases.items():
        if requested not in known or not isinstance(entries, list) or len(entries) > 12:
            raise ResearchError('模型别名请求键未冻结或映射长度超限')
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                    'returned_model', 'reviewed_by', 'approval_ref', 'reason'}:
                raise ResearchError('模型别名需同时包含返回身份及人工审核元数据')
            returned = entry['returned_model']
            if (not _safe_id(returned) or returned == requested or returned in seen
                    or not _safe_id(entry['reviewed_by'])
                    or not _safe_id(entry['approval_ref'])
                    or not isinstance(entry['reason'], str)
                    or not 8 <= len(entry['reason'].strip()) <= 500):
                raise ResearchError('模型别名审核条目不合法或重复')
            seen.add(returned)
    return known, aliases, digest({'approved_request_models': sorted(known),
                                   'reviewed_response_aliases': aliases})

def _model_identity(memory, run_id, requested, returned):
    """Exact or explicitly reviewed alias; missing/unknown identities fail closed."""
    approved, aliases, policy_hash = _model_policy(memory, run_id)
    result = {'model_identity_policy_sha256': policy_hash,
              'model_identity_status': 'rejected', 'alias_approval_ref': None}
    if not _safe_id(requested) or requested not in approved:
        result['model_identity_status'] = 'unapproved_request_model'
    elif not _safe_id(returned):
        result['model_identity_status'] = 'missing_or_invalid_returned_model'
    elif requested == returned:
        result['model_identity_status'] = 'exact_match'
    else:
        record = next((item for item in aliases.get(requested, [])
                       if item['returned_model'] == returned), None)
        if record is None:
            result['model_identity_status'] = 'unknown_model_substitution'
        else:
            result['model_identity_status'] = 'reviewed_alias'
            result['alias_approval_ref'] = record['approval_ref']
    return result


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _safe_text(value, maximum):
    if value is None:
        return None
    if isinstance(value, (dict, list, bool)):
        return '[INVALID_TYPE]'
    if isinstance(value, (int, float)):
        return value  # Preserve structured numeric types such as horizon_days.
    if not isinstance(value, str):
        return '[INVALID_TYPE]'
    result = value
    for regex, replacement in REDACTIONS:
        result = regex.sub(replacement, result)
    return result[:maximum] + ('[TRUNCATED]' if len(result) > maximum else '')


def _safe_id(value):
    return value if isinstance(value, str) and SAFE_VENDOR_ID.fullmatch(value) else None


def _safe_idea(item):
    if not isinstance(item, dict):
        return {'invalid_proposal_type': type(item).__name__[:40]}
    result = {k: _safe_text(item[k], maximum) for k, maximum in IDEA_LIMITS.items() if k in item}
    additional = len(set(item) - IDEA_LIMITS.keys())
    if additional:
        result['discarded_unapproved_keys'] = additional
    return result


def _audit(memory, run_id, kind, content):
    memory.transaction(lambda: memory.event(run_id, kind, content))


def begin(memory, run_id, *, cycle, model, prompt_hash, tool_schema_hash):
    if type(cycle) is not int or cycle < 1:
        raise ResearchError('LLM 批次周期无效')
    approved, aliases, policy_hash = _model_policy(memory, run_id)
    if model not in approved:
        raise ResearchError('LLM 请求模型未列入冻结身份白名单')
    batch_id = uuid.uuid4().hex
    _audit(memory, run_id, 'llm_batch_started', {
        'batch_id': batch_id, 'cycle': cycle, 'requested_model': _safe_id(model),
        'prompt_sha256': prompt_hash, 'tool_schema_sha256': tool_schema_hash,
        'model_identity_policy_sha256': policy_hash,
        'source_kind': 'authenticated_llm_tool_call', 'automatic_submit': False,
    })
    return batch_id


def failure(memory, run_id, *, batch_id, error, phase):
    _audit(memory, run_id, 'llm_batch_failed', {
        'batch_id': batch_id, 'phase': phase,
        'error_type': type(error).__name__[:80], 'fallback_used': False,
        'candidate_count': 0, 'vendor_usage_unknown_if_transport_failed': True,
    })


def captured(memory, run_id, *, batch_id, requested_model, response, raw_arguments,
             tool_call_id, parse_status='structured'):
    """Record the received tool-call BEFORE candidate legality screening.

    Returns a compact provenance receipt for source binding. Malformed input
    is still audited; callers must reject it and never fabricate proposals.
    """
    raw = raw_arguments if isinstance(raw_arguments, str) else ''
    raw_bytes = raw.encode('utf-8', 'replace')
    too_large = len(raw_bytes) > MAX_RAW_BYTES
    data = None
    if parse_status == 'structured':
        if not too_large:
            try:
                data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
            except (TypeError, ValueError, RecursionError):
                parse_status = 'invalid_json'
        else:
            parse_status = 'payload_too_large'
    if isinstance(data, dict) and set(data) != {'ideas'}:
        parse_status = 'invalid_schema'
    raw_list = data.get('ideas') if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        raw_list = []
        if parse_status == 'structured':
            parse_status = 'invalid_schema'
    if len(raw_list) > MAX_BATCH_IDEAS:
        parse_status = 'too_many_proposals'
    safe_list = [_safe_idea(v) for v in raw_list[:MAX_BATCH_IDEAS]]
    if parse_status == 'structured':
        if any(not isinstance(v, dict) or set(v) != set(IDEA_LIMITS) for v in raw_list):
            parse_status = 'invalid_schema'
        elif any(not ((key == 'parent_idea_id' and value is None) or
                      (key == 'horizon_days' and type(value) is int) or
                      (key not in ('parent_idea_id', 'horizon_days') and type(value) is str) or
                      (key == 'parent_idea_id' and type(value) is str))
                 for item in raw_list for key, value in item.items()):
            parse_status = 'invalid_schema'
        # A secret must NEVER be written into the legacy autonomy_idea event.
        # Keep its redacted receipt but stop the batch before the scheduler.
        elif any(_safe_text(value, IDEA_LIMITS[key]) != value
                 for item in raw_list for key, value in item.items()
                 if isinstance(value, str)):
            parse_status = 'redacted_sensitive_content'
    response_data = getattr(response, 'data', None)
    response_data = response_data if isinstance(response_data, dict) else {}
    identity = _model_identity(memory, run_id, requested_model,
                               response_data.get('model'))
    receipt = {
        'batch_id': batch_id, 'requested_model': _safe_id(requested_model),
        'returned_model': _safe_id(response_data.get('model')),
        'response_id': _safe_id(response_data.get('id')),
        'tool_call_id': _safe_id(tool_call_id),
        'raw_tool_arguments_sha256': _sha256_bytes(raw_bytes),
        'raw_bytes': len(raw_bytes), 'sanitized_structured_proposals': safe_list,
        'sanitized_proposals_sha256': digest(safe_list),
        'proposal_sha256': [digest(x) for x in raw_list[:MAX_BATCH_IDEAS]],
        'proposal_count': len(raw_list), 'parse_status': parse_status,
        'source_kind': 'authenticated_llm_tool_call',
        'redaction_policy': 'approved_keys_only_v1',
        'raw_unredacted_content_saved': False,
        **identity,
    }
    _audit(memory, run_id, 'llm_batch_proposals', receipt)
    return receipt


def verify_source(memory, run_id, batch_id, ideas):
    """Confirm full payload matches a uniquely audited LLM batch."""
    if not isinstance(ideas, list):
        raise ResearchError('LLM 提案批次不是数组')
    return verify_source_hashes(memory, run_id, batch_id, [digest(idea) for idea in ideas])


def verify_source_hashes(memory, run_id, batch_id, proposal_hashes):
    """Verify an existing immutable receipt using independently anchored hashes.

    At dispatch the original payload need not live in mutable queue columns;
    complete indexed proposal hashes are recovered from appended origin events.
    This is NOT proof of provider cryptographic identity.
    """
    if (not isinstance(proposal_hashes, list) or
            any(not isinstance(x, str) or not re.fullmatch(r'[0-9a-f]{64}', x)
                for x in proposal_hashes)):
        raise ResearchError('提案哈希列表不完整')
    if not isinstance(batch_id, str) or not batch_id:
        raise ResearchError('Live 假设没有可追溯 LLM 批次 ID')
    entries = memory.db.execute(
        "SELECT kind,payload FROM events WHERE run_id=? AND kind IN "
        "('llm_batch_started','llm_batch_proposals','llm_batch_failed') ORDER BY id",
        (run_id,)).fetchall()
    starts, receipts, failures = [], [], []
    for kind, content in entries:
        event = json.loads(content)
        if event.get('batch_id') != batch_id:
            continue
        if kind == 'llm_batch_started':
            starts.append(event)
        elif kind == 'llm_batch_proposals':
            receipts.append(event)
        else:
            failures.append(event)
    if len(starts) != 1 or len(receipts) != 1 or failures:
        raise ResearchError('模型提案缺少唯一的开始/响应审计关联，或研究请求曾失败')
    start, receipt = starts[0], receipts[0]
    identity = _model_identity(memory, run_id, start.get('requested_model'),
                               receipt.get('returned_model'))
    if identity['model_identity_status'] not in ('exact_match', 'reviewed_alias'):
        raise ResearchError('模型返回身份与请求不匹配且不存在经过审核的精确别名')
    if (start.get('model_identity_policy_sha256') != identity['model_identity_policy_sha256']
            or any(receipt.get(key) != value for key, value in identity.items())):
        raise ResearchError('模型身份审核策略哈希或返回身份验证收据不匹配')
    if (receipt.get('parse_status') != 'structured' or
            start.get('source_kind') != 'authenticated_llm_tool_call' or
            receipt.get('source_kind') != 'authenticated_llm_tool_call' or
            not start.get('requested_model') or
            receipt.get('requested_model') != start.get('requested_model') or
            receipt.get('proposal_count') != len(proposal_hashes) or
            receipt.get('proposal_sha256') != proposal_hashes):
        raise ResearchError('Live 假设与原始模型提案及不可变审计记录不匹配')
    return receipt
