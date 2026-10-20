"""Recovery/evidence regressions. All endpoints and prices here are synthetic."""
import copy
import csv
import json
import math
from datetime import date,timedelta
from pathlib import Path
from urllib.parse import urlsplit,parse_qs

from test_system import Base,fixture_result,FixtureSimulator
from alpharesearch.brain import BrainSimulator,correlation,normalize_checks
from alpharesearch.checks import summarize_checks
from alpharesearch.common import ResearchError,digest,now,snapshot
from alpharesearch.directed import create_plan,execute_plan,fixed_candidates
from alpharesearch.engine import Engine
from alpharesearch.evaluator import evaluate
from alpharesearch.evidence_audit import audit_existing_alphas,observe_pnl
from alpharesearch.http import ApiClient,ApiError,AmbiguousWrite,Response
from alpharesearch.memory import Memory
from alpharesearch.pnl_evidence import daily_evidence_valid,stability_windows
from alpharesearch.probe import one_simulation_probe,resume_simulation_probe
from alpharesearch.reconciliation import recover_alpha,settings_match,expression_matches,recover_saved_trial
from alpharesearch import local_validation as local


class AccountFixture:
    def __init__(self,scope):
        self.scope=scope;self.calls=[];self.alphas={};self.ambiguous=False
        self.location_dead=True;self.pending=True;self.detail_failures=0

    def detail(self,aid='alpha1',created=None):
        return {'id':aid,'type':'REGULAR','dateCreated':created or now(),
            'regular':{'code':'rank(close)'},'settings':{**self.scope,'startDate':'2012-01-01','endDate':'2026-01-01'},
            'is':{'sharpe':1.8,'fitness':1.4,'turnover':.2}}

    def request(self,method,url,payload=None,headers=None):
        self.calls.append((method,url,payload));path=urlsplit(url).path
        if method=='POST' and path=='/simulations':
            detail=self.detail();detail['regular']['code']=payload['regular']
            self.alphas['alpha1']=detail
            if self.ambiguous:raise AmbiguousWrite('TEST lost create response')
            return Response(201,{'Location':'/simulations/dead'})
        if method!='GET':raise AssertionError('unexpected write '+method+' '+path)
        if path=='/simulations/dead':
            return Response(404,{},None) if self.location_dead else Response(200,{}, {'alpha':'alpha1'})
        if path=='/users/self/alphas':
            params=parse_qs(urlsplit(url).query);offset=int(params['offset'][0]);limit=int(params['limit'][0])
            return Response(200,{}, {'count':len(self.alphas),'results':list(self.alphas.values())[offset:offset+limit]})
        for aid,detail in self.alphas.items():
            if path=='/alphas/'+aid:
                if self.detail_failures:
                    self.detail_failures-=1
                    return Response(404,{},None)
                return Response(200,{},detail)
            if path=='/alphas/'+aid+'/check':
                checks=copy.deepcopy(fixture_result()['checks'])
                checks[-1]['result']='PENDING' if self.pending else 'PASS'
                return Response(200,{}, {'checks':checks})
            if path=='/alphas/'+aid+'/recordsets/pnl':
                return Response(200,{}, {'schema':{'properties':[{'name':'date','type':'date'},{'name':'pnl','type':'decimal'}]},
                    'records':[['2020-01-02',1.0],['2023-09-29',7.0]]})
            if path=='/alphas/'+aid+'/correlations/self':return Response(200,{}, {'max':.2})
        return Response(404,{},None)


