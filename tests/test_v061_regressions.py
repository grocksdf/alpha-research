"""Submit and statistics regressions for the independently reported v0.6.0 gaps."""
import copy
from pathlib import Path
from unittest.mock import patch

from test_system import Base, FixtureSimulator, fixture_result
from contract_fixtures import independent_result, refresh_summary
from alpharesearch.common import ResearchError, digest
from alpharesearch.engine import Engine
from alpharesearch.evaluator import evaluate
from alpharesearch.pnl_evidence import stability_windows
from alpharesearch.return_contract import EvidenceError, require_result_return_contract, submission_return_errors


CASES = ('pnl_post', 'alpha_post', 'api_mismatch', 'source_alpha', 'currency', 'unresolved_reasons',
         'capital', 'cost_basis', 'is_bounds', 'daily_return', 'response_path', 'response_hash',
         'calendar', 'summary', 'missing_contract', 'coherent_wrong_account', 'coherent_wrong_api')


def mutate(result, case):
    source = result['evidence_source']; doc = result['verified_return_document']; c = doc['contract']
    if case == 'pnl_post': source['responses']['pnl']['method'] = 'POST'
    elif case == 'alpha_post': source['responses']['alpha']['method'] = 'POST'
    elif case == 'api_mismatch': source['api_base_url'] = 'https://other.invalid'
    elif case == 'source_alpha': source['alpha_id'] = 'OTHER'
    elif case == 'currency': c['unit'] = c['capital_basis']['unit'] = 'EUR'; refresh_summary(result)
    elif case == 'unresolved_reasons': result['return_verification']['reasons'] = ['unknown_cost_semantics']
    elif case == 'capital': c['capital_basis']['value'] = 200.; refresh_summary(result)
    elif case == 'cost_basis': c['net_of_costs'] = True; refresh_summary(result)
    elif case == 'is_bounds': c['is_interval']['start'] = '2019-12-31'; refresh_summary(result)
    elif case == 'daily_return': result['daily_returns'][next(iter(result['daily_returns']))] += .01
    elif case == 'response_path': source['responses']['pnl']['path'] = '/alphas/OTHER/recordsets/pnl'
    elif case == 'response_hash': source['responses']['pnl']['payload_sha256'] = 'd'*64
    elif case == 'calendar': doc['calendar']['source_file_sha256'] = 'd'*64; refresh_summary(result)
    elif case == 'summary': result['return_verification']['observations'] += 1
    elif case == 'missing_contract': result.pop('verified_return_document')
    elif case == 'coherent_wrong_account':
        c['account_ref'] = c['population_id'] = source['account_ref'] = digest('OTHER'); refresh_summary(result)
    elif case == 'coherent_wrong_api':
        c['api_base_url'] = source['api_base_url'] = 'https://other.invalid'; refresh_summary(result)
    else: raise AssertionError('unknown test mutation')
    return result


class SubmissionRegressionTests(Base):
    def engine_with_review(self):
        self.live(); simulator = FixtureSimulator(); engine = Engine(self.config, simulator=simulator)
        self.addCleanup(engine.memory.close)
        outcome = engine.propose('ts_mean(close,20)', 'TEST', 'TEST')
        review_path = Path(self.tmp.name)/('review-'+engine.run_id+'.json')
        review = engine.write_report(review_path)
        self.assertEqual(len(review['shortlist']), 1, review['candidates'][0]['evaluation'])
        return engine, simulator, outcome['trial_id'], review_path, review['shortlist'][0]['alpha_id']

    def test_all_reported_and_additional_anomalies_send_zero_submit_and_reserve_nothing(self):
        engine, simulator, tid, path, aid = self.engine_with_review()
        collect = simulator.collect
        for case in CASES:
            simulator.collect = lambda alpha_id, case=case: mutate(collect(alpha_id), case)
            with self.subTest(case=case), patch.object(engine, 'report', return_value={'shortlist': [{'trial_id': tid}]}):
                with self.assertRaises(ResearchError): engine.submit(tid, path, aid)
            self.assertEqual(simulator.submit_calls, 0)
            self.assertEqual(engine.memory.db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 0)
        simulator.collect = collect
        self.assertEqual(engine.submit(tid, path, aid)['status'], 'SUBMITTED')
        self.assertEqual(simulator.submit_calls, 1)

    def test_final_gate_is_independent_of_compatibility_wrapper_and_ready_evaluator(self):
        engine, simulator, tid, path, aid = self.engine_with_review()
        collect = simulator.collect
        for case in ('pnl_post', 'currency', 'unresolved_reasons'):
            simulator.collect = lambda alpha_id, case=case: mutate(collect(alpha_id), case)
            with self.subTest(case=case), patch('alpharesearch.return_contract.submission_return_errors', return_value=[]), \
                 patch.object(engine, 'report', return_value={'shortlist': [{'trial_id': tid}]}):
                with self.assertRaises(ResearchError): engine.submit(tid, path, aid)
        self.assertEqual(simulator.submit_calls, 0)
        self.assertEqual(engine.memory.db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 0)

    def test_active_authenticated_account_conflict_is_rejected(self):
        engine, simulator, tid, path, aid = self.engine_with_review()
        simulator.account_ref = digest('OTHER')
        with self.assertRaises(ResearchError): engine.submit(tid, path, aid)
        self.assertEqual(simulator.submit_calls, 0)

    def test_invalid_contract_gives_no_scores_in_engine_report_and_no_shortlist(self):
        engine, simulator, tid, path, aid = self.engine_with_review()
        result = engine.memory.latest(tid); result['return_verification'] = {
            'status': 'insufficient_evidence', 'reasons': ['unknown_cost_semantics']}
        engine.memory.result(tid, result)
        report = engine.report()
        self.assertEqual(report['shortlist'], [])
        self.assertTrue(report['research_queue'])
        for item in report['candidates']:
            self.assertIsNone(item['evaluation']['worst_window_sharpe'])
            self.assertIsNone(item['evaluation']['positive_window_fraction'])
            self.assertTrue(all(w['sharpe'] is None for w in item['evaluation']['stability']['windows']))


