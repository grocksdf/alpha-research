"""P0/P1 Live proposal source/response binding and tri-state units, offline only.

Historical SQLite is never opened; all Engine/Memory fixtures are temporary.
No live LLM, BRAIN simulations, or Submit calls are permitted.
"""
from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import Mock, patch

from test_system import Base, FixtureSimulator, ScriptedTransport
from test_autonomy import idea
from alpharesearch.autonomy import AutonomousLoop, create_plan, open_engine
from alpharesearch.campaign_core import CampaignStore, create_campaign_plan, validate_limits
from alpharesearch.campaign_runner import CampaignRunner, CampaignDiscoveryAgent
from alpharesearch.common import ResearchError, digest
from alpharesearch.discovery import DiscoveryAgent, validate_idea
from alpharesearch.engine import Engine
from alpharesearch.formula_quality import UnitError, validate_units
from alpharesearch.http import ApiClient, Response
from alpharesearch.proposal_audit import (begin, captured, verify_source)


class ProvenanceAndUnitsBoundaryTests(Base):
    def live_loop(self):
        self.live()
        self.config['llm']['model'] = 'MODEL_A'
        plan = create_plan(self.config, {
            'study_id': 'BOUNDARY_01', 'objective': 'Offline source boundary tests',
            'generator': 'llm', 'domains': ['price_volume'], 'max_simulations': 3,
            'max_cycles': 2, 'max_ideas': 4, 'ideas_per_cycle': 3})
        engine = open_engine(self.config, plan, FixtureSimulator(), simulation_limit=0)
        self.addCleanup(engine.memory.close)
        return AutonomousLoop(engine, plan)

    def reply(self, items, *, returned_model='MODEL_A'):
        return Response(200, {}, {
            'model': returned_model, 'id': 'offline-reply',
            'choices': [{'message': {'tool_calls': [{
                'id': 'offline-tool', 'function': {'name': 'research_batch',
                'arguments': json.dumps({'ideas': items})}}]}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})

    def run_scripted(self, loop, reply):
        with patch.dict(os.environ, {'LLM_API_KEY':'TEST_ONLY'}):
            agent = DiscoveryAgent(loop.engine,
                ApiClient('https://invalid.local', transport=ScriptedTransport([reply])))
        loop.generator = agent
        return agent

    def audit_rows(self, loop, kind):
        return [json.loads(r[0]) for r in loop.engine.memory.db.execute(
            'SELECT payload FROM events WHERE run_id=? AND kind=? ORDER BY id',
            (loop.engine.run_id,kind))]

    def test_model_identity_unknown_substitution_rejected_before_candidate_enqueued(self):
        loop = self.live_loop()
        agent = self.run_scripted(loop, self.reply([idea()], returned_model='MODEL_B'))
        with self.assertRaises(ResearchError):
            agent.generate(loop.plan, [], 1)
        receipts = self.audit_rows(loop, 'llm_batch_proposals')
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['model_identity_status'], 'unknown_model_substitution')
        self.assertEqual(len(self.audit_rows(loop, 'llm_batch_failed')), 1)
        self.assertEqual(loop.ideas(), [])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)
        self.assertIsNone(agent.last_batch_provenance)

    def test_returned_model_missing_rejected_as_unverifiable(self):
        loop = self.live_loop()
        reply = self.reply([idea()])
        reply.data.pop('model')
        agent = self.run_scripted(loop, reply)
        with self.assertRaises(ResearchError):
            agent.generate(loop.plan, [], 1)
        evidence = self.audit_rows(loop,'llm_batch_proposals')[0]
        self.assertEqual(evidence['model_identity_status'],'missing_or_invalid_returned_model')
        self.assertEqual(loop.ideas(), [])

    def test_reviewed_alias_accepted_and_audited_from_frozen_snapshot(self):
        self.config['llm']['response_model_aliases'] = {
            'MODEL_A': [{'returned_model': 'MODEL_B', 'reviewed_by': 'risk-review',
                         'approval_ref': 'CHG-2026-101',
                         'reason': 'Reviewed gateway canonical model response identity'}]}
        loop = self.live_loop()
        agent = self.run_scripted(loop, self.reply([idea()], returned_model='MODEL_B'))
        proposed = agent.generate(loop.plan, [], 1)
        loop.add_batch(proposed, provenance=agent.last_batch_provenance)
        receipt = self.audit_rows(loop, 'llm_batch_proposals')[0]
        self.assertEqual(receipt['model_identity_status'], 'reviewed_alias')
        self.assertEqual(receipt['alias_approval_ref'], 'CHG-2026-101')
        self.assertEqual(len(loop.ideas()), 1)
        self.assertEqual(loop.ideas()[0]['generator_origin'],'llm')
        self.assertTrue(verify_source(loop.engine.memory,loop.engine.run_id,
                                      receipt['batch_id'], proposed))
        # Configuration outside an existing Run cannot rewrite frozen approvals.
        self.config['llm']['response_model_aliases'] = {}
        self.assertTrue(verify_source(loop.engine.memory,loop.engine.run_id,
                                      receipt['batch_id'], proposed))
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'],0)

    def test_alias_without_approved_reviewer_cannot_start_model_request(self):
        self.config['llm']['response_model_aliases'] = {
            'MODEL_A': [{'returned_model':'MODEL_B'}]}
        loop = self.live_loop()
        agent = self.run_scripted(loop, self.reply([idea()], returned_model='MODEL_B'))
        with self.assertRaises(ResearchError):
            agent.generate(loop.plan, [], 1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['llm_calls'],0)
        self.assertEqual(self.audit_rows(loop,'llm_batch_started'),[])

    def test_alias_mapping_for_unapproved_model_is_rejected(self):
        self.config['llm']['response_model_aliases'] = {
            'MODEL_UNAPPROVED': [{'returned_model':'MODEL_B','reviewed_by':'risk-review',
                'approval_ref':'CHG-2026-102','reason':'Unapproved request model cannot be added'}]}
        loop = self.live_loop()
        agent = self.run_scripted(loop,self.reply([idea()]))
        with self.assertRaises(ResearchError):
            agent.generate(loop.plan,[],1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['llm_calls'],0)

    def test_unreviewed_units_warn_for_three_reported_formulas(self):
        loop = self.live_loop()
        cases = (
            '-rank(ts_mean((high-low)/close,20))',
            '-rank((vwap-close)/close)',
            'rank(ts_mean(abs(returns)/(volume+1),20))',
        )
        for expression in cases:
            with self.subTest(formula=expression):
                validated = validate_idea(loop.engine,idea(expression),loop.plan['domains'],set())
                self.assertTrue(validated.fingerprint)
                report = validate_units(expression, self.config['catalog_data'])
                self.assertEqual(report['unit_status'],'insufficient_metadata')
                self.assertTrue(report['warnings'])
                self.assertFalse(report['independent_performance_proof'])

    def test_known_unit_conflicts_still_fail_without_guessing(self):
        self.live()
        fields = self.config['catalog_data']['fields']
        for name in ('high','low','close','vwap'):
            fields[name].update(unit='USD_PRICE', unit_reviewed=True,
                                unit_review_ref='OFFLINE-PRICE-REVIEW')
        fields['volume'].update(unit='SHARES',unit_reviewed=True,
                                unit_review_ref='OFFLINE-VOLUME-REVIEW')
        self.assertEqual(validate_units('-rank((vwap-close)/close)',
                                        self.config['catalog_data'])['unit_status'],'verified')
        self.assertEqual(validate_units('-rank(ts_mean((high-low)/close,20))',
                                        self.config['catalog_data'])['unit_status'],'verified')
        # Nonzero numeric constants may be typed pseudocounts in field units.
        constant = validate_units('rank(volume+1)', self.config['catalog_data'])
        self.assertEqual(constant['unit_status'], 'insufficient_metadata')
        self.assertIn('literal_unit_unspecified',
                      [v['code'] for v in constant['warnings']])
        for wrong in ('rank(close+volume)', 'rank(log(close))'):
            with self.subTest(wrong=wrong), self.assertRaises(UnitError):
                validate_units(wrong,self.config['catalog_data'])

    def test_unreviewed_units_are_kept_as_audited_candidate_warning(self):
        loop = self.live_loop()
        proposed = [idea('-rank((vwap-close)/close)')]
        agent = self.run_scripted(loop,self.reply(proposed))
        loop.add_batch(agent.generate(loop.plan,[],1),provenance=agent.last_batch_provenance)
        row=loop.ideas()[0]
        self.assertEqual(row['state'],'queued')
        self.assertEqual(row['unit_evidence_status'],'insufficient_metadata')
        self.assertIn('additive_units_unverified',row['unit_evidence_warning_codes'])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'],0)

    def _campaign_demo_llm(self):
        self.config['mode']='demo'
        self.config['safety']['live_research_enabled']=False
        self.config['safety']['submission_enabled']=False
        self.config['llm']['model']='MODEL_A'
        plan = create_campaign_plan(self.config, {
            'campaign_id':'P0_Campaign', 'objective':'No forged LLM proposal',
            'generator':'llm','domains':['price_volume'], 'ideas_per_cycle':3})
        engine=Engine(self.config,simulation_limit=0)
        self.addCleanup(engine.memory.close)
        store=CampaignStore(engine.memory)
        store.create(plan)
        limits=validate_limits(self.config,{'max_new_simulations':1})
        store.begin_run(plan['campaign_id'],engine.run_id,limits)
        loop=CampaignRunner(engine,plan,store,limits)
        return loop

    def test_campaign_llm_direct_registration_rejects_missing_proof_before_queue_write(self):
        loop=self._campaign_demo_llm()
        with self.assertRaises(ResearchError):
            loop._register_batch([idea()],1,provenance=None)
        self.assertEqual(loop.store.rows(loop.cid),[])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'],0)

    def test_campaign_internal_live_registrar_has_mandatory_provenance_even_if_bypassed_init(self):
        # The public Campaign Live entry is currently disabled by design;
        # emulate a future internal Live caller to test the private guard.
        config={'mode':'live','llm':{'model':'MODEL_A'}}
        engine=SimpleNamespace(config=config,run_id='no-live-run',memory=None)
        generator=object.__new__(CampaignDiscoveryAgent)
        generator.engine=engine
        generator.model='MODEL_A'
        runner=object.__new__(CampaignRunner)
        runner.engine=engine
        runner.generator=generator
        runner.plan={'generator':'llm','ideas_per_cycle':3}
        runner.cid='future-live'
        runner.store=Mock()
        full_forged = {'batch_id':'forged', 'requested_model':'MODEL_A',
                       'proposal_sha256':[digest(idea())], 'proposal_count':1,
                       'response_id':'fake', 'tool_call_id':'fake',
                       'source_kind':'authenticated_llm_tool_call'}
        engine.memory=SimpleNamespace(db=SimpleNamespace(
            execute=Mock(return_value=SimpleNamespace(fetchall=lambda: []))))
        for bad in (None,{}, {'batch_id':'forged'}, full_forged):
            with self.subTest(bad=bad), self.assertRaises(ResearchError):
                runner._register_batch([idea()],1,provenance=bad)
        runner.store.add.assert_not_called()

    def test_campaign_positive_llm_source_survives_queue_and_dispatch_audit(self):
        loop=self._campaign_demo_llm()
        candidate=idea()
        raw=json.dumps({'ideas':[candidate]})
        bid=begin(loop.engine.memory,loop.engine.run_id,cycle=1,model='MODEL_A',
                  prompt_hash=digest('offline-prompt'),tool_schema_hash=digest('tool-schema'))
        response=self.reply([candidate])
        receipt=captured(loop.engine.memory,loop.engine.run_id,batch_id=bid,
                         requested_model='MODEL_A',response=response,
                         raw_arguments=raw,tool_call_id='offline-tool')
        provenance={key:receipt[key] for key in (
            'batch_id','proposal_sha256','proposal_count','requested_model',
            'response_id','tool_call_id','source_kind')}
        self.assertEqual(loop._register_batch([candidate],1,provenance=provenance),1)
        row=loop.store.rows(loop.cid)[0]
        self.assertEqual(row['state'],'queued')
        self.assertTrue(loop._require_candidate_source(row))
        with self.assertRaises(ResearchError):
            loop._register_batch([candidate],1,provenance=provenance)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'],0)
        self.assertTrue(loop.store.audit_queue(loop.cid))

    def test_campaign_duplicate_source_still_validates_original_batch(self):
        loop=self._campaign_demo_llm()
        candidate=idea()
        batch=[candidate,candidate]
        raw=json.dumps({'ideas':batch})
        bid=begin(loop.engine.memory,loop.engine.run_id,cycle=1,model='MODEL_A',
                  prompt_hash=digest('prompt'),tool_schema_hash=digest('schema'))
        receipt=captured(loop.engine.memory,loop.engine.run_id,batch_id=bid,
                         requested_model='MODEL_A',response=self.reply(batch),
                         raw_arguments=raw,tool_call_id='offline-tool')
        provenance={key:receipt[key] for key in (
            'batch_id','proposal_sha256','proposal_count','requested_model',
            'response_id','tool_call_id','source_kind')}
        self.assertEqual(loop._register_batch(batch,1,provenance=provenance),1)
        entries=loop.store.rows(loop.cid)
        self.assertEqual(len(entries),1)
        self.assertTrue(loop._require_candidate_source(entries[0]))
        self.assertEqual(loop.store.summary(loop.cid)['duplicate_attempts'],1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'],0)

    def test_campaign_proposal_proof_survives_a_new_run(self):
        first=self._campaign_demo_llm()
        candidate=idea()
        bid=begin(first.engine.memory,first.engine.run_id,cycle=1,
                  model='MODEL_A',prompt_hash=digest('prompt'),
                  tool_schema_hash=digest('schema'))
        receipt=captured(first.engine.memory,first.engine.run_id,batch_id=bid,
                         requested_model='MODEL_A',response=self.reply([candidate]),
                         raw_arguments=json.dumps({'ideas':[candidate]}),
                         tool_call_id='offline-tool')
        source={key:receipt[key] for key in (
            'batch_id','proposal_sha256','proposal_count','requested_model',
            'response_id','tool_call_id','source_kind')}
        first._register_batch([candidate],1,provenance=source)
        next_engine=Engine(self.config,simulation_limit=0)
        self.addCleanup(next_engine.memory.close)
        store=CampaignStore(next_engine.memory)
        limits=validate_limits(self.config,{'max_new_simulations':1})
        store.begin_run(first.cid,next_engine.run_id,limits)
        resumed=CampaignRunner(next_engine,first.plan,store,limits)
        row=store.rows(first.cid)[0]
        self.assertTrue(resumed._require_candidate_source(row))
        self.assertEqual(next_engine.memory.run(next_engine.run_id)['simulations'],0)
        self.assertTrue(store.audit_queue(first.cid))

    def test_campaign_stale_unaudited_queued_candidate_never_reaches_simulation(self):
        loop=self._campaign_demo_llm()
        candidate=idea()
        valid=loop.engine.validator.validate(candidate['expression'])
        iid,_=loop.store.add(loop.cid,candidate,1,validated=valid,original_id='forged')
        loop.store.move(iid,'queued','l1','invalid_injected_without_llm_proof')
        with self.assertRaises(ResearchError):
            loop._run_base(loop.store.one(iid))
        self.assertEqual(loop.store.one(iid)['state'],'queued')
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'],0)


if __name__ == '__main__':
    import unittest
    unittest.main()
