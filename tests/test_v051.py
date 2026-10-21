"""Submission contract gates. Every account, return and HTTP response is synthetic."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from test_system import Base,FixtureSimulator,fixture_result,ScriptedTransport
from alpharesearch.brain import BrainSimulator
from alpharesearch.common import ResearchError,digest
from alpharesearch.engine import Engine
from alpharesearch.evaluator import evaluate
from alpharesearch.http import ApiClient
from alpharesearch.return_contract import (EvidenceError,require_result_return_contract,
                                          verify_return_document)


class ReturnGateEvaluationTests(Base):
    def evaluation(self,value):
        return evaluate(value,[fixture_result()]*2,self.config['policy'],True)

    def test_cost_unknown_cannot_pass_otherwise_valid_old_gates(self):
        value=fixture_result()
        self.assertTrue(self.evaluation(value)['ready'])
        value['return_verification']={'status':'insufficient_evidence','reasons':['unknown_cost_semantics']}
        result=self.evaluation(value)
        self.assertFalse(result['ready']);self.assertEqual(result['status'],'evidence_incomplete')
        self.assertIn('unverified_return_contract',result['missing']);self.assertTrue(result['research_eligible'])
        self.assertIsNone(result['worst_window_sharpe'])

    def test_absent_null_malformed_or_nonverified_status_is_missing_evidence(self):
        for summary in (None,[],1,'verified',{}, {'status':'PASS'},{'status':'complete'},
                        {'status':'PENDING'},{'status':'verified'}, {'status':'unavailable'}):
            with self.subTest(summary=summary):
                value=fixture_result();value['return_verification']=summary
                self.assertIn('unverified_return_contract',self.evaluation(value)['missing'])
        value=fixture_result();value.pop('return_verification')
        self.assertFalse(self.evaluation(value)['ready'])

    def test_stored_verified_label_cannot_override_missing_or_invalid_contract(self):
        for mutation in ('missing','null','costs','unit','capital','calendar'):
            value=fixture_result();doc=value['verified_return_document']
            if mutation=='missing':value.pop('verified_return_document')
            elif mutation=='null':value['verified_return_document']=None
            elif mutation=='costs':doc['contract']['costs_reviewed']=False
            elif mutation=='unit':doc['contract']['unit']='unverified'
            elif mutation=='capital':doc['contract']['capital_basis']['value']=None
            else:doc['calendar']['sessions'].pop(100)
            self.assertFalse(self.evaluation(value)['ready'],mutation)

    def test_cached_summary_hash_or_identity_must_match_recomputed_document(self):
        for mutation in ('hash','alpha','expression','settings','account','source','response'):
            value=fixture_result()
            if mutation=='hash':value['return_verification']['input_sha256']='a'*64
            elif mutation=='alpha':value['alpha_id']='other'
            elif mutation=='expression':value['expression']='rank(volume)'
            elif mutation=='settings':value['settings']['delay']=2
            elif mutation=='account':value['evidence_source']['account_ref']='a'*64
            elif mutation=='source':value['evidence_source']['transport']='scripted_or_unknown_transport'
            else:value['evidence_source']['responses']['pnl']['payload_sha256']='a'*64
            self.assertFalse(self.evaluation(value)['ready'],mutation)

    def test_valid_document_with_other_daily_values_still_fails_binding(self):
        value=fixture_result();doc=value['verified_return_document']
        doc['observations'][10]['value']+=.1;doc['data_sha256']=digest(doc['observations'])
        value['return_verification']=verify_return_document(doc).summary()
        with self.assertRaisesRegex(EvidenceError,'daily_values_mismatch'):require_result_return_contract(value)
        self.assertFalse(self.evaluation(value)['ready'])

    def test_synthetic_contract_never_qualifies_for_live_submission(self):
        value=fixture_result();value['verified_return_document']['synthetic']=True
        value['return_verification']=verify_return_document(value['verified_return_document']).summary()
        self.assertFalse(self.evaluation(value)['ready'])

    def test_verified_contract_does_not_waive_pending_self_correlation(self):
        value=fixture_result();value['checks'][-1]['result']='PENDING'
        result=self.evaluation(value)
        self.assertEqual(result['return_verification']['status'],'verified')
        self.assertIn('unresolved_check:SELF_CORRELATION:PENDING',result['missing']);self.assertFalse(result['ready'])

    def test_valid_contract_still_passes_existing_full_gates(self):
        value=fixture_result();result=self.evaluation(value)
        self.assertTrue(result['ready']);self.assertEqual(result['missing'],[])
        self.assertEqual(require_result_return_contract(value).summary(),value['return_verification'])


class SubmitContractTests(Base):
    def prepared(self):
        self.live();sim=FixtureSimulator();engine=Engine(self.config,simulator=sim)
        self.addCleanup(engine.memory.close)
        proposed=engine.propose('ts_mean(close,20)','TEST hypothesis','TEST synthetic source')
        path=Path(self.tmp.name)/'review.json';review=engine.write_report(path)
        trial_id=proposed['trial_id'];alpha_id=review['shortlist'][0]['alpha_id']
        # A real submit path would invoke this transport. There are deliberately
        # no responses: an accidental HTTP request fails the test immediately.
        transport=ScriptedTransport([])
        brain=BrainSimulator(self.config,ApiClient(self.config['brain']['base_url'],transport=transport))
        sim.submit_once=brain.submit_once
        return engine,sim,transport,trial_id,alpha_id,path

    def assert_no_submit(self,engine,transport):
        self.assertEqual(transport.calls,[])
        self.assertEqual(engine.memory.db.execute('SELECT count(*) FROM submissions').fetchone()[0],0)

    def test_fresh_cost_unverified_blocks_before_reservation_and_any_submit_http(self):
        engine,sim,transport,trial,alpha,path=self.prepared();old=sim.collect
        def collect(aid):
            value=old(aid);value['return_verification']={'status':'insufficient_evidence','reasons':['unknown_cost_semantics']}
            return value
        sim.collect=collect
        with self.assertRaisesRegex(ResearchError,'收益契约'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)
        latest=engine.memory.latest(trial);self.assertEqual(latest['return_verification']['status'],'insufficient_evidence')
        self.assertEqual(engine.report()['shortlist'],[])

    def test_independent_submit_gate_survives_evaluator_always_ready(self):
        engine,sim,transport,trial,alpha,path=self.prepared();old=sim.collect
        value=old(alpha);ready=evaluate(value,[fixture_result()]*2,self.config['policy'],True)
        def collect(aid):
            value=old(aid);value['return_verification']={'status':'insufficient_evidence','reasons':['unknown_cost_semantics']}
            return value
        sim.collect=collect
        with patch('alpharesearch.engine.evaluate',return_value=ready):
            with self.assertRaisesRegex(ResearchError,'收益契约'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)

    def test_fake_verified_status_without_document_cannot_bypass_final_gate(self):
        engine,sim,transport,trial,alpha,path=self.prepared();old=sim.collect;trusted=engine.report()
        def collect(aid):
            value=old(aid);value.pop('verified_return_document');return value
        sim.collect=collect
        with patch.object(engine,'report',return_value=trusted):
            with self.assertRaisesRegex(ResearchError,'收益契约'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)

    def test_malformed_fresh_status_fails_closed_even_when_shortlist_is_forced(self):
        engine,sim,transport,trial,alpha,path=self.prepared();old=sim.collect;trusted=engine.report()
        for summary in (None,[],{'status':'verified'}):
            def collect(aid,summary=summary):
                value=old(aid);value['return_verification']=summary;return value
            sim.collect=collect
            with patch.object(engine,'report',return_value=trusted):
                with self.assertRaisesRegex(ResearchError,'收益契约'):engine.submit(trial,path,alpha)
            self.assert_no_submit(engine,transport)

    def test_fresh_contract_cannot_switch_account_under_forced_shortlist(self):
        engine,sim,transport,trial,alpha,path=self.prepared();old=sim.collect;trusted=engine.report()
        def collect(aid):
            value=old(aid);doc=value['verified_return_document']
            value['evidence_source']['account_ref']='a'*64
            doc['contract']['account_ref']=doc['contract']['population_id']='a'*64
            value['return_verification']=verify_return_document(doc).summary()
            return value
        sim.collect=collect
        with patch.object(engine,'report',return_value=trusted):
            with self.assertRaisesRegex(ResearchError,'current_account_mismatch'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)

    def test_fresh_valid_document_for_other_alpha_cannot_authorize_approved_id(self):
        engine,sim,transport,trial,alpha,path=self.prepared();trusted=engine.report()
        sim.collect=lambda aid:fixture_result('ts_mean(close,20)',self.config['scope'],alpha_id='other_alpha')
        with patch.object(engine,'report',return_value=trusted):
            with self.assertRaisesRegex(ResearchError,'approved_alpha_mismatch'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)

    def test_verified_label_with_raw_cost_contract_failure_still_blocks_final_http(self):
        engine,sim,transport,trial,alpha,path=self.prepared();old=sim.collect;trusted=engine.report()
        def collect(aid):
            value=old(aid);value['verified_return_document']['contract']['costs_reviewed']=False
            return value
        sim.collect=collect
        with patch.object(engine,'report',return_value=trusted):
            with self.assertRaisesRegex(ResearchError,'unknown_cost_semantics'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)

    def test_default_closed_switch_stops_before_collect_or_submit(self):
        engine,sim,transport,trial,alpha,path=self.prepared();engine.config['safety']['submission_enabled']=False
        with patch.object(sim,'collect',side_effect=AssertionError('collect when disabled')):
            with self.assertRaisesRegex(ResearchError,'submission_enabled=false'):engine.submit(trial,path,alpha)
        self.assert_no_submit(engine,transport)

    def test_incomplete_contract_stays_in_research_queue_without_rewriting_history(self):
        engine,sim,transport,trial,alpha,path=self.prepared();value=sim.collect(alpha)
        prior=copy.deepcopy(engine.memory.latest(trial));count=engine.memory.run(engine.run_id)['simulations']
        value['return_verification']={'status':'insufficient_evidence','reasons':['unknown_cost_semantics']}
        engine.memory.result(trial,value);report=engine.report()
        self.assertEqual(report['shortlist'],[]);self.assertTrue(any(c['trial_id']==trial for c in report['research_queue']))
        self.assertEqual(engine.memory.run(engine.run_id)['simulations'],count)
        stored=json.loads(engine.memory.db.execute('SELECT payload FROM results WHERE trial_id=? ORDER BY id LIMIT 1',(trial,)).fetchone()[0])
        self.assertEqual(stored,prior);self.assert_no_submit(engine,transport)
