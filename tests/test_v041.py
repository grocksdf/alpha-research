"""Synthetic queue/recordset/preview regressions; no live credentials or HTTP."""
import copy
import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_system import Base,FixtureSimulator,fixture_result
from alpharesearch.brain import records
from alpharesearch.cli import main
from alpharesearch.common import ResearchError,digest,snapshot
from alpharesearch.directed import create_plan,execute_plan,load_plan
from alpharesearch.http import ApiError
from alpharesearch.memory import Memory
from alpharesearch.preview import preview,confirm,research_candidates
from alpharesearch.validator import Validator
from alpharesearch.engine import Engine
import test_v04


class SerialClaimTests(Base):
    def setUp(self):
        super().setUp();self.live()
        self.memory=Memory(self.config['database']);self.addCleanup(self.memory.close)
        self.account=digest('TEST_ONLY');self.base=self.config['brain']['base_url']

    def task(self,expression='rank(close)',context='scope-A',run_id=None,account='TEST_ONLY'):
        snap=snapshot(self.config);snap['catalog']=copy.deepcopy(snap['catalog']);snap['catalog']['account_id']=account
        run_id=run_id or self.memory.create_run(snap)
        valid=Validator(self.config['catalog_data'],self.config['scope'],self.config['rules']).validate(expression)
        tid,_=self.memory.add_trial(run_id,{'expression':expression,'hypothesis':'TEST','source':'fixture'},valid,context)
        return run_id,tid

    def claim(self,tid,memory=None,account=None):
        return (memory or self.memory).claim(tid,15,account or self.account,self.base)

    def test_polling_blocks_same_run_without_consuming_second_budget(self):
        run,a=self.task();_,b=self.task('rank(volume)',run_id=run)
        self.claim(a);self.memory.set_state(a,'polling',location='/simulations/test')
        with self.assertRaises(ResearchError):self.claim(b)
        self.assertEqual(self.memory.run(run)['simulations'],1)
        self.assertEqual(self.memory.trial(b)['state'],'queued')

    def test_cross_run_and_different_context_still_share_account_slot(self):
        _,a=self.task();run,b=self.task('rank(volume)',context='different-scope')
        self.claim(a);self.memory.set_state(a,'polling',location='/simulations/test')
        second=Memory(self.config['database'])
        try:
            with self.assertRaises(ResearchError):self.claim(b,second)
        finally:second.close()
        self.assertEqual(self.memory.run(run)['simulations'],0)

    def test_paused_unknown_and_failed_unknown_keep_remote_slot(self):
        _,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        for state in ('paused','failed','abandoned'):
            self.memory.set_state(a,state)
            with self.assertRaises(ResearchError):self.claim(b)

    def test_alpha_binding_alone_does_not_resolve_paused_remote_task(self):
        _,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        self.memory.bind_alpha(a,'alpha123');self.memory.set_state(a,'paused')
        with self.assertRaises(ResearchError):self.claim(b)
        value=fixture_result('rank(close)',self.config['scope'])
        self.memory.confirm_remote_completed(a,value)
        self.assertEqual(self.memory.trial(a)['state'],'paused')
        self.assertEqual(self.memory.trial(a)['remote_state'],'completed')
        self.assertTrue(self.claim(b))

    def test_mismatched_completion_receipt_does_not_release_slot(self):
        _,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        self.memory.bind_alpha(a,'alpha123');self.memory.set_state(a,'paused')
        value=fixture_result('rank(open)',self.config['scope'])
        with self.assertRaises(ResearchError):self.memory.confirm_remote_completed(a,value)
        with self.assertRaises(ResearchError):self.claim(b)

    def test_local_result_missing_core_metrics_does_not_prove_remote_completion(self):
        _,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        value=fixture_result('rank(close)',self.config['scope']);value['metrics']['fitness']=None
        self.memory.result(a,value)
        self.assertEqual(self.memory.trial(a)['remote_state'],'unresolved')
        with self.assertRaises(ResearchError):self.claim(b)
        value['metrics']['fitness']=1.4;self.memory.result(a,value)
        self.assertTrue(self.claim(b))

    def test_explicit_creation_rejection_releases_remote_slot(self):
        _,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        self.memory.set_state(a,'failed',error='HTTP 422',remote_state='not_created')
        self.assertTrue(self.claim(b))

    def test_live_identity_cannot_be_omitted_to_bypass_slot(self):
        run,a=self.task()
        with self.assertRaises(ResearchError):self.memory.claim(a,15)
        self.assertEqual(self.memory.run(run)['simulations'],0)

    def test_caller_cannot_change_api_origin_to_bypass_original_snapshot(self):
        run,a=self.task()
        with self.assertRaises(ResearchError):self.memory.claim(a,15,self.account,'https://different.example')
        self.assertEqual(self.memory.run(run)['simulations'],0)

    def test_distinct_accounts_have_distinct_slots(self):
        _,a=self.task();_,b=self.task('rank(volume)',context='OTHER',account='OTHER')
        self.claim(a);self.memory.set_state(a,'polling')
        self.assertTrue(self.claim(b,account=digest('OTHER')))

    def test_legacy_missing_task_identity_uses_original_snapshot(self):
        _,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        self.memory.set_state(a,'polling')
        self.memory.db.execute('UPDATE tasks SET account_ref=NULL,api_base_url=NULL WHERE trial_id=?',(a,))
        with self.assertRaises(ResearchError):self.claim(b)

    def test_concurrent_cross_run_claims_grant_exactly_one_slot(self):
        _,a=self.task();_,b=self.task('rank(volume)',context='other-scope')
        barrier=threading.Barrier(2)
        def work(tid):
            memory=Memory(self.config['database'])
            try:
                barrier.wait(timeout=10)
                try:return self.claim(tid,memory)
                except ResearchError:return False
            finally:memory.close()
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(work,[a,b]))
        self.assertEqual(sum(outcomes),1)
        self.assertEqual(sorted(self.memory.trial(t)['state'] for t in (a,b)),['dispatching','queued'])

    def test_v04_task_migration_is_conservative_idempotent_and_preserves_history(self):
        run,a=self.task();_,b=self.task('rank(volume)');self.claim(a)
        self.memory.bind_alpha(a,'alpha123');self.memory.set_state(a,'paused')
        self.memory.migrate_intents()
        before=self.memory.run(run);audit=self.memory.verify();self.memory.close()
        # Reproduce the v0.4.0 tasks schema exactly, retaining every old field.
        db=sqlite3.connect(self.config['database'])
        db.execute('ALTER TABLE tasks DROP COLUMN remote_state');db.commit();db.close()
        self.memory=Memory(self.config['database'])
        self.addCleanup(self.memory.close)
        self.assertEqual(self.memory.trial(a)['remote_state'],'unresolved')
        self.assertEqual(self.memory.trial(b)['remote_state'],'not_started')
        self.assertEqual(self.memory.run(run),before);self.assertEqual(self.memory.verify(),audit)
        with self.assertRaises(ResearchError):self.claim(b)
        again=Memory(self.config['database'])
        try:self.assertEqual(again.trial(a),self.memory.trial(a))
        finally:again.close()

    def test_migration_does_not_trust_completed_label_with_incomplete_live_metrics(self):
        _,a=self.task();self.claim(a)
        value=fixture_result('rank(close)',self.config['scope']);value['metrics']['fitness']=None
        self.memory.result(a,value);self.memory.migrate_intents();self.memory.close()
        db=sqlite3.connect(self.config['database']);db.execute('ALTER TABLE tasks DROP COLUMN remote_state');db.commit();db.close()
        self.memory=Memory(self.config['database']);self.addCleanup(self.memory.close)
        self.assertEqual(self.memory.trial(a)['state'],'completed')
        self.assertEqual(self.memory.trial(a)['remote_state'],'unresolved')


