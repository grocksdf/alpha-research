from fixtures.legacy_engine_auto_variants import enable_legacy_historical_fixture
import copy
import csv
import itertools
import json
import math
import os
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from test_system import Base,FixtureSimulator,ScriptedTransport,fixture_result
from alpharesearch.common import ResearchError,digest,load_settings
from alpharesearch.engine import Engine
from alpharesearch.evaluator import shortlist
from alpharesearch.http import ApiClient,Response
from alpharesearch.trace import ShapeRecorder
from alpharesearch.brain import BrainSimulator
from alpharesearch.papers import PaperSearch,parse_atom,agent_search
from alpharesearch.research import search_fields,add_note,notes,tag_family,families
from alpharesearch.benchmark import compare_reports
from alpharesearch import local_validation as local
from alpharesearch import tracking
from alpharesearch import holdout_registry as register


class ProvenanceRegressionTests(Base):
    def setUp(self):
        super().setUp()
        enable_legacy_historical_fixture(self)

    def engine(self):
        self.config['rules']['max_simulations_per_run']=15
        sim=FixtureSimulator();engine=Engine(self.config,simulator=sim)
        self.addCleanup(engine.memory.close);return engine,sim

    def test_many_hypotheses_share_one_simulation_and_keep_all_sources(self):
        engine,sim=self.engine()
        outcomes=[engine.propose('ts_mean(close,20)',f'hypothesis {i}',f'paper {i}') for i in range(4)]
        self.assertEqual(len({o['trial_id'] for o in outcomes}),1)
        self.assertEqual(len({o['intent_id'] for o in outcomes}),4)
        self.assertEqual(len(sim.starts),3)
        report=engine.report()
        self.assertEqual(len(report['candidates']),1)
        self.assertEqual({i['source'] for i in report['candidates'][0]['research_intents']},{f'paper {i}' for i in range(4)})
        self.assertEqual(len(engine.memory.feedback(engine.context)),4)

    def test_two_promoted_hypotheses_keep_their_own_neighbors(self):
        engine,sim=self.engine()
        engine.propose('ts_mean(close,20)','root','root source')
        a=engine.propose('ts_mean(close,16)','mechanism A','source A')
        b=engine.propose('ts_mean(close,16)','mechanism B','source B')
        self.assertEqual(len(sim.starts),5)
        for outcome in (a,b):
            refs=engine.memory.db.execute('SELECT trial_id FROM sensitivity_refs WHERE intent_id=?',(outcome['intent_id'],)).fetchall()
            self.assertEqual(len(refs),2)
        intents=engine.memory.intents(engine.run_id,a['trial_id'])
        self.assertEqual([i['hypothesis'] for i in intents],['mechanism A','mechanism B'])

    def test_abandonment_does_not_corrupt_shared_job_or_other_hypothesis(self):
        engine,sim=self.engine()
        a=engine.propose('rank(close)','A','source A')
        b=engine.propose('rank(close)','B','source B')
        with self.assertRaises(ResearchError):engine.abandon(a['trial_id'],'ambiguous')
        engine.abandon(a['trial_id'],'falsified A',a['intent_id'])
        self.assertEqual(engine.memory.trial(a['trial_id'])['state'],'completed')
        self.assertEqual(engine.report()['candidates'][0]['hypothesis'],'B')
        engine.abandon(b['trial_id'],'falsified B',b['intent_id'])
        self.assertEqual(engine.report()['candidates'],[])
        self.assertEqual(len(sim.starts),1)

    def test_parent_intent_provenance_separate_from_formula_parent(self):
        engine,sim=self.engine()
        a=engine.propose('rank(close)','A','paper A')
        b=engine.propose('rank(close)','B','paper B')
        c=engine.propose('rank(volume)','revision of B','paper C',parent_intent_id=b['intent_id'])
        self.assertEqual(engine.memory.intents(engine.run_id,c['trial_id'])[0]['parent_intent_id'],b['intent_id'])

    def test_cached_neighbors_link_even_when_run_budget_exhausted(self):
        engine,sim=self.engine()
        a=engine.propose('ts_mean(close,20)','A','source A')
        engine.config['rules']['max_simulations_per_run']=3
        b=engine.propose('ts_mean(close,20)','B','source B')
        refs=engine.memory.db.execute('SELECT count(*) FROM sensitivity_refs WHERE intent_id=?',(b['intent_id'],)).fetchone()[0]
        self.assertEqual(refs,2);self.assertEqual(len(sim.starts),3)

    def test_pareto_order_is_permutation_invariant(self):
        def item(name,fitness,year,turnover):
            return {'trial_id':name,'family':'same','self_correlation':.2,'metrics':{'fitness':fitness,'turnover':turnover},
                'evaluation':{'ready':True,'worst_window_sharpe':year},'pnl_changes':{str(i):math.sin(i) for i in range(300)}}
        candidates=[item('dominated',1,1,.6),item('winner',2,1,.1),item('tradeoff',1,2,.3)]
        orders=[]
        for permutation in itertools.permutations(candidates):
            chosen,_=shortlist(list(permutation),self.config['policy'],family_cap=1);orders.append(chosen[0]['trial_id'])
        self.assertEqual(set(orders),{'tradeoff'})