class StatisticsRegressionTests(Base):
    def test_exact_five_reported_scenarios_reject_even_after_internal_summary_rebuild(self):
        config, baseline = independent_result(self.config)
        for case in ('pnl_post', 'api_mismatch', 'source_alpha', 'currency', 'unresolved_reasons'):
            r = mutate(copy.deepcopy(baseline), case)
            with self.subTest(case=case):
                self.assertTrue(submission_return_errors(r, config=config))
                value = evaluate(r, [baseline]*2, config['policy'], True, config=config)
                self.assertFalse(value['ready']); self.assertTrue(value['research_eligible'])
                self.assertIsNone(value['worst_window_sharpe'])

    def test_insufficient_evidence_never_invokes_numeric_window_computation(self):
        config, r = independent_result(self.config)
        r['return_verification'] = {'status': 'insufficient_evidence', 'reasons': ['unknown_cost_semantics']}
        with patch('alpharesearch.pnl_evidence.fixed_window_diagnostics', side_effect=AssertionError('unverified score computed')):
            value = evaluate(r, [r]*2, config['policy'], True, config=config)
        self.assertIsNone(value['worst_window_sharpe'])
        self.assertFalse(value['stability']['complete'])

    def test_coherently_relabelled_proof_currency_is_still_rejected_by_current_audit(self):
        config, r = independent_result(self.config)
        c = r['verified_return_document']['contract']; proof = r['daily_evidence']
        c['unit'] = c['capital_basis']['unit'] = proof['pnl_unit'] = proof['capital_unit'] = 'EUR'
        refresh_summary(r)
        with self.assertRaisesRegex(EvidenceError, 'host_audit_metadata'):
            require_result_return_contract(r, config=config)

    def test_coherently_relabelled_costs_are_still_rejected_by_current_audit(self):
        config, r = independent_result(self.config)
        r['verified_return_document']['contract']['net_of_costs'] = r['daily_evidence']['net_of_costs'] = True
        refresh_summary(r)
        with self.assertRaises(EvidenceError): require_result_return_contract(r, config=config)

    def test_proof_errors_and_nested_verification_reasons_remain_blockers(self):
        config, baseline = independent_result(self.config)
        for mode in ('evidence_errors', 'nested_review'):
            r = copy.deepcopy(baseline)
            if mode == 'evidence_errors': r['evidence_errors']['pnl_changes'] = 'unresolved'
            else: r['verified_return_document']['verification'] = {'status': 'verified', 'reasons': ['unresolved']}
            with self.subTest(mode=mode), self.assertRaises(EvidenceError):
                require_result_return_contract(r, config=config)

    def test_https_default_port_and_equivalent_absolute_get_paths_are_valid(self):
        config, r = independent_result(self.config)
        source = r['evidence_source']; c = r['verified_return_document']['contract']
        source['api_base_url'] = c['api_base_url'] = 'https://api.worldquantbrain.com:443/'
        for receipt in source['responses'].values(): receipt['path'] = 'https://api.worldquantbrain.com'+receipt['path']
        refresh_summary(r)
        self.assertEqual(submission_return_errors(r, config=config), [])