class RecoveryTests(Base):
    def simulator(self):
        fixture=AccountFixture(self.config['scope'])
        sim=BrainSimulator(self.config,ApiClient(self.config['brain']['base_url'],transport=fixture))
        sim.account_ref=digest('TEST_ONLY')
        return sim,fixture

    def record(self,sim,**kwargs):
        value={'created_at':'2000-01-01T00:00:00+00:00','state':'uncertain','expression':'rank(close)',
            'scope':sim.config['scope'],'account_ref':sim.account_ref,'api_base_url':sim.paths['base_url']}
        value.update(kwargs)
        return value

    def save_record(self,value):
        path=Path(self.tmp.name)/'probe.json';path.write_text(json.dumps(value),encoding='utf-8');return path

    def test_dead_location_recovers_from_account_list_details_and_never_reposts(self):
        sim,http=self.simulator();path=Path(self.tmp.name)/'probe.json'
        with self.assertRaises(ApiError):one_simulation_probe(sim,'rank(close)',path)
        self.assertEqual(json.loads(path.read_text())['state'],'polling_incomplete')
        value=resume_simulation_probe(sim,path)
        self.assertEqual(value['alpha_id'],'alpha1');self.assertEqual(value['state'],'completed')
        self.assertEqual(sum(m=='POST' for m,_,_ in http.calls),1)
        self.assertTrue(any('/users/self/alphas?' in u for _,u,_ in http.calls))
        self.assertFalse(value['recovery']['simulation_created'])

    def test_ambiguous_creation_no_location_recovers_with_get_only(self):
        sim,http=self.simulator();http.ambiguous=True;path=Path(self.tmp.name)/'probe.json'
        with self.assertRaises(AmbiguousWrite):one_simulation_probe(sim,'rank(close)',path)
        self.assertEqual(json.loads(path.read_text())['state'],'uncertain')
        self.assertEqual(resume_simulation_probe(sim,path)['state'],'completed')
        self.assertEqual(sum(m=='POST' for m,_,_ in http.calls),1)

    def test_zero_match_retries_stay_uncertain_without_post(self):
        sim,http=self.simulator();path=self.save_record(self.record(sim))
        for _ in range(2):
            with self.assertRaises(ApiError):resume_simulation_probe(sim,path)
            self.assertEqual(json.loads(path.read_text())['state'],'reconciliation_required')
        self.assertTrue(all(m=='GET' for m,_,_ in http.calls))

    def test_ambiguous_matches_need_explicit_id_still_verify_membership(self):
        sim,http=self.simulator()
        http.alphas={aid:http.detail(aid) for aid in ('alpha1','alpha2')}
        path=self.save_record(self.record(sim))
        with self.assertRaises(ApiError):resume_simulation_probe(sim,path)
        self.assertEqual(resume_simulation_probe(sim,path,'alpha2')['alpha_id'],'alpha2')
        self.assertTrue(all(m=='GET' for m,_,_ in http.calls))

    def test_old_or_missing_timestamp_not_automatically_claimed(self):
        sim,http=self.simulator();detail=http.detail(created='1999-01-01T00:00:00Z');http.alphas={'alpha1':detail}
        with self.assertRaises(ApiError):recover_alpha(sim,self.record(sim))
        detail.pop('dateCreated')
        with self.assertRaises(ApiError):recover_alpha(sim,self.record(sim))
        self.assertEqual(recover_alpha(sim,self.record(sim),'alpha1')['alpha_id'],'alpha1')

    def test_other_account_or_api_stops_before_get(self):
        sim,http=self.simulator()
        for change in ({'account_ref':digest('OTHER')},{'api_base_url':'https://different.example'},{'account_ref':None}):
            with self.assertRaises(ApiError):recover_alpha(sim,self.record(sim,**change))
        self.assertEqual(http.calls,[])

    def test_partial_list_cannot_establish_uniqueness(self):
        sim,http=self.simulator();sim.paths['recovery_max_alphas']=1
        http.alphas={aid:http.detail(aid) for aid in ('alpha1','alpha2')}
        with self.assertRaisesRegex(ApiError,'上限'):recover_alpha(sim,self.record(sim))
        self.assertTrue(all(m=='GET' for m,_,_ in http.calls))

    def test_explicit_id_needs_only_membership_not_full_expression_scan(self):
        sim,http=self.simulator();sim.paths['recovery_max_alphas']=1
        http.alphas={aid:http.detail(aid) for aid in ('alpha1','alpha2')}
        result=recover_alpha(sim,self.record(sim),'alpha1')
        self.assertEqual(result['alpha_id'],'alpha1');self.assertFalse(result['recovery']['list_scan_complete'])

    def test_paginated_list_and_parameter_mismatch(self):
        sim,http=self.simulator();sim.paths['recovery_page_size']=1
        wrong=http.detail('wrong');wrong['settings']['decay']=99
        http.alphas={'wrong':wrong,'alpha1':http.detail()}
        result=recover_alpha(sim,self.record(sim))
        self.assertEqual(result['alpha_id'],'alpha1');self.assertEqual(result['recovery']['scanned_alphas'],2)

    def test_all_requested_key_conflicts_are_rejected_server_dates_allowed(self):
        original=self.config['scope'];observed={**original,'startDate':'2010-01-01','endDate':'2026-01-01'}
        self.assertTrue(settings_match(observed,original))
        for key in original:
            broken={**observed,key:None}
            self.assertFalse(settings_match(broken,original),key)
        self.assertFalse(settings_match({**observed,'delay':True},original))
        self.assertFalse(settings_match(observed,{**original,'unknownParameter':123}))
        self.assertFalse(settings_match(observed,{**original,'startDate':'2011-01-01'}))

    def test_expression_whitespace_accepted_but_formula_changes_not_ignored(self):
        self.assertTrue(expression_matches('rank( close )','rank(close)'))
        self.assertFalse(expression_matches('rank(open)','rank(close)'))

    def test_engine_uncertain_task_recovers_without_new_budget_or_neighbors(self):
        self.live();sim,http=self.simulator();http.ambiguous=True
        engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        with self.assertRaises(AmbiguousWrite):engine.propose('rank(close)','TEST','fixture')
        before=engine.memory.run(engine.run_id)['simulations']
        recovered=engine.resume()
        self.assertEqual(recovered[0]['state'],'completed')
        self.assertEqual(engine.memory.run(engine.run_id)['simulations'],before)
        self.assertEqual(sum(m=='POST' for m,_,_ in http.calls),1)
        self.assertEqual(engine.memory.trials(engine.run_id)[0]['alpha_id'],'alpha1')

    def test_engine_dead_location_recovers_during_poll(self):
        self.live();sim,http=self.simulator();engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        value=engine.propose('rank(close)','TEST','fixture')
        self.assertEqual(value['state'],'completed');self.assertEqual(sum(m=='POST' for m,_,_ in http.calls),1)

    def test_id_is_saved_before_failing_detail_and_reused_without_list(self):
        self.live();sim,http=self.simulator();http.location_dead=False;http.detail_failures=1
        engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        value=engine.propose('rank(close)','TEST','fixture')
        self.assertEqual(value['state'],'completed')
        self.assertFalse(any('/users/self/alphas' in u for _,u,_ in http.calls))

    def test_default_resume_leaves_queued_tasks_and_sensitivity_untouched(self):
        sim=FixtureSimulator();engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        proposal={'expression':'ts_mean(close,20)','hypothesis':'TEST','source':'fixture'}
        tid,_=engine.memory.add_trial(engine.run_id,proposal,engine.validator.validate(proposal['expression']),engine.context)
        self.assertEqual(engine.resume(),[]);self.assertEqual(sim.starts,[])
        engine.execute(tid);engine.resume()
        self.assertEqual(len(sim.starts),1)

    def test_uncertain_create_blocks_other_new_posts_in_same_context(self):
        self.live();sim,http=self.simulator();http.ambiguous=True
        engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        with self.assertRaises(AmbiguousWrite):engine.propose('rank(close)','TEST','fixture')
        with self.assertRaisesRegex(ResearchError,'不确定创建'):engine.propose('rank(volume)','TEST','fixture')
        self.assertEqual(sum(m=='POST' for m,_,_ in http.calls),1)

    def test_old_snapshot_get_recovery_preserves_original_run_and_budget(self):
        from alpharesearch.validator import Validator
        self.live();sim,http=self.simulator();memory=Memory(self.config['database'])
        try:
            original=snapshot(self.config);original['code']={'legacy':'TEST old code fingerprint'}
            run_id=memory.create_run(original)
            proposal={'expression':'rank(close)','hypothesis':'TEST legacy','source':'fixture'}
            valid=Validator(self.config['catalog_data'],self.config['scope'],self.config['rules']).validate(proposal['expression'])
            tid,_=memory.add_trial(run_id,proposal,valid,'TEST legacy context')
            memory.claim(tid,1,sim.account_ref,sim.paths['base_url'])
            memory.db.execute('UPDATE tasks SET account_ref=NULL,api_base_url=NULL,dispatched_at=NULL WHERE trial_id=?',(tid,))
            memory.set_state(tid,'paused',location='/simulations/dead')
            before=memory.run(run_id)
        finally:memory.close()
        http.alphas={'alpha1':http.detail()}
        result=recover_saved_trial(self.config,run_id,tid,sim)
        self.assertTrue(result['original_snapshot_preserved']);self.assertFalse(result['submission_ready'])
        memory=Memory(self.config['database'])
        try:
            self.assertEqual(memory.run(run_id),before)
            self.assertEqual(memory.trial(tid)['state'],'completed')
            self.assertEqual(memory.trial(tid)['alpha_id'],'alpha1')
        finally:memory.close()
        self.assertTrue(all(m=='GET' for m,_,_ in http.calls))


