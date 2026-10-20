"""Reviewed cumulative-PnL semantics, session coverage and fixed-window diagnostics.

No holiday calendar, PnL unit, reset convention or capital basis is inferred.
Local provenance is an operational record, not a signed platform attestation.
"""
from __future__ import annotations

import csv
import hashlib
import math
import re
import statistics
from datetime import date
from pathlib import Path

from .common import digest
from .http import ApiError

PROTOCOL='brain-session-pnl-v1'
SEMANTICS='cumulative_cash_pnl_constant_capital_session_close'
MAX_SESSION_CALENDAR_GAP_DAYS=14  # Conservative protocol guard, not a BRAIN rule.


def validate_sessions(dates):
    from .brain import exact_date
    if not isinstance(dates,list) or not 2<=len(dates)<=30000:raise ApiError('交易日序列长度无效')
    dates=[exact_date(d) for d in dates]
    if dates!=sorted(set(dates)):raise ApiError('交易日序列必须有序且无重复')
    if any((date.fromisoformat(b)-date.fromisoformat(a)).days>MAX_SESSION_CALENDAR_GAP_DAYS for a,b in zip(dates,dates[1:])):
        raise ApiError('日历相邻会话间隔过长，超出本日频协议支持范围；不能把多年跨度视为单日')
    return dates


def validate_session_boundaries(sessions,start,end):
    """Reject obvious IS edge omissions; this does not certify a calendar."""
    start_gap=(date.fromisoformat(sessions[0])-date.fromisoformat(start)).days
    end_gap=(date.fromisoformat(end)-date.fromisoformat(sessions[-1])).days
    if start_gap<0 or end_gap<0 or max(start_gap,end_gap)>MAX_SESSION_CALENDAR_GAP_DAYS:
        raise ApiError('交易日历未充分覆盖 IS 首尾区间')


def reviewed_context(config,detail,bounds):
    from .brain import exact_date,finite
    contract=config['brain'].get('pnl_evidence',{})
    if contract.get('reviewed') is not True or not str(contract.get('review_note','')).strip():
        raise ApiError('PnL 累计值、重置规则和交易日语义尚未由当前账户核实')
    if contract.get('value_semantics')!=SEMANTICS:raise ApiError('仅支持已核实的固定本金、无重置、交易日收盘累计现金 PnL')
    unit=contract.get('pnl_unit')
    if not isinstance(unit,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,15}',unit) or contract.get('capital_unit')!=unit:
        raise ApiError('PnL 与固定本金的单位尚未核实一致；不得猜测美元、百分比或基点')
    for name in ('calendar_id','calendar_source','calendar_review_note'):
        if not isinstance(contract.get(name),str) or not contract[name].strip():raise ApiError('缺少 '+name)
    if contract.get('calendar_reviewed') is not True or contract.get('calendar_region')!=config['scope']['region']:
        raise ApiError('交易日历尚未核实或与研究区域不匹配')
    start,end=exact_date(bounds['start_date']),exact_date(bounds['end_date'])
    lower,upper=exact_date(contract.get('calendar_start')),exact_date(contract.get('calendar_end'))
    if not lower<=start<end<=upper:raise ApiError('交易日历声明覆盖范围不足，不能推测缺失日期是假日')
    path=Path(contract.get('calendar_file',''))
    if not path.is_absolute():path=Path(config['root'])/path
    try:
        if path.stat().st_size>5_000_000:raise ApiError('交易日历文件过大')
        raw=path.read_bytes()
    except OSError as error:raise ApiError('无法读取已核实的交易日历') from error
    fingerprint=hashlib.sha256(raw).hexdigest()
    if fingerprint!=contract.get('calendar_sha256'):raise ApiError('交易日历 SHA-256 不匹配；不能悄悄改变观测完整性基准')
    try:
        import io
        reader=csv.DictReader(io.StringIO(raw.decode('utf-8')))
        if reader.fieldnames!=['date']:raise ApiError('交易日历 CSV 必须仅有 date 列')
        dates=[exact_date(row.get('date')) for row in reader]
    except (UnicodeError,csv.Error) as error:raise ApiError('交易日历 CSV 无效') from error
    if not dates or len(dates)>30000 or dates!=sorted(set(dates)) or dates[0]<lower or dates[-1]>upper:
        raise ApiError('交易日历必须有序、无重复并位于声明覆盖区间内')
    validate_sessions(dates)
    # The reviewer attests that absent dates in this coverage interval are market
    # closures. A weekday heuristic cannot establish this from the CSV alone.
    sessions=[d for d in dates if start<=d<=end]
    if len(sessions)<2:raise ApiError('研究区间内交易日历观测不足')
    validate_session_boundaries(sessions,start,end)
    path=contract.get('capital_path')
    if not isinstance(path,list) or not 1<=len(path)<=8 or not all(isinstance(p,str) and p for p in path):raise ApiError('缺少已核实的固定本金字段路径')
    if any(p.lower() in ('os','test','prod') for p in path):raise ApiError('不得用样本外字段提供本金证据')
    capital=detail
    for part in path:
        if not isinstance(capital,dict) or part not in capital:raise ApiError('服务器缺少固定本金字段')
        capital=capital[part]
    capital=finite(capital)
    if capital is None or capital<=0:raise ApiError('固定本金必须为正有限值')
    return {'protocol':PROTOCOL,'value_semantics':SEMANTICS,'contract_sha256':digest(contract),
        'pnl_unit':unit,'capital_unit':unit,
        'calendar_id':contract['calendar_id'],'calendar_sha256':fingerprint,'calendar_region':contract['calendar_region'],
        'calendar_source':contract['calendar_source'],'calendar_start':lower,'calendar_end':upper,
        'start_date':start,'end_date':end,'session_dates':sessions,'constant_capital':capital,'capital_path':path}


