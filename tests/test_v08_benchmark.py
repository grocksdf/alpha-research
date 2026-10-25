"""Independent v0.8 research-memory benchmark; no candidate generation."""
import unittest
from benchmarks.evaluate_memory import evaluate,ARMS


def record(arm):
    return {'arm':arm,'task_id':'paired-research-task',
        'authorized_catalog_sha256':'a'*64,'budget_sha256':'b'*64,
        'evidence_grade':'synthetic_process','attempted':2,'legal':1,
        'duplicates':0,'revisions':1,'simulation_count':1,
        'independent_validation_passed':False,'source_run_id':'offline-only',
        'mechanism_labels':['mechanism-constructed-by-test-only'],
        'platform_checks_passed':0}

class MemoryBenchmarkTests(unittest.TestCase):
    def test_empty_comparison_never_claims_memory_improvement(self):
        value=evaluate([])
        self.assertEqual(value['status'],'INCOMPLETE')

    def test_four_paired_synthetic_arms_cannot_claim_real_effect(self):
        value=evaluate([record(x) for x in ARMS])
        self.assertEqual(value['status'],'ENGINEERING_ONLY')
        self.assertFalse(value['memory_improvement_proven'])
        self.assertEqual(value['arms']['llm_full_playbook']['mechanism_diversity'],1)

    def test_equal_authorization_and_budget_are_required(self):
        rows=[record(x) for x in ARMS]
        rows[-1]['budget_sha256']='c'*64
        self.assertEqual(evaluate(rows)['reason'],'unequal_frozen_resources_or_fields')

    def test_duplicate_and_invalid_platform_check_accounting_is_rejected(self):
        rows=[record(x) for x in ARMS]
        rows[0]['platform_checks_passed']=2
        with self.assertRaises(ValueError):evaluate(rows)

if __name__=='__main__':unittest.main()
