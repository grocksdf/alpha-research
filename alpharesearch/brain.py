from __future__ import annotations

import base64
import math
import os
import re
from datetime import date, timedelta
from urllib.parse import urlencode

from .common import ResearchError, digest, now
from .http import AmbiguousWrite, ApiClient, ApiError
from .validator import OPERATORS


def finite(value):
    try:
        if type(value) in (float, int) and math.isfinite(value):return float(value)
    except OverflowError:pass
    return None


def records(payload):
    """Accept named records or schema-described positional recordsets; no guessed columns."""
    if not isinstance(payload, dict):
        raise ApiError("recordset 必须为对象")
    rows = payload.get("records")
    if not isinstance(rows, list):
        raise ApiError("recordset 缺少 records 数组")
    if all(isinstance(r, dict) for r in rows):
        return rows
    props = payload.get("schema", {}).get("properties", [])
    if not isinstance(props, list) or not all(isinstance(x, dict) and isinstance(x.get("name"), str) for x in props):
        raise ApiError("未知 recordset schema")
    names = [p["name"] for p in props]
    if any(not isinstance(r, list) or len(r) != len(names) for r in rows):
        raise ApiError("recordset 列数不匹配")
    return [dict(zip(names, r)) for r in rows]


def yearly_stats(payload):
    result = []
    seen = set()
    for row in records(payload):
        year = str(row.get("year", ""))
        sharpe = finite(row.get("sharpe"))
        if not re.fullmatch(r"\d{4}", year) or year in seen or sharpe is None:
            raise ApiError("跨年数据缺少年份/Sharpe 或年份重复")
        seen.add(year)
        result.append({"year": year, "sharpe": sharpe})
    return result


def correlation(payload):
    if not isinstance(payload, dict):
        raise ApiError("相关性响应 schema 未知")
    for key in ('status', 'result'):
        if key in payload and str(payload[key]).strip().upper() not in ('PASS','COMPLETE','COMPLETED','SUCCESS'):
            raise ApiError('相关性状态尚未明确完成；不使用附带的数值')
    # A documented max plus optional min. Negative redundancy also matters.
    maximum = finite(payload.get("max"))
    minimum = finite(payload.get("min"))
    if maximum is None or abs(maximum) > 1 or (minimum is not None and abs(minimum) > 1):
        raise ApiError("相关性尚未完成或缺少可信 max")
    return max(abs(maximum), abs(minimum or 0))


def pnl_changes(payload, sessions=None):
    """Only adjacent declared trading sessions are daily observations.

    Callers needing gap diagnostics use daily_differences. There is deliberately
    no default weekday/holiday inference and no unchecked cumulative subtraction.
    """
    from .pnl_evidence import daily_differences
    return daily_differences(payload,sessions)['changes']


ISOLATION_PROTOCOL = 'server-is-date-bounds-v1'


def exact_date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ApiError('IS 边界必须是明确的 YYYY-MM-DD 日期；不推测时间戳/时区语义')
    try:return date.fromisoformat(value).isoformat()
    except ValueError as error:raise ApiError('IS 边界日期无效') from error


def discovery_isolation(detail,contract):
    """Use account-reviewed *server* metadata, never years or local cutoff dates.

    No current BRAIN response schema is assumed. The contract is disabled until
    the owner verifies its paths and inclusive-date semantics on their account.
    """
    if not isinstance(contract,dict) or contract.get('schema_reviewed') is not True or not str(contract.get('review_note','')).strip():
        raise ApiError('尚无经过账户核实的 IS 日期元数据契约；年份不能证明 IS/OS 隔离')
    if contract.get('date_semantics')!='inclusive_dates':raise ApiError('未核实 IS 日期边界的包含语义')
    paths=[contract.get('is_start_path'),contract.get('is_end_path')]
    boundaries=[]
    for path in paths:
        if not isinstance(path,list) or not 1<=len(path)<=8 or not all(isinstance(p,str) and p for p in path):raise ApiError('IS 元数据字段路径无效')
        if path[0]!='is' or any(p.lower() in ('os','test','prod') for p in path):raise ApiError('仅支持详情中明确的 is 元数据，不猜测其他样本区域')
        value=detail
        for part in path:
            if not isinstance(value,dict) or part not in value:raise ApiError('服务器未返回已核实契约所需的 IS 精确边界')
            value=value[part]
        boundaries.append(exact_date(value))
    start,end=boundaries
    if start>=end:raise ApiError('IS 边界为空或顺序无效')
    return {'status':'isolated','protocol':ISOLATION_PROTOCOL,'start_date':start,'end_date':end,
            'metadata_source':'alpha_detail.is','contract_sha256':digest(contract),
            'start_path':paths[0],'end_path':paths[1]}