class CheckAndAuditTests(Base):
    simulator=RecoveryTests.simulator

    def test_four_states_never_promote_pending_or_missing_with_numeric_correlation(self):
        for state in ('PASS','FAIL','PENDING','MISSING',None,'UNKNOWN'):
            value=fixture_result();value['checks'][-1]['result']=state
            result=evaluate(value,[fixture_result()]*2,self.config['policy'],True)
            self.assertEqual(result['ready'],state=='PASS')
            expected=state if state in ('PASS','FAIL','PENDING','MISSING') else 'MISSING'
            self.assertEqual(result['check_state']['by_name']['SELF_CORRELATION'],expected)

    def test_absent_self_check_is_not_replaced_by_numeric_endpoint(self):
        value=fixture_result();value['checks']=value['checks'][:-1]
        result=evaluate(value,[fixture_result()]*2,self.config['policy'],True)
        self.assertIn('missing_check:SELF_CORRELATION',result['missing']);self.assertFalse(result['ready'])

    def test_get_refresh_tracks_pending_to_pass_no_submission(self):
        sim,http=self.simulator();http.alphas={'alpha1':http.detail()}
        pending=sim.collect('alpha1');http.pending=False;complete=sim.collect('alpha1')
        self.assertEqual(pending['check_state']['status'],'PENDING')
        self.assertEqual(complete['check_state']['status'],'PASS')
        self.assertTrue(all(m=='GET' and not u.endswith('/submit') for m,u,_ in http.calls))

    def test_duplicate_unknown_and_null_check_status_fail_closed(self):
        normalized=normalize_checks({'checks':[{'name':'SELF_CORRELATION','result':None}]})
        self.assertEqual(normalized[0]['result'],'MISSING')
        for rows in (None,[{'name':'SELF_CORRELATION','result':'PASS'}]*2,[{}]):
            self.assertFalse(summarize_checks(rows)['submission_checks_complete'])

    def test_pending_numeric_correlation_payload_is_rejected(self):
        for state in ('PENDING','FAIL',None,'UNKNOWN'):
            with self.assertRaises(ApiError):correlation({'status':state,'max':.1})

    def test_unverified_sparse_pnl_is_only_observed_no_invented_returns(self):
        payload={'records':[{'date':'2020-01-02','pnl':1.0},{'date':'2023-09-29','pnl':7.0}]}
        result=observe_pnl(payload)
        self.assertEqual(result['value_semantics'],'unverified');self.assertEqual(result['pnl_unit'],'unverified')
        self.assertIsNone(result['missing_dates']);self.assertFalse(result['performance_metrics_computed'])
        self.assertNotIn('daily_returns',result);self.assertFalse(result['raw_values_retained'])

    def test_missing_unit_or_mismatched_capital_unit_quarantines_cached_proof(self):
        for unit in (None,'unverified','EUR'):
            result=fixture_result();result['daily_evidence']['pnl_unit']=unit
            self.assertFalse(daily_evidence_valid(result))
            self.assertIsNone(stability_windows(result,self.config['policy'])['worst_window_sharpe'])

    def test_audit_four_existing_alphas_fetches_unknown_pnl_without_computing_scores(self):
        sim,http=self.simulator();http.alphas={aid:http.detail(aid) for aid in ('one','two','three','four')}
        path=Path(self.tmp.name)/'four.json';output=Path(self.tmp.name)/'audit.json'
        manifest=[{'alpha_id':aid,'expression':'rank(close)','settings':self.config['scope']} for aid in http.alphas]
        path.write_text(json.dumps(manifest),encoding='utf-8')
        report=audit_existing_alphas(sim,path,output)
        self.assertEqual(len(report['alphas']),4);self.assertFalse(report['simulation_created'])
        self.assertEqual(sum('/recordsets/pnl' in u for _,u,_ in http.calls),4)
        self.assertTrue(all(m=='GET' for m,_,_ in http.calls))
        self.assertFalse(report['real_evidence_acceptance_completed'])
        for item in report['alphas']:
            self.assertEqual(item['status'],'observed');self.assertFalse(item['ready_to_submit'])
            self.assertFalse(item['daily_evidence_complete']);self.assertIsNone(item['stability']['worst_window_sharpe'])
            self.assertEqual(item['pnl_observation']['value_semantics'],'unverified')
            self.assertIn('pnl',item['evidence_source']['responses'])
        self.assertNotIn('daily_returns',json.dumps(report))
        with self.assertRaises(FileExistsError):audit_existing_alphas(sim,path,output)

    def test_audit_parameter_conflict_and_missing_id_do_not_stop_remaining_reads(self):
        sim,http=self.simulator();http.alphas={'good':http.detail('good'),'conflict':http.detail('conflict')}
        http.alphas['conflict']['settings']['decay']=999
        path=Path(self.tmp.name)/'manifest.json';output=Path(self.tmp.name)/'audit.json'
        path.write_text(json.dumps([{'alpha_id':aid,'expression':'rank(close)','settings':self.config['scope']}
                                   for aid in ('missing','conflict','good')]),encoding='utf-8')
        report=audit_existing_alphas(sim,path,output)
        self.assertEqual([r['status'] for r in report['alphas']],['unverified','identity_conflict','observed'])
        self.assertFalse(any(r['ready_to_submit'] for r in report['alphas']))


