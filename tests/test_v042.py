from fixtures.legacy_engine_auto_variants import enable_legacy_historical_fixture
"""Resume approval and corrupt-history regressions; all simulations are fixtures."""
import copy
import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_system import Base,FixtureSimulator,fixture_result
from alpharesearch.cli import main
from alpharesearch.common import ResearchError,snapshot
from fixtures.legacy_directed import create_plan,execute_plan
from alpharesearch.engine import Engine
from alpharesearch.http import AmbiguousWrite
from alpharesearch.memory import Memory,remote_result_complete
from alpharesearch.preview import preview_resume
from alpharesearch.validator import Validator


class MalformedHistoryTests(Base):
    def test_null_and_wrong_metrics_types_are_unresolved_without_attribute_error(self):
        self.live();trial={'expression':'rank(close)'};snap=snapshot(self.config)
        for metrics in (None,[],[1],1,True,'invalid',{}, {'sharpe':1,'fitness':None,'turnover':.1},
                        {'sharpe':[],'fitness':1,'turnover':.1}):
            with self.subTest(metrics=metrics):
                value=fixture_result('rank(close)',self.config['scope']);value['metrics']=metrics
                self.assertFalse(remote_result_complete(trial,value,snap))

    def test_invalid_outer_json_types_are_unresolved(self):
        self.live();value=fixture_result('rank(close)',self.config['scope']);snap=snapshot(self.config)
        for invalid in (None,[],1,'invalid',False):
            self.assertFalse(remote_result_complete(invalid,value,snap))
            self.assertFalse(remote_result_complete({'expression':'rank(close)'},invalid,snap))
            self.assertFalse(remote_result_complete({'expression':'rank(close)'},value,invalid))

    def test_valid_zero_metric_is_completion_evidence_not_a_performance_pass(self):
        self.live();value=fixture_result('rank(close)',self.config['scope'])
        value['metrics']={'sharpe':0.,'fitness':0.,'turnover':0.}
        self.assertTrue(remote_result_complete({'expression':'rank(close)'},value,snapshot(self.config)))

    def test_invalid_completion_confirmation_does_not_release_remote_slot(self):
        self.live();engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        valid=engine.validator.validate('rank(close)')
        tid,_=engine.memory.add_trial(engine.run_id,{'expression':valid.expression,'hypothesis':'TEST','source':'fixture'},valid,engine.context)
        engine.memory.bind_alpha(tid,'alpha123');engine.memory.set_state(tid,'paused')
        for metrics in (None,[],1,'bad'):
            value=fixture_result('rank(close)',self.config['scope']);value['metrics']=metrics
            with self.assertRaises(ResearchError):engine.memory.confirm_remote_completed(tid,value)
            self.assertEqual(engine.memory.trial(tid)['remote_state'],'unresolved')

    def test_migration_isolates_bad_rows_and_keeps_valid_row_budget_and_history(self):
        self.live();memory=Memory(self.config['database'])
        validator=Validator(self.config['catalog_data'],self.config['scope'],self.config['rules'])
        run=memory.create_run(snapshot(self.config));cases=[]
        expressions=['rank(close)','rank(volume)','rank(vwap)','rank(returns)','-rank(close)','-rank(volume)','-rank(vwap)']
        bad_values=[None,[],42,'invalid',False,{'mode':'live','metrics':None}]
        try:
            for index,expression in enumerate(expressions):
                valid=validator.validate(expression)
                tid,_=memory.add_trial(run,{'expression':expression,'hypothesis':'TEST','source':'fixture'},valid,'TEST-history')
                value=bad_values[index] if index<len(bad_values) else fixture_result(expression,self.config['scope'])
                memory.result(tid,value);cases.append((tid,copy.deepcopy(value)))
            memory.db.execute('UPDATE runs SET simulations=? WHERE id=?',(len(cases),run))
            memory.migrate_intents();before=memory.run(run);audit=memory.verify()
        finally:memory.close()
        db=sqlite3.connect(self.config['database']);db.execute('ALTER TABLE tasks DROP COLUMN remote_state');db.commit();db.close()
        memory=Memory(self.config['database'])
        try:
            self.assertEqual(memory.run(run),before);self.assertEqual(memory.verify(),audit)
            for index,(tid,value) in enumerate(cases):
                self.assertEqual(memory.latest(tid),value)
                self.assertEqual(memory.trial(tid)['remote_state'],'unresolved' if index<len(bad_values) else 'completed')
        finally:memory.close()
        again=Memory(self.config['database'])
        try:self.assertEqual(again.run(run),before)
        finally:again.close()