class StrictRecordsetTests(Base):
    def test_duplicate_pnl_columns_never_silently_overwrite(self):
        value={'schema':{'properties':[{'name':'date'},{'name':'pnl'},{'name':'pnl'}]},
            'records':[['2020-01-01',1.0,999.0]]}
        with self.assertRaisesRegex(ApiError,'重复'):records(value)

    def test_empty_records_and_named_records_cannot_bypass_invalid_schema(self):
        for rows in ([],[{'date':'2020-01-01','pnl':1.0}]):
            for props in ([{'name':''}],[{'name':' '}],[{'name':' pnl'}],[{}],[{'name':42}],[],None,
                          [{'name':'pnl'},{'name':'pnl'}]):
                with self.subTest(rows=rows,props=props):
                    with self.assertRaises(ApiError):records({'schema':{'properties':props},'records':rows})

    def test_valid_positional_and_named_schemas_agree(self):
        schema={'properties':[{'name':'date'},{'name':'pnl'}]}
        expected=[{'date':'2020-01-01','pnl':1.0}]
        self.assertEqual(records({'schema':schema,'records':[['2020-01-01',1.0]]}),expected)
        self.assertEqual(records({'schema':schema,'records':expected}),expected)
        self.assertEqual(records({'records':expected}),expected)

    def test_invalid_shape_mixed_rows_and_column_mismatch_are_rejected(self):
        for value in ({'schema':None,'records':[]},{'records':[['2020-01-01',1.0]]},
                      {'schema':{'properties':[{'name':'pnl'}]},'records':[[1,2]]},
                      {'schema':{'properties':[{'name':'pnl'}]},'records':[{'other':1}]},
                      {'records':[{'':1}]},{'records':[{'pnl':1},[2]]}):
            with self.assertRaises(ApiError):records(value)


