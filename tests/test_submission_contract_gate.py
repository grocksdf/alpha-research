from fixtures.legacy_engine_auto_variants import enable_legacy_historical_fixture
"""Reproduced v0.5.0 gap, with zero Submit requests under forced evaluator success."""
import copy
from pathlib import Path
from unittest.mock import patch

from test_system import Base, FixtureSimulator, fixture_result
from alpharesearch.engine import Engine
from alpharesearch.evaluator import evaluate
from alpharesearch.common import ResearchError, digest
from alpharesearch.return_contract import submission_return_errors, review_return_document


class SubmissionContractTests(Base):
    def setUp(self):
        super().setUp()
        enable_legacy_historical_fixture(self)

    def test_old_gates_do_not_allow_unknown_cost_semantics(self):
        result = fixture_result()
        self.assertEqual(submission_return_errors(result), [])
        result['return_verification'] = {'status': 'insufficient_evidence', 'reasons': ['unknown_cost_semantics']}
        value = evaluate(result, [fixture_result()]*2, self.config['policy'], True)
        self.assertFalse(value['ready'])
        self.assertIn('unverified_return_contract', value['missing'])
        self.assertTrue(value['research_eligible'])

    def test_verified_label_is_not_enough(self):
        for mutation in ('missing_document', 'costs', 'formula', 'settings', 'account', 'alpha', 'returns', 'summary', 'broken'):
            with self.subTest(mutation=mutation):
                result = fixture_result()
                if mutation == 'missing_document': result.pop('verified_return_document')
                elif mutation == 'costs': result['verified_return_document']['contract']['costs_reviewed'] = False
                elif mutation == 'formula': result['expression'] = 'rank(volume)'
                elif mutation == 'settings': result['settings']['delay'] = 2
                elif mutation == 'account': result['evidence_source']['account_ref'] = digest('OTHER')
                elif mutation == 'alpha': result['alpha_id'] = 'OTHER'
                elif mutation == 'returns': result['daily_returns'][next(iter(result['daily_returns']))] += .01
                elif mutation == 'summary': result['return_verification']['input_sha256'] = '0'*64
                else: result['verified_return_document'] = []
                self.assertTrue(submission_return_errors(result))

    def test_final_gate_independently_blocks_even_when_report_is_forced_ready(self):
        self.live(); simulator = FixtureSimulator(); engine = Engine(self.config, simulator=simulator)
        self.addCleanup(engine.memory.close)
        outcome = engine.propose('ts_mean(close,20)', 'TEST', 'TEST')
        tid = outcome['trial_id']; path = Path(self.tmp.name)/'review.json'
        approved = engine.write_report(path)['shortlist'][0]['alpha_id']
        collect = simulator.collect
        for mode in ('status', 'missing', 'cost', 'account', 'approved_id'):
            def bad(alpha_id):
                result = collect(alpha_id)
                if mode == 'status': result['return_verification']['status'] = 'insufficient_evidence'
                elif mode == 'missing': result.pop('verified_return_document')
                elif mode == 'cost': result['verified_return_document']['contract']['costs_reviewed'] = False
                elif mode == 'account':
                    result['verified_return_document']['contract']['account_ref'] = digest('OTHER')
                    result['verified_return_document']['contract']['population_id'] = digest('OTHER')
                    result['evidence_source']['account_ref'] = digest('OTHER')
                    result['return_verification'] = review_return_document(result['verified_return_document'])[1]
                else: result['alpha_id'] = 'OTHER'
                return result
            simulator.collect = bad
            with self.subTest(mode=mode), patch.object(engine, 'report', return_value={'shortlist': [{'trial_id': tid}]}):
                with self.assertRaises(ResearchError): engine.submit(tid, path, approved)
            self.assertEqual(simulator.submit_calls, 0)
            self.assertEqual(engine.memory.db.execute('SELECT COUNT(*) FROM submissions').fetchone()[0], 0)