class ResumeApprovalTests(Base):
    def setUp(self):
        super().setUp()
        enable_legacy_historical_fixture(self)

    def engine(self,live=True):
        if live:self.live()
        simulator=FixtureSimulator();engine=Engine(self.config,simulator=simulator)
        self.addCleanup(engine.memory.close)
        return engine,simulator

    def queue(self,engine,expression='rank(close)'):
        valid=engine.validator.validate(expression)
        tid,_=engine.memory.add_trial(engine.run_id,{'expression':expression,'hypothesis':'TEST','source':'fixture'},valid,engine.context)
        engine.memory.add_intent(engine.run_id,tid,'TEST','fixture')
        return tid

    def test_live_resume_requires_preview_confirmation_before_any_create(self):
        engine,sim=self.engine();self.queue(engine)
        with self.assertRaisesRegex(ResearchError,'--dry-run'):engine.resume(True)
        self.assertEqual(sim.starts,[]);self.assertEqual(engine.memory.run(engine.run_id)['simulations'],0)

    def test_cli_missing_confirmation_is_rejected_before_credentials(self):
        engine,sim=self.engine();self.queue(engine);buffer=io.StringIO()
        with (patch('alpharesearch.cli.load_settings',return_value=self.config),
              patch('alpharesearch.cli.credentials') as credentials,redirect_stdout(buffer)):
            with self.assertRaises(SystemExit):main(['resume',engine.run_id,'--continue-research'])
        credentials.assert_not_called();self.assertEqual(sim.starts,[])
        self.assertIn('--dry-run',buffer.getvalue())

    def test_resume_dry_run_has_no_network_or_database_mutations(self):
        engine,sim=self.engine();self.queue(engine)
        before=engine.memory.db.total_changes;audit=engine.memory.verify();buffer=io.StringIO()
        with (patch('alpharesearch.cli.load_settings',return_value=self.config),
              patch('alpharesearch.cli.credentials') as credentials,
              patch('alpharesearch.cli.Engine',side_effect=AssertionError('dry-run initialized Engine')),
              patch('alpharesearch.http.JsonTransport.request') as request,redirect_stdout(buffer)):
            main(['resume',engine.run_id,'--continue-research','--dry-run'])
        value=json.loads(buffer.getvalue());self.assertEqual(value['new_simulations_needed'],1)
        self.assertEqual(value['network_requests'],0);self.assertFalse(value['database_mutated'])
        self.assertEqual(engine.memory.db.total_changes,before);self.assertEqual(engine.memory.verify(),audit)
        credentials.assert_not_called();request.assert_not_called();self.assertEqual(sim.starts,[])

    def test_plain_resume_stays_get_only_and_dry_run_estimates_zero_creates(self):
        engine,sim=self.engine();tid=self.queue(engine)
        value=preview_resume(self.config,engine.run_id,False)
        self.assertEqual(value['new_simulations_upper_bound'],0)
        self.assertEqual(engine.resume(),[]);self.assertEqual(sim.starts,[])
        self.assertEqual(engine.memory.trial(tid)['state'],'queued')
        with self.assertRaises(ResearchError):engine.resume(False,0)

    def test_plain_resume_never_posts_even_if_queued_remote_marker_is_corrupt(self):
        engine,sim=self.engine();tid=self.queue(engine)
        engine.memory.db.execute("UPDATE tasks SET remote_state='unresolved' WHERE trial_id=?",(tid,))
        self.assertEqual(engine.resume(),[])
        self.assertEqual(sim.starts,[]);self.assertEqual(engine.memory.run(engine.run_id)['simulations'],0)

    def test_cap_applies_to_new_calls_not_total_historical_spend(self):
        engine,sim=self.engine()
        for expression in ('rank(close)','rank(volume)','rank(vwap)'):self.queue(engine,expression)
        for expected in (1,2):
            value=preview_resume(self.config,engine.run_id)
            engine.resume(True,1,value['preview_sha256'])
            self.assertEqual(engine.memory.run(engine.run_id)['simulations'],expected)
        self.assertEqual(len(sim.starts),2)
        self.assertEqual(sum(t['state']=='queued' for t in engine.memory.trials(engine.run_id)),1)
        self.assertEqual(engine.simulation_limit,self.config['rules']['max_simulations_per_run'])

    def test_zero_cap_keeps_queue_without_spending_budget(self):
        engine,sim=self.engine();self.queue(engine)
        value=preview_resume(self.config,engine.run_id)
        result=engine.resume(True,0,value['preview_sha256'])
        self.assertEqual(result[0]['reason'],'invocation_budget_exhausted')
        self.assertEqual(sim.starts,[]);self.assertEqual(engine.memory.run(engine.run_id)['simulations'],0)

    def test_legacy_resume_preview_does_not_generate_implicit_variants(self):
        engine,sim=self.engine();self.queue(engine,'ts_mean(close,20)')
        value=preview_resume(self.config,engine.run_id)
        self.assertEqual(value['new_simulations_needed'],1)
        engine.resume(True,2,value['preview_sha256'])
        self.assertEqual(len(sim.starts),1)
        later=preview_resume(self.config,engine.run_id)
        self.assertEqual(later['new_simulations_needed'],0)
        self.assertEqual(len(sim.starts),1)

    def test_stale_task_state_invalidates_confirmation(self):
        engine,sim=self.engine();a=self.queue(engine);self.queue(engine,'rank(volume)')
        value=preview_resume(self.config,engine.run_id);engine.execute(a)
        with self.assertRaisesRegex(ResearchError,'预览已过期'):engine.resume(True,1,value['preview_sha256'])
        self.assertEqual(len(sim.starts),1)

    def test_authentication_time_state_change_is_rechecked_before_new_post(self):
        engine,sim=self.engine();a=self.queue(engine);self.queue(engine,'rank(volume)')
        value=preview_resume(self.config,engine.run_id)
        def login():
            engine.execute(a)
            return {'user_id':'TEST_ONLY'}
        sim.login=login
        with (patch('alpharesearch.cli.load_settings',return_value=self.config),
              patch('alpharesearch.cli.credentials'),patch('alpharesearch.cli.BrainSimulator',return_value=sim),
              redirect_stdout(io.StringIO())):
            with self.assertRaises(SystemExit):main(['resume',engine.run_id,'--continue-research',
                '--confirm-preview',value['preview_sha256'],'--max-new-simulations','1'])
        self.assertEqual(len(sim.starts),1)

    def test_cached_candidate_from_other_run_does_not_implicitly_generate_variants(self):
        engine,sim=self.engine();tid=self.queue(engine,'ts_mean(close,20)');engine.execute(tid)
        later=Engine(self.config,simulator=sim);self.addCleanup(later.memory.close)
        self.queue(later,'ts_mean(close,20)')
        value=preview_resume(self.config,later.run_id)
        self.assertEqual(value['new_simulations_needed'],0)
        self.assertEqual(value['new_simulations_upper_bound'],0)
        self.assertEqual(len(sim.starts),1)

    def test_uncertain_create_consumes_cap_and_stops_next_creation(self):
        class Lost(FixtureSimulator):
            def start(self,expression,scope):
                self.starts.append(expression);raise AmbiguousWrite('TEST lost response')
        engine,_=self.engine();sim=Lost();engine.simulator=sim
        self.queue(engine);self.queue(engine,'rank(volume)')
        value=preview_resume(self.config,engine.run_id)
        with self.assertRaises(AmbiguousWrite):engine.resume(True,2,value['preview_sha256'])
        self.assertEqual(len(sim.starts),1);self.assertEqual(engine.memory.run(engine.run_id)['simulations'],1)
        current=preview_resume(self.config,engine.run_id)
        self.assertTrue(current['creation_blocked'])
        with self.assertRaises(ResearchError):engine.resume(True,1,current['preview_sha256'])
        self.assertEqual(len(sim.starts),1)

    def test_directed_resume_does_not_expand_the_frozen_design(self):
        path=Path(self.tmp.name)/'directed.json';candidates=Path(self.tmp.name)/'candidates.json'
        create_plan(self.config,'v042-design',path,candidates);sim=FixtureSimulator()
        result=execute_plan(self.config,path,Path(self.tmp.name)/'review.json',sim,2)
        engine=Engine(self.config,result['run_id'],sim);self.addCleanup(engine.memory.close)
        value=preview_resume(self.config,engine.run_id)
        self.assertEqual(value['simulation_budget'],6);self.assertEqual(value['new_simulations_needed'],1)
        engine.resume(True,1,value['preview_sha256'])
        self.assertEqual(len(sim.starts),3)
        after=preview_resume(self.config,engine.run_id)
        self.assertEqual(after['new_simulations_needed'],0)
        engine.resume(True,6,after['preview_sha256'])
        self.assertEqual(len(sim.starts),3)

    def test_rejected_formula_does_not_abort_resume_preview(self):
        engine,sim=self.engine();engine.propose('ts_delay(close,-1)','TEST invalid','fixture')
        self.queue(engine,'rank(volume)')
        value=preview_resume(self.config,engine.run_id)
        self.assertEqual(value['new_simulations_needed'],1)
        self.assertTrue(any(i['action']=='skip_rejected' for i in value['items']))
        engine.resume(True,1,value['preview_sha256']);self.assertEqual(len(sim.starts),1)

    def test_preview_of_missing_run_does_not_create_database(self):
        with self.assertRaises(ResearchError):preview_resume(self.config,'missing-run')
        self.assertFalse(Path(self.config['database']).exists())
