"""Synthetic protocol regression tests; no account or market performance claims."""
import copy
import json
from datetime import date,timedelta

from test_system import Base,FixtureSimulator,ScriptedTransport,fixture_result,configure_pnl_contract
from alpharesearch.brain import BrainSimulator,pnl_isolation_valid
from alpharesearch.engine import Engine
from alpharesearch.evaluator import evaluate
from alpharesearch.http import ApiClient,Response


class BrainIsolationTests(Base):
    def contract(self):
        return {'schema_reviewed':True,'review_note':'TEST ONLY simulated schema',
            'date_semantics':'inclusive_dates','is_start_path':['is','startDate'],'is_end_path':['is','endDate']}

    def collect(self,start='2020-01-01',end='2023-09-30',reviewed=True):
        configure_pnl_contract(self.config,self.tmp.name)
        contract=self.contract();contract['schema_reviewed']=reviewed
        self.config['brain']['discovery_evidence']=contract
        detail={'id':'alpha123','is':{'sharpe':2,'fitness':1.5,'turnover':.2,'bookSize':100,'startDate':start,'endDate':end},
                'os':{'private_os_metric':987654},'regular':{'code':'rank(close)'},'settings':self.config['scope']}
        replies=[Response(200,{},detail),Response(200,{}, {'checks':fixture_result()['checks']})]
        valid=reviewed and isinstance(start,str) and isinstance(end,str) and start=='2020-01-01' and end=='2023-09-30'
        if valid:
            replies += [Response(200,{}, {'records':[{'year':y,'sharpe':1.2 if y<2023 else 999.0} for y in range(2019,2025)]}),
                Response(200,{}, {'records':[{'date':d,'pnl':p} for d,p in [
                    ('2019-12-31',0),('2020-01-01',1),('2020-01-02',2),('2023-09-29',3),
                    ('2023-09-30',5),('2023-10-01',1000),('2023-11-01',2000)]]})]
        replies += [Response(200,{}, {'max':.2})]
        transport=ScriptedTransport(replies)
        result=BrainSimulator(self.config,ApiClient('https://api.worldquantbrain.com',transport=transport)).collect('alpha123')
        self.assertEqual(transport.responses,[])
        return result,transport

    def test_partial_year_os_is_excluded_by_exact_dates_and_both_endpoints(self):
        result,_=self.collect()
        self.assertEqual(set(result['pnl_changes']),{'2020-01-01/2020-01-02','2023-09-29/2023-09-30'})
        self.assertEqual(result['pnl_changes']['2023-09-29/2023-09-30'],2)
        self.assertEqual([r['year'] for r in result['years']],['2020','2021','2022'])
        self.assertTrue(pnl_isolation_valid(result))
        self.assertEqual(result['daily_evidence']['status'],'incomplete')
        self.assertTrue(result['daily_evidence']['gap_intervals'])
        self.assertNotIn('private_os_metric',json.dumps(result));self.assertNotIn('999.0',json.dumps(result))

    def test_no_reviewed_contract_means_no_series_requests_or_year_fallback(self):
        result,transport=self.collect(reviewed=False)
        self.assertEqual(result['pnl_changes'],{});self.assertEqual(result['years'],[])
        self.assertFalse(pnl_isolation_valid(result))
        self.assertFalse(any('/recordsets/' in call[1] for call in transport.calls))
        self.assertIn('pnl_changes',result['evidence_errors'])

    def test_missing_reversed_or_timestamp_boundaries_are_not_guessed(self):
        for start,end in [(None,'2023-09-30'),('2024-01-01','2023-09-30'),('2020-01-01','2023-09-30T00:00:00Z'),('bad','2023-09-30')]:
            with self.subTest(start=start,end=end):
                result,transport=self.collect(start,end)
                self.assertFalse(pnl_isolation_valid(result));self.assertEqual(result['pnl_changes'],{})
                self.assertEqual(len(transport.calls),3)

    def test_years_do_not_define_the_pnl_boundary(self):
        result,_=self.collect()
        # Valid partial-year daily IS observations survive even though the unsafe
        # mixed 2023 annual aggregate was discarded.
        self.assertIn('2023-09-29/2023-09-30',result['pnl_changes'])
        self.assertNotIn('2023',[r['year'] for r in result['years']])

    def test_legacy_or_contaminated_cached_pnl_cannot_be_submission_evidence(self):
        for patch in ('missing','cross_boundary','fake_protocol'):
            result=fixture_result()
            if patch=='missing':result.pop('pnl_isolation')
            elif patch=='cross_boundary':result['pnl_changes']['2022-12-31/2023-01-01']=1
            else:result['pnl_isolation']['protocol']='year-filter'
            evaluation=evaluate(result,[fixture_result()]*2,self.config['policy'],True)
            self.assertFalse(evaluation['ready']);self.assertTrue(evaluation['research_eligible'])
            self.assertIn('unverified_pnl_sample_isolation',evaluation['missing'])
            self.assertIsNone(evaluation['worst_window_sharpe'])


class CandidateQueueTests(Base):
    def test_missing_evidence_is_preserved_in_research_queue_without_shortlist(self):
        self.live();sim=FixtureSimulator();old=sim.finish
        def incomplete(location):
            result=old(location);result['years']=[];result['self_correlation']=None
            result['pnl_changes']={};result.pop('pnl_isolation')
            return result
        sim.finish=incomplete
        engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        outcome=engine.propose('rank(close)','hypothesis','TEST ONLY')
        report=engine.report()
        self.assertEqual(report['shortlist'],[])
        self.assertEqual([c['trial_id'] for c in report['research_queue']],[outcome['trial_id']])
        self.assertEqual(report['research_queue'][0]['evaluation']['research_status'],'continue_research')
        self.assertFalse(report['research_queue'][0]['evaluation']['ready'])

    def test_known_failure_still_rejects_when_other_metrics_are_missing(self):
        result=fixture_result();result['metrics']['fitness']=None;result['metrics']['sharpe']=.1
        value=evaluate(result,[],self.config['policy'],True)
        self.assertFalse(value['research_eligible']);self.assertIn('low_sharpe',value['rejections'])

    def test_old_series_are_quarantined_in_report_without_rewriting_history(self):
        self.live();engine=Engine(self.config,simulator=FixtureSimulator());self.addCleanup(engine.memory.close)
        trial=engine.propose('rank(close)','hypothesis','TEST ONLY')['trial_id']
        old=engine.memory.latest(trial);old.pop('pnl_isolation');engine.memory.result(trial,old)
        report=engine.report()
        self.assertEqual(report['candidates'][0]['years'],[]);self.assertEqual(report['shortlist'],[])
        self.assertEqual(engine.memory.latest(trial),old)

    def test_get_refresh_reuses_alpha_and_spends_no_new_simulation(self):
        self.live();sim=FixtureSimulator();engine=Engine(self.config,simulator=sim);self.addCleanup(engine.memory.close)
        trial=engine.propose('rank(close)','hypothesis','TEST ONLY')['trial_id'];before=engine.memory.run(engine.run_id)['simulations']
        previous=engine.memory.latest(trial);previous.pop('pnl_isolation');engine.memory.result(trial,previous)
        outcome=engine.refresh_evidence(trial)
        self.assertFalse(outcome['simulation_created']);self.assertEqual(len(sim.starts),1)
        self.assertEqual(engine.memory.run(engine.run_id)['simulations'],before)
        self.assertTrue(pnl_isolation_valid(engine.memory.latest(trial)))
