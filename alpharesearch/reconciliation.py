"""GET-only recovery. A matching Alpha is evidence, never permission to POST again."""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

from .common import digest, now, snapshot
from .http import ApiError

# Versioned agreement. Unknown requested parameters require an adapter update;
# returned server metadata is reported, not treated as an IS boundary.
SETTINGS_PROTOCOL = 'brain-key-settings-v1'
KEY_SETTINGS = frozenset(('instrumentType', 'region', 'universe', 'delay', 'decay',
    'neutralization', 'truncation', 'pasteurization', 'unitHandling', 'nanHandling',
    'language', 'visualization', 'testPeriod', 'startDate', 'endDate'))


def settings_comparison(observed, requested):
    if not isinstance(observed, dict) or not isinstance(requested, dict) or not requested:
        return {'matches': False, 'conflicts': ['invalid_settings'], 'protocol': SETTINGS_PROTOCOL}
    conflicts = []
    for key, value in requested.items():
        if key not in KEY_SETTINGS:
            conflicts.append(key + ':unsupported_requested_parameter')
        elif key not in observed:
            conflicts.append(key + ':missing')
        elif (isinstance(value, bool) != isinstance(observed[key], bool)
              or observed[key] != value):
            conflicts.append(key + ':different')
    return {'matches': not conflicts, 'conflicts': conflicts,
            'compared_fields': sorted(requested), 'server_extra_fields': sorted(set(observed)-set(requested)),
            'protocol': SETTINGS_PROTOCOL}


def settings_match(observed, requested):
    return settings_comparison(observed, requested)['matches']


def expression_matches(observed, requested):
    if not isinstance(observed, str) or not isinstance(requested, str):
        return False
    try:
        return ast.dump(ast.parse(observed.strip(), mode='eval')) == ast.dump(ast.parse(requested.strip(), mode='eval'))
    except (SyntaxError, ValueError, RecursionError):
        return False


def timestamp(value):
    try:
        point = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if point.tzinfo is None:
            raise ValueError('timezone missing')
        return point.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError) as error:
        raise ApiError('恢复对账缺少带时区的创建时间；不能猜测 Alpha 来源') from error


def assert_identity(simulator, record):
    if (not re.fullmatch(r'[0-9a-f]{64}', str(record.get('account_ref', '')))
        or record['account_ref'] != simulator.account_ref
        or record.get('api_base_url') != simulator.paths['base_url']):
        raise ApiError('恢复记录缺少当前账户/API 身份绑定；禁止自动认领 Alpha')
    if not settings_match(simulator.config['scope'], record.get('scope')):
        raise ApiError('恢复记录的关键设置与当前配置冲突')


