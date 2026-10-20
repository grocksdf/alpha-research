from __future__ import annotations

import json
import time
from pathlib import Path

from .brain import BrainSimulator, DemoSimulator, pnl_isolation_valid
from .common import ResearchError, digest, now, snapshot
from .evaluator import evaluate, shortlist
from .pnl_evidence import daily_evidence_valid
from .http import AmbiguousWrite, ApiError, PollTimeout
from .memory import Memory
from .validator import InvalidExpression, Validator


class Engine:
    def __init__(self, config, run_id=None, simulator=None):
        self.memory = Memory(config["database"])
        try:self._initialize(config,run_id,simulator)
        except BaseException:
            self.memory.close()
            raise

    def _initialize(self, config, run_id=None, simulator=None):
        self.config = config
        self.snapshot = snapshot(config)
        self.simulator = simulator or (DemoSimulator() if config["mode"] == "demo" else BrainSimulator(config))
        self.validator = Validator(config["catalog_data"], config["scope"], config["rules"])
        catalog = config["catalog_data"]
        semantic_catalog = {"account_id": catalog.get("account_id"), "source": catalog.get("source"),
            "operators": sorted(catalog["operators"]),
            "fields": {k: {a: v.get(a) for a in ("kind", "point_in_time", "min_delay")} for k, v in catalog["fields"].items()}}
        self.context = digest({"scope": config["scope"], "catalog": semantic_catalog, "mode": config["mode"]})
        if config["mode"] == "live":
            catalog = config["catalog_data"]
            if catalog.get("source") != "brain-account-api":
                raise ResearchError("live 模式必须使用自己账户同步的字段目录")
            expected = {k: config["scope"][k] for k in ("instrumentType", "region", "universe", "delay")}
            if catalog.get("scope") != expected:
                raise ResearchError("字段目录与研究范围不匹配，请重新同步")
        if run_id:
            run = self.memory.run(run_id)
            if run["snapshot_hash"] != digest(self.snapshot):
                raise ResearchError("代码、目录或规则已变更；原实验不可悄悄更改，请创建新 run")
            self.run_id = run_id
        else:
            self.run_id = self.memory.create_run(self.snapshot)

    def execute(self, trial_id):
        trial = self.memory.trial(trial_id)
        if trial["run_id"] != self.run_id:
            return {"trial_id": trial_id, "state": trial["state"], "reused": True}
        if trial["state"] in ("completed", "rejected", "abandoned", "failed"):
            return {"trial_id": trial_id, "state": trial["state"]}
        if trial["state"] in ("dispatching", "uncertain"):
            if isinstance(self.simulator,BrainSimulator):
                return self.recover_trial(trial_id)
            raise ResearchError("存在无 Location 的不确定写请求；请在平台对账后使用 reconcile")
        if trial['state']=='queued' and self.config['mode']=='live' and not self.config.get('safety',{}).get('live_research_enabled',False):
            raise ResearchError('真实研究创建开关关闭；未占用模拟预算')
        try:
            if trial["state"] == "queued":
                account_ref=getattr(self.simulator,'account_ref',None)
                if isinstance(self.simulator,BrainSimulator) and not account_ref:
                    raise ResearchError('须先验证当前登录账户；未发送 POST')
                budget=self.config['rules']['max_simulations_per_run']
                if self.memory.db.execute('SELECT 1 FROM directed_studies WHERE run_id=?',(self.run_id,)).fetchone():
                    budget=min(budget,6)
                if not self.memory.claim(trial_id, budget,
                        account_ref,self.config['brain']['base_url']):
                    return {"trial_id": trial_id, "state": "already_claimed"}
                started=time.monotonic()
                try:
                    location = self.simulator.start(trial["expression"], self.config["scope"])
                finally:
                    self.memory.cost(self.run_id,'simulation_create',time.monotonic()-started,detail={'trial_id':trial_id})
                self.memory.set_state(trial_id, "polling", location=location)
            else:
                location = trial["location"]
            if not location:
                if isinstance(self.simulator,BrainSimulator):
                    return self.recover_trial(trial_id)
                raise ResearchError("可恢复任务缺少 Location")
            started=time.monotonic()
            try:
                if isinstance(self.simulator,BrainSimulator):
                    trial=self.memory.trial(trial_id)
                    if trial.get('account_ref'):
                        from .reconciliation import assert_identity
                        assert_identity(self.simulator,self.recovery_record(trial))
                    if trial.get('alpha_id'):
                        result=self.simulator.collect(trial['alpha_id'])
                    else:
                        try:
                            result=self.simulator.finish(location,on_alpha=lambda aid:self.memory.bind_alpha(trial_id,aid))
                        except ApiError as error:
                            if error.status in (401,403):raise
                            return self.recover_trial(trial_id)
                else:
                    result = self.simulator.finish(location)
            finally:
                self.memory.cost(self.run_id,'simulation_poll',time.monotonic()-started,detail={'trial_id':trial_id})
            self.verify_result(trial, result)
            self.memory.result(trial_id, result)
            return {"trial_id": trial_id, "state": "completed", "metrics": result["metrics"]}
        except AmbiguousWrite as e:
            self.memory.set_state(trial_id, "uncertain", error=str(e))
            raise
        except (PollTimeout, ApiError) as e:
            current = self.memory.trial(trial_id)
            if current["location"]:
                self.memory.set_state(trial_id, "paused", error=str(e))
            else:
                # Explicit 4xx rejection is terminal. Other errors may have created a remote simulation.
                self.memory.set_state(trial_id, "failed" if e.status in (400, 401, 403, 422, 429) else "uncertain", error=str(e))
            raise

    def verify_result(self, trial, result):
        if self.config["mode"] != "live":
            return
        if result.get("mode") != "live" or result.get("synthetic"):
            raise ApiError("live 任务收到了测试证据")
        if not isinstance(result.get("expression"), str) or not isinstance(result.get("settings"), dict):
            raise ApiError("平台证据缺少公式/设置，无法对账")
        if self.validator.validate(result["expression"]).canonical != self.validator.validate(trial["expression"]).canonical:
            raise ApiError("远程 Alpha 公式与本地实验不匹配")
        from .reconciliation import settings_match
        if not settings_match(result['settings'],self.config['scope']):
            raise ApiError("远程 Alpha 设置与本地实验不匹配")

    def recovery_record(self,trial):
        return {**{k:trial.get(k) for k in ('expression','account_ref','api_base_url','dispatched_at')},
                'created_at':trial['created'],'scope':self.config['scope']}

    def recover_trial(self,trial_id,alpha_id=None):
        """No POST, including for dispatching/uncertain tasks without a Location."""
        from .reconciliation import assert_identity,recover_alpha
        trial=self.memory.trial(trial_id)
        if trial['run_id']!=self.run_id or trial['state'] not in ('dispatching','uncertain','polling','paused'):
            raise ResearchError('只能恢复当前 run 的未完成任务')
        record=self.recovery_record(trial)
        try:
            assert_identity(self.simulator,record)
            if trial.get('alpha_id') and not alpha_id:
                value=self.simulator.collect(trial['alpha_id'],minimal=True)
            else:
                value=recover_alpha(self.simulator,record,alpha_id)
            self.verify_result(trial,value)
            self.memory.bind_alpha(trial_id,value['alpha_id'],value.get('recovery'))
            self.memory.set_state(trial_id,'paused',error='Alpha 已绑定；只读刷新证据')
            result=self.simulator.collect(value['alpha_id'])
            self.verify_result(trial,result)
            self.memory.result(trial_id,result)
            return {'trial_id':trial_id,'state':'completed','alpha_id':value['alpha_id'],
                    'simulation_created':False,'recovery':value.get('recovery'),'metrics':result['metrics']}
        except ApiError as error:
            current=self.memory.trial(trial_id)
            self.memory.set_state(trial_id,'paused' if current.get('alpha_id') else 'uncertain',error=str(error))
            raise

    def propose(self, expression, hypothesis, source, parent_id=None, parent_intent_id=None):
        if self.memory.db.execute('SELECT 1 FROM directed_studies WHERE run_id=?',(self.run_id,)).fetchone():
            raise ResearchError('定向研究已冻结六公式；不能通过 Agent 扩展')
        for name, value, maxlen in (("expression", expression, 5000), ("hypothesis", hypothesis, 4000), ("source", source, 2000)):
            if not isinstance(value, str) or not value.strip() or len(value) > maxlen:
                raise ResearchError(f"{name} 缺失或过长")
        if parent_id:
            parent = self.memory.trial(parent_id)
            if parent["context"] != self.context:
                raise ResearchError("父实验不属于当前研究范围")
            if not parent_intent_id:
                parents=self.memory.intents(trial_id=parent_id)
                if len(parents)==1:parent_intent_id=parents[0]['id']
                elif len(parents)>1:raise ResearchError('父公式对应多个研究假设，请显式传 parent_intent_id')
        if parent_intent_id:
            matches=[i for i in self.memory.intents() if i['id']==parent_intent_id]
            if not matches or self.memory.trial(matches[0]['trial_id'])['context']!=self.context:
                raise ResearchError('父研究假设不存在或范围不匹配')
            if parent_id and matches[0]['trial_id']!=parent_id:
                raise ResearchError('父公式与父研究假设不匹配')
            parent_id=matches[0]['trial_id']
        self.memory.consume(self.run_id, "proposals", self.config["rules"]["max_proposals_per_run"])
        proposal = {"expression": expression, "hypothesis": hypothesis, "source": source}
        try:
            valid = self.validator.validate(expression)
        except InvalidExpression as e:
            trial_id, _ = self.memory.add_trial(self.run_id, proposal, None, self.context, parent_id=parent_id)
            intent_id=self.memory.add_intent(self.run_id,trial_id,hypothesis,source,parent_intent_id)
            self.memory.set_state(trial_id, "rejected", error=str(e))
            return {"trial_id": trial_id, 'intent_id':intent_id, "state": "rejected", "reason": str(e)}
        trial_id, created = self.memory.add_trial(self.run_id, proposal, valid, self.context, parent_id=parent_id)
        intent_id=self.memory.add_intent(self.run_id,trial_id,hypothesis,source,parent_intent_id)
        if not created:
            self.ensure_sensitivity(trial_id)
            return {"trial_id": trial_id,'intent_id':intent_id, "state": "duplicate", "shared_state":self.memory.trial(trial_id)['state'], "metrics": (self.memory.latest(trial_id) or {}).get("metrics", {})}
        outcome = self.execute(trial_id)
        outcome['intent_id']=intent_id
        self.ensure_sensitivity(trial_id)
        result = self.memory.latest(trial_id)
        if result:
            outcome["evaluation"] = self.evaluate_trial(trial_id)
        return outcome

    def ensure_sensitivity(self, trial_id):
        if self.memory.db.execute('SELECT 1 FROM directed_studies WHERE run_id=?',(self.run_id,)).fetchone():
            return
        trial = self.memory.trial(trial_id)
        intents=[i for i in self.memory.intents(self.run_id,trial_id) if i['state']=='active']
        if not intents and trial['run_id']==self.run_id and trial['kind']=='candidate':
            # Backfill old/directly-created jobs once, without rewriting original records.
            self.memory.migrate_intents()
            intents=[i for i in self.memory.intents(self.run_id,trial_id) if i['state']=='active']
        if trial["state"] != "completed" or not intents:
            return
        design_hash=digest({'expressions':self.validator.neighbors(trial['expression']),'scope':self.config['scope']})
        # The evaluator, not the Agent, selects these perturbations; each consumes budget.
        for expression in self.validator.neighbors(trial["expression"]):
            neighbor = {"expression": expression, "hypothesis": trial["hypothesis"], "source": "deterministic_parameter_sensitivity"}
            val = self.validator.validate(expression)
            existing=self.memory.existing_trial(self.context,val.fingerprint)
            if not existing and self.memory.run(self.run_id)["simulations"] >= self.config["rules"]["max_simulations_per_run"]:
                continue
            neighbor_id, fresh = self.memory.add_trial(self.run_id, neighbor, val, self.context, kind="sensitivity", parent_id=trial_id)
            # Link reused evidence explicitly; exact expression + settings + catalog dedup.
            self.memory.transaction(lambda: self.memory.event(self.run_id, "sensitivity_link", {"parent_id": trial_id, "neighbor_id": neighbor_id}))
            for intent in intents: self.memory.link_sensitivity(intent['id'],neighbor_id,design_hash)
            if fresh:
                self.execute(neighbor_id)

    def neighbor_ids_for(self, trial_id):
        links = self.memory.db.execute("SELECT payload FROM events WHERE kind='sensitivity_link'").fetchall()
        ids = {json.loads(row[0])["neighbor_id"] for row in links if json.loads(row[0])["parent_id"] == trial_id}
        for intent in self.memory.intents(self.run_id,trial_id):
            ids.update(r[0] for r in self.memory.db.execute('SELECT trial_id FROM sensitivity_refs WHERE intent_id=?',(intent['id'],)))
        expected = {self.validator.validate(x).fingerprint for x in self.validator.neighbors(self.memory.trial(trial_id)["expression"])}
        return [i for i in sorted(ids) if self.memory.trial(i)["fingerprint"] in expected]

    def neighbors_for(self, trial_id):
        return [self.memory.latest(i) for i in self.neighbor_ids_for(trial_id)]

    def neighbor_summary(self, trial_id):
        result = []
        for i in self.neighbor_ids_for(trial_id):
            trial = self.memory.trial(i)
            evidence = self.memory.latest(i) or {}
            result.append({"trial_id": i, "expression": trial["expression"], "state": trial["state"],
                           "alpha_id": evidence.get("alpha_id"), "metrics": evidence.get("metrics", {}), "error": trial["error"]})
        return result

    def evaluate_trial(self, trial_id):
        return evaluate(self.memory.latest(trial_id) or {}, self.neighbors_for(trial_id), self.config["policy"], self.config["account_profile_verified"])

    def refresh_evidence(self,trial_id):
        if self.config['mode']!='live':raise ResearchError('refresh-evidence 只读取真实 Alpha，不运行新模拟')
        trial=self.memory.trial(trial_id)
        if trial['context']!=self.context or not self.memory.intents(self.run_id,trial_id):raise ResearchError('只能刷新当前研究引用的候选')
        previous=self.memory.latest(trial_id)
        if not previous or not previous.get('alpha_id'):raise ResearchError('候选缺少已完成模拟的 Alpha ID')
        started=time.monotonic()
        try:result=self.simulator.collect(previous['alpha_id'])
        finally:self.memory.cost(self.run_id,'evidence_get',time.monotonic()-started,detail={'trial_id':trial_id})
        self.verify_result(trial,result)
        self.memory.result(trial_id,result)
        return {'trial_id':trial_id,'alpha_id':result['alpha_id'],'simulation_created':False,'evaluation':self.evaluate_trial(trial_id)}

    def abandon(self, trial_id, reason, intent_id=None):
        intents=[i for i in self.memory.intents(self.run_id,trial_id) if i['state']=='active' and (not intent_id or i['id']==intent_id)]
        if len(intents)!=1: raise ResearchError('请提供当前 run 中唯一的 intent_id，分别管理共享公式的假设')
        target=intents[0]['id']
        def work():
            self.memory.db.execute("UPDATE intent_states SET state='abandoned',reason=?,updated=? WHERE intent_id=?",(reason[:1000],now(),target))
            self.memory.event(self.run_id,'intent_abandoned',{'intent_id':target,'reason':reason[:1000]})
        self.memory.transaction(work)
        return {'trial_id':trial_id,'intent_id':target,'state':'abandoned'}

    def resume(self,continue_research=False):
        outcomes = []
        for trial in self.memory.trials(self.run_id):
            if trial["state"] in ("paused", "polling", "dispatching", "uncertain") or (continue_research and trial['state']=='queued'):
                outcomes.append(self.execute(trial["id"]))
        if not continue_research:
            return outcomes
        for trial in self.memory.trials(self.run_id):
            self.ensure_sensitivity(trial["id"])
        for trial_id in {i['trial_id'] for i in self.memory.intents(self.run_id)}:
            self.ensure_sensitivity(trial_id)
        return outcomes

    def report(self):
        from .research import families,notes
        items, states = [], {}
        trials = self.memory.trials(self.run_id)
        duplicates = self.memory.db.execute("SELECT payload FROM events WHERE run_id=? AND kind='duplicate'", (self.run_id,)).fetchall()
        referenced = {i['trial_id'] for i in self.memory.intents(self.run_id)}
        current_ids = {t["id"] for t in trials}
        trials.extend(self.memory.trial(i) for i in sorted(referenced-current_ids))
        for trial in trials:
            states[trial["state"]] = states.get(trial["state"], 0)+1
            result = self.memory.latest(trial["id"])
            intents=self.memory.intents(self.run_id,trial['id'])
            active=[i for i in intents if i['state']=='active']
            intent=active[-1] if active else None
            if result and intent:
                if result.get('mode')=='live' and not daily_evidence_valid(result):
                    # Preserve historical DB payloads, but quarantine legacy mixed
                    # sample evidence in reports instead of silently reusing it.
                    result={**result,'years':[],'pnl_changes':{},'daily_returns':{}}
                items.append({"trial_id": trial["id"], "expression": trial["expression"],
                    'intent_id':intent['id'],'research_intents':intents,
                    "hypothesis": intent['hypothesis'],
                    "original_run_id": trial["run_id"], "promoted_from_sensitivity": trial['kind']=='sensitivity',
                    "source": intent['source'],
                    "parent_id": trial["parent_id"], "family": trial["family"],
                    **result, "sensitivity": self.neighbor_summary(trial["id"]), "evaluation": self.evaluate_trial(trial["id"])})
        selected, excluded = shortlist(items, self.config["policy"], self.config["rules"]["max_candidates_per_family"])
        run = self.memory.run(self.run_id)
        label=self.memory.db.execute("SELECT payload FROM events WHERE run_id=? AND kind='research_arm' ORDER BY id DESC LIMIT 1",(self.run_id,)).fetchone()
        def public(x):
            return {k: v for k, v in x.items() if k not in ("pnl_changes","daily_returns")}
        return {"run_id": self.run_id, "mode": self.config["mode"], "created_at": now(),
                'research_arm':json.loads(label[0]) if label else {'agent':'unspecified'},
                "snapshot_hash": run["snapshot_hash"], "snapshot": self.snapshot,
                "counts": {k: run[k] for k in ("simulations", "proposals", "llm_calls")}, "task_states": states,
                'research_intents':self.memory.intents(self.run_id),'costs':self.memory.cost_summary(self.run_id),
                'families':families(self),'research_notes':notes(self),
                "trials": [{k: t[k] for k in ("id", "expression", "hypothesis", "source", "parent_id", "kind", "state", "error")} for t in trials],
                "independent_holdout_validated": False,
                "shortlist": [public(x) for x in selected], "diversity_exclusions": excluded,
                'research_queue':[public(x) for x in items if x['evaluation']['research_eligible'] and not x['evaluation']['ready']],
                "candidates": [public(x) for x in items], "audit": self.memory.verify(),
                "notice": "DEMO：全部指标是合成测试数据，禁止提交" if self.config["mode"] == "demo" else "研究候选需人工评审；跨年和扰动不等于独立样本外验证"}

    def write_report(self, path):
        from .common import dumps
        report = self.report()
        review_hash = digest({k: v for k, v in report.items() if k not in ("created_at", "audit")})
        report["review_hash"] = review_hash
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        self.memory.transaction(lambda: self.memory.event(self.run_id, "review_exported", {"review_hash": review_hash, "path": str(path)}))
        return report

    def submit(self, trial_id, review_path, approved_alpha_id):
        if not self.config.get('safety',{}).get('submission_enabled',False):
            raise ResearchError('submission_enabled=false，未发送提交请求')
        if self.config["mode"] != "live":
            raise ResearchError("DEMO 证据不可提交")
        review = json.loads(Path(review_path).read_text(encoding="utf-8"))
        supplied_hash = review.pop("review_hash", None)
        if supplied_hash != digest({k: v for k, v in review.items() if k not in ("created_at", "audit")}):
            raise ResearchError("评审包哈希不匹配")
        if review.get("run_id") != self.run_id or review.get("snapshot_hash") != digest(self.snapshot):
            raise ResearchError("评审包不属于当前版本")
        exported = self.memory.db.execute("SELECT payload FROM events WHERE run_id=? AND kind='review_exported'", (self.run_id,)).fetchall()
        if not any(json.loads(x[0]).get("review_hash") == supplied_hash for x in exported):
            raise ResearchError("评审包未经系统归档")
        eligible = [x for x in review["shortlist"] if x["trial_id"] == trial_id]
        if not eligible or eligible[0]["alpha_id"] != approved_alpha_id:
            raise ResearchError("必须明确批准评审包 shortlist 中的精确 alpha_id")
        result = self.memory.latest(trial_id)
        if not result or result["alpha_id"] != approved_alpha_id:
            raise ResearchError("实验与批准 ID 不匹配")
        # Re-fetch evidence immediately before the irreversible write.
        refreshed = self.simulator.collect(approved_alpha_id)
        self.verify_result(self.memory.trial(trial_id), refreshed)
        self.memory.result(trial_id, refreshed)
        current = self.report()
        if trial_id not in {x["trial_id"] for x in current["shortlist"]}:
            raise ResearchError("最新检查未通过或已从多样性候选池移除，未提交")
        self.memory.reserve_submission(trial_id, approved_alpha_id, supplied_hash)
        try:
            outcome = self.simulator.submit_once(approved_alpha_id)
        except ApiError as e:
            self.memory.submission_state(approved_alpha_id, "uncertain", {"error": str(e)})
            raise
        self.memory.submission_state(approved_alpha_id, "confirmed", outcome)
        return outcome
