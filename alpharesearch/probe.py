"""Bounded access probe for the current account, not an entitlement bypass.

All probe facts are observed server responses. OPTIONS is metadata, NOT
proof that POST /simulations is allowed. A write probe is explicitly opt-in.
"""
from __future__ import annotations

import json
import re
import os
from pathlib import Path
from urllib.parse import urlencode

from .brain import BrainSimulator
from .common import ResearchError, now
from .http import ApiError, AmbiguousWrite, PollTimeout


def _summary(name, fn, schema_ok=lambda _: True):
    try:
        response = fn()
        allowed = response.status in (200, 201, 202, 204)
        schema_valid = schema_ok(response.data)
        return {"endpoint": name, "status": response.status,
                "available": allowed and schema_valid,
                "schema_valid": schema_valid,
                "note": "Observed account response; not a grant of broader rights"}
    except ApiError as error:
        return {"endpoint": name, "status": error.status,
                "available": False, "error": str(error)}


def inspect_access(simulator: BrainSimulator, include_options=False):
    scope = simulator.config["scope"]
    selectors = {k: scope[k] for k in ("instrumentType", "region", "universe", "delay")}
    fields_path = simulator.path("fields") + "?" + urlencode({**selectors, "limit": 1, "offset": 0})
    # Do not leak account details or return raw datasets. OPTIONS may be 405
    # while POST is usable; probe result therefore leaves write access unknown.
    return {
        "observed_at": now(), "scope": selectors,
        "authentication": "confirmed_by_login_and_users_self",
        "operators": _summary("GET /operators", lambda: simulator.api.once("GET", simulator.path("operators")),
                              lambda data: isinstance(data, list)),
        "fields": _summary("GET /data-fields", lambda: simulator.api.once("GET", fields_path),
                           lambda data: isinstance(data, dict) and isinstance(data.get("results"), list)),
        "simulation_options": _summary("OPTIONS /simulations", lambda: simulator.api.once(
            "OPTIONS", simulator.path("simulations"))) if include_options else {'available':None,'note':'not_requested'},
        "single_simulation_write_access": "unverified",
        "multiple_simulation_access": "not_probed",
        "submission_access": "not_probed",
        "no_captcha_or_limit_bypass": True
    }