def interval_in_is(key,isolation):
    if not isinstance(key,str) or key.count('/')!=1:return False
    try:left,right=[exact_date(x) for x in key.split('/')]
    except ApiError:return False
    return isolation['start_date']<=left<right<=isolation['end_date']


def pnl_isolation_valid(result):
    """Legacy cached sequences without a precise provenance contract fail closed."""
    proof=result.get('pnl_isolation')
    if not isinstance(proof,dict) or proof.get('status')!='isolated' or proof.get('protocol')!=ISOLATION_PROTOCOL:return False
    if proof.get('metadata_source')!='alpha_detail.is' or not re.fullmatch(r'[0-9a-f]{64}',str(proof.get('contract_sha256',''))):return False
    try:
        if exact_date(proof.get('start_date'))>=exact_date(proof.get('end_date')):return False
    except ApiError:return False
    return all(interval_in_is(key,proof) for key in result.get('pnl_changes',{}))


def normalize_checks(payload):
    if not isinstance(payload, dict):
        raise ApiError("检查响应 schema 未知")
    sample = payload.get('is')
    rows = sample.get('checks',payload.get('checks')) if isinstance(sample,dict) else payload.get('checks')
    if not isinstance(rows, list) or not rows:
        raise ApiError("缺少平台检查结果")
    result = []
    names = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row['name'].strip():
            raise ApiError("检查项格式未知")
        if row["name"] in names:
            raise ApiError("平台检查项重复")
        names.add(row["name"])
        from .checks import check_state
        result.append({"name": row["name"], "result": check_state(row.get('result')),
                       "value": finite(row.get("value")), "limit": finite(row.get("limit"))})
    return result


