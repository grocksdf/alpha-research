"""Offline regression against semantic-contract drift and sensitive-output false positives.

All model responses are TEST fixtures; none is a genuine remote LLM call.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alpharesearch.autoresearch_v08 import SCHEMAS, _check_structure, execute_batch, make_plan, Playbook
from alpharesearch.common import ResearchError, load_settings
from alpharesearch.memory import Memory
from alpharesearch.v083_semantic_contracts import describe_contract, validate_contract
from alpharesearch.v083_output_privacy import screen_and_sanitize
from test_v08_autoresearch import CatalogDerivedScript

ROOT=Path(__file__).resolve().parents[1]


class VersionTests(unittest.TestCase):
    def test_package_runtime_and_build_versions_match(self):
        import tomllib
        from alpharesearch import __version__
        metadata=tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))
        self.assertEqual(__version__,metadata['project']['version'])


class ContractTests(unittest.TestCase):
    def test_schema_guidance_is_derived_from_actual_schema(self):
        altered=copy.deepcopy(SCHEMAS['explorer'])
        altered['properties']['hypotheses']['maxItems']=3
        description=describe_contract('explorer',altered,{'approved_fields':{'field_from_catalog':{}}})
        self.assertIn('hypotheses: array up to 3 records',description)
        self.assertIn('required_fields',description)
        self.assertIn('approved_fields',description)
        self.assertNotIn('rank(',description)

    def test_strategist_contract_emphasizes_blank_memory_effect_and_pairing(self):
        guidance=describe_contract('strategist',SCHEMAS['strategist'],{})
        self.assertIn('memory_effect',guidance)
        self.assertIn('memory_application',guidance)
        self.assertIn('empty',guidance)

    def test_explorer_requires_bare_approved_fields(self):
        hypothesis={'required_fields':['volume'] , 'sources':['catalog']}
        with self.assertRaisesRegex(ResearchError,'未经授权'):
            validate_contract('explorer',{'hypotheses':[{**hypothesis,'required_fields':['ts_mean(volume,10)']}]},
                              {'approved_fields':{'volume':{}}})
        self.assertTrue(validate_contract('explorer',{'hypotheses':[hypothesis]},
                                          {'approved_fields':{'volume':{}}}))

    def test_explorer_refuses_no_sources(self):
        with self.assertRaisesRegex(ResearchError,'依据'):
            validate_contract('explorer',{'hypotheses':[{'required_fields':['volume'],'sources':[]}]},
                              {'approved_fields':{'volume':{}}})

    def test_strategist_one_to_one_memory_usage(self):
        decision={'hypothesis_index':0,'memory_ids_used':['m1'],
                  'memory_application':[]}
        with self.assertRaisesRegex(ResearchError,'逐条对应'):
            validate_contract('strategist',{'decisions':[decision]},
                              {'hypotheses':[{}],'playbook':[{'id':'m1'}]})
        decision['memory_application']=[{'memory_id':'m1'}]
        self.assertTrue(validate_contract('strategist',{'decisions':[decision]},
                                          {'hypotheses':[{}],'playbook':[{'id':'m1'}]}))
        with self.assertRaisesRegex(ResearchError,'未检索'):
            validate_contract('strategist',{'decisions':[decision]},
                              {'hypotheses':[{}],'playbook':[]})

    def test_nonblank_memory_effect_is_structural_even_with_no_memory(self):
        prototype=SCHEMAS['strategist']['properties']['decisions']['items']['properties']
        empty={key: ([] if rule['type']=='array' else 0 if rule['type']=='integer' else '')
               for key,rule in prototype.items()}
        empty.update({'hypothesis_index':0,'priority':1,'action':'test',
                      'rationale':'rationale','experiment':'experiment',
                      'memory_effect':''})
        with self.assertRaisesRegex(ResearchError,'memory_effect'):
            _check_structure('strategist',{'decisions':[empty],
                                           'direction_update':'explore without memory'})

    def test_critic_exact_coverage_refuses_missing_and_duplicate(self):
        context={'formula_proposals':[{},{}]}
        for indexes in ([0],[0,0],[0,3],[1,1]):
            with self.subTest(indexes=indexes):
                with self.assertRaisesRegex(ResearchError,'逐条'):
                    validate_contract('critic',{'reviews':[{'proposal_index':i} for i in indexes]},context)
        self.assertTrue(validate_contract('critic',{'reviews':[{'proposal_index':1},
                                                             {'proposal_index':0}]},context))

    def test_worker_cannot_use_unselected_hypothesis(self):
        with self.assertRaisesRegex(ResearchError,'未经 Strategist'):
            validate_contract('worker',{'proposals':[{'hypothesis_index':2,'parent_revision_id':None}]},
                              {'selected_indices':[0,1]})


class PrivacyTests(unittest.TestCase):
    def test_scholarly_url_and_contact_do_not_block(self):
        body={'rationale':'Source https://doi.org/10.1234/abcd?utm=case and author@university.edu'}
        safe,receipt=screen_and_sanitize(body)
        self.assertEqual(receipt['decision'],'accepted_sanitized')
        self.assertIn('https://doi.org/10.1234/abcd',safe['rationale'])
        self.assertNotIn('utm=',safe['rationale'])
        self.assertNotIn('author@university.edu',safe['rationale'])
        self.assertIn('[REDACTED_CONTACT]',safe['rationale'])
        self.assertEqual(receipt['emails_redacted'],1)

    def test_arbitrary_source_url_carries_no_automatic_block(self):
        body={'reference':'https://papers.example.org/working-paper/2001'}
        safe,receipt=screen_and_sanitize(body)
        self.assertEqual(safe,body)
        self.assertEqual(receipt['credential_signal_categories'],[])

    def test_bearer_prefix_and_password_assignments_are_blocked(self):
        for text in ('Bearer ABCDEFGHIJKLMNOPQRSTUV',
                     'password=VerySensitivePassword123',
                     'https://user:secret@research.example.org/paper'):
            with self.subTest(kind=text.split(':')[0]):
                safe,receipt=screen_and_sanitize({'rationale':text})
                self.assertIsNone(safe)
                self.assertEqual(receipt['decision'],'blocked_credentials')
                self.assertNotIn('VerySensitivePassword123',str(receipt))

    def test_actual_environment_secret_matches_even_without_prefix(self):
        with patch.dict(os.environ,{'LLM_API_KEY':'ActualOpaqueSecretValue987654'},clear=False):
            safe,receipt=screen_and_sanitize({'source':'reference ActualOpaqueSecretValue987654'})
            self.assertIsNone(safe)
            self.assertIn('environment_secret_match',receipt['credential_signal_categories'])
            self.assertNotIn('ActualOpaqueSecretValue987654',str(receipt))

    def test_nested_text_is_screened_and_redacted(self):
        payload={'knowledge':[{'references':['editor@papers.edu',
                                             'https://ssrn.com/abstract=123456?ref=private']} ]}
        safe,receipt=screen_and_sanitize(payload)
        self.assertEqual(receipt['emails_redacted'],1)
        self.assertNotIn('editor@papers.edu',str(safe))
        self.assertNotIn('?ref=',str(safe))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.conf=load_settings(ROOT/'config/settings.toml')
        self.conf['database']=str(Path(t.name)/'semantic-test.sqlite3')
        self.conf['mode']='demo'
        self.conf['llm']['model']='offline-catalog-test'
        self.plan=make_plan(self.conf,project_id='semantic-test',objective='Test audit integrity with a catalog-only generator')

    def open_store(self):
        mem=Memory(self.conf['database'])
        self.addCleanup(mem.close)
        return mem,Playbook(mem)

    def test_legitimate_critic_citations_complete_and_are_sanitized_at_rest(self):
        class CitationScript(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                response=super().complete(phase,messages,schema,model)
                if phase=='critic':
                    func=response.data['choices'][0]['message']['tool_calls'][0]['function']
                    obj=json.loads(func['arguments'])
                    obj['reviews'][0]['rationale'] += ' https://doi.org/10.5555/paper?source=test researcher@lab.example'
                    self.original=func['arguments']=json.dumps(obj)
                return response
        fixture=CitationScript()
        report=execute_batch(self.conf,self.plan,adapter=fixture)
        self.assertEqual(report['new_trials'],1)
        mem,book=self.open_store()
        row=mem.db.execute("SELECT * FROM v08_role_payloads WHERE role='critic'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row['original_raw_sha256'],hashlib.sha256(fixture.original.encode()).hexdigest())
        self.assertNotIn('researcher@lab.example',row['body_json'])
        self.assertIn('https://doi.org/10.5555/paper',row['body_json'])
        self.assertNotIn('?source=test',row['body_json'])
        self.assertNotEqual(row['original_raw_sha256'],row['canonical_body_sha256'])
        reviews=[json.loads(r[0]) for r in mem.db.execute(
            "SELECT payload FROM events WHERE kind='v08_structured_output_security_review'")]
        self.assertEqual(len(reviews),5)
        critic=next(r for r in reviews if r['role']=='critic')
        self.assertEqual(critic['decision'],'accepted_sanitized')
        self.assertEqual(critic['emails_redacted'],1)
        self.assertTrue(book.audit('semantic-test')['event_chain_verified'])

    def test_semantic_failure_records_rule_id_without_archiving_bad_model_data(self):
        class WrongFieldsScript(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                response=super().complete(phase,messages,schema,model)
                if phase=='explorer':
                    func=response.data['choices'][0]['message']['tool_calls'][0]['function']
                    obj=json.loads(func['arguments'])
                    obj['hypotheses'][0]['required_fields']=['illegal(expression)']
                    func['arguments']=json.dumps(obj)
                return response
        with self.assertRaisesRegex(ResearchError,'未经授权'):
            execute_batch(self.conf,self.plan,adapter=WrongFieldsScript())
        mem,book=self.open_store()
        event=mem.db.execute("SELECT payload FROM events WHERE kind='v08_structured_contract_rejected'").fetchone()
        self.assertIsNotNone(event)
        reason=json.loads(event[0])
        self.assertEqual(reason['rule_id'],'authorized_data_fields')
        self.assertEqual(reason['kind'],'semantic')
        self.assertIn('original_raw_sha256',reason)
        self.assertFalse(reason['raw_response_persisted'])
        self.assertEqual(mem.db.execute('SELECT COUNT(*) FROM v08_role_payloads').fetchone()[0],0)
        self.assertTrue(book.audit('semantic-test')['event_chain_verified'])

    def test_secret_response_fails_closed_and_leaves_hash_only_audit(self):
        class SecretScript(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                response=super().complete(phase,messages,schema,model)
                if phase=='critic':
                    func=response.data['choices'][0]['message']['tool_calls'][0]['function']
                    obj=json.loads(func['arguments'])
                    obj['reviews'][0]['rationale'] += ' Bearer SecretLongTokenABCDEFGHIJKL'
                    self.secret='SecretLongTokenABCDEFGHIJKL'
                    func['arguments']=json.dumps(obj)
                return response
        fixture=SecretScript()
        with self.assertRaisesRegex(ResearchError,'高置信度凭据'):
            execute_batch(self.conf,self.plan,adapter=fixture)
        mem,book=self.open_store()
        self.assertEqual(mem.db.execute("SELECT COUNT(*) FROM v08_role_payloads WHERE role='critic'").fetchone()[0],0)
        self.assertEqual(mem.db.execute("SELECT COUNT(*) FROM results").fetchone()[0],0)
        review=mem.db.execute("SELECT payload FROM events WHERE kind='v08_structured_output_security_review' ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertIn('blocked_credentials',review)
        self.assertIn('original_raw_sha256',review)
        self.assertNotIn(fixture.secret,review)
        self.assertEqual(mem.db.execute("SELECT state FROM v08_requests WHERE role='critic'").fetchone()[0],'failed')
        self.assertEqual(book.status('semantic-test')['status'],'paused_fault')
        self.assertTrue(book.audit('semantic-test')['event_chain_verified'])


if __name__=='__main__':
    unittest.main()
