"""Budgeted autonomous discovery MVP with append-only checkpoints.

The model proposes; deterministic host code validates, schedules and executes.
No method in this module submits an Alpha or reveals final/forward samples.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .common import ResearchError, digest, now, snapshot, dumps
from .discovery import DOMAINS, coverage, validate_idea, similarity, DiscoveryAgent
from .formula_quality import find_equivalent, structural_similarity, validate_units
from .proposal_audit import verify_source
from .engine import Engine
from .http import ApiError
from .preview import preview, confirm

PROTOCOL = 'autonomous-discovery-mvp-v1'


def create_plan(config, spec):
    allowed = {'study_id', 'objective', 'domains', 'max_simulations', 'max_cycles',
               'max_ideas', 'ideas_per_cycle', 'generator', 'research_notes'}
    if not isinstance(spec, dict) or set(spec)-allowed:
        raise ResearchError('自主研究计划含未知字段')
    defaults = {'domains': list(DOMAINS), 'max_simulations': 6, 'max_cycles': 3,
                'max_ideas': 12, 'ideas_per_cycle': 3, 'generator': 'llm', 'research_notes': ''}
    data = {**defaults, **spec}
    if not isinstance(data.get('study_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', data['study_id']):
        raise ResearchError('study_id 必须为稳定的短标识')
    if not isinstance(data.get('objective'), str) or not 1 <= len(data['objective'].strip()) <= 2000:
        raise ResearchError('需要明确的研究目标')
    domains = data['domains']
    if (not isinstance(domains, list) or not domains or any(not isinstance(d, str) or d not in DOMAINS for d in domains)
            or len(set(domains)) != len(domains)):
        raise ResearchError('研究方向必须来自已支持的四类，且不能重复')
    bounds = {'max_simulations': config['rules']['max_simulations_per_run'],
              'max_cycles': config['rules']['max_llm_calls_per_run'],
              'max_ideas': config['rules']['max_proposals_per_run'], 'ideas_per_cycle': 8}
    for key, maximum in bounds.items():
        if type(data[key]) is not int or not 1 <= data[key] <= maximum:
            raise ResearchError('自主研究预算无效：'+key)
    if not isinstance(data['research_notes'], str) or len(data['research_notes']) > 12000:
        raise ResearchError('研究资料必须为不超过 12000 字符的显式文本')
    if data['generator'] not in ('llm', 'demo') or (data['generator'] == 'demo' and config['mode'] != 'demo'):
        raise ResearchError('固定 Demo 生成器只允许在 demo 模式使用')
    data.update(protocol=PROTOCOL, snapshot_sha256=digest(snapshot(config)),
                database_ref=digest(str(Path(config['database']).resolve())),
                scope=config['scope'], domain_coverage=coverage(config, domains), automatic_submit=False)
    data['plan_sha256'] = digest(data)
    return data


def validate_plan(config, plan):
    if not isinstance(plan, dict) or plan.get('protocol') != PROTOCOL:
        raise ResearchError('自主研究计划协议不匹配')
    spec = {k: v for k, v in plan.items() if k not in
            ('protocol', 'snapshot_sha256', 'database_ref', 'scope', 'domain_coverage', 'automatic_submit', 'plan_sha256')}
    rebuilt = create_plan(config, spec)
    if rebuilt != plan:
        raise ResearchError('计划、代码、字段目录或规则已改变；不能静默更新冻结研究')
    return plan


def events(db, run_id, kind=None):
    rows = db.execute('SELECT kind,payload FROM events WHERE run_id=? ORDER BY id', (run_id,))
    return [(r[0], json.loads(r[1])) for r in rows if kind is None or r[0] == kind]


def preview_plan(config, plan):
    validate_plan(config, plan)
    value = preview(config, None)
    value.update(operation='autonomy-run', study_id=plan['study_id'], plan_sha256=plan['plan_sha256'],
                 generator=plan['generator'], simulation_budget=plan['max_simulations'],
                 domain_coverage=plan['domain_coverage'], max_cycles=plan['max_cycles'],
                 max_ideas=plan['max_ideas'], checkpoint_sha256=None)
    path = Path(config['database'])
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autonomous_studies'").fetchone()
            row = db.execute('SELECT run_id,plan_hash FROM autonomous_studies WHERE study_id=?',
                             (plan['study_id'],)).fetchone() if exists else None
            if row:
                if row[1] != plan['plan_sha256']:
                    raise ResearchError('study_id 已绑定不同的自主研究计划')
                run = db.execute('SELECT simulations,snapshot_hash FROM runs WHERE id=?', (row[0],)).fetchone()
                if run[1] != plan['snapshot_sha256']:
                    raise ResearchError('旧自主研究快照不一致，只能恢复旧任务')
                value['run_id'] = row[0]
                value['simulations_already_reserved'] = run[0]
                value['checkpoint_sha256'] = digest(events(db, row[0]))
    remaining = max(0, plan['max_simulations']-value['simulations_already_reserved'])
    value.update(remaining_budget=remaining, new_simulations_needed=None,
                 new_simulations_upper_bound=remaining, estimate_kind='autonomous_candidates_budget_upper_bound')
    value.pop('preview_sha256', None)
    value['preview_sha256'] = digest(value)
    return value


def open_engine(config, plan, simulator=None, *, simulation_limit=None):
    validate_plan(config, plan)
    estimate = preview_plan(config, plan)
    engine = Engine(config, estimate['run_id'], simulator, simulation_limit=simulation_limit)
    try:
        if not estimate['run_id']:
            def bind():
                engine.memory.db.execute('INSERT INTO autonomous_studies VALUES(?,?,?,?)',
                    (plan['study_id'], engine.run_id, plan['plan_sha256'], now()))
                engine.memory.event(engine.run_id, 'autonomy_plan', plan)
                engine.memory.event(engine.run_id, 'research_arm', {'agent': 'autonomous_'+plan['generator']})
            engine.memory.transaction(bind)
        return engine
    except BaseException:
        engine.memory.close()
        raise


class AutonomousLoop:
    def __init__(self, engine, plan, generator=None):
        validate_plan(engine.config, plan)
        record = engine.memory.db.execute('SELECT plan_hash FROM autonomous_studies WHERE run_id=?',
                                          (engine.run_id,)).fetchone()
        if not record or record[0] != plan['plan_sha256']:
            raise ResearchError('执行器未绑定自主研究计划')
        if engine.config['mode'] == 'live':
            if plan['generator'] != 'llm':
                raise ResearchError('Live 研究只允许冻结的 llm generator')
            if generator is not None and (type(generator) is not DiscoveryAgent or
                    generator.engine is not engine or
                    generator.model != engine.config['llm']['model']):
                raise ResearchError('Live 只接受绑定当前执行 Run 的 DiscoveryAgent')
        self.engine, self.plan = engine, plan
        self.generator = generator

    def record(self, kind, payload):
        self.engine.memory.transaction(lambda: self.engine.memory.event(self.engine.run_id, 'autonomy_'+kind, payload))

    def history(self, kind=None):
        return [p for k, p in events(self.engine.memory.db, self.engine.run_id, 'autonomy_'+kind if kind else None)
                if kind is not None or k.startswith('autonomy_')]

    def ideas(self):
        values = {p['idea_id']: dict(p) for p in self.history('idea')}
        for p in self.history('idea_state'):
            if p['idea_id'] in values:
                values[p['idea_id']].update(p)
        return list(values.values())

    def update(self, idea_id, **changes):
        self.record('idea_state', {'idea_id': idea_id, **changes})

    def feedback(self):
        feedback = []
        for item in self.ideas():
            value = {k: item[k] for k in ('idea_id', 'state', 'reason', 'idea') if k in item}
            if item.get('trial_id'):
                result = self.engine.memory.latest(item['trial_id']) or {}
                evaluation = self.engine.evaluate_trial(item['trial_id'])
                value.update(metrics={k: result.get('metrics', {}).get(k) for k in ('sharpe', 'fitness', 'turnover')},
                    rejections=evaluation['rejections'], missing_evidence=evaluation['missing'],
                    research_eligible=evaluation['research_eligible'], ready_for_human_review=evaluation['ready'],
                    interpretation='IS discovery feedback; constraints are observations, not causal failure explanations')
            feedback.append(value)
        # Explicit allowlist: never include raw responses, return documents, OS or forward data.
        return feedback[-self.plan['max_ideas']:]

    def historical_feedback(self):
        """Reuse prior discovery research, with the same field allowlist as this loop."""
        values = []
        for row in self.engine.memory.feedback(self.engine.context):
            if row['run_id'] == self.engine.run_id:
                continue
            metrics = row.get('metrics')
            metrics = metrics if isinstance(metrics, dict) else {}
            values.append({k: row.get(k) for k in ('id', 'expression', 'hypothesis', 'source', 'family', 'state', 'error')} |
                {'metrics': {k: metrics.get(k) for k in ('sharpe', 'fitness', 'turnover')}})
        return values

    def add_batch(self, ideas, *, provenance=None):
        if not isinstance(ideas, list) or len(ideas) > self.plan['ideas_per_cycle']:
            raise ResearchError('假设批次超过冻结上限')
        if self.engine.config['mode'] == 'live' and len(self.ideas()) + len(ideas) > self.plan['max_ideas']:
            raise ResearchError('Live LLM 批次超过剩余冻结假设数，禁止部分接受')
        if self.engine.config['mode'] == 'live':
            if (provenance is None or type(self.generator) is not DiscoveryAgent or
                    self.generator.engine is not self.engine or
                    self.generator.model != self.engine.config['llm']['model'] or
                    provenance is not getattr(self.generator, 'last_batch_provenance', None)):
                raise ResearchError('Live 禁止手工假设、固定模板或未经审计的来源')
            receipt = verify_source(self.engine.memory, self.engine.run_id,
                                    provenance.get('batch_id'), ideas)
            if (receipt['requested_model'] != self.engine.config['llm']['model'] or
                    receipt['response_id'] != provenance.get('response_id') or
                    receipt['tool_call_id'] != provenance.get('tool_call_id') or
                    receipt['proposal_sha256'] != provenance.get('proposal_sha256') or
                    receipt['proposal_count'] != provenance.get('proposal_count')):
                raise ResearchError('Live 批次来源收据与冻结模型身份不一致')
            if any(i.get('proposal_batch_id') == provenance['batch_id'] for i in self.ideas()):
                raise ResearchError('LLM 批次已经入队；禁止重放提案')
        current = self.ideas()
        parents = {i['idea_id'] for i in current if i.get('intent_id') and i['state'] == 'tested'}
        for idea in ideas:
            if len(self.ideas()) >= self.plan['max_ideas']:
                break
            idea_id = digest([self.engine.run_id, len(self.ideas()), idea])[:20]
            try:
                val = validate_idea(self.engine, idea, self.plan['domains'], parents)
                item = {'idea_id': idea_id, 'idea': idea, 'state': 'queued',
                        'fingerprint': val.fingerprint, 'structural_family': val.family, 'nodes': val.nodes}
                if self.engine.config['mode'] == 'live':
                    unit_review = validate_units(idea['expression'], self.engine.config['catalog_data'])
                    item['unit_evidence_status'] = unit_review['unit_status']
                    item['unit_evidence_warning_codes'] = sorted({
                        warning['code'] for warning in unit_review['warnings']})
                prior_ideas = [{'id': x['idea_id'], 'expression': x['idea']['expression'],
                                'fingerprint': x.get('fingerprint')} for x in self.ideas()
                               if x.get('fingerprint')]
                equivalent = find_equivalent(self.engine.memory, self.engine.context,
                    idea['expression'], val.fingerprint, additional=prior_ideas)
                if equivalent:
                    item.update(state='deferred', reason='normalized_equivalent_requires_review',
                                equivalent_prior_id=equivalent['existing'])
            except ResearchError as error:
                # Keep every invalid attempt for discovery feedback and multiplicity review.
                item = {'idea_id': idea_id, 'idea': idea, 'state': 'invalid', 'reason': str(error)}
            if provenance is not None:
                item.update(proposal_batch_id=provenance['batch_id'],
                            proposal_index=len(self.ideas()) - len(current),
                            proposal_sha256=digest(idea),
                            model_id=provenance['requested_model'],
                            response_id=provenance['response_id'],
                            tool_call_id=provenance['tool_call_id'],
                            generator_origin='llm')
            elif self.plan['generator'] == 'demo':
                item['generator_origin'] = 'offline_demo_fixture'
            self.record('idea', item)

    def schedule(self):
        engine = self.engine
        all_ideas = self.ideas()
        tested = [i for i in all_ideas if i['state'] in ('tested', 'reused')]
        expressions = [i['idea']['expression'] for i in tested]
        remaining = min(engine.simulation_limit, self.plan['max_simulations']) - engine.memory.run(engine.run_id)['simulations']
        choices = []
        for item in all_ideas:
            if item['state'] != 'queued':
                continue
            family_count = sum(i.get('structural_family') == item['structural_family'] for i in tested)
            existing = engine.memory.existing_trial(engine.context, item['fingerprint'])
            if family_count >= engine.config['rules']['max_candidates_per_family'] and not existing:
                self.update(item['idea_id'], state='deferred', reason='structural_family_search_cap')
                continue
            # Count the exact deterministic sensitivity bundle, including cached formulas.
            variants = [item['idea']['expression']] + engine.validator.neighbors(item['idea']['expression'])
            new = set()
            for expr in variants:
                val = engine.validator.validate(expr)
                tid = engine.memory.existing_trial(engine.context, val.fingerprint)
                if not tid or (engine.memory.trial(tid)['run_id'] == engine.run_id and engine.memory.trial(tid)['state'] == 'queued'):
                    new.add(val.fingerprint)
            if len(new) > remaining:
                continue
            domain_count = sum(i['idea']['domain'] == item['idea']['domain'] for i in tested)
            max_similarity = max((structural_similarity(item['idea']['expression'], e) for e in expressions), default=0.)
            priority = (bool(existing), family_count, domain_count, max_similarity, item['nodes'], item['idea_id'])
            choices.append((priority, item, len(new)))
        if not choices:
            return None
        _, item, cost = min(choices, key=lambda x: x[0])
        self.record('selection', {'idea_id': item['idea_id'], 'estimated_new_simulations': cost,
            'reason': 'new_formula_then_underexplored_structure_and_domain_then_lower_AST_similarity_and_complexity',
            'similarity_is_return_correlation': False, 'remaining_invocation_budget': remaining})
        return item

    def _require_live_item_provenance(self, item):
        """Read-only check immediately before ANY Live trial operation.

        Registered tasks may bypass register() on recovery; both register() and
        test() therefore require the same per-candidate original LLM receipt.
        """
        if self.engine.config['mode'] != 'live':
            return
        batch_id = item.get('proposal_batch_id')
        if item.get('generator_origin') != 'llm' or not batch_id:
            raise ResearchError('Live 禁止测试未绑定原始 LLM 提案的候选')
        members = [p for p in self.ideas() if p.get('proposal_batch_id') == batch_id]
        members.sort(key=lambda p: p.get('proposal_index', -1))
        if not members or [p.get('proposal_index') for p in members] != list(range(len(members))):
            raise ResearchError('LLM 批次候选序号不连续，拒绝恢复未审计实验')
        receipt = verify_source(self.engine.memory, self.engine.run_id,
                                batch_id, [p['idea'] for p in members])
        recorded = next((p for p in members if p['idea_id'] == item.get('idea_id')), None)
        if (receipt['requested_model'] != self.engine.config['llm']['model'] or
                item.get('model_id') != receipt['requested_model'] or
                item.get('response_id') != receipt['response_id'] or
                item.get('tool_call_id') != receipt['tool_call_id'] or
                item.get('proposal_sha256') != digest(item['idea']) or
                recorded is None or
                recorded.get('trial_id') != item.get('trial_id') or
                recorded.get('intent_id') != item.get('intent_id') or
                recorded.get('state') != item.get('state') or
                recorded.get('idea') != item.get('idea') or
                recorded.get('proposal_sha256') != digest(item['idea'])):
            raise ResearchError('候选与 LLM 原始响应的模型、哈希或调用关联不一致')

    def register(self, item):
        engine, idea = self.engine, item['idea']
        self._require_live_item_provenance(item)
        source = 'autonomy:'+self.plan['study_id']+':'+item['idea_id']
        known = [i for i in engine.memory.intents(engine.run_id) if i['source'] == source]
        if known:
            intent = known[0]
            self.update(item['idea_id'], state='registered', trial_id=intent['trial_id'], intent_id=intent['id'])
            return intent['trial_id']
        parent = next((i for i in self.ideas() if i['idea_id'] == idea['parent_idea_id']), None)
        valid = validate_idea(engine, idea, self.plan['domains'], {i['idea_id'] for i in self.ideas() if i.get('intent_id')})
        engine.memory.consume(engine.run_id, 'proposals', engine.config['rules']['max_proposals_per_run'])
        proposal = {'expression': idea['expression'], 'hypothesis': dumps({k: idea[k] for k in
                    ('title', 'mechanism', 'expected_sign', 'horizon_days', 'falsification', 'change', 'source')}), 'source': source}
        tid, _ = engine.memory.add_trial(engine.run_id, proposal, valid, engine.context,
            parent_id=parent.get('trial_id') if parent else None)
        intent_id = engine.memory.add_intent(engine.run_id, tid, proposal['hypothesis'], source,
            parent.get('intent_id') if parent else None)
        self.update(item['idea_id'], state='registered', trial_id=tid, intent_id=intent_id)
        return tid

    def test(self, item):
        self._require_live_item_provenance(item)
        engine = self.engine
        tid = item.get('trial_id') or self.register(item)
        trial = engine.memory.trial(tid)
        if trial['run_id'] != engine.run_id and trial['state'] != 'completed':
            self.update(item['idea_id'], state='waiting', reason='shared_trial_requires_original_run_recovery')
            return
        engine.execute(tid)
        engine.ensure_sensitivity(tid)
        trial = engine.memory.trial(tid)
        if trial['state'] != 'completed' or trial.get('remote_state') == 'unresolved':
            raise ResearchError('远程实验尚未完成；停止新建并先恢复对账')
        self.update(item['idea_id'], state='tested' if trial['run_id'] == engine.run_id else 'reused',
                    trial_id=tid, reason='discovery_experiment_complete')
        self.record('feedback', self.feedback()[-self.plan['max_ideas']:])

    def run(self, steps=None):
        if steps is not None and (type(steps) is not int or steps < 1):
            raise ResearchError('steps 必须为正整数')
        count = 0
        try:
            # Interrupted POST/polling is recovered by a separate GET-only entry.
            for trial in self.engine.memory.trials(self.engine.run_id):
                if trial['state'] in ('dispatching', 'uncertain', 'polling', 'paused') or trial.get('remote_state') == 'unresolved':
                    raise ResearchError('存在未完成任务；先运行 autonomy-recover，不重发 POST')
            if not any(v['status'] == 'available' for v in self.plan['domain_coverage'].values()):
                self.record('state', {'status': 'paused', 'reason': 'needs_authorized_fields'})
                return self.summary()
            while True:
                for item in self.ideas():
                    if item['state'] == 'registered':
                        if steps is not None and count >= steps:
                            break
                        self.test(item)
                        count += 1
                if steps is not None and count >= steps:
                    self.record('state', {'status': 'paused', 'reason': 'step_limit'}); break
                cycles = len(self.history('cycle'))
                can_generate = (cycles < self.plan['max_cycles'] and len(self.ideas()) < self.plan['max_ideas']
                                and not self.history('generation_finished'))
                remaining = min(self.plan['max_simulations'], self.engine.simulation_limit) - self.engine.memory.run(self.engine.run_id)['simulations']
                # Generate fresh hypotheses after feedback, while retaining untested alternatives.
                if can_generate and remaining > 0:
                    if self.generator is None:
                        if self.plan['generator'] == 'demo':
                            if self.engine.config['mode'] != 'demo':
                                raise ResearchError('Live 路径严禁使用固定模板生成器')
                            from ._demo_fixtures import DemoDiscovery
                            self.generator = DemoDiscovery()
                        else:
                            self.generator = DiscoveryAgent(self.engine)
                    self.record('cycle', {'cycle': cycles+1, 'status': 'requested'})
                    if self.engine.config['mode'] == 'live' and (
                            type(self.generator) is not DiscoveryAgent or
                            self.generator.engine is not self.engine or
                            self.generator.model != self.engine.config['llm']['model']):
                        raise ResearchError('Live 调度禁止非授权 LLM 生成器，包括 Demo fixtures')
                    batch = self.generator.generate(self.plan, self.feedback()+self.historical_feedback(), cycles+1)
                    provenance = (self.generator.last_batch_provenance
                                  if type(self.generator) is DiscoveryAgent else None)
                    self.add_batch(batch, provenance=provenance)
                    if not batch:
                        self.record('generation_finished', {'cycle': cycles+1})
                item = self.schedule()
                if item:
                    self.test(item); count += 1
                    continue
                if can_generate and remaining > 0 and len(self.history('cycle')) < self.plan['max_cycles']:
                    continue
                spent = self.engine.memory.run(self.engine.run_id)['simulations']
                if spent >= self.plan['max_simulations']:
                    state = {'status': 'completed', 'reason': 'total_budget_exhausted'}
                elif remaining <= 0:
                    state = {'status': 'paused', 'reason': 'invocation_budget_exhausted'}
                elif any(i['state'] == 'queued' for i in self.ideas()):
                    state = {'status': 'paused', 'reason': 'no_affordable_candidate_bundle'}
                else:
                    state = {'status': 'completed', 'reason': 'search_limits_reached'}
                self.record('state', state)
                break
        except KeyboardInterrupt:
            self.record('state', {'status': 'paused', 'reason': 'user_interrupt'})
        except Exception:
            self.record('state', {'status': 'paused', 'reason': 'execution_or_model_error_requires_review'})
            raise  # Never switch to the Demo generator after LLM failure.
        return self.summary()

    def summary(self):
        engine = self.engine
        review = engine.report()
        # Reporting uses the existing fixed evaluator. Missing evidence stays visible.
        candidates = sorted(review['candidates'], key=lambda x: (
            not x['evaluation']['ready'], bool(x['evaluation']['rejections']),
            len(x['evaluation']['missing']), x['trial_id']))
        states = self.history('state')
        return {'mode': 'autonomous_discovery_mvp', 'generator': self.plan['generator'],
            'study_id': self.plan['study_id'], 'run_id': engine.run_id, 'plan_sha256': self.plan['plan_sha256'],
            'state': states[-1] if states else {'status': 'created'}, 'counts': review['counts'],
            'domain_coverage': self.plan['domain_coverage'], 'ideas': self.ideas(),
            'research_feedback': self.feedback(), 'planner_decisions': self.history('selection'),
            'candidate_ranking': [{'trial_id': c['trial_id'], 'expression': c['expression'],
                'metrics': c.get('metrics', {}), 'evaluation': c['evaluation'],
                'sensitivity': c['sensitivity'], 'self_correlation': c.get('self_correlation'),
                'risks': ['IS_search_evidence_requires_independent_validation']+c['evaluation']['rejections']+c['evaluation']['missing'],
                'source': c['source'], 'research_intents': c['research_intents']} for c in candidates],
            'shortlist': review['shortlist'], 'research_queue': review['research_queue'],
            'costs': review['costs'], 'audit': review['audit'], 'automatic_submit': False,
            'independent_holdout_validated': False,
            'empirical_ai_discovery_validated': False,
            'notice': 'DEMO 固定假设与合成指标仅验证工程流程' if self.plan['generator'] == 'demo'
                      else '模型研究能力与 Alpha 有效性仍需真实同预算对照和独立验证'}


def execute_plan(config, plan, report_path, simulator=None, maximum=None, confirmation=None, steps=None, generator=None):
    current = preview_plan(config, plan)
    if config['mode'] == 'live' and generator is not None and type(generator) is not DiscoveryAgent:
        raise ResearchError('Live 不允许注入离线 BatchGenerator 或模板生成器')
    allowed = confirm(config, current, maximum, confirmation)
    if config['mode'] == 'live':
        if not config.get('safety', {}).get('live_research_enabled'):
            raise ResearchError('真实研究开关关闭')
        if simulator is None or getattr(simulator, 'account_ref', None) != digest(str(config['catalog_data'].get('account_id'))):
            raise ResearchError('须先登录并核对字段目录账户')
        from .probe import require_smoke_receipt
        require_smoke_receipt(config, config['catalog_data']['account_id'])
    absolute_limit = min(plan['max_simulations'], current['simulations_already_reserved']+allowed)
    engine = open_engine(config, plan, simulator, simulation_limit=absolute_limit)
    loop = AutonomousLoop(engine, plan, generator)
    try:
        loop.record('authorization', {'preview_sha256': current['preview_sha256'],
            'max_new_simulations': allowed, 'absolute_limit': absolute_limit})
        try:
            value = loop.run(steps)
        finally:
            # Always leave a reviewable checkpoint, including on model/API trouble.
            from .cli import save_json
            save_json(report_path, loop.summary())
        return value
    finally:
        engine.memory.close()


def recover_plan(config, plan, report_path, simulator=None):
    current = preview_plan(config, plan)
    if not current['run_id']:
        raise ResearchError('计划尚未运行，无需恢复')
    engine = open_engine(config, plan, simulator, simulation_limit=current['simulations_already_reserved'])
    try:
        outcome = engine.resume(False)
        loop = AutonomousLoop(engine, plan)
        loop.record('state', {'status': 'paused', 'reason': 'get_recovery_complete_no_new_authorization'})
        from .cli import save_json
        save_json(report_path, loop.summary())
        return {'run_id': engine.run_id, 'outcomes': outcome, 'new_simulations': 0, 'model_calls': 0, 'submit_posts': 0}
    finally:
        engine.memory.close()
