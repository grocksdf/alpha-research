"""Private GET-only audits; unidentified sample data never becomes research input."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .brain import exact_date, finite, records
from .checks import summarize_checks
from .common import ResearchError, digest, now
from .http import ApiError
from .pnl_evidence import daily_evidence_valid, stability_windows
from .reconciliation import expression_matches, settings_comparison


def observe_pnl(payload, context=None, isolation=None):
    """Describe dates and declared schema, with no inference from curve shape.

    A field called 'pnl' does not establish currency or cumulative semantics.
    Raw PnL values and unknown-sample returns are never retained in the report.
    """
    rows=records(payload)
    dates=[]
    invalid_dates=invalid_values=0
    for row in rows:
        try:dates.append(exact_date(row.get('date')))
        except ApiError:invalid_dates+=1
        if finite(row.get('pnl')) is None:invalid_values+=1
    unique=sorted(set(dates))
    schema=payload.get('schema',{})
    props=schema.get('properties',[]) if isinstance(schema,dict) else []
    columns=[{k:p[k] for k in ('name','type','unit') if k in p and isinstance(p[k],str)}
             for p in props if isinstance(p,dict)] if isinstance(props,list) else []
    value={'status':'observed_only','payload_sha256':digest(payload),'records':len(rows),
        'declared_columns':columns,'first_record_date':unique[0] if unique else None,
        'last_record_date':unique[-1] if unique else None,'duplicate_dates':len(dates)-len(unique),
        'invalid_date_rows':invalid_dates,'invalid_value_rows':invalid_values,
        'value_semantics':'unverified','pnl_unit':'unverified','calendar':'unverified',
        'missing_dates':None,'off_calendar_dates':None,'sample_isolation_verified':False,
        'raw_values_retained':False,'performance_metrics_computed':False}
    if isolation and isolation.get('status')=='isolated':
        start,end=isolation['start_date'],isolation['end_date']
        value['sample_isolation_verified']=True
        value['is_record_count']=sum(start<=d<=end for d in dates)
        value['out_of_is_record_count']=sum(not start<=d<=end for d in dates)
    if context:
        inside={d for d in unique if context['start_date']<=d<=context['end_date']}
        value.update(value_semantics=context['value_semantics'],pnl_unit=context['pnl_unit'],
            calendar=context['calendar_id'],calendar_sha256=context['calendar_sha256'],
            missing_dates=sorted(set(context['session_dates'])-inside),
            off_calendar_dates=sorted(inside-set(context['session_dates'])))
    return value


def audit_existing_alphas(simulator,manifest_path,output,returns_output=None):
    """Manifest binds IDs to expected formulas/settings; it never creates jobs."""
    from .probe import _write_fresh,_update
    manifest=json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if not isinstance(manifest,list) or not 1<=len(manifest)<=20:
        raise ResearchError('证据清单需 1..20 个已有 Alpha')
    ids=set()
    for row in manifest:
        if (not isinstance(row,dict) or not re.fullmatch(r'[A-Za-z0-9_-]+',str(row.get('alpha_id','')))
            or not isinstance(row.get('expression'),str) or not row['expression'].strip()
            or not isinstance(row.get('settings'),dict) or not row['settings']):
            raise ResearchError('每个已有 Alpha 需 alpha_id、原始 expression 与 settings')
        if row['alpha_id'] in ids:raise ResearchError('证据清单 Alpha ID 重复')
        ids.add(row['alpha_id'])
    if not re.fullmatch(r'[0-9a-f]{64}',str(simulator.account_ref)):
        raise ResearchError('先验证当前账户；报告不是授权证明')
    if returns_output:
        if Path(returns_output).resolve()==Path(output).resolve() or Path(returns_output).resolve()==Path(manifest_path).resolve():
            raise ResearchError('收益导出不能覆盖输入清单或审计报告')
        if Path(returns_output).exists():raise ResearchError('收益导出已存在；保留历史并指定新路径')
    returns_manifest={'format':'verified-returns-manifest-v1','series':[],
                      'network_requests':'GET_evidence_only_after_authentication','simulation_posts':0,'submit_posts':0}
    report={'version':'0.5.0','mode':'existing_alphas_readonly_audit','state':'auditing','created_at':now(),
        'account_ref':simulator.account_ref,'api_base_url':simulator.paths['base_url'],
        'manifest_sha256':digest(manifest),'simulation_created':False,'submission_requested':False,
        'alphas':[],'real_evidence_acceptance_completed':False}
    target=_write_fresh(output,report)
    for row in manifest:
        item={'alpha_id':row['alpha_id'],'status':'unverified','ready_to_submit':False}
        try:
            value=simulator.collect(row['alpha_id'],audit=True)
            comparison=settings_comparison(value.get('settings'),row['settings'])
            formula=expression_matches(value.get('expression'),row['expression'])
            reconciled=formula and comparison['matches']
            daily=bool(reconciled and daily_evidence_valid(value))
            item.update(status='observed' if reconciled else 'identity_conflict',
                expected_expression=row['expression'],observed_expression=value.get('expression'),
                observed_settings=value.get('settings'),expression_reconciled=formula,settings_reconciliation=comparison,
                metrics=value.get('metrics'),checks=value.get('checks'),
                check_state=summarize_checks(value.get('checks'),simulator.config['policy']['required_checks']),
                evidence_source=value.get('evidence_source'),pnl_observation=value.get('pnl_observation'),
                pnl_isolation=value.get('pnl_isolation'),daily_evidence=value.get('daily_evidence'),
                daily_evidence_complete=daily,evidence_errors=value.get('evidence_errors'),
                return_verification=value.get('return_verification',{'status':'insufficient_evidence','reasons':['missing_reviewed_return_contract']}),
                stability=stability_windows(value if reconciled and value.get('return_verification',{}).get('status')=='verified' else {},simulator.config['policy'],config=simulator.config),
                # This audit has no sensitivity or independent validation gate.
                ready_to_submit=False)
            document=value.get('verified_return_document') if reconciled else None
            returns_manifest['series'].append(document or {'candidate_id':row['alpha_id'],
                'verification':item['return_verification'],'contract':None})
        except (ApiError,ResearchError) as error:
            item['error']=str(error)
            returns_manifest['series'].append({'candidate_id':row['alpha_id'],'contract':None,
                'verification':{'status':'insufficient_evidence','reasons':[str(error)]}})
        report['alphas'].append(item)
        _update(target,report)
    report['state']='completed'
    report['real_evidence_acceptance_completed']=bool(report['alphas']) and all(
        a.get('daily_evidence_complete') and a.get('return_verification',{}).get('status')=='verified'
        and a.get('evidence_source',{}).get('transport')=='account_http'
        for a in report['alphas'])
    report['notice']='GET 证据审计不等于可提交；未核实的单位、日历和 IS 边界不计算稳定性。报告不要反馈给 Agent。'
    _update(target,report)
    if returns_output:_write_fresh(returns_output,returns_manifest)
    return report
