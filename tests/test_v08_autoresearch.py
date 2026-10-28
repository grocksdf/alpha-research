"""v0.8 scripted protocol tests. They are NOT real LLM discovery evidence.

All expressions are constructed by tests from the authorized catalog; the
production research engine contains no formula examples or seed list.
"""
from __future__ import annotations
import copy
import json
import os
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from alpharesearch.common import ResearchError, load_settings
from alpharesearch.autoresearch_v08 import (
    Playbook, make_plan, execute_batch, control, SCHEMAS)
from alpharesearch.memory import Memory
from alpharesearch.validator import Validator

ROOT=Path(__file__).resolve().parents[1]

class CatalogDerivedScript:
    """Test-only scripted protocol; never presented as autonomous LLM success."""
    def __init__(self, illegal=False, fail_role=None, contradictions=False):
        self.contexts=[]
        self.illegal=illegal
        self.fail_role=fail_role
        self.contradictions=contradictions
        self.hypothesis_field=None
        self.field_counter=0

    def complete(self,phase,messages,schema,model):
        if self.fail_role==phase:
            raise TimeoutError('injected model transport outage')
        context=json.loads(messages[-1]['content'])
        self.contexts.append((phase,context))
        if phase=='explorer':
            fields=sorted(name for name,meta in context['approved_fields'].items() if meta['kind']=='MATRIX')
            assert fields, 'Need an authorized matrix field for the fixture'
            field=fields[self.field_counter%len(fields)]
            self.field_counter+=1
            self.hypothesis_field=field
            parent=(context['experiment_evidence'][0]['hypothesis_id']
                    if context['experiment_evidence'] else None)
            output={'hypotheses':[{'mechanism':'Hypothesis about '+field+
                    ' with evolving inventory constraints',
                    'rationale':'Derived directly from authorized metadata; no market validation',
                    'prediction':'Observable conditional relationship subject to falsification',
                    'required_fields':[field],
                    'falsification':'No repeatability in an independent verification sample',
                    'risks':['unreviewed semantics'], 'sources':['field_catalog'],
                    'research_value':'Test whether the permitted feature has predictive information',
                    'parent_hypothesis_id':parent,
                    'mechanism_label':'novel_free_text_mechanism_'+str(self.field_counter)}]}
        elif phase=='strategist':
            output={'decisions':[{'hypothesis_index':0,'action':'test','priority':1,
                    'rationale':'Unknown direction, test one base simulation',
                    'experiment':'One source-bound base test',
                    'evidence_needed':['independent returns'],
                    'memory_ids_used':[m['id'] for m in context['playbook']][:1],
                    'memory_effect':'Use previous cited evidence to prioritize this base test',
                    'memory_application':[{
                        'memory_id':m['id'],
                        'decision_change':'Prioritize this experiment over an ungrounded new proposal',
                        'evidence_rationale':'The prior experiment record '+m['supporting_evidence_ids'][0]+
                            ' has synthetic-process scope; it informs process selection, not market validity',
                    } for m in context['playbook'][:1]]}],
                    'direction_update':'Reassess earlier evidence; avoid assuming synthetic metrics are real'}
        elif phase=='worker':
            expression='rank('+self.hypothesis_field+')'
            if self.illegal: expression='rank(unapproved_future_data)'
            output={'proposals':[{'hypothesis_index':0,'expression':expression,
                'mechanism_mapping':'Transform only the reviewed source-bound field',
                'expected_direction':'conditional',
                'difference_from_parent':'Reconsider mechanism based on the previous result',
                'parent_revision_id':None}]}
        elif phase=='critic':
            output={'reviews':[{'proposal_index':0,'verdict':'approve',
                'falsifiability':'Requires independent forward validation',
                'mechanism_fidelity':'The proposed rank uses the approved field only',
                'point_in_time_risk':'The host verifies delay and PIT catalog flags',
                'semantic_uncertainty':'No economically verified causal mapping yet',
                'pseudo_novelty_risk':'Host should check exact AST fingerprints',
                'rationale':'One offline engineering simulation may proceed'}]}
        else:
            ids=context.get('allowed_evidence_ids',[])
            assert ids
            output={'knowledge':[{'topic':'workflow:'+self.hypothesis_field,
                'claim':'A simulation completed; no real-market finding is inferred',
                'applicability':'Only the configured synthetic Demo experiment',
                'uncertainty':'Uncertain economic significance',
                'supporting_evidence_ids':[ids[0]],
                'contradicting_evidence_ids':([ids[-1]] if self.contradictions and len(ids)>1 else []),
                'open_question':'Would independent real data support the claim?',
                'state':'provisional'}]}
        return types.SimpleNamespace(data={
            'model':model,'id':'offline-scripted-'+str(len(self.contexts)),
            'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'id':'offline-call-'+str(len(self.contexts)),
            'function':{'name':'v08_'+phase,'arguments':json.dumps(output)}}]}}]})

