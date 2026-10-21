"""BRAIN evidence fixtures, not real account or investment evidence."""
import unittest
import copy
import json
import math
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch

from test_system import Base,ScriptedTransport,configure_pnl_contract,fixture_result,attach_return_contract
from alpharesearch.brain import pnl_changes,discovery_isolation,BrainSimulator
from alpharesearch.http import ApiError
from alpharesearch.pnl_evidence import reviewed_context,build_daily_evidence,daily_differences,daily_evidence_valid,stability_windows
from alpharesearch.evaluator import evaluate,shortlist
from alpharesearch.probe import inspect_alpha_evidence
from alpharesearch.common import ResearchError,digest
from alpharesearch.http import ApiClient,Response


class PnlReliabilityTests(unittest.TestCase):
    def test_no_calendar_cannot_turn_a_three_year_difference_into_daily_pnl(self):
        payload={'records':[{'date':'2020-01-02','pnl':1.0},{'date':'2023-09-29','pnl':7.0}]}
        with self.assertRaises(ApiError):pnl_changes(payload)

    def test_sparse_two_point_calendar_cannot_attest_a_three_year_day(self):
        payload={'records':[{'date':'2020-01-02','pnl':1.0},{'date':'2023-09-29','pnl':7.0}]}
        with self.assertRaises(ApiError):pnl_changes(payload,['2020-01-02','2023-09-29'])


def synthetic_sessions(start='2020-07-01',end='2023-06-30'):
    # TEST calendar: weekdays with no real exchange holiday claim.
    left,right=date.fromisoformat(start),date.fromisoformat(end)
    return [(left+timedelta(days=i)).isoformat() for i in range((right-left).days+1) if (left+timedelta(days=i)).weekday()<5]