def daily_differences(payload,sessions,start=None,end=None):
    from .brain import exact_date,finite,records
    sessions=validate_sessions(sessions)
    start,end=start or sessions[0],end or sessions[-1]
    points={}
    for row in records(payload):
        day=exact_date(row.get('date'))
        if not start<=day<=end:continue
        value=finite(row.get('pnl'))
        if value is None:raise ApiError('PnL 缺少有限数值')
        if day in points:raise ApiError('PnL 日期重复')
        points[day]=value
    ordered=sorted(points);positions={d:i for i,d in enumerate(sessions)}
    off_calendar=[d for d in ordered if d not in positions]
    changes={};gaps=[]
    for left,right in zip(ordered,ordered[1:]):
        if left not in positions or right not in positions:continue
        gap=positions[right]-positions[left]-1
        if gap:
            # Keep no aggregate PnL value for the missing-session span.
            gaps.append({'start_date':left,'end_date':right,'missing_sessions':gap})
            continue
        value=points[right]-points[left]
        if finite(value) is None:raise ApiError('PnL 差分溢出')
        changes[left+'/'+right]=value
    return {'changes':changes,'observed_dates':ordered,'missing_dates':[d for d in sessions if d not in points],
            'gap_intervals':gaps,'off_calendar_dates':off_calendar}


def build_daily_evidence(payload,context):
    from .brain import finite
    result=daily_differences(payload,context['session_dates'],context['start_date'],context['end_date'])
    changes=result.pop('changes');capital=context['constant_capital']
    returns={k:v/capital for k,v in changes.items()}
    if any(finite(v) is None or v<=-1 for v in returns.values()):raise ApiError('固定本金归一 PnL 收益无效；须核对单位/重置/本金语义')
    expected=len(context['session_dates'])-1
    complete=not result['missing_dates'] and not result['gap_intervals'] and not result['off_calendar_dates'] and len(changes)==expected
    proof={**context,**result,'status':'complete' if complete else 'incomplete','expected_intervals':expected,
           'accepted_intervals':len(changes),'interval_coverage':len(changes)/expected}
    return {'pnl_changes':changes,'daily_returns':returns,'daily_evidence':proof}


