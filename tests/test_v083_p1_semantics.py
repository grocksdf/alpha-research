"""v0.8.3 P1 semantic trust boundary and per-item Playbook fault isolation.

Every tool response here is an explicitly SCRIPTED local test fixture, never
real LLM evidence. No network, BRAIN, market evaluation, or Submit requests.
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from alpharesearch.autoresearch_v08 import (Playbook, _check_structure,
                                           execute_batch, make_plan)
from alpharesearch.common import ResearchError, digest, load_settings
from alpharesearch.memory import Memory
from alpharesearch.v082_protocol import ModelOutputTruncated, ModelToolCallInvalid
from test_v083_playbook import MixedEvidenceScript

ROOT = Path(__file__).resolve().parents[1]
UNSUPPORTED_MARKET_ASSERTION = 'Guaranteed market profit forever on all exchanges, 100% live Alpha win rate'
UNSUPPORTED_APPLICABILITY = 'Validated across real live financial markets without any holdout testing'


class ContaminatedPlaybookScript(MixedEvidenceScript):
    def __init__(self, database, attack):
        super().__init__(database, outcome='all_valid')
        self.attack=attack
        self.raw_payload=None

    def complete(self, phase, messages, schema, model):
        reply=super().complete(phase,messages,schema,model)
        if phase!='playbook':
            return reply
        choice=reply.data['choices'][0]
        tool=choice['message']['tool_calls'][0]['function']
        payload=json.loads(tool['arguments'])
        prototype=payload['knowledge'][0]
        if self.attack in ('adversarial','adversarial_with_valid_sibling'):
            prototype['claim']=UNSUPPORTED_MARKET_ASSERTION
            prototype['applicability']=UNSUPPORTED_APPLICABILITY
            prototype['topic']=UNSUPPORTED_MARKET_ASSERTION
            prototype['uncertainty']='There is no uncertainty; it has already been proved profitable.'
            prototype['open_question']=UNSUPPORTED_MARKET_ASSERTION
            if self.attack=='adversarial_with_valid_sibling':
                sibling=copy.deepcopy(prototype)
                sibling['claim']='Model interpretation: observed synthetic process score'
                sibling['topic']='Independent hypothesis of a different mechanism'
                payload['knowledge'].append(sibling)
        if self.attack=='missing_claim_between_valid':
            first=copy.deepcopy(prototype)
            second=copy.deepcopy(prototype)
            third=copy.deepcopy(prototype)
            first['topic']='accepted_first'
            third['topic']='accepted_third'
            second.pop('claim')
            payload['knowledge']=[first,second,third]
        if self.attack=='all_bad_items':
            payload['knowledge']=[dict(prototype,claim=''),
                                  dict(prototype,state='verified_market'),
                                  {'applicability':'missing other required fields'}]
        if self.attack=='bad_nested_type':
            payload['knowledge']=[dict(prototype,supporting_evidence_ids='not-a-list'),
                                  dict(prototype,topic='valid_sibling')]
        if self.attack=='too_many_items':
            payload['knowledge']=[copy.deepcopy(prototype) for _ in range(9)]
        if self.attack=='bad_top_level':
            payload={'not_knowledge':payload['knowledge']}
        self.raw_payload=copy.deepcopy(payload)
        tool['arguments']=json.dumps(payload,ensure_ascii=False,allow_nan=False)
        if self.attack=='truncated':
            choice['finish_reason']='length'
        if self.attack=='invalid_json':
            tool['arguments']='{"knowledge": [{"topic": "unclosed"}'
        if self.attack=='text_only':
            choice['message']['tool_calls']=[]
            choice['message']['content']=UNSUPPORTED_MARKET_ASSERTION
        return reply


class KnowledgeP1Tests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.database=str(Path(temp.name)/'isolated-v083-p1.sqlite3')
        self.config=load_settings(ROOT/'config/settings.toml')
        self.config['database']=self.database
        self.config['mode']='demo'
        self.config['llm']['model']='scripted-fixture-not-a-provider'
        self.project='p1-semantics'
        self.plan=make_plan(self.config,project_id=self.project,
                            objective='Test typed evidence boundaries, not financial performance',
                            max_simulations_per_batch=2)

    def run_case(self,attack):
        script=ContaminatedPlaybookScript(self.database,attack)
        return execute_batch(self.config,self.plan,adapter=script),script

    def book(self):
        memory=Memory(self.database)
        self.addCleanup(memory.close)
        return memory,Playbook(memory)

    def test_adversarial_market_claim_never_becomes_persisted_knowledge(self):
        outcome,script=self.run_case('adversarial')
        self.assertEqual(len(outcome['knowledge_revisions']),1)
        db,book=self.book()
        row=db.db.execute('SELECT * FROM v08_knowledge').fetchone()
        host=json.loads(row['body'])
        raw=db.db.execute("SELECT body_json FROM v08_role_payloads WHERE role='playbook'").fetchone()[0]
        self.assertIn(UNSUPPORTED_MARKET_ASSERTION,raw)
        self.assertNotIn(UNSUPPORTED_MARKET_ASSERTION,row['body'])
        self.assertNotIn(UNSUPPORTED_APPLICABILITY,row['body'])
        self.assertNotIn(UNSUPPORTED_MARKET_ASSERTION,row['topic'])
        self.assertEqual(host['knowledge_semantics'],'host-observation-v1')
        self.assertEqual(host['evidence_scope']['grade'],'synthetic_process')
        self.assertFalse(host['evidence_scope']['market_alpha_validated'])
        self.assertEqual(host['applicability'],'demo_synthetic_process_only')
        self.assertTrue(host['observations'])
        self.assertTrue(all(o['result_is_synthetic'] for o in host['observations']))
        self.assertEqual(host['model_interpretation']['source_item_sha256'],
                         digest(script.raw_payload['knowledge'][0]))
        self.assertTrue(book.audit(self.project)['event_chain_verified'])

    def test_retrieved_memory_has_scoped_observations_without_polluted_prose(self):
        self.run_case('adversarial')
        db,book=self.book()
        memory=book.memory_for(self.project,None)
        self.assertEqual(len(memory),1)
        self.assertEqual(memory[0]['evidence_grade'],'synthetic_process')
        self.assertEqual(memory[0]['evidence_scope']['applies_to'],'synthetic_demo_execution')
        self.assertFalse(memory[0]['market_alpha_validated'])
        self.assertEqual(memory[0]['model_interpretation_status'],'unverified_archived_only')
        self.assertNotIn(UNSUPPORTED_MARKET_ASSERTION,json.dumps(memory))
        self.assertNotIn(UNSUPPORTED_APPLICABILITY,json.dumps(memory))
        self.assertIn('uncertainty',memory[0])
        self.assertIn('applicability',memory[0])

    def test_unverified_interpretation_is_not_market_causality(self):
        first,_=self.run_case('adversarial')
        db,book=self.book()
        self.assertFalse(any(row['evidence_scope']['market_alpha_validated']
                             for row in book.memory_for(self.project,None)))
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM v08_knowledge WHERE evidence_grade='verified_market'").fetchone()[0],0)
        self.assertFalse(first['memory_effectiveness_causally_proven'])

    def test_non_numeric_simulation_metrics_cannot_pollute_observations(self):
        from unittest.mock import patch
        self.run_case('adversarial')
        db,book=self.book()
        real_latest=db.latest
        def poisoned(trial_id):
            original=real_latest(trial_id)
            if original is None:
                return None
            value=copy.deepcopy(original)
            value['metrics']['sharpe']=UNSUPPORTED_MARKET_ASSERTION
            return value
        with patch.object(db,'latest',side_effect=poisoned):
            self.assertEqual(book.completed_evidence(self.project),[])
            self.assertEqual(book.memory_for(self.project,None),[])

    def test_multiple_adversarial_claims_are_archived_only(self):
        outcome,_=self.run_case('adversarial_with_valid_sibling')
        self.assertEqual(len(outcome['knowledge_revisions']),2)
        db,book=self.book()
        knowledge=[r[0] for r in db.db.execute('SELECT body FROM v08_knowledge')]
        self.assertTrue(all(UNSUPPORTED_MARKET_ASSERTION not in r and
                            UNSUPPORTED_APPLICABILITY not in r for r in knowledge))
        self.assertTrue(book.audit(self.project)['event_chain_verified'])

    def test_one_missing_claim_is_rejected_without_losing_valid_siblings(self):
        result,script=self.run_case('missing_claim_between_valid')
        self.assertEqual(len(result['knowledge_revisions']),2)
        self.assertEqual(len(result['knowledge_rejected']),1)
        self.assertEqual(result['knowledge_rejected'][0]['item_index'],1)
        self.assertIn('必填',result['knowledge_rejected'][0]['reason'])
        db,book=self.book()
        receipts=[dict(x) for x in db.db.execute('SELECT * FROM v08_knowledge_reviews ORDER BY proposal_index')]
        self.assertEqual([x['decision'] for x in receipts],['accepted','rejected','accepted'])
        self.assertEqual(receipts[1]['item_sha256'],digest(script.raw_payload['knowledge'][1]))
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM results').fetchone()[0],2)
        self.assertTrue(book.audit(self.project)['event_chain_verified'])
        source=db.db.execute("SELECT body_json FROM v08_role_payloads WHERE role='playbook'").fetchone()[0]
        self.assertEqual(len(json.loads(source)['knowledge']),3)

    def test_invalid_nested_evidence_field_does_not_hide_valid_sibling(self):
        outcome,_=self.run_case('bad_nested_type')
        self.assertEqual(len(outcome['knowledge_revisions']),1)
        self.assertEqual(len(outcome['knowledge_rejected']),1)
        db,book=self.book()
        self.assertTrue(book.audit(self.project)['event_chain_verified'])

    def test_all_malformed_knowledge_preserves_trials_and_budget(self):
        outcome,_=self.run_case('all_bad_items')
        self.assertEqual(len(outcome['knowledge_revisions']),0)
        self.assertEqual(len(outcome['knowledge_rejected']),3)
        db,book=self.book()
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM results').fetchone()[0],2)
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_queue').fetchone()[0],3)
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],0)
        self.assertEqual(book.status(self.project)['status'],'active')
        self.assertTrue(book.audit(self.project)['event_chain_verified'])

    def test_invalid_top_level_tool_structure_fails_closed(self):
        with self.assertRaisesRegex(ResearchError,'字段不匹配'):
            self.run_case('bad_top_level')
        db,book=self.book()
        self.assertEqual(book.status(self.project)['status'],'paused_fault')
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],0)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM v08_role_payloads WHERE role='playbook'").fetchone()[0],0)
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM results').fetchone()[0],2)

    def test_overlong_top_level_knowledge_array_is_not_partially_accepted(self):
        with self.assertRaisesRegex(ResearchError,'批次长度异常'):
            self.run_case('too_many_items')
        db,_=self.book()
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],0)

    def test_truncated_json_tool_response_never_archived_as_knowledge(self):
        with self.assertRaises(ModelOutputTruncated):
            self.run_case('truncated')
        db,book=self.book()
        self.assertEqual(book.status(self.project)['status'],'paused_fault')
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],0)
        self.assertEqual(db.db.execute("SELECT COUNT(*) FROM v08_role_payloads WHERE role='playbook'").fetchone()[0],0)
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM results').fetchone()[0],2)

    def test_incomplete_json_cannot_recover_subitems(self):
        with self.assertRaises(ModelToolCallInvalid):
            self.run_case('invalid_json')
        db,_=self.book()
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],0)

    def test_text_only_fallback_is_never_a_knowledge_source(self):
        with self.assertRaises(ResearchError):
            self.run_case('text_only')
        db,_=self.book()
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],0)

    def test_replay_of_partially_accepted_response_is_idempotent(self):
        self.run_case('missing_claim_between_valid')
        db,book=self.book()
        source=db.db.execute("SELECT * FROM v08_role_payloads WHERE role='playbook'").fetchone()
        before=[db.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                for table in ('v08_knowledge','v08_knowledge_reviews','events','results')]
        result=book.review_knowledge(self.project,source['run_id'],source['request_id'],
                                     json.loads(source['body_json'])['knowledge'])
        after=[db.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
               for table in ('v08_knowledge','v08_knowledge_reviews','events','results')]
        self.assertEqual(before,after)
        self.assertEqual(len(result['accepted']),2)
        self.assertEqual(len(result['rejected']),1)
        self.assertTrue(book.audit(self.project)['event_chain_verified'])

    def test_direct_learn_cannot_forge_review_receipt(self):
        self.run_case('adversarial')
        db,book=self.book()
        row=db.db.execute("SELECT * FROM v08_role_payloads WHERE role='playbook'").fetchone()
        original=json.loads(row['body_json'])['knowledge'][0]
        with self.assertRaisesRegex(ResearchError,'来源必须来自|原始模型提案'):
            book.learn(self.project,row['run_id'],[original],review_receipt={
                'request_id':'made-up-source', 'proposal_index':0,
                'item_sha256':digest(original)})
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],1)

    def test_forged_payload_cannot_be_used_to_upgrade_scope(self):
        self.run_case('adversarial')
        db,book=self.book()
        source=db.db.execute("SELECT * FROM v08_role_payloads WHERE role='playbook'").fetchone()
        forged=json.loads(source['body_json'])['knowledge']
        forged[0]['claim']='Model says we made 100% real market profit'
        counts=db.db.execute('SELECT COUNT(*) FROM v08_knowledge_reviews').fetchone()[0]
        with self.assertRaisesRegex(ResearchError,'原始不可变来源不一致'):
            book.review_knowledge(self.project,source['run_id'],source['request_id'],forged)
        self.assertEqual(db.db.execute('SELECT COUNT(*) FROM v08_knowledge_reviews').fetchone()[0],counts)

if __name__=='__main__':
    unittest.main()