class BrainSimulator:
    mode = "live"

    def __init__(self, config, api=None):
        self.config, self.paths = config, config["brain"]
        self.api = api or ApiClient(self.paths["base_url"], self.paths["timeout_seconds"],
            self.paths["poll_deadline_seconds"], self.paths["max_get_attempts"])
        self._single_probe_budget=0
        self.account_ref=None

    def allow_one_probe(self):
        if self._single_probe_budget!=0: raise ResearchError('单模拟探测已经授权')
        self._single_probe_budget=1

    def path(self, name, alpha_id=None):
        if alpha_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]+", alpha_id):
            raise ApiError("无效 alpha_id")
        return self.paths[name].format(id=alpha_id)

    def login(self):
        user, password = os.getenv("BRAIN_EMAIL"), os.getenv("BRAIN_PASSWORD")
        if not user or not password:
            raise ResearchError("请在运行环境设置 BRAIN_EMAIL/BRAIN_PASSWORD；不要在聊天中发送密码")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        response = self.api.once("POST", self.path("authentication"), headers={"Authorization": "Basic " + token})
        if response.status not in (200, 201) or (isinstance(response.data, dict) and (response.data.get("inquiry") or response.data.get("_non_json"))):
            raise ApiError("认证未完成，需要账户本人通过官方流程完成验证", response.status)
        # Probe separately; an inquiry envelope is not proof of a usable session.
        user_data = self.api.wait(self.path("user"))
        if not isinstance(user_data, dict) or not user_data.get("id"):
            raise ApiError("无法验证当前账户身份")
        self.account_ref=digest(str(user_data['id']))
        return {"user_id": user_data["id"], "mode": self.mode}

    def start(self, expression, scope):
        if self._single_probe_budget==1:
            self._single_probe_budget=-1
        elif not self.config.get('safety',{}).get('live_research_enabled',False):
            raise ResearchError('真实批量研究默认关闭；先完成只读检查和单 Alpha 冒烟测试')
        response = self.api.once("POST", self.path("simulations"),
            {"type": "REGULAR", "settings": scope, "regular": expression})
        if response.status in (200, 201, 202) and response.headers.get("location"):
            try:
                return self.api.url(response.headers["location"])
            except ApiError as error:
                raise AmbiguousWrite('服务端返回不可信 Location；可能已创建，禁止重复 POST') from error
        if response.status in (400, 422, 429):
            raise ApiError(f"模拟未接受 HTTP {response.status}；不自动重发", response.status)
        raise AmbiguousWrite("模拟创建状态不明确；请对账，禁止自动重发", response.status)

    def finish(self, location, minimal=False, on_alpha=None):
        def terminal(payload):
            if not isinstance(payload, dict):
                raise ApiError("模拟状态 schema 未知")
            if payload.get("status") in ("ERROR", "FAILED", "CANCELLED"):
                raise ApiError("平台模拟失败；请查看平台详情")
            return bool(payload.get("alpha"))
        payload = self.api.wait(location, terminal)
        alpha = payload["alpha"]
        # Single simulation responses may carry an id or an embedded alpha object.
        # A list is still a multi-simulation response and is deliberately rejected.
        alpha_id = alpha.get("id") if isinstance(alpha, dict) else alpha
        if not isinstance(alpha_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", alpha_id):
            raise ApiError("单模拟的 Alpha ID 格式未知")
        if on_alpha:
            on_alpha(alpha_id)
        return self.collect(alpha_id,minimal=True) if minimal else self.collect(alpha_id)

    def finish_probe(self,location,on_alpha=None):
        # Initial account acceptance needs only simulation completion and metric parsing.
        return self.finish(location,minimal=True,on_alpha=on_alpha)

    def collect(self, alpha_id, minimal=False, audit=False):
        detail = self.api.wait(self.path("alpha", alpha_id))
        if not isinstance(detail, dict) or detail.get("id") != alpha_id or not isinstance(detail.get("is"), dict):
            raise ApiError("Alpha 详情缺少当前 ID 或 IS 指标")
        metrics = {k: finite(detail["is"].get(k)) for k in ("sharpe", "fitness", "turnover", "returns", "drawdown", "margin")}
        result = {"mode": "live", "alpha_id": alpha_id, "metrics": metrics,
                  'date_created': detail.get('dateCreated'), 'alpha_type': detail.get('type'),
                  "expression": detail.get("regular", {}).get("code") if isinstance(detail.get("regular"), dict) else None,
                  "settings": detail.get("settings"),
                  "checks": [], "years": [], "self_correlation": None,
                  "production_correlation": None, "pnl_changes": {},
                  "pnl_isolation": {'status':'unverified'}, "daily_evidence":{'status':'unverified'},
                  "daily_returns":{}, "evidence_errors": {}, "collected_at": now()}
        result['evidence_source']={'api_base_url':self.paths['base_url'],'alpha_id':alpha_id,
            'account_ref':self.account_ref,'detail_path':self.path('alpha',alpha_id),'pnl_path':self.path('pnl',alpha_id),
            'transport':'account_http' if type(self.api.transport).__name__=='JsonTransport' else 'scripted_or_unknown_transport',
            'detail_reconciliation_sha256':digest({'id':alpha_id,'expression':result['expression'],'settings':result['settings'],'metrics':metrics})}
        result['evidence_source']['responses']={'alpha':{'method':'GET','path':self.path('alpha',alpha_id),
            'payload_sha256':digest(detail),'observed_at':now()}}
        if minimal:return result
        def fetch(name):
            payload=self.api.wait(self.path(name,alpha_id))
            result['evidence_source']['responses'][name]={'method':'GET','path':self.path(name,alpha_id),
                'payload_sha256':digest(payload),'observed_at':now()}
            return payload
        try:
            result['pnl_isolation']=discovery_isolation(detail,self.paths.get('discovery_evidence',{}))
        except ApiError as error:
            # Avoid fetching mixed sample series when a precise boundary is unknown.
            result['evidence_errors']['years']=str(error)
            result['evidence_errors']['pnl_changes']=str(error)
        from .pnl_evidence import reviewed_context,build_daily_evidence
        context=None
        if result['pnl_isolation']['status']=='isolated':
            try:context=reviewed_context(self.config,detail,result['pnl_isolation'])
            except ApiError as error:result['evidence_errors']['pnl_changes']=str(error)
        # Independent endpoints: unsupported evidence is explicit, never replaced with fake values.
        for name, key, parse in (("check", "checks", normalize_checks),
                                 ("yearly", "years", yearly_stats),
                                 ("pnl", "pnl_changes", pnl_changes),
                                 ("self_correlation", "self_correlation", correlation)):
            try:
                if name=='yearly' and result['pnl_isolation']['status']!='isolated':continue
                if name=='pnl':
                    if context is None and not audit:continue
                    payload=fetch(name)
                    if audit:
                        from .evidence_audit import observe_pnl
                        result['pnl_observation']=observe_pnl(payload,context,result['pnl_isolation'])
                    if context is None:continue
                    result.update(build_daily_evidence(payload,context))
                    if result['daily_evidence']['status']!='complete':
                        result['evidence_errors']['pnl_changes']='交易日观测不完整或含非交易日记录；跨缺口差分已隔离'
                else:result[key] = parse(fetch(name))
            except ApiError as e:
                result["evidence_errors"][key] = str(e)
        if self.config["policy"]["require_production_correlation"]:
            try:
                result["production_correlation"] = correlation(fetch('production_correlation'))
            except ApiError as e:
                result["evidence_errors"]["production_correlation"] = str(e)
        proof=result['pnl_isolation']
        if proof['status']=='isolated':
            result['pnl_changes']={k:v for k,v in result['pnl_changes'].items() if interval_in_is(k,proof)}
            # Yearly aggregates spanning an IS boundary could contain OS returns.
            # Without reviewed per-row IS labels retain only complete calendar years.
            result['years']=[row for row in result['years'] if
                proof['start_date']<=row['year']+'-01-01' and row['year']+'-12-31'<=proof['end_date']]
        result['evidence_source']['discovery_contract_sha256']=digest(self.paths.get('discovery_evidence',{}))
        result['evidence_source']['pnl_contract_sha256']=digest(self.paths.get('pnl_evidence',{}))
        result['evidence_source']['calendar_sha256']=result['daily_evidence'].get('calendar_sha256')
        result['evidence_source']['normalized_is_pnl_sha256']=digest({'pnl':result['pnl_changes'],'returns':result['daily_returns'],'isolation':result['pnl_isolation']})
        from .checks import summarize_checks
        result['check_state']=summarize_checks(result['checks'],self.config['policy']['required_checks'])
        # Deliberately discard os/test/train/prod/raw bodies. They never enter research memory.
        return result

    def sync_catalog(self):
        scope = self.config["scope"]
        params = {k: scope[k] for k in ("instrumentType", "region", "universe", "delay")}
        fields, offset = {}, 0
        for _ in range(1000):
            payload = self.api.wait(self.path("fields") + "?" + urlencode({**params, "limit": 50, "offset": offset}))
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ApiError("字段目录响应未知")
            rows = payload["results"]
            for row in rows:
                if not isinstance(row, dict):
                    raise ApiError("字段目录条目不是对象")
                key = row.get("id")
                if not isinstance(key, str) or not key.isidentifier() or key in fields:
                    raise ApiError("字段目录重复或无效 ID")
                fields[key] = {"kind": row.get("type"), "point_in_time": False, "min_delay": scope["delay"],
                               "description": str(row.get("description", ""))[:1000], "review_note": "待负责人审计时点语义"}
            offset += len(rows)
            count = payload.get("count")
            if count is not None and offset >= count:
                break
            if not rows:
                if count is not None and offset < count:
                    raise ApiError("字段分页提前终止")
                break
        else:
            raise ApiError("字段分页超过限制")
        operators = self.api.wait(self.path("operators"))
        if not isinstance(operators, list):
            raise ApiError("算子目录需为数组")
        allowed = sorted({x["name"] for x in operators if isinstance(x, dict) and isinstance(x.get("name"), str)} & OPERATORS.keys())
        return {"source": "brain-account-api", "synced_at": now(), "scope": params,
                "fields": fields, "operators": allowed,
                "note": "字段禁用至人工审核；算子仅取账号目录与本地审计子集交集"}

    def submit_once(self, alpha_id):
        if not self.config.get('safety',{}).get('submission_enabled',False):
            raise ResearchError('提交开关默认关闭；冒烟测试不会开启提交权限')
        # One POST only, including when a timeout/503/redirect leaves state ambiguous.
        response = self.api.once("POST", self.path("submit", alpha_id))
        if response.status in (200, 201, 202, 204, 303, 503):
            return self.poll_submission(alpha_id)
        if response.status == 429:
            raise AmbiguousWrite("提交限流或平台繁忙，需查询提交状态，禁止自动重复 POST", 429)
        raise ApiError(f"提交响应 HTTP {response.status}", response.status)

    def poll_submission(self, alpha_id):
        def terminal(payload):
            if not isinstance(payload, dict):
                raise ApiError("提交响应 schema 未知")
            checks = payload.get("is", {}).get("checks", payload.get("checks", []))
            if any(x.get("result") == "FAIL" for x in checks if isinstance(x, dict)):
                raise ApiError("平台提交检查失败")
            return payload.get("status") in ("SUBMITTED", "ACTIVE")
        # Final acceptance is independently confirmed by alpha state, not POST's status.
        self.api.wait(self.path("submit", alpha_id), lambda d: isinstance(d, dict) and not any(
            x.get("result") == "PENDING" for x in d.get("is", {}).get("checks", []) if isinstance(x, dict)))
        detail = self.api.wait(self.path("alpha", alpha_id), terminal)
        return {"alpha_id": alpha_id, "status": detail["status"], "dateSubmitted": detail.get("dateSubmitted")}


class DemoSimulator:
    """Synthetic protocol fixture. These numbers have zero investment meaning."""
    mode = "demo"

    def start(self, expression, scope):
        return "demo:" + digest({"expression": expression, "scope": scope})

    def finish(self, location):
        n = int(location[-8:], 16)
        sharpe = round(0.7 + (n % 140)/100, 3)
        changes = {(date(2020, 1, 1)+timedelta(days=i)).isoformat()+"/"+
                   (date(2020, 1, 2)+timedelta(days=i)).isoformat():
                   math.sin((i+1)*(0.05+(n%101)/100))+math.cos(i*0.71) * 0.1 for i in range(360)}
        return {"mode": "demo", "alpha_id": "DEMO_" + location[-10:],
                "metrics": {"sharpe": sharpe, "fitness": round(sharpe*0.8, 3), "turnover": 0.18},
                "checks": [{"name": name, "result": "PASS"} for name in
                           ("LOW_SHARPE", "LOW_FITNESS", "LOW_TURNOVER", "HIGH_TURNOVER", "CONCENTRATED_WEIGHT")],
                "years": [{"year": str(y), "sharpe": sharpe-0.3} for y in (2020, 2021, 2022, 2023)],
                "self_correlation": 0.3, "production_correlation": None, "pnl_changes": changes,
                "evidence_errors": {}, "collected_at": now(), "synthetic": True}