def daily_evidence_valid(result):
    """Recheck stored evidence instead of trusting a stored 'complete' label."""
    from .brain import exact_date,finite,pnl_isolation_valid
    proof=result.get('daily_evidence')
    if not pnl_isolation_valid(result) or not isinstance(proof,dict):return False
    if proof.get('protocol')!=PROTOCOL or proof.get('status')!='complete' or proof.get('value_semantics')!=SEMANTICS:return False
    unit=proof.get('pnl_unit')
    if not isinstance(unit,str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{1,15}',unit) or proof.get('capital_unit')!=unit:return False
    for name in ('calendar_sha256','contract_sha256'):
        if not re.fullmatch(r'[0-9a-f]{64}',str(proof.get(name,''))):return False
    isolation=result['pnl_isolation'];sessions=proof.get('session_dates')
    if not isinstance(sessions,list) or not 2<=len(sessions)<=30000:return False
    try:
        sessions=validate_sessions(sessions)
        if proof['start_date']!=isolation['start_date'] or proof['end_date']!=isolation['end_date']:return False
        if not exact_date(proof['calendar_start'])<=proof['start_date']<=sessions[0]<sessions[-1]<=proof['end_date']<=exact_date(proof['calendar_end']):return False
        validate_session_boundaries(sessions,proof['start_date'],proof['end_date'])
    except (ApiError,KeyError,TypeError):return False
    expected={a+'/'+b for a,b in zip(sessions,sessions[1:])}
    changes=result.get('pnl_changes',{});returns=result.get('daily_returns',{})
    if not isinstance(changes,dict) or not isinstance(returns,dict) or set(changes)!=expected or set(returns)!=expected:return False
    if proof.get('observed_dates')!=sessions or any(proof.get(k) for k in ('missing_dates','gap_intervals','off_calendar_dates')):return False
    if proof.get('expected_intervals')!=len(expected) or proof.get('accepted_intervals')!=len(expected) or proof.get('interval_coverage')!=1:return False
    capital=finite(proof.get('constant_capital'))
    if capital is None or capital<=0:return False
    for key in expected:
        if finite(changes[key]) is None or finite(returns[key]) is None or returns[key]<=-1:return False
        if not math.isclose(returns[key],changes[key]/capital,rel_tol=1e-12,abs_tol=1e-15):return False
    return True


def stability_windows(result,policy):
    """Anchor at the first IS session; do not shift windows to avoid bad dates."""
    length=policy.get('stability_window_sessions');minimum=policy.get('min_stability_windows')
    annual=policy.get('stability_annualization')
    if type(length)is not int or length<2 or type(minimum)is not int or minimum<2 or type(annual)is not int or not 1<=annual<=366:
        raise ValueError('固定稳定性窗口配置无效；旧年度策略必须显式迁移')
    result_base={'method':'fixed_nonoverlapping_session_windows_v1','window_sessions':length,'minimum_windows':minimum,
                 'annualization':annual,'windows':[],'complete':False,'positive_fraction':None,'worst_window_sharpe':None,
                 'reasons':[],'not_brain_reported_sharpe':True}
    if not daily_evidence_valid(result):
        # Coverage diagnostics keep the same anchored blocks even when evidence
        # is incomplete. They never supply performance scores or skip bad blocks.
        proof=result.get('daily_evidence',{})
        sessions=proof.get('session_dates',[]) if isinstance(proof,dict) else []
        changes=result.get('pnl_changes',{})
        if isinstance(sessions,list) and all(isinstance(d,str) for d in sessions) and isinstance(changes,dict):
            keys=[a+'/'+b for a,b in zip(sessions,sessions[1:])]
            for start in range(0,len(keys)-length+1,length):
                block=keys[start:start+length];count=sum(k in changes for k in block)
                result_base['windows'].append({'start_date':block[0].split('/')[0],'end_date':block[-1].split('/')[1],
                    'expected_observations':length,'observations':count,'coverage':count/length,'sharpe':None})
        result_base['reasons']=['unverified_or_incomplete_daily_pnl'];return result_base
    sessions=result['daily_evidence']['session_dates'];keys=[a+'/'+b for a,b in zip(sessions,sessions[1:])]
    windows=[]
    for start in range(0,len(keys)-length+1,length):
        block=keys[start:start+length];values=[result['daily_returns'][k] for k in block]
        sd=statistics.stdev(values)
        score=statistics.mean(values)/sd*math.sqrt(annual) if sd>1e-12 else None
        if score is not None and not math.isfinite(score):score=None
        windows.append({'start_date':block[0].split('/')[0],'end_date':block[-1].split('/')[1],
            'expected_observations':length,'observations':len(values),'coverage':1.0,'sharpe':score})
    result_base.update(windows=windows,trailing_observations=len(keys)%length,available_intervals=len(keys))
    if len(windows)<minimum:result_base['reasons'].append('insufficient_fixed_stability_windows')
    if any(w['sharpe'] is None for w in windows):result_base['reasons'].append('undefined_window_sharpe')
    if result_base['reasons']:return result_base
    scores=[w['sharpe'] for w in windows]
    result_base.update(complete=True,positive_fraction=sum(s>0 for s in scores)/len(scores),worst_window_sharpe=min(scores))
    return result_base