class PreviewTests(Base):
    def plan(self):
        path=Path(self.tmp.name)/'plan.json';candidates=Path(self.tmp.name)/'candidates.json'
        value=create_plan(self.config,'v041-fixed',path,candidates)
        return path,value

    def test_offline_preview_does_not_create_database_or_call_network(self):
        candidates=research_candidates(self.config,'baseline')
        with patch('alpharesearch.http.JsonTransport.request',side_effect=AssertionError('HTTP')) as request:
            value=preview(self.config,candidates)
        self.assertEqual(value['new_simulations_needed'],15);self.assertEqual(value['new_simulations_upper_bound'],15)
        self.assertFalse(Path(self.config['database']).exists());request.assert_not_called()

    def test_live_cli_dry_run_does_not_prompt_credentials_or_create_state(self):
        self.live();buffer=io.StringIO()
        with (patch('alpharesearch.cli.load_settings',return_value=self.config),patch('alpharesearch.cli.credentials') as credentials,
              patch('alpharesearch.http.JsonTransport.request') as request,redirect_stdout(buffer)):
            main(['run','--agent','baseline','--dry-run'])
        value=json.loads(buffer.getvalue());self.assertEqual(value['network_requests'],0)
        self.assertFalse(Path(self.config['database']).exists());credentials.assert_not_called();request.assert_not_called()

    def test_missing_live_confirmation_fails_before_login(self):
        self.live();buffer=io.StringIO()
        with patch('alpharesearch.cli.load_settings',return_value=self.config),patch('alpharesearch.cli.credentials') as credentials,redirect_stdout(buffer):
            with self.assertRaises(SystemExit):main(['run','--agent','baseline'])
        credentials.assert_not_called();self.assertIn('--dry-run',buffer.getvalue())
        self.assertFalse(Path(self.config['database']).exists())

    def test_llm_preview_reports_only_upper_bound_and_does_not_call_model(self):
        value=preview(self.config,None)
        self.assertIsNone(value['new_simulations_needed']);self.assertEqual(value['new_simulations_upper_bound'],15)
        self.assertEqual(value['estimate_kind'],'unknown_llm_candidates_budget_upper_bound')

    def test_live_directed_cap_is_enforced_and_next_preview_accounts_for_spend(self):
        self.live();path,plan=self.plan();value=preview(self.config,plan['candidates'],plan=plan)
        sim=FixtureSimulator();output=Path(self.tmp.name)/'report.json'
        with self.assertRaises(ResearchError):execute_plan(self.config,path,output,sim)
        self.assertEqual(sim.starts,[])
        first=execute_plan(self.config,path,output,sim,2,value['preview_sha256'])
        self.assertEqual(first['counts']['simulations'],2);self.assertFalse(first['plan_completed'])
        new=preview(self.config,plan['candidates'],plan=plan)
        self.assertEqual(new['new_simulations_needed'],4);self.assertEqual(new['remaining_budget'],4)
        with self.assertRaises(ResearchError):execute_plan(self.config,path,output,sim,4,value['preview_sha256'])
        self.assertEqual(len(sim.starts),2)
        final=execute_plan(self.config,path,output,sim,4,new['preview_sha256'])
        self.assertEqual(final['counts']['simulations'],6);self.assertTrue(final['plan_completed'])
        self.assertEqual(len(sim.starts),6)

    def test_preview_reuse_and_zero_new_budget_are_explicit(self):
        path,plan=self.plan();sim=FixtureSimulator();output=Path(self.tmp.name)/'report.json'
        execute_plan(self.config,path,output,sim)
        value=preview(self.config,plan['candidates'],plan=plan)
        self.assertEqual(value['new_simulations_upper_bound'],0)
        self.assertTrue(all(r['action']=='reuse' for r in value['items']))
        again=execute_plan(self.config,path,output,sim,0)
        self.assertTrue(again['plan_completed']);self.assertEqual(len(sim.starts),6)

    def test_preview_reads_database_without_migrations_and_unknown_task_blocks_live_creation(self):
        self.live();memory=Memory(self.config['database'])
        try:
            run=memory.create_run(snapshot(self.config));validator=Validator(self.config['catalog_data'],self.config['scope'],self.config['rules'])
            expression='rank(close)';tid,_=memory.add_trial(run,{'expression':expression,'hypothesis':'TEST','source':'fixture'},validator.validate(expression),'another-context')
            memory.claim(tid,15,digest('TEST_ONLY'),self.config['brain']['base_url']);memory.set_state(tid,'polling')
            before=memory.db.total_changes
            value=preview(self.config,research_candidates(self.config,'baseline'))
            self.assertTrue(value['creation_blocked']);self.assertEqual(value['remote_blockers'][0]['trial_id'],tid)
            with self.assertRaises(ResearchError):confirm(self.config,value,15,value['preview_sha256'])
            self.assertEqual(memory.db.total_changes,before)
        finally:memory.close()

    def test_old_schema_preview_requires_explicit_offline_migration(self):
        self.live();memory=Memory(self.config['database']);memory.close()
        db=sqlite3.connect(self.config['database']);db.execute('ALTER TABLE tasks DROP COLUMN remote_state');db.commit();db.close()
        value=preview(self.config,research_candidates(self.config,'baseline'))
        self.assertTrue(value['schema_migration_required'])
        with self.assertRaisesRegex(ResearchError,'migrate-state'):confirm(self.config,value,15,value['preview_sha256'])
        db=sqlite3.connect(self.config['database'])
        try:self.assertNotIn('remote_state',{r[1] for r in db.execute('PRAGMA table_info(tasks)')})
        finally:db.close()


class RemoteResolutionTests(Base):
    simulator=test_v04.RecoveryTests.simulator

    def test_failed_local_marker_with_unresolved_remote_can_still_get_recovered(self):
        from alpharesearch.http import AmbiguousWrite
        self.live();sim,http=self.simulator();http.ambiguous=True
        engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        with self.assertRaises(AmbiguousWrite):engine.propose('rank(close)','TEST','fixture')
        task=engine.memory.trials(engine.run_id)[0]
        engine.memory.set_state(task['id'],'failed')
        outcome=engine.resume()
        self.assertEqual(outcome[0]['state'],'completed')
        self.assertEqual(engine.memory.trial(task['id'])['remote_state'],'completed')
        self.assertEqual(sum(m=='POST' for m,_,_ in http.calls),1)