class V08Tests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config=load_settings(ROOT/'config/settings.toml')
        self.config['database']=str(Path(temporary.name)/'new-v08-only.sqlite3')
        self.config['llm']['model']='offline-identity-for-scripted-tests'
        self.config['mode']='demo'
        self.plan=make_plan(self.config,project_id='novel-v08',objective='Find new legal research relationships')

    def _store(self):
        mem=Memory(self.config['database'])
        return mem,Playbook(mem)

    def test_no_fixed_seed_imports(self):
        body=(ROOT/'alpharesearch/autoresearch_v08.py').read_text()
        self.assertNotIn('_demo_fixtures',body)
        self.assertNotIn('DemoDiscovery',body)
        self.assertNotIn('neighbors(',body)
        self.assertNotIn('rank(',body)
        self.assertFalse((ROOT/'alpharesearch/_demo_fixtures.py').exists())
        self.assertNotIn('enum',SCHEMAS['explorer']['properties']['hypotheses']['items']['properties']['mechanism_label'])

    def test_without_llm_no_new_formula_or_fallback(self):
        # Remove only credential-bearing variables. Clearing the whole
        # environment (clear=True) also drops PATH, which makes snapshot()'s
        # `git rev-parse` fail and flips git_commit to "uncommitted", wrongly
        # tripping the frozen-plan check on Windows.
        with patch.dict(os.environ, {"LLM_API_KEY": ""}, clear=False):
            os.environ.pop("LLM_API_KEY", None)
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaises(ResearchError):
                execute_batch(self.config,self.plan)
        mem,book=self._store()
        try:
            self.assertEqual(book.status('novel-v08')['status'],'paused_fault')
            self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_queue').fetchone()[0],0)
            self.assertEqual(mem.db.execute('SELECT count(*) FROM trials').fetchone()[0],0)
        finally:mem.close()

    def test_new_catalog_generated_hypothesis_is_queued_and_tested(self):
        adapter=CatalogDerivedScript()
        report=execute_batch(self.config,self.plan,adapter=adapter)
        self.assertEqual(report['new_trials'],1)
        self.assertEqual(len(report['knowledge_revisions']),1)
        self.assertFalse(report['memory_retrieved'])
        self.assertFalse(report['memory_referenced'])
        self.assertFalse(report['memory_applied'])
        mem,book=self._store()
        try:
            rows=mem.db.execute('SELECT q.*,t.kind FROM v08_queue q JOIN trials t ON q.trial_id=t.id').fetchall()
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['kind'],'candidate')
            self.assertEqual(rows[0]['state'],'evaluated')
            self.assertEqual(len(mem.db.execute('SELECT * FROM v08_requests').fetchall()),5)
            events=mem.db.execute("SELECT payload FROM events WHERE kind='v08_role_provenance'").fetchall()
            self.assertEqual(len(events),5)
            self.assertTrue(all(json.loads(x[0])['source']=='offline_scripted_test' for x in events))
            self.assertTrue(book.audit('novel-v08')['event_chain_verified'])
        finally:mem.close()
        self.assertNotIn('expression',adapter.contexts[0][1])

    def test_three_batches_parent_lineage_and_memory_usage(self):
        script=CatalogDerivedScript()
        for cycle in range(3):
            report=execute_batch(self.config,self.plan,adapter=script)
            self.assertEqual(report['status'],'active')
            self.assertEqual(report['new_trials'],1)
            if cycle>0:
                self.assertTrue(report['memory_retrieved'])
                self.assertTrue(report['memory_referenced'])
                self.assertTrue(report['memory_applied'])
                self.assertFalse(report['memory_effectiveness_causally_proven'])
        mem,book=self._store()
        try:
            self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_batches').fetchone()[0],3)
            self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_hypotheses WHERE parent_id IS NOT NULL').fetchone()[0],2)
            self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_knowledge').fetchone()[0],3)
            self.assertTrue(book.audit('novel-v08'))
        finally:mem.close()

    def test_model_timeout_pauses_without_fallback(self):
        script=CatalogDerivedScript(fail_role='strategist')
        with self.assertRaises(TimeoutError):
            execute_batch(self.config,self.plan,adapter=script)
        mem,book=self._store()
        try:
            self.assertEqual(book.status('novel-v08')['status'],'paused_fault')
            self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_queue').fetchone()[0],0)
            self.assertEqual(mem.db.execute('SELECT count(*) FROM trials').fetchone()[0],0)
        finally:mem.close()

    def test_ast_invalid_fails_before_simulation(self):
        script=CatalogDerivedScript(illegal=True)
        with self.assertRaises(ResearchError):
            execute_batch(self.config,self.plan,adapter=script)
        mem,book=self._store()
        try:
            self.assertEqual(mem.db.execute('SELECT count(*) FROM trials').fetchone()[0],0)
            self.assertEqual(book.status('novel-v08')['status'],'paused_fault')
        finally:mem.close()

    def test_contradicted_and_stale_knowledge_not_used(self):
        s=CatalogDerivedScript(contradictions=True)
        execute_batch(self.config,self.plan,adapter=s)
        execute_batch(self.config,self.plan,adapter=s)
        mem,book=self._store()
        try:
            # First note is superseded by the contradicted revision; only
            # recent safe memories may be selected for new decisions.
            self.assertNotIn('workflow:'+s.hypothesis_field,
                [x['topic'] for x in book.memory_for('novel-v08',None)])
            self.assertIn('contradicted',[r[0] for r in mem.db.execute('SELECT status FROM v08_knowledge')])
        finally:mem.close()

    def test_memory_retrieved_but_ignored_is_not_applied(self):
        from verification.v08_genuine_llm_acceptance import audit_real_rounds
        class IgnoreMemory(CatalogDerivedScript):
            def complete(self, phase, messages, schema, model):
                result=super().complete(phase,messages,schema,model)
                if phase=="strategist":
                    call=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    parsed=json.loads(call["arguments"])
                    for choice in parsed["decisions"]:
                        choice["memory_ids_used"]=[]
                        choice["memory_application"]=[]
                        choice["memory_effect"]="No earlier evidence influenced this decision"
                    call["arguments"]=json.dumps(parsed)
                return result
        adapter=IgnoreMemory()
        responses=[execute_batch(self.config,self.plan,adapter=adapter) for _ in range(2)]
        self.assertTrue(responses[1]["memory_retrieved"])
        self.assertEqual(responses[1]["memory_referenced"],[])
        self.assertEqual(responses[1]["memory_applied"],[])
        audited=audit_real_rounds(self.config["database"],"novel-v08",
                                  responses,min_rounds=2)
        self.assertEqual(audited["status"],"INCOMPLETE")
        self.assertIn("no_cross_batch_memory_applied",audited["reasons"])
        self.assertFalse(audited["memory_effectiveness_causally_proven"])

    def test_memory_application_has_bound_decision_and_source(self):
        scripted=CatalogDerivedScript()
        responses=[execute_batch(self.config,self.plan,adapter=scripted)
                   for _ in range(2)]
        self.assertTrue(responses[1]["memory_applied"])
        mem,book=self._store()
        try:
            events=[json.loads(x[0]) for x in mem.db.execute(
                "SELECT payload FROM events WHERE kind='v08_memory_influence' ORDER BY id")]
            self.assertEqual(len(events),2)
            item=events[1]["decision_memory_refs"][0]
            self.assertEqual(item["action"],"test")
            self.assertEqual(item["memory_application"][0]["memory_id"],
                             responses[1]["memory_applied"][0])
            self.assertTrue(item["memory_application"][0]["evidence_ids"])
            self.assertTrue(item["memory_application"][0]["evidence_rationale"])
            self.assertTrue(events[1]["decision_explanations_are_not_causal_proof"])
            self.assertTrue(book.audit("novel-v08")["event_chain_verified"])
        finally:mem.close()

    def test_unretrieved_memory_reference_is_rejected_before_simulation(self):
        class InventMemory(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                result=super().complete(phase,messages,schema,model)
                if phase=="strategist":
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    value=json.loads(fn["arguments"])
                    item=value["decisions"][0]
                    item["memory_ids_used"]=["nonexistent-memory-id"]
                    item["memory_application"]=[{
                        "memory_id":"nonexistent-memory-id",
                        "decision_change":"Prioritize this study due to a fabricated memory record",
                        "evidence_rationale":"The non-existent record is not backed by any study evidence"}]
                    fn["arguments"]=json.dumps(value)
                return result
        with self.assertRaises(ResearchError):
            execute_batch(self.config,self.plan,adapter=InventMemory())
        mem,book=self._store()
        try:
            self.assertEqual(book.status("novel-v08")["status"],"paused_fault")
            self.assertEqual(mem.db.execute("SELECT COUNT(*) FROM v08_queue").fetchone()[0],0)
        finally:mem.close()

    def test_superseded_memory_cannot_be_forced_back_into_strategist(self):
        first=execute_batch(self.config,self.plan,adapter=CatalogDerivedScript())
        self.assertTrue(first["knowledge_revisions"])
        old_id=first["knowledge_revisions"][0]
        # A knowledge revision must be attributed to a *second archived model*
        # response. Direct book.learn() injection is now source-forbidden.
        mem,book=self._store()
        try:
            raw=mem.db.execute(
                "SELECT body_json FROM v08_role_payloads WHERE role='playbook' ORDER BY created LIMIT 1"
            ).fetchone()[0]
            previous_topic=json.loads(raw)['knowledge'][0]['topic']
        finally:mem.close()
        class StaleNext(CatalogDerivedScript):
            def __init__(self):
                super().__init__()
                self.field_counter=1  # avoid exact prior formula reuse
            def complete(self,phase,messages,schema,model):
                result=super().complete(phase,messages,schema,model)
                if phase=='playbook':
                    fn=result.data['choices'][0]['message']['tool_calls'][0]['function']
                    value=json.loads(fn['arguments'])
                    value['knowledge'][0]['topic']=previous_topic
                    value['knowledge'][0]['state']='stale'
                    fn['arguments']=json.dumps(value)
                return result
        execute_batch(self.config,self.plan,adapter=StaleNext())
        mem,book=self._store()
        try:
            self.assertEqual(book.memory_for('novel-v08',None),[])
        finally:mem.close()
        class ForceOld(CatalogDerivedScript):
            def complete(self,phase,messages,schema,model):
                result=super().complete(phase,messages,schema,model)
                if phase=="strategist":
                    fn=result.data["choices"][0]["message"]["tool_calls"][0]["function"]
                    value=json.loads(fn["arguments"])
                    choice=value["decisions"][0]
                    choice["memory_ids_used"]=[old_id]
                    choice["memory_application"]=[{
                        "memory_id":old_id,
                        "decision_change":"Increase testing priority based on a now-stale historical hypothesis",
                        "evidence_rationale":"A historical outcome exists but its topic was superseded and must not be reused"}]
                    fn["arguments"]=json.dumps(value)
                return result
        with self.assertRaises(ResearchError):
            execute_batch(self.config,self.plan,adapter=ForceOld())

    def test_cannot_modify_frozen_plan(self):
        altered=copy.deepcopy(self.plan)
        altered['resources']['max_simulations_per_batch']=12
        with self.assertRaises(ResearchError):
            execute_batch(self.config,altered,adapter=CatalogDerivedScript())

    def test_no_real_live_with_scripted_agent_or_offline_run(self):
        live=copy.deepcopy(self.config)
        live['mode']='live'
        with self.assertRaises(ResearchError):
            execute_batch(live,self.plan,adapter=CatalogDerivedScript())

    def test_all_llm_roles_preserve_full_structured_origin_and_raw_hash(self):
        script=CatalogDerivedScript()
        report=execute_batch(self.config,self.plan,adapter=script)
        mem,book=self._store()
        try:
            receipts=mem.db.execute('SELECT * FROM v08_role_payloads ORDER BY created,request_id').fetchall()
            self.assertEqual(len(receipts),5)
            for row in receipts:
                payload=json.loads(row['body_json'])
                from alpharesearch.common import digest
                self.assertEqual(digest(payload),row['canonical_body_sha256'])
                self.assertEqual(len(row['original_raw_sha256']),64)
                self.assertTrue(row['model'])
                self.assertTrue(row['response_id'])
                self.assertTrue(row['tool_call_id'])
                self.assertNotIn('sk-',row['body_json'])
            with self.assertRaises(sqlite3.DatabaseError):
                mem.db.execute('UPDATE v08_role_payloads SET model=? WHERE request_id=?',
                               ('forged-model',receipts[0]['request_id']))
            self.assertTrue(book.audit('novel-v08')['event_chain_verified'])
            self.assertEqual(mem.db.execute('SELECT status FROM v08_batches').fetchone()[0],'completed')
        finally: mem.close()

    def test_historic_database_must_not_be_mutated_or_reused(self):
        mem=Memory(self.config['database'])
        before=mem.db.execute('SELECT count(*) FROM runs').fetchone()[0]
        mem.close()
        with self.assertRaisesRegex(ResearchError,'旧研究数据库'):
            execute_batch(self.config,self.plan,adapter=CatalogDerivedScript())
        mem=Memory(self.config['database'])
        try:
            self.assertEqual(mem.db.execute('SELECT count(*) FROM runs').fetchone()[0],before)
            self.assertEqual(mem.db.execute("SELECT count(*) FROM sqlite_master WHERE name='v08_projects'").fetchone()[0],0)
        finally: mem.close()

    def test_unconfirmed_batch_cannot_be_replayed(self):
        execute_batch(self.config,self.plan,adapter=CatalogDerivedScript())
        mem,book=self._store()
        try:
            book.start_batch('novel-v08','abandoned-run-fixture')
        finally: mem.close()
        with self.assertRaisesRegex(ResearchError,'未确认结束'):
            execute_batch(self.config,self.plan,adapter=CatalogDerivedScript())
        mem,book=self._store()
        try:
            self.assertEqual(book.status('novel-v08')['status'],'paused_fault')
        finally: mem.close()

    def test_free_text_mechanism_is_not_frozen_tag(self):
        from alpharesearch.discovery import validate_idea
        from alpharesearch.engine import Engine
        engine=Engine(self.config,simulation_limit=0)
        try:
            expression='rank(close)'
            body={'title':'Unclassified evidence hypothesis','domain':'price_volume',
                  'mechanism':'Research previously unclassified data mechanism',
                  'expected_sign':'conditional','horizon_days':5,
                  'falsification':'Absent in independent evidence',
                  'expression':expression,'source':'source:catalog',
                  'family':'unclassified_new_mechanism_not_in_old_list',
                  'parent_idea_id':None,'change':'new mechanism'}
            validated=validate_idea(engine,body,['price_volume'],set())
            self.assertTrue(validated.fingerprint)
        finally: engine.memory.close()

    def test_manual_pause_resume_termination(self):
        execute_batch(self.config,self.plan,adapter=CatalogDerivedScript())
        self.assertEqual(control(self.config,self.plan,'pause'),'paused_user')
        with self.assertRaises(ResearchError):
            execute_batch(self.config,self.plan,adapter=CatalogDerivedScript())
        self.assertEqual(control(self.config,self.plan,'resume'),'active')
        self.assertEqual(control(self.config,self.plan,'terminate'),'terminated')
        with self.assertRaises(ResearchError):control(self.config,self.plan,'resume')

if __name__=='__main__': unittest.main()