class AccessAndMemoryTests(Base):
    def test_shape_trace_redacts_credentials_ids_metrics_and_location(self):
        path=Path(self.tmp.name)/'shapes.jsonl'
        api=ApiClient('https://api.worldquantbrain.com',transport=ScriptedTransport([
            Response(200,{'Set-Cookie':'SECRET_COOKIE','Location':'SECRET_LOCATION'},
                {'id':'PRIVATE_ACCOUNT','email':'PRIVATE_EMAIL','PRIVATE_DYNAMIC_KEY':{'token':'SECRET_TOKEN'},'is':{'sharpe':123.456},'records':[['PRIVATE_VALUE',99]]})]),observer=ShapeRecorder(path))
        api.once('GET','/alphas/PRIVATE_ALPHA/check?token=PRIVATE_QUERY')
        raw=path.read_text(encoding="utf-8")
        for value in ['PRIVATE_ACCOUNT','PRIVATE_ALPHA','PRIVATE_EMAIL','PRIVATE_VALUE','PRIVATE_QUERY','PRIVATE_DYNAMIC_KEY','SECRET_TOKEN','SECRET_COOKIE','SECRET_LOCATION','123.456']:
            self.assertNotIn(value,raw)
        self.assertEqual(json.loads(raw)['path'],'/alphas/{id}/check')

    def test_single_probe_capability_does_not_enable_research_or_submission(self):
        self.config['safety']={'live_research_enabled':False,'submission_enabled':False}
        transport=ScriptedTransport([Response(201,{'Location':'/simulations/s1'})])
        sim=BrainSimulator(self.config,ApiClient('https://api.worldquantbrain.com',transport=transport))
        with self.assertRaises(ResearchError):sim.start('rank(close)',self.config['scope'])
        sim.allow_one_probe();sim.start('rank(close)',self.config['scope'])
        with self.assertRaises(ResearchError):sim.start('rank(close)',self.config['scope'])
        with self.assertRaises(ResearchError):sim.submit_once('alpha123')
        self.assertEqual(len(transport.calls),1)

    def test_smoke_metrics_do_not_query_optional_research_endpoints(self):
        transport=ScriptedTransport([Response(200,{}, {'alpha':'alpha123'}),
            Response(200,{}, {'id':'alpha123','regular':{'code':'rank(close)'},'settings':self.config['scope'],
                'is':{'sharpe':1.1,'fitness':.8,'turnover':.2}})])
        sim=BrainSimulator(self.config,ApiClient('https://api.worldquantbrain.com',transport=transport))
        result=sim.finish_probe('/simulations/smoke')
        self.assertEqual(result['metrics']['sharpe'],1.1)
        self.assertEqual(len(transport.calls),2)
        self.assertTrue(transport.calls[-1][1].endswith('/alphas/alpha123'))

    def test_live_gate_blocks_before_spending_budget(self):
        self.live();self.config['safety']['live_research_enabled']=False
        sim=FixtureSimulator();engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        with self.assertRaises(ResearchError):engine.propose('rank(close)','hyp','source')
        self.assertEqual(engine.memory.run(engine.run_id)['simulations'],0)
        self.assertEqual(sim.starts,[])

    def test_bulk_gate_requires_matching_real_smoke_receipt(self):
        from alpharesearch.probe import require_smoke_receipt
        path=Path(self.tmp.name)/'smoke.json'
        self.config['safety']['validated_probe_record']=str(path)
        receipt={'state':'completed','metrics_parse_verified':True,'observation_source':'account_http',
            'account_ref':digest('TEST_ACCOUNT'),'scope':self.config['scope'],'api_base_url':self.config['brain']['base_url']}
        path.write_text(json.dumps(receipt))
        self.assertTrue(require_smoke_receipt(self.config,'TEST_ACCOUNT')['scope_verified'])
        with self.assertRaises(ResearchError):require_smoke_receipt(self.config,'OTHER_ACCOUNT')
        receipt['observation_source']='scripted_or_unknown_transport';path.write_text(json.dumps(receipt))
        with self.assertRaises(ResearchError):require_smoke_receipt(self.config,'TEST_ACCOUNT')

    def test_trace_retains_only_safe_recordset_column_names(self):
        from alpharesearch.trace import shape
        value=shape({'schema':{'properties':[{'name':'date'},{'name':'pnl'},{'name':'PRIVATE_COLUMN'}]},'records':[]})
        self.assertEqual(value['recordset_columns'],['date','pnl','<unknown-column>'])
        self.assertNotIn('PRIVATE_COLUMN',json.dumps(value))

    def test_field_search_is_bounded_and_approved_only(self):
        self.config['catalog_data']['fields']['volume']['description']='trading volume activity'
        rows=search_fields(self.config['catalog_data'],'volume',1)
        self.assertEqual(rows[0]['id'],'volume')
        self.config['catalog_data']['fields']['volume']['point_in_time']=False
        self.assertEqual(search_fields(self.config['catalog_data'],'volume'),[])
        with self.assertRaises(ResearchError):search_fields(self.config['catalog_data'],'volume',1000)

    def test_structured_notes_link_to_evidence_and_do_not_change_results(self):
        engine=Engine(self.config);self.addCleanup(engine.memory.close)
        outcome=engine.propose('rank(close)','hyp','source');before=engine.memory.latest(outcome['trial_id'])
        with self.assertRaises(ResearchError):add_note(engine,'failure_constraint','unsupported',[])
        note=add_note(engine,'observation','Only discovery metrics inspected',[outcome['trial_id']],outcome['intent_id'])
        self.assertEqual(note['status'],'research_note_not_verified_fact')
        self.assertEqual(len(notes(engine)),1);self.assertEqual(engine.memory.latest(outcome['trial_id']),before)
        tag_family(engine,outcome['intent_id'],['momentum'],'research mechanism only')
        self.assertEqual(families(engine)[0]['tags'],['momentum'])
        self.assertFalse(families(engine)[0]['economic_independence_verified'])

    def test_costs_do_not_turn_missing_price_or_tokens_into_zero(self):
        engine=Engine(self.config);self.addCleanup(engine.memory.close)
        engine.memory.cost(engine.run_id,'llm',1.2,input_tokens=100,output_tokens=None)
        value=engine.memory.cost_summary(engine.run_id)
        self.assertFalse(value['token_usage_complete']);self.assertIsNone(value['known_usd'])
        engine.memory.cost(engine.run_id,'human',60,usd=1,detail={'note':'owner estimate'})
        self.assertFalse(engine.memory.cost_summary(engine.run_id)['all_costs_priced'])

    def test_benchmark_rejects_demo_unequal_budget_and_cross_run_cache(self):
        engine=Engine(self.config);self.addCleanup(engine.memory.close)
        engine.propose('rank(close)','hyp','source');a=engine.report();b=copy.deepcopy(a)
        self.assertIn('synthetic_or_nonlive_evidence',compare_reports(a,b)['reasons'])
        a['mode']=b['mode']='live';b['counts']['simulations']+=1
        self.assertIn('unequal_or_zero_simulation_spend',compare_reports(a,b)['reasons'])
        b['counts']=copy.deepcopy(a['counts']);b['candidates'][0]['original_run_id']='other'
        self.assertIn('cross_run_cached_evidence',compare_reports(a,b)['reasons'])
        self.assertFalse(compare_reports(a,b)['ai_superiority_established'])

    def test_matching_protocols_can_pass_without_claiming_ai_superiority(self):
        self.live();engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        engine.propose('rank(close)','TEST ONLY','fixture');a=engine.report();b=copy.deepcopy(a)
        a['research_arm']={'agent':'baseline'};b['research_arm']={'agent':'llm'}
        result=compare_reports(a,b)
        self.assertTrue(result['protocol_comparable']);self.assertFalse(result['ai_superiority_established'])