def recover_alpha(simulator, record, alpha_id=None):
    """Scan the authenticated account, then verify details; zero/multiple => stop.

    A time-qualified unique expression/settings match is operational recovery,
    not a server-issued idempotency guarantee. Explicit IDs resolve ambiguity
    only after account membership and full detail reconciliation.
    """
    assert_identity(simulator, record)
    dispatched = timestamp(record.get('dispatched_at') or record.get('created_at'))
    if alpha_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]+', alpha_id):
        raise ApiError('无效 Alpha ID')
    maximum = simulator.paths.get('recovery_max_alphas', 1000)
    page_size = simulator.paths.get('recovery_page_size', 100)
    if type(maximum) is not int or not 1 <= maximum <= 10000 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise ApiError('恢复扫描预算配置无效')
    candidates, seen, offset, total = [], set(), 0, None
    def recovered(value,scanned,complete):
        value['recovery'] = {'method': 'account_alpha_list_and_detail_get_v1', 'observed_at': now(),
            'scanned_alphas': scanned, 'list_scan_complete':complete,'account_ref': simulator.account_ref,
            'explicit_alpha_id': bool(alpha_id), 'simulation_created': False,
            'settings': settings_comparison(value['settings'], record['scope']),
            'request_identity_sha256': digest({k: record.get(k) for k in ('expression', 'scope', 'account_ref', 'api_base_url', 'created_at', 'dispatched_at')})}
        return value
    while True:
        path = simulator.paths.get('alpha_list', '/users/self/alphas') + '?' + urlencode(
            {'limit': min(page_size, maximum-offset), 'offset': offset, 'order': '-dateCreated'})
        payload = simulator.api.wait(path)
        if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
            raise ApiError('账户 Alpha 列表 schema 未核实；没有重复创建')
        count, rows = payload.get('count'), payload['results']
        if type(count) is not int or count < 0 or (total is not None and count != total):
            raise ApiError('账户 Alpha 分页总数未知或变动；请稍后只读重试')
        total = count
        if len(rows) > min(page_size, maximum-offset) or offset+len(rows) > count:
            raise ApiError('账户 Alpha 分页不一致')
        for row in rows:
            aid = row.get('id') if isinstance(row, dict) else None
            if not isinstance(aid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', aid) or aid in seen:
                raise ApiError('Alpha 列表 ID 无效/重复，无法证明唯一性')
            seen.add(aid)
            if alpha_id and aid != alpha_id:
                continue
            # An explicit mismatching formula can be skipped before detail GET.
            code = row.get('regular', {}).get('code') if isinstance(row.get('regular'), dict) else None
            if code is not None and not expression_matches(code, record['expression']):
                continue
            value = simulator.collect(aid, minimal=True)
            if not expression_matches(value.get('expression'), record['expression']) or not settings_match(value.get('settings'), record['scope']):
                continue
            if value.get('alpha_type') != 'REGULAR':
                raise ApiError('匹配的 Alpha 缺少 REGULAR 类型证据')
            if not alpha_id:
                # No clock-skew guess: ambiguous timestamps need an explicit ID.
                created = timestamp(value.get('date_created'))
                if created < dispatched:
                    continue
                if created > timestamp(now()):
                    raise ApiError('Alpha 创建时间在未来；需要人工核对时钟')
            candidates.append(value)
            if alpha_id:
                # A user-selected exact ID needs membership, not a global
                # uniqueness proof for the expression. Avoid a needless scan.
                return recovered(value,offset+len(rows),offset+len(rows)==count)
        offset += len(rows)
        if offset == count:
            break
        if not rows:
            raise ApiError('Alpha 分页提前结束；没有重复创建')
        if offset >= maximum:
            raise ApiError('Alpha 扫描预算已达上限；未证明唯一性，禁止自动 POST')
    if len(candidates) != 1:
        raise ApiError('未定位唯一 Alpha（匹配数量 %d）；保持不确定状态，禁止自动 POST' % len(candidates))
    return recovered(candidates[0],offset,True)


def recover_saved_trial(config,run_id,trial_id,simulator,alpha_id=None):
    """Upgrade-safe GET recovery without changing the original run snapshot.

    Old dispatch identity is reconstructed only from immutable reservation and
    account-catalog records. Never infer dispatch time from proposal creation.
    """
    import json
    from .memory import Memory
    memory=Memory(config['database'])
    identity_confirmed=False
    try:
        trial=memory.trial(trial_id);run=memory.run(run_id)
        if trial['run_id']!=run_id or (trial['state'] not in ('dispatching','uncertain','polling','paused')
                and trial.get('remote_state')!='unresolved'):
            raise ApiError('只能只读恢复指定 run 的未完成任务')
        original=json.loads(run['snapshot'])
        catalog=original.get('catalog',{})
        if (original.get('mode')!='live' or catalog.get('source')!='brain-account-api' or not catalog.get('account_id')
            or digest(str(catalog['account_id']))!=simulator.account_ref
            or original.get('brain',{}).get('base_url')!=simulator.paths['base_url']
            or not settings_match(config['scope'],original.get('scope'))):
            raise ApiError('旧任务快照与当前账户/API/关键设置不一致')
        events=memory.db.execute("SELECT created,payload FROM events WHERE run_id=? AND kind='simulation_reserved' ORDER BY id",(run_id,)).fetchall()
        reserved=next((e[0] for e in events if json.loads(e[1]).get('trial_id')==trial_id),None)
        if not reserved:raise ApiError('缺少原始预算预留记录；禁止猜测创建来源')
        record={'expression':trial['expression'],'scope':original['scope'],
            'account_ref':trial.get('account_ref') or simulator.account_ref,
            'api_base_url':trial.get('api_base_url') or simulator.paths['base_url'],
            'dispatched_at':trial.get('dispatched_at') or reserved}
        assert_identity(simulator,record)
        identity_confirmed=True
        def bind_dispatch():
            memory.db.execute('UPDATE tasks SET account_ref=?,api_base_url=?,dispatched_at=? WHERE trial_id=?',
                (record['account_ref'],record['api_base_url'],record['dispatched_at'],trial_id))
            memory.event(run_id,'readonly_recovery_identity',{'trial_id':trial_id,'original_snapshot_hash':run['snapshot_hash'],
                'current_code_snapshot_hash':digest(snapshot(config)),
                'identity_source':'original_snapshot_and_simulation_reservation'})
        memory.transaction(bind_dispatch)
        known=trial.get('alpha_id') or (memory.latest(trial_id) or {}).get('alpha_id')
        if known and not alpha_id:
            value=simulator.collect(known,minimal=True)
        elif trial.get('location') and not alpha_id:
            try:
                value=simulator.finish_probe(trial['location'],on_alpha=lambda aid:memory.bind_alpha(trial_id,aid))
            except ApiError as error:
                if error.status in (401,403):raise
                bound=memory.trial(trial_id).get('alpha_id')
                value=simulator.collect(bound,minimal=True) if bound else recover_alpha(simulator,record)
        else:
            value=recover_alpha(simulator,record,alpha_id)
        if not expression_matches(value.get('expression'),record['expression']) or not settings_match(value.get('settings'),record['scope']):
            raise ApiError('恢复 Alpha 公式/关键设置冲突；没有改写旧结果')
        memory.bind_alpha(trial_id,value['alpha_id'],value.get('recovery'))
        memory.confirm_remote_completed(trial_id,value)
        result=simulator.collect(value['alpha_id'])
        if not expression_matches(result.get('expression'),record['expression']) or not settings_match(result.get('settings'),record['scope']):
            raise ApiError('证据刷新时 Alpha 身份发生冲突')
        result['recovery']={'original_snapshot_hash':run['snapshot_hash'],'method':'upgrade_safe_get_only',
                            'matched_alpha':value.get('recovery'),'simulation_created':False}
        memory.result(trial_id,result)
        return {'run_id':run_id,'trial_id':trial_id,'alpha_id':result['alpha_id'],'state':'completed',
            'metrics':result['metrics'],'check_state':result.get('check_state'),
            'daily_evidence':result['daily_evidence'],'evidence_errors':result['evidence_errors'],
            'simulation_created':False,'submission_ready':False,'original_snapshot_preserved':True,
            'notice':'新代码只追加恢复证据；旧版本评审包不可据此直接提交，需按当前规则重新评审。'}
    except ApiError as error:
        if identity_confirmed:
            current=memory.trial(trial_id)
            memory.set_state(trial_id,'paused' if current.get('alpha_id') else 'uncertain',error=str(error))
        raise
    finally:memory.close()
