"""Regression for truncated calendar edges; all observations are synthetic."""
from datetime import date,timedelta

from test_system import Base,configure_pnl_contract,fixture_result
from alpharesearch.brain import discovery_isolation
from alpharesearch.http import ApiError
from alpharesearch.pnl_evidence import reviewed_context,build_daily_evidence,daily_evidence_valid,stability_windows
from alpharesearch.evaluator import evaluate


def sessions_between(start,end):
    left,right=date.fromisoformat(start),date.fromisoformat(end)
    return [(left+timedelta(days=i)).isoformat() for i in range((right-left).days+1) if (left+timedelta(days=i)).weekday()<5]


class CalendarBoundaryTests(Base):
    def setup_calendar(self,sessions,start,end):
        contract=configure_pnl_contract(self.config,self.tmp.name,sessions)
        contract.update(calendar_start=start,calendar_end=end)
        detail={'is':{'startDate':start,'endDate':end,'bookSize':100.}}
        discovery={'schema_reviewed':True,'review_note':'TEST ONLY',
            'date_semantics':'inclusive_dates','is_start_path':['is','startDate'],'is_end_path':['is','endDate']}
        return detail,discovery_isolation(detail,discovery)

    def legacy_cache(self,sessions,start,end):
        # Simulate the v0.3.2 proof shape without relying on the repaired creator.
        # PnL is complete relative to this truncated calendar; its IS edges are not.
        context={'protocol':'brain-session-pnl-v1','value_semantics':'cumulative_cash_pnl_constant_capital_session_close',
            'pnl_unit':'USD','capital_unit':'USD', # Isolate the boundary regression from the v0.4 unit gate.
            'contract_sha256':'b'*64,'calendar_id':'TEST-calendar','calendar_sha256':'c'*64,'calendar_region':'USA',
            'calendar_source':'TEST ONLY','calendar_start':start,'calendar_end':end,
            'start_date':start,'end_date':end,'session_dates':sessions,'constant_capital':100.,'capital_path':['is','bookSize']}
        import math
        cumulative=0.;rows=[{'date':sessions[0],'pnl':0.}]
        for i,d in enumerate(sessions[1:]):
            cumulative+=.2+math.sin(i*.71);rows.append({'date':d,'pnl':cumulative})
        result=fixture_result();result.update(build_daily_evidence({'records':rows},context))
        result['pnl_isolation'].update(start_date=start,end_date=end)
        return result

    def test_reviewed_context_rejects_the_184_and_150_day_counterexample(self):
        sessions=sessions_between('2021-01-01','2023-01-31')
        start,end='2020-07-01','2023-06-30'
        self.assertEqual((date.fromisoformat(sessions[0])-date.fromisoformat(start)).days,184)
        self.assertEqual((date.fromisoformat(end)-date.fromisoformat(sessions[-1])).days,150)
        detail,bounds=self.setup_calendar(sessions,start,end)
        with self.assertRaisesRegex(ApiError,'首尾'):reviewed_context(self.config,detail,bounds)

    def test_cached_complete_marker_does_not_certify_truncated_is_edges(self):
        result=self.legacy_cache(sessions_between('2021-01-01','2023-01-31'),'2020-07-01','2023-06-30')
        self.assertEqual(result['daily_evidence']['status'],'complete')
        self.assertFalse(daily_evidence_valid(result))
        windows=stability_windows(result,self.config['policy'])
        self.assertFalse(windows['complete']);self.assertIsNone(windows['worst_window_sharpe'])
        value=evaluate(result,[fixture_result()]*2,self.config['policy'],True)
        self.assertFalse(value['ready']);self.assertTrue(value['research_eligible'])

    def test_creator_rejects_a_start_only_long_gap(self):
        sessions=sessions_between('2021-01-01','2023-06-30')
        detail,bounds=self.setup_calendar(sessions,'2020-07-01','2023-06-30')
        with self.assertRaises(ApiError):reviewed_context(self.config,detail,bounds)

    def test_creator_rejects_an_end_only_long_gap(self):
        sessions=sessions_between('2020-07-01','2023-01-31')
        detail,bounds=self.setup_calendar(sessions,'2020-07-01','2023-06-30')
        with self.assertRaises(ApiError):reviewed_context(self.config,detail,bounds)

    def test_cached_start_only_long_gap_is_rejected(self):
        result=self.legacy_cache(sessions_between('2021-01-01','2023-06-30'),'2020-07-01','2023-06-30')
        self.assertFalse(daily_evidence_valid(result))

    def test_cached_end_only_long_gap_is_rejected(self):
        result=self.legacy_cache(sessions_between('2020-07-01','2023-01-31'),'2020-07-01','2023-06-30')
        self.assertFalse(daily_evidence_valid(result))

    def test_creator_and_cache_agree_at_14_and_15_day_boundaries(self):
        sessions=sessions_between('2021-01-04','2023-01-31')
        for start_gap,end_gap,accepted in ((0,0,True),(14,0,True),(0,14,True),(14,14,True),(15,0,False),(0,15,False),(15,15,False)):
            start=(date.fromisoformat(sessions[0])-timedelta(days=start_gap)).isoformat()
            end=(date.fromisoformat(sessions[-1])+timedelta(days=end_gap)).isoformat()
            with self.subTest(start_gap=start_gap,end_gap=end_gap):
                detail,bounds=self.setup_calendar(sessions,start,end)
                if accepted:
                    context=reviewed_context(self.config,detail,bounds)
                    self.assertEqual(context['session_dates'],sessions)
                else:
                    with self.assertRaises(ApiError):reviewed_context(self.config,detail,bounds)
                self.assertEqual(daily_evidence_valid(self.legacy_cache(sessions,start,end)),accepted)

    def test_legitimate_nearby_weekend_edges_remain_accepted(self):
        sessions=sessions_between('2021-01-04','2023-01-06')
        start,end='2021-01-01','2023-01-08'
        detail,bounds=self.setup_calendar(sessions,start,end)
        self.assertEqual(reviewed_context(self.config,detail,bounds)['session_dates'],sessions)
        self.assertTrue(daily_evidence_valid(self.legacy_cache(sessions,start,end)))

    def test_actual_is_subinterval_is_checked_not_outer_calendar_edges(self):
        # A larger verified calendar must not make a valid IS subinterval fail.
        sessions=sessions_between('2020-01-01','2023-12-29')
        detail,bounds=self.setup_calendar(sessions,sessions[0],sessions[-1])
        detail['is'].update(startDate='2021-07-01',endDate='2022-06-30')
        bounds.update(start_date='2021-07-01',end_date='2022-06-30')
        context=reviewed_context(self.config,detail,bounds)
        self.assertEqual(context['session_dates'][0],'2021-07-01')
        self.assertEqual(context['session_dates'][-1],'2022-06-30')
