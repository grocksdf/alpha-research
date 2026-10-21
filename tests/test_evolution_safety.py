import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_system import Base,FixtureSimulator,fixture_result
from alpharesearch.cli import main
from alpharesearch.common import ResearchError,digest
from alpharesearch.engine import Engine
from alpharesearch.evolution import create_plan,preview_plan,validate_plan
from alpharesearch.return_contract import EvidenceError


def evolution_spec():
    return {'study_id':'TEST_EVOLUTION','sample_role':'research_is',
        'families':[{'family_id':'normalized_reversal','dimension':'window','baseline_value':5,'values':[5,10,20],
                     'template':'-rank(ts_mean(returns, {window}))','expected_direction':'TEST reduce turnover',
                     'counter_hypothesis':'TEST slower signal loses information'}],
        'independent_validation':{'mode':'separate_frozen_validation','manifest_sha256':None,'feedback_to_agent':False}}


class EvolutionSafetyTests(Base):
    def test_plan_and_preview_default_to_zero_without_creating_database(self):
        value=create_plan(self.config,evolution_spec());preview=preview_plan(self.config,value)
        self.assertEqual(value['max_simulations'],0);self.assertFalse(value['execution_enabled'])
        self.assertEqual(preview['new_simulations_upper_bound'],0)
        self.assertEqual(preview['new_simulations_needed'],3);self.assertFalse(Path(self.config['database']).exists())

    def test_each_variant_records_one_parameter_from_baseline(self):
        value=create_plan(self.config,evolution_spec())
        self.assertEqual(value['candidates'][0]['change'],{})
        self.assertEqual(value['candidates'][1]['change'],{'window':{'before':5,'after':10}})
        self.assertEqual(value['candidates'][2]['baseline_expression'],'-rank(ts_mean(returns, 5))')
        self.assertFalse(value['automatic_sensitivity'])

    def test_two_dimensions_or_posthoc_parameter_changes_are_rejected(self):
        spec=evolution_spec();spec['families'][0]['template']='ts_mean(ts_mean(returns,{a}),{b})'
        with self.assertRaises(EvidenceError):create_plan(self.config,spec)
        plan=create_plan(self.config,evolution_spec());plan['candidates'][0]['parameter']=99
        with self.assertRaises(EvidenceError):validate_plan(self.config,plan)
        plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
        with self.assertRaises(EvidenceError):validate_plan(self.config,plan)

    def test_planned_budget_cannot_raise_limits_or_enable_execution(self):
        for limit in (-1,5,True):
            spec=evolution_spec();spec['max_new_simulations']=limit
            with self.assertRaises(EvidenceError):create_plan(self.config,spec)
        spec=evolution_spec();spec['max_new_simulations']=4;plan=create_plan(self.config,spec)
        preview=preview_plan(self.config,plan)
        self.assertEqual(preview['planned_new_simulations_upper_bound'],3)
        self.assertEqual(preview['new_simulations_upper_bound'],0)
        plan['execution_enabled']=True;plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
        with self.assertRaises(EvidenceError):validate_plan(self.config,plan)

    def test_cross_run_polling_blocker_is_visible_and_preserved(self):
        self.live();engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        valid=engine.validator.validate('rank(close)')
        tid,_=engine.memory.add_trial(engine.run_id,{'expression':valid.expression,'hypothesis':'TEST','source':'TEST'},valid,engine.context)
        engine.memory.claim(tid,15,digest(str(self.config['catalog_data']['account_id'])),self.config['brain']['base_url'])
        engine.memory.set_state(tid,'polling',location='https://api.worldquantbrain.com/simulations/TEST')
        before=engine.memory.run(engine.run_id)
        spec=evolution_spec();spec['max_new_simulations']=4
        value=preview_plan(self.config,create_plan(self.config,spec))
        self.assertTrue(value['creation_blocked']);self.assertTrue(value['remote_blockers'])
        self.assertEqual(value['new_simulations_upper_bound'],0)
        self.assertEqual(engine.memory.run(engine.run_id),before)

    def test_completed_alpha_results_reused_in_preview_without_additional_sensitivity(self):
        engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        spec=evolution_spec();spec['max_new_simulations']=4
        candidate=create_plan(self.config,spec)['candidates'][0]
        v=engine.validator.validate(candidate['expression'])
        tid,_=engine.memory.add_trial(engine.run_id,candidate,v,engine.context);engine.execute(tid)
        before=engine.memory.run(engine.run_id);report=preview_plan(self.config,create_plan(self.config,spec))
        self.assertEqual(sum(i['action']=='reuse' for i in report['items']),1)
        self.assertEqual(report['new_simulations_needed'],2)
        self.assertEqual(engine.memory.run(engine.run_id),before)

    def test_final_holdout_cannot_be_bound_as_evolution_input(self):
        spec=evolution_spec();spec['sample_role']='final_holdout'
        with self.assertRaises(EvidenceError):create_plan(self.config,spec)
        spec=evolution_spec();spec['independent_validation']['feedback_to_agent']=True
        with self.assertRaises(EvidenceError):create_plan(self.config,spec)

    def test_cli_plan_preview_no_login_engine_worker_or_network(self):
        folder=Path(self.tmp.name);spec=folder/'spec.json';plan=folder/'plan.json'
        spec.write_text(json.dumps(evolution_spec()),encoding='utf-8')
        with (patch('alpharesearch.cli.load_settings',return_value=self.config),
              patch('alpharesearch.cli.credentials',side_effect=AssertionError('credentials')),
              patch('alpharesearch.cli.Engine',side_effect=AssertionError('Engine')),
              patch('alpharesearch.cli.worker_lock',side_effect=AssertionError('worker lock')),
              patch('alpharesearch.http.JsonTransport.request',side_effect=AssertionError('HTTP')) as request,
              redirect_stdout(io.StringIO())):
            main(['evolution-plan','--spec',str(spec),'--output',str(plan)])
            main(['evolution-preview','--plan',str(plan)])
        request.assert_not_called();self.assertFalse(Path(self.config['database']).exists())

    def test_original_budget_unknown_post_and_serial_regressions_still_use_existing_engine(self):
        # The new planner deliberately has no separate create implementation.
        import alpharesearch.evolution as evolution
        self.assertFalse(hasattr(evolution,'execute_plan'));self.assertFalse(hasattr(evolution,'submit'))