class PaperTests(Base):
    XML=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/0000.00000v1</id><title>TEST ONLY</title><summary>Not an actual paper.</summary><author><name>Fixture</name></author><published>2020-01-01</published></entry></feed>'''

    def test_atom_metadata_and_cache(self):
        calls=[]
        def fetch(url):calls.append(url);return self.XML
        search=PaperSearch(Path(self.tmp.name)/'papers.sqlite3',fetch=fetch)
        a=search.search('all:factor',2);b=search.search('all:factor',2)
        self.assertFalse(a['cache_hit']);self.assertTrue(b['cache_hit']);self.assertEqual(len(calls),1)
        self.assertTrue(a['papers'][0]['url'].startswith('https://arxiv.org/abs/'))
        self.assertFalse(a['papers'][0]['financial_claims_verified'])

    def test_rejects_entities_and_unknown_external_links(self):
        for xml in [b'<!DOCTYPE x><feed/>',self.XML.replace(b'arxiv.org/abs/',b'evil.example/abs/')]:
            with self.assertRaises(ResearchError):parse_atom(xml)

    def test_search_disabled_makes_no_external_request(self):
        engine=Engine(self.config);self.addCleanup(engine.memory.close)
        with patch.object(PaperSearch,'search') as request:
            with self.assertRaises(ResearchError):agent_search(engine,'factor')
            request.assert_not_called()


class LocalValidationTests(Base):
    def setUp(self):
        super().setUp();self.root=Path(self.tmp.name);self.panel=self.root/'panel.csv'
        self.write_panel()
        self.candidates=self.root/'candidates.json';self.candidates.write_text(json.dumps([
            {'expression':'rank(close)','hypothesis':'synthetic test only','source':'fixture'},
            {'expression':'-rank(ts_delta(close,5))','hypothesis':'synthetic test only','source':'fixture'}]))
        self.plan={'study_id':'test-study','data_permission_confirmed':True,'data_source':'TEST ONLY',
            'dataset_id':'synthetic-test-panel-v1',
            'point_in_time_review':'synthetic same-day snapshots','synthetic':True,
            'discovery_start':'2020-01-01','holdout_start':'2020-04-10','holdout_end':'2020-05-19',
            'train_min_days':30,'test_days':20,'embargo_days':2,'delay':1,'min_assets':4,'min_train_observations':20,
            'fee_bps':5,'min_train_ic':-1,'min_train_sharpe':-100,'max_train_turnover':3,'min_train_coverage':.5}
        self.planpath=self.root/'plan.json';self.planpath.write_text(json.dumps(self.plan))
        self.ledger=self.root/'validation.sqlite3';self.manifest=self.root/'manifest.json'
        self.config['validation']={'holdout_registry':str(self.root/'holdout-registry.sqlite3')}

    def write_panel(self,future_change=False):
        with self.panel.open('w',newline='') as file:
            writer=csv.writer(file);writer.writerow(['date','asset','available_date','eligible','close','volume','session'])
            for t in range(140):
                day=(date(2020,1,1)+timedelta(days=t)).isoformat()
                for j in range(8):
                    price=30+j*3+math.sin(t*.2+j)*2+t*.05
                    if future_change and t>=110:price*=2+j*.1
                    writer.writerow([day,str(j),day,1,price,10000+j*100+t,t])

    def freeze(self):return local.freeze(self.panel,self.candidates,self.planpath,self.config,self.ledger,self.manifest)

    def test_future_prices_cannot_change_past_features_or_selection(self):
        first=local.Panel(self.panel);signals=local.FormulaKernel(first).compute('ts_mean(close,5)')
        self.write_panel(True);second=local.Panel(self.panel);later=local.FormulaKernel(second).compute('ts_mean(close,5)')
        self.assertEqual(signals[:110],later[:110]);self.assertNotEqual(signals[111:],later[111:])

    def test_walk_forward_purges_label_overlap_and_keeps_holdout_unread(self):
        m=self.freeze();self.assertIsNotNone(m['frozen_selected_id'])
        result=local.walk_forward(self.manifest,self.ledger)
        self.assertGreater(len(result['folds']),1)
        for fold in result['folds']:
            self.assertLess(fold['last_train_label_end'],fold['test_start'])
            self.assertLess(fold['test_end'],self.plan['holdout_start'])
        for row in result['daily']:
            self.assertLess(row['signal_date'],row['date']);self.assertLess(row['date'],row['label_end'])
        self.assertFalse(result['final_holdout_read']);self.assertFalse(result['independent_holdout_validated'])

    def test_final_is_one_shot_but_saved_report_is_recoverable(self):
        self.freeze();result=local.final_holdout(self.manifest,self.ledger)
        self.assertTrue(result['holdout_revealed_once']);self.assertTrue(result['synthetic'])
        self.assertFalse(result['independent_holdout_validated'])
        with self.assertRaises(ResearchError):local.final_holdout(self.manifest,self.ledger)
        self.assertEqual(local.saved_final_report('test-study',self.ledger),result)

    def test_new_study_id_cannot_reveal_same_holdout_again(self):
        self.freeze();local.final_holdout(self.manifest,self.ledger)
        self.plan['study_id']='another-name';self.planpath.write_text(json.dumps(self.plan));other=self.root/'other.json'
        local.freeze(self.panel,self.candidates,self.planpath,self.config,self.ledger,other)
        with self.assertRaises(ResearchError):local.final_holdout(other,self.ledger)

    def test_reordered_rows_and_new_study_ledger_cannot_repeat_holdout(self):
        self.freeze();first=local.final_holdout(self.manifest,self.ledger)
        with self.panel.open() as source:reader=csv.DictReader(source);columns=reader.fieldnames;rows=list(reader)
        with self.panel.open('w',newline='') as target:
            writer=csv.DictWriter(target,columns);writer.writeheader();writer.writerows(reversed(rows))
        self.plan['study_id']='reordered-study';self.planpath.write_text(json.dumps(self.plan))
        other=self.root/'reordered.json';other_ledger=self.root/'fresh-study.sqlite3'
        local.freeze(self.panel,self.candidates,self.planpath,self.config,other_ledger,other)
        with self.assertRaises(ResearchError):local.final_holdout(other,other_ledger)
        self.assertTrue(first['holdout_revealed_once'])

    def new_study(self,identifier,**changes):
        plan={**self.plan,'study_id':identifier,**changes};self.planpath.write_text(json.dumps(plan))
        manifest=self.root/(identifier+'.json');ledger=self.root/(identifier+'.sqlite3')
        local.freeze(self.panel,self.candidates,self.planpath,self.config,ledger,manifest)
        return manifest,ledger

    def test_renamed_dataset_with_reordered_columns_numeric_format_is_duplicate(self):
        self.freeze();local.final_holdout(self.manifest,self.ledger)
        with self.panel.open() as source:reader=csv.DictReader(source);columns=reader.fieldnames;rows=list(reader)
        for row in rows:
            row['close']=str(float(row['close']))+'0' # same parsed float
            row['volume']=str(float(row['volume']))
        with self.panel.open('w',newline='') as target:
            writer=csv.DictWriter(target,list(reversed(columns)),quoting=csv.QUOTE_ALL);writer.writeheader();writer.writerows(reversed(rows))
        manifest,ledger=self.new_study('alias-study',dataset_id='accidental-new-dataset-alias')
        with self.assertRaisesRegex(ResearchError,'语义相同'):
            local.final_holdout(manifest,ledger)

    def test_changed_history_does_not_reset_dataset_holdout_permission(self):
        self.freeze();local.final_holdout(self.manifest,self.ledger)
        text=self.panel.read_text(encoding="utf-8");self.panel.write_text(text.replace('10000','10000.0',1))
        manifest,ledger=self.new_study('changed-history')
        with self.assertRaises(ResearchError):local.final_holdout(manifest,ledger)

    def test_overlapping_interval_is_denied_before_reading_values(self):
        self.freeze();local.final_holdout(self.manifest,self.ledger)
        manifest,ledger=self.new_study('overlap-study',holdout_start='2020-04-15')
        with patch.object(register,'semantic_hash',side_effect=AssertionError('must not read holdout')):
            with self.assertRaises(ResearchError):local.final_holdout(manifest,ledger)

    def test_missing_or_replaced_registry_is_not_silently_recreated(self):
        m=self.freeze();path=Path(m['holdout_registry']['path']);path.unlink()
        with self.assertRaisesRegex(ResearchError,'缺失'):local.final_holdout(self.manifest,self.ledger)
        self.assertFalse(path.exists())
        db,_=register.connect(path,create=True);db.close()
        with self.assertRaisesRegex(ResearchError,'身份'):local.final_holdout(self.manifest,self.ledger)

    def test_failure_consumes_permission_across_new_study_database(self):
        self.freeze()
        with patch.object(local.FormulaKernel,'compute',side_effect=ResearchError('TEST FAILURE')):
            with self.assertRaises(ResearchError):local.final_holdout(self.manifest,self.ledger)
        manifest,ledger=self.new_study('after-failed-reveal')
        with self.assertRaises(ResearchError):local.final_holdout(manifest,ledger)

    def test_concurrent_reservations_cannot_both_win(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        path=Path(self.config['validation']['holdout_registry']);db,identity=register.connect(path,create=True);db.close()
        barrier=Barrier(2)
        def claim(identifier):
            db,_=register.connect(path,expected_id=identity)
            try:
                barrier.wait(timeout=5)
                try:register.reserve(db,self.plan['dataset_id'],self.plan['holdout_start'],self.plan['holdout_end'],identifier,self.ledger)
                except ResearchError:return False
                return True
            finally:db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,['A','B']))
        self.assertEqual(sorted(results),[False,True])

    def test_dataset_identity_and_fixed_protocol_are_required(self):
        for change in ({'dataset_id':''},{'dataset_id':' new-id '},{'validation_protocol':'reset-v2'}):
            with self.assertRaises(ResearchError):local.validate_plan({**self.plan,**change})

    def test_legacy_exposure_import_locks_interval_without_recomputing(self):
        # Manufacture the old schema and its byte-keyed exposure, with no market
        # evaluation; the import must conservatively treat any state as exposed.
        db=local.ledger(self.ledger)
        db.execute('INSERT INTO studies VALUES(?,?,?,?,?)',('legacy-study','old-hash',json.dumps({'version':'0.3.0','plan':self.plan}),'completed','old-date'))
        db.execute('INSERT INTO final_reveals VALUES(?,?,?,?)',('old-byte-key','legacy-study','revealing','old-date'));db.close()
        with self.assertRaisesRegex(ResearchError,'validation-import-legacy'):self.freeze()
        result=register.import_legacy(self.ledger,self.config['validation']['holdout_registry'],self.plan['dataset_id'])
        self.assertEqual(result['imported_intervals'],1)
        self.assertEqual(register.import_legacy(self.ledger,self.config['validation']['holdout_registry'],self.plan['dataset_id'])['imported_intervals'],0)
        self.freeze()
        with self.assertRaises(ResearchError):local.final_holdout(self.manifest,self.ledger)

    def test_imported_overlapping_intervals_keep_the_entire_exposed_tail(self):
        db=local.ledger(self.ledger)
        for name,start,end in [('old-A','2020-04-10','2020-04-30'),('old-B','2020-04-20','2020-05-19')]:
            plan={**self.plan,'holdout_start':start,'holdout_end':end}
            db.execute('INSERT INTO studies VALUES(?,?,?,?,?)',(name,'old-hash',json.dumps({'version':'0.3.0','plan':plan}),'completed','old-date'))
            db.execute('INSERT INTO final_reveals VALUES(?,?,?,?)',(name+'-bytes',name,'completed','old-date'))
        db.close()
        path=self.config['validation']['holdout_registry']
        self.assertEqual(register.import_legacy(self.ledger,path,self.plan['dataset_id'])['imported_intervals'],2)
        db,_=register.connect(path)
        try:
            with self.assertRaises(ResearchError):register.reserve(db,self.plan['dataset_id'],'2020-05-10','2020-05-18','new-study',self.ledger)
        finally:db.close()

    def test_changed_data_and_tampered_manifest_are_rejected(self):
        self.freeze();self.write_panel(True)
        with self.assertRaises(ResearchError):local.walk_forward(self.manifest,self.ledger)
        self.write_panel(False)
        m=json.loads(self.manifest.read_text(encoding="utf-8"));m['frozen_selected_id']='FAKE';m['manifest_hash']=digest({k:v for k,v in m.items() if k!='manifest_hash'})
        self.manifest.write_text(json.dumps(m))
        with self.assertRaises(ResearchError):local.final_holdout(self.manifest,self.ledger)

    def test_permission_delay_and_label_horizon_are_required(self):
        for patch_plan in ({'data_permission_confirmed':False},{'embargo_days':0},{'label_horizon_days':5},{'annualization':-1},{'min_holdout_observations':0}):
            plan={**self.plan,**patch_plan}
            with self.assertRaises(ResearchError):local.validate_plan(plan)

    def test_failure_after_reveal_cannot_be_retried(self):
        self.freeze()
        with patch.object(local.FormulaKernel,'compute',side_effect=ResearchError('TEST FAILURE AFTER READING')):
            with self.assertRaises(ResearchError):local.final_holdout(self.manifest,self.ledger)
        with self.assertRaises(ResearchError):local.final_holdout(self.manifest,self.ledger)

    def test_late_data_and_missing_delisting_returns_not_silently_dropped(self):
        text=self.panel.read_text(encoding="utf-8");self.panel.write_text(text.replace('2020-01-01,0,2020-01-01','2020-01-01,0,2020-01-02',1))
        with self.assertRaises(ResearchError):local.Panel(self.panel)
        self.panel.write_text(text)
        with self.panel.open() as file:rows=list(csv.DictReader(file))
        rows=[r for r in rows if not(r['date']=='2020-02-01' and r['asset']=='0')]
        with self.panel.open('w',newline='') as file:
            writer=csv.DictWriter(file,rows[0].keys());writer.writeheader();writer.writerows(rows)
        with self.assertRaises(ResearchError):local.Panel(self.panel)

    def test_missing_entire_session_is_detected(self):
        with self.panel.open() as file:rows=list(csv.DictReader(file))
        rows=[r for r in rows if r['date']!='2020-02-01']
        with self.panel.open('w',newline='') as file:
            writer=csv.DictWriter(file,rows[0].keys());writer.writeheader();writer.writerows(rows)
        with self.assertRaises(ResearchError):local.Panel(self.panel)

    def test_transaction_costs_reduce_returns(self):
        panel=local.Panel(self.panel,before=self.plan['holdout_start'])
        signals={'a':local.FormulaKernel(panel).compute('rank(close)')};schedule=[(i,'a') for i in range(2,30)]
        no_fee=local.evaluate_schedule(panel,signals,schedule,{**self.plan,'fee_bps':0})
        fee=local.evaluate_schedule(panel,signals,schedule,{**self.plan,'fee_bps':50})
        self.assertLess(fee['metrics']['total_return'],no_fee['metrics']['total_return'])


class TrackingTests(Base):
    def setUp(self):
        super().setUp();engine=Engine(self.config);engine.propose('rank(close)','hyp','source')
        self.report=engine.report();engine.memory.close()
        self.path=Path(self.tmp.name)/'review.json';self.path.write_text(json.dumps(self.report))
        self.ledger=Path(self.tmp.name)/'tracking.sqlite3';self.tid=self.report['candidates'][0]['trial_id']

    def test_future_registration_and_append_only_observations(self):
        with self.assertRaises(ResearchError):tracking.register(self.path,self.tid,'2020-01-01',self.ledger,today=date(2020,1,1))
        monitor=tracking.register(self.path,self.tid,'2020-01-02',self.ledger,today=date(2020,1,1))
        identifier=monitor['monitor_id']
        self.assertEqual(tracking.tracking_report(self.ledger,identifier)['status'],'awaiting_observations')
        report=tracking.add_observations(self.ledger,identifier,[('2020-01-02',.01),('2020-01-03',-.002)],'TEST ONLY',today=date(2020,1,4))
        self.assertEqual(report['metrics']['observations'],2)
        self.assertFalse(report['research_feedback_allowed'])
        with self.assertRaises(ResearchError):tracking.add_observations(self.ledger,identifier,[('2020-01-02',.5)],'TEST ONLY',today=date(2020,1,4))
        with self.assertRaises(ResearchError):tracking.add_observations(self.ledger,identifier,[('2020-01-05',.1)],'TEST ONLY',today=date(2020,1,4))
        with self.assertRaises(ResearchError):tracking.add_observations(self.ledger,identifier,[('2020-01-01',.1)],'TEST ONLY',today=date(2020,1,4))

    def test_agent_has_no_validation_or_tracking_access(self):
        from alpharesearch.agent import ResearchAgent,TOOLS
        names={x['function']['name'] for x in TOOLS}
        self.assertFalse(names&{'final_holdout','walk_forward','tracking_report','submit','validation_result'})


if __name__=='__main__':unittest.main()
