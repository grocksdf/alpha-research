"""Offline provenance, no-template and expression-quality security regressions.

Run with the full repository: python -m unittest discover -s tests -v
These tests contain NO live credential use, real LLM request or BRAIN POST.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_system import Base, FixtureSimulator, ScriptedTransport
from test_autonomy import idea
from alpharesearch.autonomy import (AutonomousLoop, create_plan, execute_plan,
                                  open_engine, preview_plan)
from alpharesearch.common import ResearchError, digest, dumps
from alpharesearch.discovery import DiscoveryAgent, validate_idea
from alpharesearch.formula_quality import (UnitError, find_equivalent,
                                          normalized_signature, structural_similarity,
                                          validate_units)
from alpharesearch.http import ApiClient, ApiError, Response
from alpharesearch.proposal_audit import captured


class LiveDiscoveryIsolationTests(Base):
    def setup_live(self):
        self.live()
        self.config['llm']['model'] = 'TEST_MODEL_ONLY'
        return create_plan(self.config, {
            'study_id': 'TEST_LLM_ONLY', 'objective': 'OFFLINE provenance contract',
            'generator': 'llm', 'domains': ['price_volume'], 'max_simulations': 6,
            'max_cycles': 2, 'max_ideas': 4, 'ideas_per_cycle': 2})

    def new_loop(self, plan=None, limit=0):
        plan = plan or self.setup_live()
        engine = open_engine(self.config, plan, FixtureSimulator(), simulation_limit=limit)
        self.addCleanup(engine.memory.close)
        return AutonomousLoop(engine, plan)

    def response(self, ideas, *, response_id='offline-response-17', tool_id='offline-tool-17'):
        raw = json.dumps({'ideas': ideas}, ensure_ascii=False)
        return Response(200, {}, {'id': response_id, 'model': 'TEST_MODEL_ONLY',
            'choices': [{'message': {'tool_calls': [{'id': tool_id,
                'function': {'name': 'research_batch', 'arguments': raw}}]}}],
            'usage': {'prompt_tokens': 12, 'completion_tokens': 34}})

    def agent(self, loop, responses):
        with patch.dict(os.environ, {'LLM_API_KEY': 'OFFLINE_TEST_ONLY'}):
            agent = DiscoveryAgent(loop.engine, ApiClient(
                'https://invalid.test.local', transport=ScriptedTransport(responses)))
        loop.generator = agent
        return agent

    def event_payloads(self, loop, kind):
        return [json.loads(r[0]) for r in loop.engine.memory.db.execute(
            'SELECT payload FROM events WHERE run_id=? AND kind=? ORDER BY id',
            (loop.engine.run_id, kind))]

    def test_live_plan_cannot_choose_demo_generator(self):
        self.setup_live()
        with self.assertRaises(ResearchError):
            create_plan(self.config, {'study_id': 'NO_DEMO', 'objective': 'no demo in live',
                                      'generator': 'demo'})

    def test_live_execute_rejects_any_injected_fixed_batch_before_side_effects(self):
        plan = self.setup_live()
        from alpharesearch._demo_fixtures import DemoDiscovery
        with patch.object(DemoDiscovery, 'generate', side_effect=AssertionError('fixture invoked')):
            with self.assertRaises(ResearchError):
                execute_plan(self.config, plan, Path(self.tmp.name)/'review.json',
                             simulator=FixtureSimulator(), generator=DemoDiscovery(),
                             maximum=0, confirmation='test-preview')
        self.assertFalse(Path(self.config['database']).exists())

    def test_live_loop_injected_fixture_stops_before_generate(self):
        plan = self.setup_live()
        loop = self.new_loop(plan, limit=3)
        from alpharesearch._demo_fixtures import DemoDiscovery
        with patch.object(DemoDiscovery, 'generate', side_effect=AssertionError('fixture invoked')) as called:
            loop.generator = DemoDiscovery()
            with self.assertRaises(ResearchError):
                loop.run()
            called.assert_not_called()
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)
        self.assertEqual(self.event_payloads(loop, 'autonomy_idea'), [])

    def test_live_rejects_untraced_manual_proposals(self):
        loop = self.new_loop()
        with self.assertRaises(ResearchError):
            loop.add_batch([idea()])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_valid_structured_llm_proposals_provenance_and_per_idea_binding(self):
        loop = self.new_loop()
        generated = [idea(), idea('rank(ts_corr(close,volume,10))', family='liquidity')]
        raw = json.dumps({'ideas': generated}, ensure_ascii=False)
        agent = self.agent(loop, [self.response(generated)])
        received = agent.generate(loop.plan, [], 1)
        self.assertEqual(received, generated)
        loop.add_batch(received, provenance=agent.last_batch_provenance)
        receipts = self.event_payloads(loop, 'llm_batch_proposals')
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertEqual(receipt['requested_model'], 'TEST_MODEL_ONLY')
        self.assertEqual(receipt['returned_model'], 'TEST_MODEL_ONLY')
        self.assertEqual(receipt['response_id'], 'offline-response-17')
        self.assertEqual(receipt['tool_call_id'], 'offline-tool-17')
        self.assertEqual(receipt['raw_tool_arguments_sha256'], hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(receipt['proposal_sha256'], [digest(x) for x in generated])
        self.assertEqual(receipt['sanitized_structured_proposals'][0]['expression'], generated[0]['expression'])
        items = loop.ideas()
        self.assertEqual(len(items), 2)
        for index, row in enumerate(items):
            self.assertEqual(row['proposal_batch_id'], receipt['batch_id'])
            self.assertEqual(row['proposal_sha256'], digest(generated[index]))
            self.assertEqual(row['generator_origin'], 'llm')
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)
        self.assertTrue(loop.engine.memory.verify())

    def test_no_silent_fallback_on_model_error(self):
        loop = self.new_loop(limit=3)
        self.agent(loop, [ApiError('OFFLINE forced model timeout')])
        from alpharesearch._demo_fixtures import DemoDiscovery
        with patch.object(DemoDiscovery, 'generate', side_effect=AssertionError('fixture invoked')) as called:
            with self.assertRaises(ApiError):
                loop.run()
            called.assert_not_called()
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)
        self.assertEqual(loop.history('state')[-1]['status'], 'paused')
        self.assertEqual(len(self.event_payloads(loop, 'llm_batch_failed')), 1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['llm_calls'], 1)
        self.assertFalse(loop.engine.memory.cost_summary(loop.engine.run_id)['token_usage_complete'])

    def test_unexpected_provider_exception_pauses_without_fixture_fallback(self):
        loop = self.new_loop(limit=3)
        self.agent(loop, [RuntimeError('OFFLINE unexpected provider exception')])
        from alpharesearch._demo_fixtures import DemoDiscovery
        with patch.object(DemoDiscovery, 'generate', side_effect=AssertionError('template invoked')) as seed:
            with self.assertRaises(RuntimeError):
                loop.run()
            seed.assert_not_called()
        self.assertEqual(loop.history('state')[-1]['status'], 'paused')
        self.assertEqual(len(self.event_payloads(loop, 'llm_batch_started')), 1)
        failed = self.event_payloads(loop, 'llm_batch_failed')
        self.assertEqual(failed[0]['phase'], 'model_request')
        self.assertFalse(failed[0]['fallback_used'])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_bad_tool_call_no_template_fallback_and_audited(self):
        loop = self.new_loop(limit=3)
        data = {'choices': [{'message': {'tool_calls': [{
            'id': 'malicious', 'function': {'name': 'submit', 'arguments': '{}'}}]}}]}
        self.agent(loop, [Response(200, {}, data)])
        with self.assertRaises(ResearchError):
            loop.run()
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)
        events = self.event_payloads(loop, 'llm_batch_proposals')
        self.assertEqual(events[0]['parse_status'], 'invalid_tool_call')
        self.assertEqual(loop.ideas(), [])

    def test_redacted_batch_refused_before_raw_idea_persistence(self):
        loop = self.new_loop()
        secret = 'john.doe@example.com'
        bad = idea(source='Unreviewed source '+secret)
        agent = self.agent(loop, [self.response([bad])])
        with self.assertRaises(ResearchError):
            agent.generate(loop.plan, [], 1)
        rows = self.event_payloads(loop, 'llm_batch_proposals')
        self.assertEqual(rows[0]['parse_status'], 'redacted_sensitive_content')
        self.assertIn('[REDACTED_EMAIL]', json.dumps(rows[0]))
        self.assertNotIn(secret, json.dumps(rows[0]))
        self.assertEqual(self.event_payloads(loop, 'autonomy_idea'), [])

    def test_invalid_extra_key_does_not_leak_into_legacy_events(self):
        loop = self.new_loop()
        bad = {**idea(), 'LLM_API_KEY': 'NEVER_STORE_SECRET_12345'}
        agent = self.agent(loop, [self.response([bad])])
        with self.assertRaises(ResearchError):
            agent.generate(loop.plan, [], 1)
        receipts = self.event_payloads(loop, 'llm_batch_proposals')
        self.assertEqual(receipts[0]['parse_status'], 'invalid_schema')
        self.assertNotIn('NEVER_STORE_SECRET', json.dumps(receipts))
        self.assertEqual(self.event_payloads(loop, 'autonomy_idea'), [])

    def test_live_semantics_warns_for_unreviewed_units_but_rejects_proven_mismatch(self):
        loop = self.new_loop()
        # Lack of unit metadata is evidence uncertainty, not a proven error.
        for expression in ('rank(close + volume)', 'rank(log(close))',
                           '-rank(ts_mean((high-low)/close,20))',
                           '-rank((vwap-close)/close)',
                           'rank(ts_mean(abs(returns)/(volume+1),20))'):
            with self.subTest(expression=expression):
                self.assertTrue(validate_idea(loop.engine, idea(expression),
                                              loop.plan['domains'], set()))
                quality = validate_units(expression, self.config['catalog_data'])
                self.assertEqual(quality['unit_status'], 'insufficient_metadata')
                self.assertTrue(quality['warnings'])
        # Traditional single-field factors and correlations remain legal.
        for expression in ('rank(close)', 'rank(ts_mean(returns,20))',
                           'rank(ts_corr(close,volume,10))',
                           'rank(ts_delta(close,5)/ts_delay(close,5))'):
            with self.subTest(expression=expression):
                self.assertTrue(validate_idea(loop.engine, idea(expression),
                                              loop.plan['domains'], set()))
        fields = self.config['catalog_data']['fields']
        fields['close'].update(unit='USD_PRICE',unit_reviewed=True)
        fields['volume'].update(unit='SHARES',unit_reviewed=True)
        with self.assertRaises(UnitError):
            validate_units('rank(close+volume)', self.config['catalog_data'])
        with self.assertRaises(UnitError):
            validate_units('rank(log(close))', self.config['catalog_data'])

    def test_reviewed_shared_units_do_not_automatically_block_classical_factors(self):
        loop = self.new_loop()
        for field in ('close', 'vwap'):
            self.config['catalog_data']['fields'][field].update(unit='USD_PRICE',unit_reviewed=True)
        self.assertEqual(validate_units('rank((vwap-close)/close)', self.config['catalog_data'])['output_unit'], ())
        self.config['catalog_data']['fields']['volume'].update(unit='SHARES',unit_reviewed=True)
        with self.assertRaises(UnitError):
            validate_units('rank(volume+close)', self.config['catalog_data'])

    def test_normalization_ignores_commutative_associative_parentheses_but_not_windows(self):
        self.assertEqual(normalized_signature('rank(close + (volume + vwap))'),
                         normalized_signature('rank((vwap + close) + volume)'))
        self.assertNotEqual(normalized_signature('rank(ts_mean(close,5))'),
                            normalized_signature('rank(ts_mean(close,10))'))
        self.assertGreater(structural_similarity('ts_mean(close,5)', 'ts_mean(close,10)'), 0.5)
        self.assertLess(structural_similarity('ts_mean(close,5)', 'ts_corr(close,volume,10)'), 0.8)

    def test_normalized_duplicate_deferred_without_eliminating_distinct_mechanisms(self):
        loop = self.new_loop()
        # The two are structurally identical after safe canonicalization.
        a = idea('rank(close+(volume+vwap))')
        b = idea('rank((vwap+close)+volume)')
        # In Live, preflight correctly rejects the mixed units. Structural
        # duplicate matching is tested on a dimensionally valid single field.
        a = idea('rank(vwap+(close+close))')
        b = idea('rank((vwap+close)+close)', title='Different provenance hypothesis')
        self.assertEqual(normalized_signature(a['expression']), normalized_signature(b['expression']))
        engine=loop.engine
        first=engine.validator.validate(a['expression'])
        trial, created=engine.memory.add_trial(engine.run_id,
            {'expression':a['expression'],'hypothesis':'OFFLINE', 'source':'TEST'},first,engine.context)
        self.assertTrue(created)
        match=find_equivalent(engine.memory,engine.context,b['expression'],
                              engine.validator.validate(b['expression']).fingerprint)
        self.assertEqual(match['scope'],'prior_trial')
        self.assertEqual(engine.memory.run(engine.run_id)['simulations'],0)

    def test_live_campaign_entry_refuses_demo_fixture_even_if_injected(self):
        self.setup_live()
        from alpharesearch.campaign_core import create_campaign_plan
        from alpharesearch.campaign_runner import execute_campaign
        from alpharesearch._demo_fixtures import CampaignDemoDiscovery
        plan = create_campaign_plan(self.config, {'campaign_id':'LIVE_CAMPAIGN_BLOCKED',
            'objective':'OFFLINE block Live Campaign fixture', 'generator':'llm',
            'domains':['price_volume']})
        with patch.object(CampaignDemoDiscovery, 'generate',
                          side_effect=AssertionError('fixture must not run')) as called:
            with self.assertRaises(ResearchError):
                execute_campaign(self.config, plan, {'max_new_simulations':1},
                    generator=CampaignDemoDiscovery(self.config))
            called.assert_not_called()
        self.assertFalse(Path(self.config['database']).exists())

    def test_forged_or_duplicate_batch_events_cannot_authorize_live_candidate(self):
        loop = self.new_loop()
        candidate = idea()
        from alpharesearch.proposal_audit import begin, verify_source
        batch_id = begin(loop.engine.memory,loop.engine.run_id,cycle=1,
            model='TEST_MODEL_ONLY',prompt_hash=digest('fixture'),tool_schema_hash=digest('schema'))
        # A begin event by itself is not evidence a model returned any candidate.
        with self.assertRaises(ResearchError):
            verify_source(loop.engine.memory,loop.engine.run_id,batch_id,[candidate])
        from alpharesearch.proposal_audit import captured
        model_reply = self.response([candidate])
        captured(loop.engine.memory,loop.engine.run_id,batch_id=batch_id,
            requested_model='TEST_MODEL_ONLY',response=model_reply,
            raw_arguments=json.dumps({'ideas':[candidate]}),tool_call_id='offline-tool-17')
        self.assertTrue(verify_source(loop.engine.memory,loop.engine.run_id,batch_id,[candidate]))
        # A second response with the same batch ID is a provenance inconsistency.
        captured(loop.engine.memory,loop.engine.run_id,batch_id=batch_id,
            requested_model='TEST_MODEL_ONLY',response=model_reply,
            raw_arguments=json.dumps({'ideas':[candidate]}),tool_call_id='offline-tool-17')
        with self.assertRaises(ResearchError):
            verify_source(loop.engine.memory,loop.engine.run_id,batch_id,[candidate])

    def test_registered_trial_cannot_bypass_source_verification(self):
        loop = self.new_loop(limit=3)
        formula = idea()
        validated = loop.engine.validator.validate(formula['expression'])
        trial_id, _ = loop.engine.memory.add_trial(loop.engine.run_id,
            {'expression': formula['expression'], 'hypothesis': 'OFFLINE', 'source': 'TEST'},
            validated, loop.engine.context)
        # Even a checkpoint saying 'registered' cannot authorize a Live test
        # without the original LLM batch + per-proposal source receipt.
        row = {'idea_id': 'forged_registered', 'idea': formula, 'state': 'registered',
               'trial_id': trial_id, 'fingerprint': validated.fingerprint,
               'generator_origin': 'llm', 'proposal_batch_id': 'forged_batch'}
        loop.record('idea', row)
        with self.assertRaises(ResearchError):
            loop.test(loop.ideas()[0])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_proposal_receipt_replay_rejected_and_forged_trial_binding_blocked(self):
        loop = self.new_loop(limit=3)
        first = idea()
        agent = self.agent(loop, [self.response([first])])
        received = agent.generate(loop.plan, [], 1)
        loop.add_batch(received, provenance=agent.last_batch_provenance)
        with self.assertRaises(ResearchError):
            loop.add_batch(received, provenance=agent.last_batch_provenance)
        self.assertEqual(len(loop.ideas()), 1)
        forged = dict(loop.ideas()[0])
        forged['trial_id'] = 'random_other_trial'
        with self.assertRaises(ResearchError):
            loop.test(forged)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_proposal_mutation_rejected_before_live_registration(self):
        loop = self.new_loop(limit=3)
        generated = [idea()]
        agent = self.agent(loop, [self.response(generated)])
        received = agent.generate(loop.plan, [], 1)
        mutated = [dict(received[0], expression='rank(ts_corr(close, volume, 10))')]
        with self.assertRaises(ResearchError):
            loop.add_batch(mutated, provenance=agent.last_batch_provenance)
        self.assertFalse(loop.ideas())
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_production_modules_do_not_contain_seed_lists(self):
        from alpharesearch import discovery, campaign_runner
        source_a=inspect.getsource(discovery)
        source_b=inspect.getsource(campaign_runner)
        self.assertNotIn('class DemoDiscovery:', source_a)
        self.assertNotIn('class CampaignDemoDiscovery:', source_b)
        self.assertNotIn('seeds = [', source_a)
        self.assertNotIn('_forms = [', source_b)
        self.assertFalse(hasattr(discovery,'DemoDiscovery'))


if __name__ == '__main__':
    unittest.main()
