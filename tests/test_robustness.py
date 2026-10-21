import copy
import random
from unittest import TestCase

from robust_fixtures import document,protocol,manifest
from alpharesearch.common import digest
from alpharesearch.return_contract import EvidenceError,verify_return_document
from alpharesearch.robustness import bootstrap_sharpe,moving_block_sample,robustness_report,plateau_report
from alpharesearch.research_report import analyze_manifest


class BootstrapTests(TestCase):
    def options(self):return dict(block_length=10,replicates=100,seed=7,confidence=.95,annualization=252)

    def test_seeded_block_bootstrap_is_reproducible_and_not_future_claim(self):
        series=verify_return_document(document(n=252));a=bootstrap_sharpe(series,**self.options())
        self.assertEqual(a,bootstrap_sharpe(series,**self.options()))
        self.assertEqual(a['status'],'computed');self.assertEqual(a['observed_days'],252)
        self.assertLess(a['confidence_interval'][0],a['confidence_interval'][1])
        b=bootstrap_sharpe(series,**(self.options()|{'seed':8}))
        self.assertNotEqual(a['sample_sharpes_sha256'],b['sample_sharpes_sha256'])

    def test_samples_preserve_adjacency_inside_every_nonoverlapping_output_block(self):
        sample=moving_block_sample(list(range(120)),10,random.Random(1))
        self.assertEqual(len(sample),120)
        for start in range(0,len(sample),10):self.assertEqual(sample[start:start+10],list(range(sample[start],sample[start]+10)))
        with self.assertRaises(EvidenceError):moving_block_sample(list(range(120)),1,random.Random(1))

    def test_raw_or_incomplete_data_is_never_bootstrapped(self):
        with self.assertRaises(EvidenceError):bootstrap_sharpe([.1,.2],**self.options())
        short=verify_return_document(document(n=30));value=bootstrap_sharpe(short,**self.options())
        self.assertEqual(value['status'],'unavailable');self.assertIsNone(value['confidence_interval'])

    def test_constant_return_does_not_become_infinite_sharpe(self):
        doc=document(n=252)
        for row in doc['observations']:row['value']=.001
        doc['data_sha256']=digest(doc['observations'])
        value=bootstrap_sharpe(verify_return_document(doc),**self.options())
        self.assertEqual(value['reasons'],['undefined_sharpe']);self.assertIsNone(value['sharpe'])

    def test_invalid_bootstrap_parameters_are_not_silently_downgraded(self):
        series=verify_return_document(document(n=252))
        for extra in ({'replicates':3},{'confidence':1},{'annualization':0},{'block_length':1},{'seed':False}):
            with self.assertRaises(EvidenceError):bootstrap_sharpe(series,**(self.options()|extra))

    def test_all_frozen_blocks_and_anchored_windows_are_reported(self):
        doc=document();p=protocol([doc]);series=verify_return_document(doc)
        value=robustness_report(series,p)
        self.assertEqual([b['block_length'] for b in value['bootstrap']],[5,10,20])
        self.assertEqual(len(value['fixed_windows']['windows']),4)
        self.assertEqual(value['fixed_windows']['trailing_observations'],0)
        self.assertFalse(value['submission_eligibility_changed'])

    def test_changed_protocol_hash_and_holdout_interval_are_rejected(self):
        doc=document();p=protocol([doc]);p['bootstrap']['seed']=9
        with self.assertRaises(EvidenceError):robustness_report(verify_return_document(doc),p)
        p=protocol([doc]);p['is_interval']={'start':'2019-01-01','end':'2020-01-01'}
        p['protocol_sha256']=digest({k:v for k,v in p.items() if k!='protocol_sha256'})
        with self.assertRaises(EvidenceError):robustness_report(verify_return_document(doc),p)

    def test_parameter_plateau_requires_all_prespecified_neighbors(self):
        docs=[document('p'+str(i),252,i*.0002) for i in range(3)]
        group={'family':'TEST','dimension':'window','candidate_ids':[d['candidate_id'] for d in docs],
               'parameter_values':[5,10,20],'baseline_id':'p1','minimum_sharpe':0.,'minimum_fraction_of_baseline':.8}
        p=protocol(docs,[group]);series=[verify_return_document(d) for d in docs]
        value=plateau_report(series,p)[0]
        self.assertEqual(value['status'],'computed');self.assertEqual(value['median_sharpe'],value['scores']['p1'])
        self.assertFalse(value['best_candidate_selected']);self.assertIsNone(value['cost_tradeoff'])
        self.assertEqual(plateau_report(series[:-1],p)[0]['status'],'insufficient_evidence')

    def test_missing_return_contract_produces_no_statistics(self):
        m=manifest(count=1,n=126);m['series'][0]['contract']['costs_reviewed']=False;p=protocol(m['series'])
        value=analyze_manifest(m,p);item=value['series'][0]
        self.assertEqual(value['status'],'insufficient_evidence')
        self.assertIsNone(item['robustness']);self.assertIsNone(item['dsr']);self.assertIsNone(item['cost_stress'])

    def test_repeat_analysis_has_same_identity_not_new_independent_evidence(self):
        m=manifest(count=1,n=126);p=protocol(m['series'])
        a=analyze_manifest(m,p);b=analyze_manifest(copy.deepcopy(m),p)
        self.assertEqual(a,b);self.assertFalse(a['repeated_input_is_new_independent_evidence'])
