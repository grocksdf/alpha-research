import copy
import math
import statistics
import random
from unittest import TestCase

from robust_fixtures import manifest,protocol
from alpharesearch.common import digest
from alpharesearch.return_contract import verify_return_document
from alpharesearch.robustness import sharpe
from alpharesearch.selection_bias import pbo,dsr,audited_history


class SelectionBiasTests(TestCase):
    def inputs(self,count=8):
        m=manifest(count,252);return m,protocol(m['series']),[verify_return_document(d) for d in m['series']]

    def test_four_alphas_are_not_a_credible_overall_overfitting_probability(self):
        m,p,series=self.inputs(4)
        self.assertEqual(pbo(series,p,m['history'])['status'],'unavailable')
        self.assertEqual(dsr(series[0],p,m['history'])['status'],'unavailable')

    def test_cscv_enumerates_all_balanced_time_splits_and_correct_best_rank(self):
        m,p,series=self.inputs();value=pbo(series,p,m['history'])
        self.assertEqual(value['status'],'computed');self.assertEqual(value['split_count'],70)
        self.assertEqual(value['value'],0.)
        for fold in value['folds']:
            self.assertEqual(fold['winner'],'SYNTHETIC_7');self.assertEqual(fold['test_rank_ascending'],8)
            self.assertEqual(fold['relative_rank'],8/9);self.assertAlmostEqual(fold['logit'],math.log(8))
            self.assertEqual(set(fold['train_blocks'])|set(fold['test_blocks']),set(range(8)))
            self.assertFalse(set(fold['train_blocks'])&set(fold['test_blocks']))

    def test_dates_are_not_silently_intersected(self):
        m,p,series=self.inputs();d=m['series'][0];d['observations'].pop();d['calendar']['sessions'].pop()
        d['data_sha256']=digest(d['observations']);d['calendar']['sha256']=digest(d['calendar']['sessions'])
        series[0]=verify_return_document(d)
        self.assertEqual(pbo(series,p,m['history'])['reasons'],['candidate_dates_not_identical'])

    def test_cscv_detects_constructed_training_winners_losing_outside_training(self):
        m=manifest(8,336);rng=random.Random(123)
        for doc in m['series']:
            shifts=[rng.uniform(-.0001,.0001) for _ in range(8)];center=statistics.mean(shifts)
            for i,row in enumerate(doc['observations']):
                row['value']=.01*math.sin(2*math.pi*(i%42)/42)+shifts[i//42]-center
            doc['data_sha256']=digest(doc['observations'])
        series=[verify_return_document(d) for d in m['series']]
        value=pbo(series,protocol(m['series']),m['history'])
        self.assertEqual(value['status'],'computed');self.assertEqual(value['value'],1.)
        self.assertTrue(all(f['test_rank_ascending']==1 for f in value['folds']))

    def test_partial_history_or_failed_unmodeled_variant_prevents_pbo(self):
        m,p,series=self.inputs()
        for mutate in ('unknown','survivors','failed','hash','count'):
            h=copy.deepcopy(m['history'])
            if mutate=='unknown':h=None
            elif mutate=='survivors':h['complete']=False
            elif mutate=='failed':
                h['attempts'][0]['status']='failed';h['attempts_sha256']=digest(h['attempts'])
            elif mutate=='hash':h['attempts_sha256']='a'*64
            else:h['total_attempts']=999
            self.assertEqual(pbo(series,p,h)['status'],'unavailable',mutate)

    def test_missing_frozen_candidate_and_rank_ties_fail_closed(self):
        m,p,series=self.inputs();self.assertEqual(pbo(series[:-1],p,m['history'])['status'],'unavailable')
        m['series'][1]['observations']=copy.deepcopy(m['series'][0]['observations'])
        m['series'][1]['data_sha256']=digest(m['series'][1]['observations'])
        # Tie the two highest candidates, so the training winner cannot be unique.
        m['series'][7]['observations']=copy.deepcopy(m['series'][6]['observations'])
        m['series'][7]['data_sha256']=digest(m['series'][7]['observations'])
        series=[verify_return_document(d) for d in m['series']]
        self.assertEqual(pbo(series,p,m['history'])['status'],'unavailable')

    def test_dsr_uses_daily_units_and_normal_probability_at_null_threshold(self):
        m,p,series=self.inputs();h=m['history'];normal=statistics.NormalDist();n=8;gamma=.5772156649015329
        factor=(1-gamma)*normal.inv_cdf(1-1/n)+gamma*normal.inv_cdf(1-1/(n*math.e))
        score=sharpe(series[0].returns);h['dsr_calibration']['sharpe_variance_daily']=(score/factor)**2
        value=dsr(series[0],p,h)
        self.assertEqual(value['status'],'computed');self.assertAlmostEqual(value['value'],.5)
        self.assertAlmostEqual(value['daily_null_max_sharpe'],value['daily_sharpe'])
        p['annualization']=126;p['protocol_sha256']=digest({k:v for k,v in p.items() if k!='protocol_sha256'})
        self.assertAlmostEqual(dsr(series[0],p,h)['value'],.5)

    def test_dsr_cannot_infer_independence_or_variance_from_current_survivors(self):
        m,p,series=self.inputs()
        for key,value in [('effective_independent_trials',None),('sharpe_variance_daily',None),
                          ('includes_all_attempts',False),('sharpe_unit','annualized'),('effective_observations',None)]:
            h=copy.deepcopy(m['history']);h['dsr_calibration'][key]=value
            self.assertEqual(dsr(series[0],p,h)['status'],'unavailable',key)
        self.assertEqual(dsr(series[0],p,None)['status'],'unavailable')

    def test_unfinished_attempts_are_kept_and_block_significance_calculation(self):
        m,p,series=self.inputs();h=m['history'];h['attempts'][0]['status']='uncertain'
        h['attempts_sha256']=digest(h['attempts']);h['dsr_calibration']['history_sha256']=digest(h['attempts'])
        self.assertEqual(len(audited_history(h)),8)
        self.assertEqual(dsr(series[0],p,h)['reasons'],['historical_trials_unfinished'])

    def test_effective_count_cannot_exceed_attempts_or_observations(self):
        m,p,series=self.inputs();h=m['history'];h['dsr_calibration']['effective_independent_trials']=9
        self.assertEqual(dsr(series[0],p,h)['status'],'unavailable')
        h=m['history'];h['dsr_calibration']['effective_independent_trials']=8
        h['dsr_calibration']['effective_observations'][series[0].candidate_id]['value']=10000
        self.assertEqual(dsr(series[0],p,h)['status'],'unavailable')
