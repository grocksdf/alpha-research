import copy
import math
from unittest import TestCase

from robust_fixtures import document
from alpharesearch.common import digest
from alpharesearch.return_contract import (EvidenceError,VerifiedReturnSeries,aligned_series,
    require_verified,verify_return_document,review_return_document,brain_return_document)


class ReturnContractTests(TestCase):
    def test_daily_fraction_is_explicit_and_immutable(self):
        doc=document();series=verify_return_document(doc);original=series.returns
        doc['observations'][0]['value']=99.;meta=series.contract;meta['unit']='percent'
        self.assertEqual(series.returns,original);self.assertEqual(series.contract['unit'],'fraction')
        self.assertTrue(series.synthetic);self.assertFalse(series.summary()['empirical_evidence'])

    def test_cash_daily_and_cumulative_need_reviewed_capital(self):
        doc=document(n=180);c=doc['contract'];c.update(value_semantics='daily_cash_pnl',unit='USD',
            return_basis='constant_capital',no_resets_or_flows_reviewed=True,
            capital_basis={'kind':'constant','unit':'USD','value':100.,'reviewed':True,'source_sha256':digest('TEST capital')})
        expected=[r['value'] for r in doc['observations']]
        for r in doc['observations']:r['value']*=100
        doc['data_sha256']=digest(doc['observations'])
        daily=verify_return_document(doc)
        for a,b in zip(daily.returns,expected):self.assertAlmostEqual(a,b)
        c['value_semantics']='cumulative_cash_pnl';total=0.
        for r in doc['observations']:total+=r['value'];r['value']=total
        doc['data_sha256']=digest(doc['observations']);cumulative=verify_return_document(doc)
        for a,b in zip(cumulative.returns,expected[1:]):self.assertAlmostEqual(a,b)
        c['capital_basis']['value']=None
        self.assertIsNone(review_return_document(doc)[0])

    def test_nav_requires_no_flows_and_total_return_semantics(self):
        doc=document(n=10);c=doc['contract'];c.update(value_semantics='nav',unit='USD',return_basis='total_return_nav',
            no_resets_or_flows_reviewed=True,capital_basis={'kind':'total_return_nav','reviewed':True,'source_sha256':digest('TEST NAV')})
        for i,r in enumerate(doc['observations']):r['value']=100.+i
        doc['data_sha256']=digest(doc['observations']);series=verify_return_document(doc)
        self.assertAlmostEqual(series.returns[0],.01);self.assertEqual(len(series.returns),9)
        c['no_resets_or_flows_reviewed']=False;self.assertIsNone(review_return_document(doc)[0])

    def test_unknown_or_inconsistent_unit_semantics_costs_never_yield_returns(self):
        for key,value in [('unit','percent'),('value_semantics','unverified'),('net_of_costs','unverified'),
                          ('net_of_costs',1),('costs_reviewed',False),('cost_model_id',''),('reviewed',False),
                          ('is_reviewed',False),('pit_reviewed',False)]:
            with self.subTest(key=key,value=value):
                doc=document();doc['contract'][key]=value;series,report=review_return_document(doc)
                self.assertIsNone(series);self.assertEqual(report['status'],'insufficient_evidence')
                self.assertFalse(report['performance_metrics_computed'])

    def test_duplicate_missing_nonfinite_and_mixed_dates_rejected(self):
        for mutate in ('duplicate','missing','nan','outside'):
            doc=document()
            if mutate=='duplicate':doc['observations'][1]['date']=doc['observations'][0]['date']
            elif mutate=='missing':doc['observations'].pop(20)
            elif mutate=='nan':doc['observations'][0]['value']=float('nan')
            else:doc['observations'][0]['date']='2019-12-31'
            if mutate!='nan':doc['data_sha256']=digest(doc['observations'])
            self.assertIsNone(review_return_document(doc)[0],mutate)

    def test_calendar_boundary_and_hash_are_independently_checked(self):
        doc=document();doc['contract']['is_interval']['start']='2019-01-01';doc['calendar']['coverage_start']='2019-01-01'
        self.assertIsNone(review_return_document(doc)[0])
        doc=document();doc['calendar']['sha256']='a'*64;self.assertIsNone(review_return_document(doc)[0])

    def test_final_holdout_forward_and_unknown_samples_are_forbidden(self):
        for sample in ('final_holdout','forward','os',None):
            doc=document();doc['contract']['sample_role']=sample;self.assertIsNone(review_return_document(doc)[0])
        doc=document();doc['contract']['excluded_intervals']=[doc['contract']['is_interval'].copy()]
        self.assertIsNone(review_return_document(doc)[0])

    def test_invalid_normalized_return_and_bool_values_rejected(self):
        for value in (-1.,-2.,True,float('inf')):
            doc=document();doc['observations'][1]['value']=value
            if math.isfinite(value):doc['data_sha256']=digest(doc['observations'])
            self.assertIsNone(review_return_document(doc)[0])

    def test_different_universe_costs_populations_and_dates_not_intersected(self):
        a=document('a');base=verify_return_document(a)
        for mutate in ('universe','cost','population','dates','synthetic'):
            b=document('b')
            if mutate=='universe':b['contract']['scope']['universe']='OTHER'
            elif mutate=='cost':b['contract']['net_of_costs']=True
            elif mutate=='population':b['contract']['dataset_id']=b['contract']['population_id']='OTHER'
            elif mutate=='dates':b['observations'].pop();b['calendar']['sessions'].pop();b['calendar']['sha256']=digest(b['calendar']['sessions']);b['data_sha256']=digest(b['observations'])
            else:b['synthetic']=False
            with self.assertRaises(EvidenceError):aligned_series([base,verify_return_document(b)])

    def test_brain_requires_account_and_alpha_settings_identity(self):
        doc=document();c=doc['contract'];c.update(source_type='brain_pnl',source_id=doc['candidate_id'],
            population_id='a'*64,account_ref='a'*64,settings_sha256=digest('TEST settings'),expression_sha256=digest('TEST formula'))
        self.assertIsNotNone(verify_return_document(doc))
        c['source_id']='other';self.assertIsNone(review_return_document(doc)[0])
        c['source_id']=doc['candidate_id'];c['account_ref']=None;self.assertIsNone(review_return_document(doc)[0])

    def test_stored_complete_label_is_not_a_verified_series(self):
        with self.assertRaises(EvidenceError):require_verified({'status':'verified','returns':[.1]})
        forged=VerifiedReturnSeries('x',('2020-01-01',),(.1,),'a'*64,True,'{}',object())
        with self.assertRaises(EvidenceError):require_verified(forged)

    def test_unverified_brain_never_builds_normalized_document(self):
        with self.assertRaises(EvidenceError):brain_return_document({'brain':{}},{'pnl_isolation':{}},{})

    def test_reviewed_brain_adapter_preserves_units_capital_and_daily_values(self):
        from test_system import fixture_result
        result=fixture_result('rank(close)',{'region':'USA','universe':'TOP3000','delay':1})
        proof=result['daily_evidence'];total=0.;rows=[{'date':proof['session_dates'][0],'pnl':0.}]
        for day in proof['session_dates'][1:]:
            key=proof['session_dates'][len(rows)-1]+'/'+day;total+=result['pnl_changes'][key]
            rows.append({'date':day,'pnl':total})
        payload={'records':rows};result['evidence_source']={'transport':'account_http','account_ref':'a'*64,
            'responses':{'pnl':{'payload_sha256':digest(payload)},'alpha':{'payload_sha256':digest('TEST detail')}}}
        config={'brain':{'pnl_evidence':{'reviewed':True,'review_note':'TEST ONLY synthetic transport fixture',
            'calendar_review_note':'TEST ONLY not a real market calendar','costs_reviewed':True,
            'net_of_costs':False,'cost_model_id':'TEST_GROSS','cost_review_note':'TEST ONLY gross'}}}
        document=brain_return_document(config,result,payload);series=verify_return_document(document)
        self.assertEqual(series.contract['unit'],'USD');self.assertEqual(series.contract['capital_basis']['value'],100.)
        for a,b in zip(series.returns,result['daily_returns'].values()):self.assertAlmostEqual(a,b)
        config['brain']['pnl_evidence']['costs_reviewed']=False
        self.assertIsNone(review_return_document(brain_return_document(config,result,payload))[0])
