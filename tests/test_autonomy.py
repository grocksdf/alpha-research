"""Offline contracts for autonomous research; no real model/account/market data."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

from test_system import Base, FixtureSimulator, ScriptedTransport
from alpharesearch.autonomy import (create_plan, validate_plan, preview_plan, open_engine,
    AutonomousLoop, execute_plan, recover_plan)
from alpharesearch.common import ResearchError, digest
from alpharesearch.discovery import DiscoveryAgent, coverage, validate_idea, similarity
from alpharesearch.engine import Engine
from alpharesearch.http import ApiClient, ApiError, PollTimeout, Response


def idea(expression='-rank(ts_delta(close, 5))', **changes):
    return {'title': 'TEST hypothesis', 'domain': 'price_volume', 'mechanism': 'TEST conditional liquidity mechanism',
            'expected_sign': 'negative', 'horizon_days': 5, 'falsification': 'TEST effect absent in independent research',
            'expression': expression, 'source': 'TEST prior; no citation', 'family': 'reversion',
            'parent_idea_id': None, 'change': 'TEST independent mechanism', **changes}


class BatchGenerator:
    def __init__(self, batches):
        self.batches = list(batches)
        self.inputs = []

    def generate(self, plan, feedback, cycle):
        self.inputs.append(copy.deepcopy(feedback))
        return self.batches.pop(0) if self.batches else []


class AutonomyTests(Base):
    def plan(self, **changes):
        return create_plan(self.config, {'study_id': 'TEST_STUDY', 'objective': 'TEST discovery loop',
            'generator': 'demo' if self.config['mode'] == 'demo' else 'llm', 'max_simulations': 6,
            'ideas_per_cycle': 2, 'max_cycles': 3, 'max_ideas': 8, **changes})

    def loop(self, plan=None, simulator=None, generator=None, limit=None):
        plan = plan or self.plan()
        engine = open_engine(self.config, plan, simulator, simulation_limit=limit)
        self.addCleanup(engine.memory.close)
        return AutonomousLoop(engine, plan, generator)

    def test_dry_run_has_no_database_model_or_network_side_effects(self):
        value = preview_plan(self.config, self.plan())
        self.assertFalse(Path(self.config['database']).exists())
        self.assertEqual(value['new_simulations_upper_bound'], 6)
        self.assertFalse(value['database_mutated'])
        self.assertEqual(value['network_requests'], 0)

    def test_budgets_and_unknown_permissions_are_rejected(self):
        for key, value in [('max_simulations', 999), ('max_cycles', True), ('max_ideas', 0),
                           ('ideas_per_cycle', 100), ('domains', ['unknown']), ('automatic_submit', True)]:
            with self.subTest(key=key), self.assertRaises(ResearchError):
                self.plan(**{key: value})

    def test_frozen_plan_rejects_catalog_rule_and_plan_changes(self):
        plan = self.plan()
        for mode in ('plan', 'policy', 'catalog'):
            config = copy.deepcopy(self.config); changed = copy.deepcopy(plan)
            if mode == 'plan': changed['max_simulations'] += 1
            if mode == 'policy': config['policy']['min_sharpe'] += .1
            if mode == 'catalog': config['catalog_data']['fields']['close']['point_in_time'] = False
            with self.assertRaises(ResearchError): validate_plan(config, changed)

    def test_domains_require_actual_authorized_reviewed_fields(self):
        value = coverage(self.config, ['price_volume', 'fundamental', 'microstructure', 'sentiment'])
        self.assertEqual(value['price_volume']['status'], 'available')
        self.assertEqual(value['fundamental']['status'], 'needs_authorized_fields')
        self.config['catalog_data']['fields']['earnings_pit'] = {'kind': 'MATRIX', 'point_in_time': True,
            'min_delay': 1, 'research_domains': ['fundamental']}
        loop = self.loop()
        loop.add_batch([idea('rank(earnings_pit)', domain='fundamental', family='quality')])
        self.assertEqual(loop.ideas()[0]['state'], 'queued')

    def test_unknown_unreviewed_future_and_wrong_domain_formulas_are_preserved_as_invalid(self):
        loop = self.loop(self.plan(ideas_per_cycle=8))
        loop.add_batch([idea('rank(unknown)'), idea('ts_delay(close,-1)'),
                        idea('rank(close)', domain='sentiment'), idea('rank(close)', family=[]),
                        {**idea(), 'permissions': 'submit'}])
        self.assertEqual([i['state'] for i in loop.ideas()], ['invalid']*5)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_no_available_research_domain_pauses_without_calling_model(self):
        generator = BatchGenerator([[idea()]])
        loop = self.loop(self.plan(domains=['fundamental']), generator=generator)
        result = loop.run()
        self.assertEqual(result['state']['reason'], 'needs_authorized_fields')
        self.assertEqual(generator.inputs, [])
        self.assertEqual(result['counts']['simulations'], 0)

    def test_same_plan_resumes_without_resetting_budget_or_simulating_duplicates(self):
        plan = self.plan()
        path = Path(self.tmp.name)/'review.json'
        first = execute_plan(self.config, plan, path, maximum=3, steps=1)
        second = execute_plan(self.config, plan, path, maximum=3)
        third = execute_plan(self.config, plan, path, maximum=3)
        self.assertEqual(first['counts']['simulations'], 3)
        self.assertEqual(second['counts']['simulations'], 6)
        self.assertEqual(third['counts']['simulations'], 6)
        self.assertEqual(first['run_id'], third['run_id'])
        self.assertFalse(third['automatic_submit'])
        self.assertFalse(third['empirical_ai_discovery_validated'])
        self.assertEqual(third['shortlist'], [])

    def test_invocation_budget_includes_sensitivity_and_requires_affordable_bundle(self):
        loop = self.loop(limit=2)
        loop.add_batch([idea()])
        self.assertIsNone(loop.schedule())
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_planner_explores_a_new_structure_before_a_window_variant(self):
        loop = self.loop()
        loop.add_batch([idea()]); loop.test(loop.schedule())
        loop.add_batch([idea('-rank(ts_delta(close, 10))'), idea('-rank(ts_corr(close, volume, 10))', family='liquidity')])
        self.assertIn('ts_corr', loop.schedule()['idea']['expression'])
        self.assertFalse(loop.history('selection')[-1]['similarity_is_return_correlation'])

    def test_structural_similarity_does_not_count_window_changes_as_novel(self):
        self.assertEqual(similarity('ts_mean(close,5)', 'ts_mean(close,10)'), 1.)
        self.assertLess(similarity('ts_mean(close,5)', 'ts_corr(returns,volume,10)'), .5)

    def test_formula_reuse_keeps_distinct_hypotheses_without_new_simulations(self):
        loop = self.loop()
        loop.add_batch([idea(), idea(title='TEST distinct story')])
        loop.test(loop.schedule()); loop.test(loop.schedule())
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 3)
        self.assertEqual(len(loop.engine.memory.intents(loop.engine.run_id)), 2)

    def test_revision_links_parent_and_receives_failure_feedback(self):
        loop = self.loop()
        loop.add_batch([idea()]); loop.test(loop.schedule())
        parent = loop.ideas()[0]
        loop.add_batch([idea('-rank(ts_delta(close, 10))', parent_idea_id=parent['idea_id'], change='TEST increase horizon')])
        child = loop.schedule(); loop.test(child)
        intents = loop.engine.memory.intents(loop.engine.run_id)
        self.assertEqual(intents[-1]['parent_intent_id'], intents[0]['id'])
        self.assertIn('missing_evidence', loop.feedback()[0])

    def test_feedback_filters_raw_holdout_forward_and_arbitrary_metrics(self):
        loop = self.loop(); loop.add_batch([idea()]); loop.test(loop.schedule())
        tid = loop.ideas()[0]['trial_id']; result = loop.engine.memory.latest(tid)
        result.update(raw={'holdout': 'SENTINEL_SECRET'}, forward='SENTINEL_SECRET')
        result['metrics']['oos_sharpe'] = 'SENTINEL_SECRET'
        loop.engine.memory.result(tid, result)
        self.assertNotIn('SENTINEL_SECRET', json.dumps(loop.feedback()))

    def test_model_feedback_drives_next_batch_and_is_not_a_submission_tool(self):
        generator = BatchGenerator([[idea()], [idea('rank(ts_corr(close,volume,10))', family='liquidity')]])
        loop = self.loop(generator=generator)
        value = loop.run()
        self.assertTrue(generator.inputs[1])
        self.assertIn('metrics', generator.inputs[1][0])
        self.assertEqual(value['counts']['simulations'], 6)
        self.assertEqual(loop.engine.memory.db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 0)

    def test_generic_agent_and_continue_resume_cannot_expand_an_autonomous_plan(self):
        plan = self.plan(max_simulations=3)
        loop = self.loop(plan)
        reopened = Engine(self.config, loop.engine.run_id)
        self.addCleanup(reopened.memory.close)
        self.assertEqual(reopened.simulation_limit, 3)
        with self.assertRaises(ResearchError): reopened.propose('rank(close)', 'TEST', 'TEST')
        with self.assertRaises(ResearchError): reopened.resume(True, 15)

    def test_different_plan_cannot_reuse_same_study_id(self):
        self.loop()
        with self.assertRaises(ResearchError): preview_plan(self.config, self.plan(max_simulations=3))

    def test_switching_database_cannot_silently_reset_same_plan_budget(self):
        plan = self.plan()
        config = copy.deepcopy(self.config)
        config['database'] = str(Path(self.tmp.name)/'different.db')
        with self.assertRaises(ResearchError): validate_plan(config, plan)

    def test_empty_model_batch_keeps_existing_untried_ideas(self):
        generator = BatchGenerator([[idea(), idea('rank(ts_mean(volume,20))')], []])
        loop = self.loop(generator=generator)
        value = loop.run()
        self.assertEqual(value['counts']['simulations'], 6)
        self.assertEqual(len(loop.history('generation_finished')), 1)

    def test_previous_run_discovery_feedback_is_reused_but_raw_data_is_not(self):
        previous = Engine(self.config)
        self.addCleanup(previous.memory.close)
        previous.propose('rank(ts_mean(close,20))', 'TEST prior hypothesis', 'TEST prior')
        loop = self.loop()
        history = loop.historical_feedback()
        self.assertTrue(history)
        self.assertIn('TEST prior hypothesis', json.dumps(history))
        self.assertNotIn('daily_returns', json.dumps(history))

    def test_live_missing_confirmation_stops_before_engine_or_simulation(self):
        self.live(); plan = self.plan(); sim = FixtureSimulator()
        with self.assertRaises(ResearchError): execute_plan(self.config, plan, Path(self.tmp.name)/'review.json', sim)
        self.assertEqual(sim.starts, [])
        self.assertFalse(Path(self.config['database']).exists())

    def test_live_approved_invocation_consumes_at_most_its_cap_and_never_submits(self):
        self.live(); plan = self.plan(); sim = FixtureSimulator()
        sim.account_ref = digest('TEST_ONLY')
        estimate = preview_plan(self.config, plan)
        with patch('alpharesearch.probe.require_smoke_receipt'):
            value = execute_plan(self.config, plan, Path(self.tmp.name)/'review.json', sim, 3,
                                 estimate['preview_sha256'], generator=BatchGenerator([[idea()]]))
        self.assertEqual(value['counts']['simulations'], 3)
        self.assertEqual(len(sim.starts), 3)
        self.assertEqual(sim.submit_calls, 0)

    def test_cli_live_confirmation_is_checked_before_login(self):
        from alpharesearch.cli import main
        self.live(); plan = self.plan(); path = Path(self.tmp.name)/'plan.json'
        path.write_text(json.dumps(plan), encoding='utf-8')
        with patch('alpharesearch.cli.load_settings', return_value=self.config), patch('alpharesearch.cli.credentials') as credentials:
            with self.assertRaises(SystemExit): main(['autonomy-run', '--plan', str(path)])
        credentials.assert_not_called()
        self.assertFalse(Path(self.config['database']).exists())

    def test_stale_preview_rejected_after_checkpoint_change(self):
        plan = self.plan(); loop = self.loop(plan); estimate = preview_plan(self.config, plan)
        loop.record('state', {'status': 'paused', 'reason': 'TEST change'})
        with self.assertRaisesRegex(ResearchError, '预览'):
            execute_plan(self.config, plan, Path(self.tmp.name)/'review.json', maximum=3,
                         confirmation=estimate['preview_sha256'])

    def test_unfinished_simulation_stops_new_loop_and_get_recovery_does_not_create(self):
        class Interrupted(FixtureSimulator):
            broken = True
            def finish(self, location):
                if self.broken: raise PollTimeout('TEST interruption')
                return super().finish(location)
        self.live(); plan = self.plan(); sim = Interrupted()
        loop = self.loop(plan, sim); loop.add_batch([idea()])
        with self.assertRaises(PollTimeout): loop.test(loop.schedule())
        self.assertEqual(len(sim.starts), 1)
        with self.assertRaises(ResearchError): loop.run()
        sim.broken = False
        recovery = recover_plan(self.config, plan, Path(self.tmp.name)/'recovery.json', sim)
        self.assertEqual(recovery['new_simulations'], 0)
        self.assertEqual(len(sim.starts), 1)

    def test_controlled_pause_retains_state_and_can_resume(self):
        loop = self.loop(generator=BatchGenerator([[idea()], [idea('rank(ts_mean(volume,20))')]]))
        value = loop.run(steps=1)
        self.assertEqual(value['state']['reason'], 'step_limit')
        self.assertEqual(value['counts']['simulations'], 3)
        self.assertEqual(len(loop.history('cycle')), 1)

    def test_keyboard_interrupt_keeps_a_paused_checkpoint(self):
        class Interrupted:
            def generate(self, *args): raise KeyboardInterrupt()
        loop = self.loop(generator=Interrupted()); result = loop.run()
        self.assertEqual(result['state']['reason'], 'user_interrupt')
        self.assertEqual(len(loop.history('cycle')), 1)

    def test_real_model_adapter_accepts_only_one_structured_batch_and_charges_usage(self):
        self.config['llm']['model'] = 'TEST_MODEL'; loop = self.loop(self.plan(generator='llm'))
        data = {'choices': [{'message': {'tool_calls': [{'function': {'name': 'research_batch',
            'arguments': json.dumps({'ideas': [idea()]})}}]}}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}
        transport = ScriptedTransport([Response(200, {}, data)])
        with patch.dict(os.environ, {'LLM_API_KEY': 'TEST_ONLY'}):
            agent = DiscoveryAgent(loop.engine, ApiClient('https://model.test', transport=transport))
            result = agent.generate(loop.plan, [], 1)
        self.assertEqual(result, [idea()])
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['llm_calls'], 1)
        self.assertEqual(loop.engine.memory.cost_summary(loop.engine.run_id)['input_tokens'], 100)
        self.assertNotIn('submit', json.dumps(transport.calls[0][2]['tools']))

    def test_malicious_model_tool_is_rejected_without_any_simulation(self):
        self.config['llm']['model'] = 'TEST_MODEL'; loop = self.loop(self.plan(generator='llm'))
        data = {'choices': [{'message': {'tool_calls': [{'function': {'name': 'submit', 'arguments': '{}'}}]}}]}
        with patch.dict(os.environ, {'LLM_API_KEY': 'TEST_ONLY'}):
            agent = DiscoveryAgent(loop.engine, ApiClient('https://model.test', transport=ScriptedTransport([Response(200, {}, data)])))
            with self.assertRaises(ResearchError): agent.generate(loop.plan, [], 1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)

    def test_model_failure_consumes_call_and_does_not_spin(self):
        self.config['llm']['model'] = 'TEST_MODEL'; loop = self.loop(self.plan(generator='llm'))
        with patch.dict(os.environ, {'LLM_API_KEY': 'TEST_ONLY'}):
            agent = DiscoveryAgent(loop.engine, ApiClient('https://model.test', transport=ScriptedTransport([ApiError('TEST offline')])))
            loop.generator = agent
            with self.assertRaises(ApiError): loop.run()
        self.assertEqual(len(loop.history('cycle')), 1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['llm_calls'], 1)
        self.assertEqual(loop.engine.memory.run(loop.engine.run_id)['simulations'], 0)