def _write_fresh(path, data):
    """Create an exclusive record before the irreversible POST."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as out:
        os.chmod(path,0o600)
        json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
        out.flush();os.fsync(out.fileno())
    return path


def _update(path, data):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp,0o600)
    # Windows requires a writable descriptor for fsync.
    with temp.open("r+b") as file:
        os.fsync(file.fileno())
    temp.replace(path)


def one_simulation_probe(simulator: BrainSimulator, expression, report_path):
    """Only callable after a human explicitly requests exactly one simulation.

    Never retries creation; an ambiguous response is recorded for reconciliation.
    This is NOT the agent's batch runner. It tests the current account once.
    """
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 2000:
        raise ResearchError("Provide a short, explicit Fast Expression")
    if not re.fullmatch(r"[A-Za-z0-9_(),.+*/\- ]+", expression):
        raise ResearchError("Probe expression contains unsupported characters")
    if expression.replace(' ','')!='rank(close)':
        raise ResearchError('V0.1.2 冒烟测试固定使用 rank(close)，无参数搜索')
    result = {
        "created_at": now(), "state": "dispatching", "mode": "single_simulation_permission_probe",
        "expression": expression, "scope": simulator.config["scope"],
        'account_ref':getattr(simulator,'account_ref',None),
        'api_base_url':simulator.config.get('brain',{}).get('base_url'),
        'observation_source':'account_http' if isinstance(simulator,BrainSimulator) and type(simulator.api.transport).__name__=='JsonTransport' else 'scripted_or_unknown_transport',
        "warning": "This probe creates one real remote simulation. Do not retry on uncertain response."
    }
    saved = _write_fresh(report_path, result)
    if isinstance(simulator,BrainSimulator): simulator.allow_one_probe()
    try:
        location = simulator.start(expression, simulator.config["scope"])
    except AmbiguousWrite as error:
        result.update(state="uncertain", error=str(error))
        _update(saved, result)
        raise
    except ApiError as error:
        result.update(state="rejected", http_status=error.status, error=str(error))
        _update(saved, result)
        raise
    result.update(state="polling", location=location)
    _update(saved, result)
    try:
        value = getattr(simulator,'finish_probe',simulator.finish)(location)
    except (ApiError, PollTimeout) as error:
        result.update(state="polling_incomplete", error=str(error))
        _update(saved, result)
        raise
    result.update({"state": "completed", "alpha_id": value["alpha_id"],
                   "simulator_mode": value.get("mode"), "metrics": value.get("metrics", {}),
                   "evidence_errors": value.get("evidence_errors", {})})
    _verify_probe(simulator,result,value)
    _update(saved, result)
    return result


def resume_simulation_probe(simulator: BrainSimulator, report_path):
    """GET-only recovery from a saved Location. Never repeats the POST."""
    saved = Path(report_path)
    payload = json.loads(saved.read_text(encoding="utf-8"))
    if payload.get("state") not in ("polling", "polling_incomplete",'metrics_incomplete'):
        raise ResearchError("Only polling probe records can be resumed")
    if payload.get("scope") != simulator.config["scope"]:
        raise ResearchError("Probe scope does not match current settings")
    if payload.get('account_ref') and payload['account_ref']!=getattr(simulator,'account_ref',None):
        raise ResearchError('冒烟记录与当前登录账户不一致')
    if not payload.get('location'): raise ResearchError('恢复记录缺少 Location')
    location = simulator.api.url(payload.get("location", ""))
    try:
        value = getattr(simulator,'finish_probe',simulator.finish)(location)
    except (ApiError, PollTimeout) as error:
        payload.update(state="polling_incomplete", error=str(error))
        _update(saved, payload)
        raise
    payload.update({"state": "completed", "alpha_id": value["alpha_id"],
                    "simulator_mode": value.get("mode"), "metrics": value.get("metrics", {}),
                    "evidence_errors": value.get("evidence_errors", {})})
    payload.pop("error", None)
    _verify_probe(simulator,payload,value)
    _update(saved, payload)
    return payload


def reconcile_simulation_probe(simulator,report_path,location):
    saved=Path(report_path);payload=json.loads(saved.read_text(encoding="utf-8"))
    if payload.get('state') not in ('dispatching','uncertain'):raise ResearchError('仅对账不确定的创建请求')
    if payload.get('scope')!=simulator.config['scope'] or (payload.get('account_ref') and payload['account_ref']!=simulator.account_ref):raise ResearchError('对账账户/范围不匹配')
    payload.update(state='polling',location=simulator.api.url(location),manual_reconciliation=True)
    _update(saved,payload)
    return {'state':'polling','note':'已绑定人工核对的 Location；下一步只恢复 GET，仍需核对远程公式/设置'}


def _settings_match(observed, requested):
    """BRAIN may append server-owned dates and metadata to requested settings."""
    return isinstance(observed, dict) and isinstance(requested, dict) and all(
        observed.get(key) == value for key, value in requested.items()
    )


def _verify_probe(simulator,record,value):
    if isinstance(simulator,BrainSimulator):
        if value.get('expression','').replace(' ','')!=record['expression'].replace(' ','') or not _settings_match(value.get('settings'),record['scope']):
            raise ResearchError('真实冒烟结果的公式/设置不一致')
        from .brain import finite
        parsed=all(finite(value.get('metrics',{}).get(k)) is not None for k in ('sharpe','fitness','turnover'))
        record['metrics_parse_verified']=parsed
        record['state']='completed' if parsed else 'metrics_incomplete'
    else: record['metrics_parse_verified']=False
    record['submission_enabled_by_probe']=False


def require_smoke_receipt(config,user_id):
    """Local operational guard only. A file is not an entitlement attestation.

    The server independently decides all access. Owners with filesystem control
    can forge these records; scope/account checks catch mistakes, not adversaries.
    """
    from .common import digest
    record=config.get('safety',{}).get('validated_probe_record')
    if not record:raise ResearchError('开启真实研究前须配置 safety.validated_probe_record，指向当前账户完成的单 Alpha 冒烟记录')
    path=Path(config['root'])/record
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('state')!='completed' or value.get('metrics_parse_verified') is not True or value.get('observation_source')!='account_http':
        raise ResearchError('冒烟记录未完成真实指标解析，或来自脚本化测试')
    if value.get('account_ref')!=digest(str(user_id)) or value.get('scope')!=config['scope'] or value.get('api_base_url')!=config['brain']['base_url']:
        raise ResearchError('冒烟记录的账户、范围或 API 地址不匹配')
    return {'scope_verified':True,'account_verified':True,'submission_enabled':False}


def inspect_alpha_evidence(simulator,smoke_path,output):
    """GET-only reconciliation of the Alpha already created by the smoke probe.

    Neither the transport marker nor this local report grants platform rights.
    No simulation, resubmission or bulk research capability is opened here.
    """
    from .brain import finite
    from .common import digest
    from .pnl_evidence import daily_evidence_valid,stability_windows
    if Path(output).exists():raise ResearchError('证据验收文件已存在，请保留原记录并指定新输出路径')
    record=json.loads(Path(smoke_path).read_text(encoding="utf-8"))
    config=simulator.config
    if not re.fullmatch(r'[0-9a-f]{64}',str(simulator.account_ref)):raise ResearchError('须先确认当前登录账户身份')
    if record.get('state')!='completed' or record.get('metrics_parse_verified') is not True:raise ResearchError('须先完成单 Alpha 冒烟指标解析')
    if record.get('account_ref')!=simulator.account_ref or record.get('scope')!=config['scope'] or record.get('api_base_url')!=config['brain']['base_url']:
        raise ResearchError('冒烟记录账户、设置或 API 地址不匹配')
    alpha_id=record.get('alpha_id')
    if not isinstance(alpha_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',alpha_id):raise ResearchError('缺少有效的已创建 Alpha ID')
    result=simulator.collect(alpha_id)
    expression=result.get('expression')
    if result.get('alpha_id')!=alpha_id or not isinstance(expression,str) or expression.replace(' ','')!=record['expression'].replace(' ','') or not _settings_match(result.get('settings'),record['scope']):
        raise ResearchError('真实 Alpha 公式或设置对账失败；未保存为通过')
    parsed=all(finite(result.get('metrics',{}).get(k)) is not None for k in ('sharpe','fitness','turnover'))
    source=result.get('evidence_source',{})
    observed=(record.get('observation_source')=='account_http' and isinstance(simulator,BrainSimulator) and type(simulator.api.transport).__name__=='JsonTransport' and
        source.get('transport')=='account_http' and source.get('account_ref')==simulator.account_ref and
        source.get('alpha_id')==alpha_id and source.get('api_base_url')==config['brain']['base_url'])
    value={'state':'observed' if parsed else 'metrics_incomplete','mode':'existing_alpha_evidence_probe','created_at':now(),
        'simulation_created':False,'account_ref':simulator.account_ref,'scope':config['scope'],'alpha_id':alpha_id,
        'expression_reconciled':True,'settings_reconciled':True,'metrics_parse_verified':parsed,
        'original_metrics':record.get('metrics',{}),'current_metrics':result['metrics'],
        'metric_changes':{k:result['metrics'].get(k)!=record.get('metrics',{}).get(k) for k in ('sharpe','fitness','turnover')},
        'observation_source':'account_http' if observed else 'scripted_or_unknown_transport',
        'source':result.get('evidence_source',{}),'pnl_isolation':result['pnl_isolation'],
        'daily_evidence':result['daily_evidence'],'daily_evidence_complete':daily_evidence_valid(result),
        'stability':stability_windows(result,config['policy']),'evidence_errors':result['evidence_errors'],
        'evidence_sha256':digest({'pnl':result['pnl_changes'],'returns':result['daily_returns'],'isolation':result['pnl_isolation'],'daily':result['daily_evidence']}),
        'real_account_acceptance_completed':bool(observed and parsed and daily_evidence_valid(result)),
        'submission_enabled_by_probe':False,'notice':'本机对账记录不是授权证明，也不证明因子经济有效或可提交'}
    _write_fresh(output,value)
    return value
