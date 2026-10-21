import copy
from unittest import TestCase

from robust_fixtures import document,protocol
from alpharesearch.common import digest
from alpharesearch.cost_stress import cost_stress
from alpharesearch.return_contract import verify_return_document


class CostStressTests(TestCase):
    def inputs(self,net=False):
        d=document(n=126);d['contract']['net_of_costs']=net;s=verify_return_document(d)
        rows=[{'date':day,'value':.2} for day in s.dates]
        contract={'candidate_id':s.candidate_id,'return_input_sha256':s.input_sha256,'reviewed':True,
            'review_note':'SYNTHETIC ONLY','source_sha256':digest('TEST turnover'),
            'unit':'one_way_traded_notional_over_same_capital','cost_basis':'incremental_only',
            'observations':rows,'data_sha256':digest(rows)}
        return s,protocol([d]),contract

    def test_unknown_cost_contract_only_reports_assumptions_no_net_metrics(self):
        s,p,c=self.inputs();result=cost_stress(s,p,None)
        self.assertEqual(result['status'],'assumption_only');self.assertIsNone(result['scenarios'][0]['net_return_metrics'])

    def test_net_backtest_only_receives_explicit_incremental_stress(self):
        s,p,c=self.inputs(True);result=cost_stress(s,p,c)
        self.assertFalse(result['base_costs_subtracted_again'])
        value=result['scenarios'][0];self.assertEqual(value['interpretation'],'additional_cost_stress_on_existing_net')
        self.assertAlmostEqual(value['net_return_metrics']['mean_daily_return'],sum(s.returns)/len(s.returns)-.2*2/10000)
        self.assertTrue(result['not_brain_reported_metrics'])

    def test_zero_incremental_bps_preserves_base_and_never_charges_again(self):
        s,p,c=self.inputs(True);p['cost_scenarios'][0]['bps']=0
        p['protocol_sha256']=digest({k:v for k,v in p.items() if k!='protocol_sha256'})
        value=cost_stress(s,p,c)['scenarios'][0]
        self.assertEqual(value['return_sha256'],digest(list(s.returns)))

    def test_turnover_dates_units_identity_and_hash_must_match(self):
        s,p,c=self.inputs()
        for key,value in [('unit','unknown'),('return_input_sha256','b'*64),('candidate_id','other'),
                          ('data_sha256','a'*64),('cost_basis','subtract_total_cost'),('observations',[])]:
            other=copy.deepcopy(c);other[key]=value
            self.assertEqual(cost_stress(s,p,other)['status'],'assumption_only',key)