class ReliabilityTests(Base):
    def evidence(self,sessions=None,missing=(),extra=(),values=None):
        sessions=sessions or synthetic_sessions()
        configure_pnl_contract(self.config,self.tmp.name,sessions)
        self.config['brain']['discovery_evidence']={'schema_reviewed':True,'review_note':'TEST ONLY',
            'date_semantics':'inclusive_dates','is_start_path':['is','startDate'],'is_end_path':['is','endDate']}
        detail={'is':{'startDate':sessions[0],'endDate':sessions[-1],'bookSize':100.}}
        isolation=discovery_isolation(detail,self.config['brain']['discovery_evidence'])
        context=reviewed_context(self.config,detail,isolation)
        cumulative=0.;rows=[]
        for i,d in enumerate(sessions):
            if i:cumulative+=(values[i-1] if values is not None else .2+math.sin(i*.71))
            if d not in missing:rows.append({'date':d,'pnl':cumulative})
        rows.extend(extra)
        result=fixture_result();result['pnl_isolation']=isolation
        result.update(build_daily_evidence({'records':rows},context))
        if daily_evidence_valid(result):
            attach_return_contract(result)
        return result,detail,rows

    def test_three_year_gap_is_quarantined_even_with_valid_is_bounds(self):
        sessions=synthetic_sessions('2020-01-02','2023-09-29')
        result,_,_=self.evidence(sessions,missing=sessions[1:-1])
        self.assertEqual(result['pnl_changes'],{})
        self.assertEqual(result['daily_evidence']['gap_intervals'][0]['missing_sessions'],len(sessions)-2)
        self.assertFalse(daily_evidence_valid(result))
        self.assertFalse(evaluate(result,[fixture_result()]*2,self.config['policy'],True)['ready'])

    def test_weekend_and_attested_holiday_are_not_missing_sessions(self):
        # Friday -> Tuesday is adjacent when the reviewer attests Monday closed.
        sessions=['2023-01-06','2023-01-10','2023-01-11']
        result,_,_=self.evidence(sessions)
        self.assertIn('2023-01-06/2023-01-10',result['pnl_changes'])
        self.assertTrue(daily_evidence_valid(result));self.assertEqual(result['daily_evidence']['interval_coverage'],1)

    def test_missing_expected_session_is_not_relabelled_as_a_holiday(self):
        sessions=['2023-01-06','2023-01-09','2023-01-10','2023-01-11']
        result,_,_=self.evidence(sessions,missing=['2023-01-09'])
        self.assertNotIn('2023-01-06/2023-01-10',result['pnl_changes'])
        self.assertIn('2023-01-10/2023-01-11',result['pnl_changes'])
        self.assertEqual(result['daily_evidence']['gap_intervals'][0]['missing_sessions'],1)
        self.assertFalse(daily_evidence_valid(result))

    def test_missing_boundary_points_are_incomplete_not_zero_filled(self):
        sessions=synthetic_sessions()
        for missing in ([sessions[0]],[sessions[-1]]):
            result,_,_=self.evidence(sessions,missing=missing)
            self.assertFalse(daily_evidence_valid(result));self.assertEqual(result['daily_evidence']['missing_dates'],missing)
            self.assertEqual(len(result['pnl_changes']),len(sessions)-2)

    def test_non_session_rows_are_flagged_and_not_used_to_bridge(self):
        result,_,_=self.evidence(['2023-01-06','2023-01-09','2023-01-10'],extra=[{'date':'2023-01-07','pnl':5.}])
        self.assertEqual(result['daily_evidence']['off_calendar_dates'],['2023-01-07'])
        self.assertNotIn('2023-01-06/2023-01-09',result['pnl_changes'])
        self.assertFalse(daily_evidence_valid(result))

    def test_unreviewed_units_resets_and_calendar_do_not_get_defaults(self):
        result,detail,_=self.evidence()
        base=copy.deepcopy(self.config['brain']['pnl_evidence'])
        for change in ({'reviewed':False},{'value_semantics':'daily_pnl'},{'capital_path':[]},{'calendar_reviewed':False},
                       {'calendar_region':'EUR'},{'calendar_start':'2021-01-01'},{'calendar_sha256':'0'*64}):
            self.config['brain']['pnl_evidence']={**base,**change}
            with self.subTest(change=change):
                with self.assertRaises(ApiError):reviewed_context(self.config,detail,result['pnl_isolation'])

    def test_calendar_change_after_review_is_detected(self):
        result,detail,_=self.evidence();path=Path(self.config['brain']['pnl_evidence']['calendar_file'])
        path.write_text(path.read_text(encoding="utf-8")+'2024-01-01\n')
        with self.assertRaisesRegex(ApiError,'SHA-256'):reviewed_context(self.config,detail,result['pnl_isolation'])

    def test_duplicate_dates_nonfinite_values_and_overflow_are_rejected(self):
        sessions=['2023-01-06','2023-01-09']
        for rows in ([{'date':sessions[0],'pnl':1},{'date':sessions[0],'pnl':2}],
                     [{'date':sessions[0],'pnl':float('nan')}],
                     [{'date':sessions[0],'pnl':-1e308},{'date':sessions[1],'pnl':1e308}]):
            with self.assertRaises(ApiError):daily_differences({'records':rows},sessions)

    def test_midyear_three_year_is_uses_fixed_windows_instead_of_three_calendar_years(self):
        result,_,_=self.evidence()
        result['years']=[{'year':'2021','sharpe':1.},{'year':'2022','sharpe':1.}]
        value=evaluate(result,[fixture_result()]*2,self.config['policy'],True)
        self.assertTrue(value['ready']);self.assertGreaterEqual(len(value['stability']['windows']),4)
        self.assertEqual(value['stability']['window_sessions'],126)
        self.assertTrue(all(w['observations']==126 and w['coverage']==1 for w in value['stability']['windows']))

    def test_unavailable_annual_endpoint_is_not_a_stability_gate(self):
        result,_,_=self.evidence();result['years']=[]
        self.assertTrue(evaluate(result,[fixture_result()]*2,self.config['policy'],True)['ready'])

    def test_windows_are_nonoverlapping_anchored_and_trailing_count_is_explicit(self):
        result,_,_=self.evidence();windows=stability_windows(result,self.config['policy'])
        expected=len(result['daily_evidence']['session_dates'])-1
        self.assertEqual(len(windows['windows']),expected//126)
        self.assertEqual(windows['trailing_observations'],expected%126)
        self.assertEqual(windows['windows'][0]['start_date'],'2020-07-01')
        for left,right in zip(windows['windows'],windows['windows'][1:]):self.assertEqual(left['end_date'],right['start_date'])

    def test_gap_window_is_shown_with_coverage_and_no_performance_score(self):
        sessions=synthetic_sessions();result,_,_=self.evidence(sessions,missing=[sessions[150]])
        value=stability_windows(result,self.config['policy'])
        self.assertFalse(value['complete']);self.assertIsNone(value['worst_window_sharpe'])
        self.assertLess(value['windows'][1]['coverage'],1)
        self.assertTrue(all(w['sharpe'] is None for w in value['windows']))
        self.assertEqual(value['windows'][1]['start_date'],sessions[126])

    def test_short_history_does_not_shorten_windows_or_minimum_to_get_a_pass(self):
        result,_,_=self.evidence(synthetic_sessions('2021-01-01','2021-12-31'))
        value=evaluate(result,[fixture_result()]*2,self.config['policy'],True)
        self.assertFalse(value['ready']);self.assertTrue(value['research_eligible'])
        self.assertIn('insufficient_fixed_stability_windows',value['missing'])

    def test_weak_or_constant_windows_cannot_pass_on_platform_sharpe_alone(self):
        sessions=synthetic_sessions();values=[-.2+math.sin(i*.71) for i in range(len(sessions)-1)]
        result,_,_=self.evidence(sessions,values=values)
        self.assertIn('unstable_fixed_windows',evaluate(result,[fixture_result()]*2,self.config['policy'],True)['rejections'])
        result,_,_=self.evidence(sessions,values=[.2]*(len(sessions)-1))
        self.assertIn('undefined_window_sharpe',evaluate(result,[fixture_result()]*2,self.config['policy'],True)['missing'])

    def test_legacy_marker_or_tampered_coverage_cannot_bypass_recomputation(self):
        for change in ('protocol','cross_gap','returns','coverage'):
            result,_,_=self.evidence()
            if change=='protocol':result['daily_evidence']['protocol']='year-filter'
            elif change=='cross_gap':
                dates=result['daily_evidence']['session_dates'];result['pnl_changes'][dates[0]+'/'+dates[2]]=1
            elif change=='returns':result['daily_returns'][next(iter(result['daily_returns']))]=999.
            else:result['pnl_changes'].pop(next(iter(result['pnl_changes'])))
            self.assertFalse(daily_evidence_valid(result))

    def test_record_order_does_not_change_daily_intervals(self):
        result,detail,rows=self.evidence()
        context=reviewed_context(self.config,detail,result['pnl_isolation'])
        other=build_daily_evidence({'records':list(reversed(rows))},context)
        self.assertEqual(result['pnl_changes'],other['pnl_changes']);self.assertEqual(result['daily_returns'],other['daily_returns'])

    def test_evidence_probe_does_no_simulation_and_fixture_is_not_real_acceptance(self):
        self.live();result,_,_=self.evidence();result['settings']=self.config['scope']
        sim=BrainSimulator(self.config);sim.account_ref=digest('TEST_ONLY')
        record={'state':'completed','metrics_parse_verified':True,'account_ref':sim.account_ref,'alpha_id':'alpha123',
            'scope':self.config['scope'],'api_base_url':self.config['brain']['base_url'],'expression':result['expression'],'metrics':result['metrics']}
        path=Path(self.tmp.name)/'smoke.json';path.write_text(json.dumps(record));out=Path(self.tmp.name)/'evidence.json'
        # Explicit fixture transport ensures this test cannot claim real HTTP.
        sim.api.transport=ScriptedTransport([])
        with patch.object(sim,'collect',return_value=result) as collect,patch.object(sim,'start') as start:
            value=inspect_alpha_evidence(sim,path,out)
        start.assert_not_called();collect.assert_called_once_with('alpha123')
        self.assertTrue(value['daily_evidence_complete']);self.assertFalse(value['real_account_acceptance_completed'])
        self.assertFalse(value['simulation_created']);self.assertFalse(value['submission_enabled_by_probe'])

    def test_evidence_probe_mismatches_fail_without_a_pass_record(self):
        self.live();result,_,_=self.evidence();result['settings']=self.config['scope']
        sim=BrainSimulator(self.config);sim.account_ref=digest('TEST_ONLY')
        record={'state':'completed','metrics_parse_verified':True,'account_ref':sim.account_ref,'alpha_id':'alpha123',
            'scope':self.config['scope'],'api_base_url':self.config['brain']['base_url'],'expression':'WRONG','metrics':result['metrics']}
        path=Path(self.tmp.name)/'smoke.json';path.write_text(json.dumps(record));out=Path(self.tmp.name)/'evidence.json'
        with patch.object(sim,'collect',return_value=result):
            with self.assertRaises(ResearchError):inspect_alpha_evidence(sim,path,out)
        self.assertFalse(out.exists())

    def test_readonly_probe_reconciles_via_scripted_get_endpoints_with_provenance(self):
        self.live();result,detail,rows=self.evidence()
        detail.update(id='alpha123',regular={'code':'rank(close)'},settings=self.config['scope'])
        detail['is'].update(result['metrics'])
        transport=ScriptedTransport([Response(200,{},detail),Response(200,{}, {'checks':result['checks']}),
            Response(404,{},{}),Response(200,{}, {'records':rows}),Response(200,{}, {'max':.2})])
        sim=BrainSimulator(self.config,ApiClient(self.config['brain']['base_url'],transport=transport));sim.account_ref=digest('TEST_ONLY')
        record={'state':'completed','metrics_parse_verified':True,'account_ref':sim.account_ref,'alpha_id':'alpha123',
            'scope':self.config['scope'],'api_base_url':self.config['brain']['base_url'],'expression':'rank(close)',
            'metrics':result['metrics'],'observation_source':'scripted_or_unknown_transport'}
        path=Path(self.tmp.name)/'smoke.json';path.write_text(json.dumps(record));out=Path(self.tmp.name)/'evidence.json'
        value=inspect_alpha_evidence(sim,path,out)
        self.assertTrue(all(c[0]=='GET' for c in transport.calls));self.assertEqual(transport.responses,[])
        self.assertTrue(value['daily_evidence_complete'])
        # Scripted transport is not verified live return evidence, even with
        # complete synthetic daily observations. It must not yield trusted scores.
        self.assertFalse(value['stability']['complete']);self.assertIsNone(value['stability']['worst_window_sharpe'])
        self.assertEqual(value['source']['alpha_id'],'alpha123');self.assertEqual(value['source']['account_ref'],sim.account_ref)
        self.assertEqual(value['source']['calendar_sha256'],self.config['brain']['pnl_evidence']['calendar_sha256'])
        self.assertFalse(value['real_account_acceptance_completed'])

    def test_forward_brain_path_also_rejects_cross_session_gaps(self):
        from alpharesearch import tracking
        self.live();sessions=['2020-01-02','2020-01-03','2020-01-06','2020-01-07'];result,detail,rows=self.evidence(sessions,missing=['2020-01-06'])
        review={'mode':'live','candidates':[{'trial_id':'TEST','alpha_id':'alpha123','expression':'rank(close)'}],
            'snapshot':{'scope':self.config['scope'],'catalog':{'account_id':'TEST_ONLY'},'policy':self.config['policy'],'brain':self.config['brain']}}
        path=Path(self.tmp.name)/'review.json';path.write_text(json.dumps(review));ledger=Path(self.tmp.name)/'forward.sqlite3'
        spec=tracking.register(path,'TEST','2020-01-02',ledger,today=date(2020,1,1))
        detail.update(id='alpha123',regular={'code':'rank(close)'},settings=self.config['scope'])
        transport=ScriptedTransport([Response(200,{},detail),Response(200,{}, {'records':rows})])
        sim=BrainSimulator(self.config,ApiClient(self.config['brain']['base_url'],transport=transport));sim.account_ref=digest('TEST_ONLY')
        with patch.object(tracking,'today_in',return_value=date(2020,1,7)):
            with self.assertRaisesRegex(ResearchError,'不完整'):tracking.update_from_brain(ledger,spec['monitor_id'],sim)
        self.assertEqual(tracking.tracking_report(ledger,spec['monitor_id'])['metrics']['observations'],0)

    def test_diversity_pool_does_not_mix_different_calendar_contracts(self):
        result,_,_=self.evidence();result['evaluation']=evaluate(result,[fixture_result()]*2,self.config['policy'],True)
        result.update(trial_id='A',family='A');other=copy.deepcopy(result);other.update(trial_id='B',family='B')
        other['daily_evidence']['calendar_sha256']='d'*64
        selected,excluded=shortlist([result,other],self.config['policy'])
        self.assertEqual(len(selected),1);self.assertEqual(excluded[0]['reason'],'daily_evidence_protocol_mismatch')
