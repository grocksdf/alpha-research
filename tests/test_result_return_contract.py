"""17 reconstructed contract categories; original v0.5.1 test source unavailable.

These independent fixtures supplement and retain every shipped v0.6.0 test.
They are not represented as a verbatim restoration of the historical file.
"""
import copy

from test_system import Base
from contract_fixtures import independent_result, refresh_summary
from alpharesearch.common import digest
from alpharesearch.evaluator import evaluate
from alpharesearch.pnl_evidence import stability_windows
from alpharesearch.return_contract import EvidenceError, require_result_return_contract, submission_return_errors


class ResultReturnContractTests(Base):
    def setUp(self):
        super().setUp()
        self.config, self.result = independent_result(self.config)

    def assert_invalid(self, result, *, config=None):
        config = self.config if config is None else config
        with self.assertRaises(EvidenceError): require_result_return_contract(result, config=config)
        self.assertTrue(submission_return_errors(result, config=config))
        value = evaluate(result, [self.result]*2, config['policy'], True, config=config)
        self.assertFalse(value['ready'])
        self.assertIsNone(value['worst_window_sharpe'])
        self.assertIsNone(value['positive_window_fraction'])
        self.assertFalse(value['stability']['complete'])
        self.assertTrue(all(w['sharpe'] is None for w in value['stability']['windows']))
        self.assertIsNone(stability_windows(result, config['policy'], config=config)['worst_window_sharpe'])

    def test_01_valid_independent_receipts_have_verified_series_and_scores(self):
        series = require_result_return_contract(self.result, config=self.config)
        self.assertEqual(len(series.returns), 504)
        self.assertEqual(submission_return_errors(self.result), [])
        value = evaluate(self.result, [self.result]*2, self.config['policy'], True, config=self.config)
        self.assertTrue(value['ready']); self.assertIsNotNone(value['worst_window_sharpe'])

    def test_02_missing_unverified_and_malformed_status(self):
        for review in (None, [], {}, {'status': 'insufficient_evidence'}, {'status': True}):
            r = copy.deepcopy(self.result); r['return_verification'] = review
            with self.subTest(review=review): self.assert_invalid(r)

    def test_03_missing_corrupt_or_nonresearch_documents(self):
        for mode in ('missing', 'array', 'holdout', 'forward'):
            r = copy.deepcopy(self.result)
            if mode == 'missing': r.pop('verified_return_document')
            elif mode == 'array': r['verified_return_document'] = []
            else: r['verified_return_document']['contract']['sample_role'] = mode; refresh_summary(r)
            with self.subTest(mode=mode): self.assert_invalid(r)

    def test_04_cost_review_and_net_gross_model_are_bound(self):
        for key, value in (('costs_reviewed', False), ('net_of_costs', 'unverified'),
                           ('net_of_costs', True), ('cost_model_id', 'OTHER'), ('cost_review_note', 'OTHER')):
            r = copy.deepcopy(self.result); r['verified_return_document']['contract'][key] = value; refresh_summary(r)
            with self.subTest(key=key, value=value): self.assert_invalid(r)

    def test_05_synthetic_nonlive_or_untrusted_transport_is_rejected(self):
        for mode in ('result_mode', 'result_synthetic', 'document_synthetic', 'transport'):
            r = copy.deepcopy(self.result)
            if mode == 'result_mode': r['mode'] = 'demo'
            elif mode == 'result_synthetic': r['synthetic'] = True
            elif mode == 'document_synthetic': r['verified_return_document']['synthetic'] = True
            else: r['evidence_source']['transport'] = 'scripted_or_unknown_transport'
            with self.subTest(mode=mode): self.assert_invalid(r)

    def test_06_account_identity_and_host_account_must_match(self):
        for mode in ('source', 'contract', 'host', 'response'):
            r = copy.deepcopy(self.result); config = copy.deepcopy(self.config)
            if mode == 'source': r['evidence_source']['account_ref'] = digest('OTHER')
            elif mode == 'contract':
                r['verified_return_document']['contract']['account_ref'] = digest('OTHER'); refresh_summary(r)
            elif mode == 'host': config['catalog_data']['account_id'] = 'OTHER'
            else: r['evidence_source']['responses']['pnl']['account_ref'] = digest('OTHER')
            with self.subTest(mode=mode): self.assert_invalid(r, config=config)

    def test_07_api_base_response_and_current_host_are_bound(self):
        for mode in ('source', 'contract', 'receipt', 'host', 'insecure', 'credentials'):
            r = copy.deepcopy(self.result); config = copy.deepcopy(self.config)
            if mode == 'source': r['evidence_source']['api_base_url'] = 'https://other.invalid'
            elif mode == 'contract': r['verified_return_document']['contract']['api_base_url'] = 'https://other.invalid'; refresh_summary(r)
            elif mode == 'receipt': r['evidence_source']['responses']['pnl']['path'] = 'https://other.invalid/alphas/INDEPENDENT_ALPHA/recordsets/pnl'
            elif mode == 'host': config['brain']['base_url'] = 'https://other.invalid'
            elif mode == 'insecure': r['evidence_source']['api_base_url'] = 'http://api.worldquantbrain.com'
            else: r['evidence_source']['api_base_url'] = 'https://user:pass@api.worldquantbrain.com'
            with self.subTest(mode=mode): self.assert_invalid(r, config=config)

    def test_08_all_alpha_ids_and_approved_alpha_are_bound(self):
        for target in ('result', 'source', 'document', 'contract', 'response'):
            r = copy.deepcopy(self.result)
            if target == 'result': r['alpha_id'] = 'OTHER'
            elif target == 'source': r['evidence_source']['alpha_id'] = 'OTHER'
            elif target == 'document': r['verified_return_document']['candidate_id'] = 'OTHER'; refresh_summary(r)
            elif target == 'contract': r['verified_return_document']['contract']['source_id'] = 'OTHER'; refresh_summary(r)
            else: r['evidence_source']['responses']['pnl']['alpha_id'] = 'OTHER'
            with self.subTest(target=target): self.assert_invalid(r)
        with self.assertRaises(EvidenceError): require_result_return_contract(self.result, alpha_id='OTHER')

    def test_09_both_responses_require_explicit_get_and_correct_paths(self):
        for endpoint in ('alpha', 'pnl'):
            for change in ('POST', 'missing_method', 'wrong_path', 'no_hash'):
                r = copy.deepcopy(self.result); receipt = r['evidence_source']['responses'][endpoint]
                if change == 'POST': receipt['method'] = 'POST'
                elif change == 'missing_method': receipt.pop('method')
                elif change == 'wrong_path': receipt['path'] = '/alphas/OTHER'
                else: receipt.pop('payload_sha256')
                with self.subTest(endpoint=endpoint, change=change): self.assert_invalid(r)

    def test_10_document_and_raw_pnl_capital_currency_must_match(self):
        for target in ('document', 'pnl', 'capital'):
            r = copy.deepcopy(self.result)
            if target == 'document':
                r['verified_return_document']['contract']['unit'] = 'EUR'
                r['verified_return_document']['contract']['capital_basis']['unit'] = 'EUR'; refresh_summary(r)
            elif target == 'pnl': r['daily_evidence']['pnl_unit'] = 'EUR'
            else: r['daily_evidence']['capital_unit'] = 'EUR'
            with self.subTest(target=target): self.assert_invalid(r)

    def test_11_capital_value_source_path_and_response_hash_are_bound(self):
        for key, value in (('value', 200.), ('source_path', ['os', 'bookSize']), ('source_sha256', 'd'*64)):
            r = copy.deepcopy(self.result); r['verified_return_document']['contract']['capital_basis'][key] = value; refresh_summary(r)
            with self.subTest(key=key): self.assert_invalid(r)

    def test_12_is_interval_and_sample_semantics_must_agree(self):
        for target in ('document', 'isolation', 'proof', 'source_hash'):
            r = copy.deepcopy(self.result)
            if target == 'document': r['verified_return_document']['contract']['is_interval']['start'] = '2020-01-01'; refresh_summary(r)
            elif target == 'isolation': r['pnl_isolation']['end_date'] = '2021-12-31'
            elif target == 'proof': r['daily_evidence']['start_date'] = '2020-01-01'
            else: r['evidence_source']['discovery_contract_sha256'] = 'd'*64
            with self.subTest(target=target): self.assert_invalid(r)

    def test_13_dates_raw_pnl_and_daily_returns_are_reconciled(self):
        for target in ('date', 'raw_pnl', 'daily_return', 'document_value', 'pnl_none', 'returns_none', 'proof_none', 'metrics_none'):
            r = copy.deepcopy(self.result)
            if target == 'date': r['daily_evidence']['session_dates'][1] = '2019-12-31'
            elif target == 'raw_pnl': r['pnl_changes'][next(iter(r['pnl_changes']))] += 1.
            elif target == 'daily_return': r['daily_returns'][next(iter(r['daily_returns']))] += .01
            elif target == 'pnl_none': r['pnl_changes'] = None
            elif target == 'returns_none': r['daily_returns'] = None
            elif target == 'proof_none': r['daily_evidence'] = None
            elif target == 'metrics_none': r['metrics'] = None
            else:
                doc = r['verified_return_document']; doc['observations'][1]['value'] += .5
                doc['data_sha256'] = digest(doc['observations']); refresh_summary(r)
            with self.subTest(target=target): self.assert_invalid(r)

    def test_14_all_summary_identity_counts_hashes_and_dates_are_rechecked(self):
        for key, value in (('input_sha256', 'd'*64), ('contract_sha256', 'd'*64), ('observations', 999),
                           ('start_date', '2019-01-01'), ('end_date', '2030-01-01'), ('return_unit', 'percent'),
                           ('synthetic', True), ('empirical_evidence', False)):
            r = copy.deepcopy(self.result); r['return_verification'][key] = value
            with self.subTest(key=key): self.assert_invalid(r)

    def test_15_calendar_identity_coverage_file_hash_and_provenance_are_bound(self):
        for key, value in (('id', 'OTHER'), ('source', 'OTHER'), ('region', 'EUR'),
                           ('coverage_start', '2019-01-01'), ('source_file_sha256', 'd'*64), ('review_note', 'OTHER')):
            r = copy.deepcopy(self.result); r['verified_return_document']['calendar'][key] = value; refresh_summary(r)
            with self.subTest(key=key): self.assert_invalid(r)

    def test_16_formula_settings_detail_and_audit_snapshots_cannot_diverge(self):
        for target in ('formula', 'settings', 'metrics', 'audit_hash', 'current_review'):
            r = copy.deepcopy(self.result); config = copy.deepcopy(self.config)
            if target == 'formula': r['expression'] = 'rank(volume)'
            elif target == 'settings': r['settings']['delay'] = 2
            elif target == 'metrics': r['metrics']['sharpe'] = 99.
            elif target == 'audit_hash': r['evidence_source']['pnl_contract_sha256'] = 'd'*64
            else: config['brain']['pnl_evidence']['costs_reviewed'] = False
            with self.subTest(target=target): self.assert_invalid(r, config=config)

    def test_17_verified_status_cannot_carry_unresolved_or_forged_reasons(self):
        for reasons in (['unknown_cost_semantics'], [''], [False], 'unknown', None):
            r = copy.deepcopy(self.result); r['return_verification']['reasons'] = reasons
            with self.subTest(reasons=reasons): self.assert_invalid(r)
        r = copy.deepcopy(self.result)
        r['verified_return_document']['verification'] = {'status': 'verified', 'reasons': ['unresolved']}
        self.assert_invalid(r)