class DirectedTests(Base):
    def plan(self):
        plan=Path(self.tmp.name)/'directed.json';candidates=Path(self.tmp.name)/'candidates.json'
        value=create_plan(self.config,'stable-directed-id',plan,candidates)
        return plan,candidates,value

    def test_fixed_design_has_six_candidates_four_single_parameter_changes(self):
        rows=fixed_candidates();self.assertEqual(len(rows),6)
        for row in rows:
            self.assertEqual(len(row['change']),1 if row['parent_key'] else 0)
        self.assertEqual(rows[1]['parameters']['normalize_return'],True)
        self.assertEqual(rows[-1]['parameters']['smoothing_sessions'],5)

    def test_repeated_execution_reuses_run_and_six_simulations_without_neighbors(self):
        plan,_,_=self.plan();sim=FixtureSimulator();output=Path(self.tmp.name)/'report.json'
        a=execute_plan(self.config,plan,output,sim)
        b=execute_plan(self.config,plan,output,sim)
        self.assertEqual(a['run_id'],b['run_id']);self.assertEqual(len(sim.starts),6)
        self.assertEqual(b['counts']['simulations'],6);self.assertEqual(b['counts']['llm_calls'],0)
        engine=Engine(self.config,a['run_id'],sim);self.addCleanup(engine.memory.close)
        engine.resume(continue_research=True)
        self.assertEqual(len(sim.starts),6)
        with self.assertRaises(ResearchError):engine.propose('rank(close)','new','forbidden')
        events=[json.loads(r[0]) for r in engine.memory.db.execute("SELECT payload FROM events WHERE kind='directed_trial'")]
        self.assertEqual(len(events),6)

    def test_tampered_plan_even_with_recomputed_hash_is_rejected(self):
        plan,_,value=self.plan();value['candidates'][1]['expression']='rank(close)'
        value['plan_sha256']=digest({k:v for k,v in value.items() if k!='plan_sha256'})
        plan.write_text(json.dumps(value),encoding='utf-8')
        sim=FixtureSimulator()
        with self.assertRaises(ResearchError):execute_plan(self.config,plan,Path(self.tmp.name)/'report.json',sim)
        self.assertEqual(sim.starts,[])

    def test_uncertain_directed_write_reserves_budget_and_aborts_other_candidates(self):
        class Uncertain(FixtureSimulator):
            def start(self,expression,scope):
                self.starts.append(expression);raise AmbiguousWrite('TEST lost response')
        plan,_,_=self.plan();sim=Uncertain();output=Path(self.tmp.name)/'report.json'
        with self.assertRaises(AmbiguousWrite):execute_plan(self.config,plan,output,sim)
        with self.assertRaises(ResearchError):execute_plan(self.config,plan,output,sim)
        self.assertEqual(len(sim.starts),1)
        memory=Memory(self.config['database'])
        try:
            run_id=memory.db.execute('SELECT run_id FROM directed_studies').fetchone()[0]
            self.assertEqual(memory.run(run_id)['simulations'],1)
        finally:memory.close()

    def test_directed_independent_comparison_is_one_shot_and_reports_all_pairs(self):
        root=Path(self.tmp.name);panel=root/'panel.csv'
        with panel.open('w',newline='') as out:
            writer=csv.writer(out);writer.writerow(['date','session','asset','available_date','eligible','close','vwap'])
            for t in range(140):
                day=(date(2020,1,1)+timedelta(days=t)).isoformat()
                for j in range(8):
                    price=30+j*3+math.sin(t*.2+j)*2+t*.05
                    writer.writerow([day,t,str(j),day,1,price,price*(1+.01*math.sin(t*.5+j))])
        _,candidates,_=self.plan()
        value={'study_id':'directed-holdout','dataset_id':'TEST-v04-synthetic','data_permission_confirmed':True,
            'data_source':'TEST ONLY','point_in_time_review':'synthetic fixture','synthetic':True,
            'discovery_start':'2020-01-01','holdout_start':'2020-04-10','holdout_end':'2020-05-19',
            'train_min_days':30,'test_days':20,'embargo_days':2,'delay':1,'min_assets':4,'min_train_observations':20,
            'fee_bps':5,'min_train_ic':-1,'min_train_sharpe':-100,'max_train_turnover':3,'min_train_coverage':.5,
            'holdout_mode':'directed_fixed_comparisons_v1'}
        planpath=root/'validation-plan.json';planpath.write_text(json.dumps(value),encoding='utf-8')
        self.config['validation']['holdout_registry']=str(root/'holdout-register.sqlite3')
        manifest=root/'manifest.json';ledger=root/'validation.sqlite3'
        local.freeze(panel,candidates,planpath,self.config,ledger,manifest)
        result=local.final_holdout(manifest,ledger)
        self.assertEqual(result['mode'],'one_shot_directed_comparison')
        self.assertEqual(len(result['metrics_by_candidate']),6);self.assertEqual(len(result['prespecified_comparisons']),4)
        self.assertFalse(result['feedback_to_agent']);self.assertFalse(result['selected_after_validation'])
        self.assertFalse(result['brain_equivalent']);self.assertFalse(result['independent_holdout_validated'])
        with self.assertRaises(ResearchError):local.final_holdout(manifest,ledger)
        self.assertEqual(local.saved_final_report(value['study_id'],ledger),result)
