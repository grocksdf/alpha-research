"""v0.8.3 Playbook regression: independent scripted transport, no live models.

This module intentionally constructs an unauthorized queued evidence reference to
reproduce the real round-3 failure without copying its market formulas or DB.
The scripted proposals are fixtures, NOT proof of true LLM discovery.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from alpharesearch.autoresearch_v08 import (Playbook, execute_batch, make_plan,
                                           control)
from alpharesearch.common import ResearchError, load_settings
from alpharesearch.memory import Memory
from test_v08_autoresearch import CatalogDerivedScript

ROOT=Path(__file__).resolve().parents[1]


class MixedEvidenceScript(CatalogDerivedScript):
    """Exercise multi-proposal model protocol from *test-only* catalog inputs."""
    def __init__(self, database, outcome='mixed', deferred=False):
        super().__init__()
        self.database=database
        self.outcome=outcome
        self.deferred=deferred
        self.fields=[]
        self.critic_count=0
        self.worker_count=0
        self.playbook_contexts=[]

    def complete(self, phase, messages, schema, model):
        response=super().complete(phase,messages,schema,model)
        fn=response.data['choices'][0]['message']['tool_calls'][0]['function']
        payload=json.loads(fn['arguments'])
        ctx=json.loads(messages[-1]['content'])
        if phase=='explorer':
            matrix=[name for name,meta in ctx['approved_fields'].items()
                    if meta['kind']=='MATRIX']
            preferred=[name for name in ('close','volume','returns','vwap') if name in matrix]
            self.fields=(preferred+[x for x in sorted(matrix) if x not in preferred])[:3]
            assert len(self.fields)==3
            proto=payload['hypotheses'][0]
            pending=ctx.get('pending_critic_revisions',[])
            items=[]
            for i,field in enumerate(self.fields):
                item=copy.deepcopy(proto)
                item['required_fields']=[field]
                item['mechanism']=f'Catalog-scoped model protocol test hypothesis {i} {field}'
                item['mechanism_label']=f'fixture_protocol_{i}'
                item['parent_hypothesis_id']=(pending[0]['hypothesis_id']
                                              if i==0 and pending else None)
                items.append(item)
            payload={'hypotheses':items}
        elif phase=='strategist':
            proto=payload['decisions'][0]
            payload['decisions']=[dict(proto,hypothesis_index=i,priority=i+1)
                                  for i in range(len(ctx['hypotheses']))]
        elif phase=='worker':
            self.worker_count+=1
            proto=payload['proposals'][0]
            pending=ctx.get('pending_critic_revisions',[])
            proposals=[]
            for i,field in enumerate(self.fields):
                proposal=dict(proto,hypothesis_index=i,
                              expression=(f'rank({field})' if self.worker_count==1 else
                              f'ts_rank({field},{5*self.worker_count})'),
                              difference_from_parent=f'Independent source-bound revision of {field}',
                              parent_revision_id=pending[0]['revision_id']
                              if i==0 and pending else None)
                proposals.append(proposal)
            payload={'proposals':proposals}
        elif phase=='critic':
            proto=payload['reviews'][0]
            self.critic_count+=1
            payload={'reviews':[
                dict(proto,proposal_index=i,
                     verdict='defer' if (self.deferred and self.critic_count==1 and i==2)
                     else 'approve')
                for i in range(len(ctx['formula_proposals']))]}
        elif phase=='playbook':
            self.playbook_contexts.append(ctx)
            eligible=ctx['allowed_evidence_ids']
            assert eligible
            with closing(sqlite3.connect('file:'+str(Path(self.database).resolve())+'?mode=ro',uri=True)) as db:
                queued=db.execute("SELECT id FROM v08_queue WHERE state='queued' LIMIT 1").fetchone()
            # Force precisely the invalid reference from the reported failure.
            unknown=queued[0] if queued else 'queued-not-yet-simulated'
            proto=payload['knowledge'][0]
            def item(index,support,contradiction=()):
                return dict(proto,topic=f'fixture-audited-knowledge-{index}',
                    claim='Completed Demo processing observed in a synthetic-only environment',
                    supporting_evidence_ids=list(support),
                    contradicting_evidence_ids=list(contradiction),
                    applicability='Synthetic process observations only')
            if self.outcome=='all_invalid':
                payload={'knowledge':[item(0,[unknown]),item(1,[unknown])]}
            elif self.outcome=='mixed':
                payload={'knowledge':[item(0,[unknown]),item(1,[eligible[0]]),
                          item(2,[eligible[-1]]),item(3,[eligible[0],unknown])]}
            else:
                payload={'knowledge':[item(1,[eligible[0]])]}
        fn['arguments']=json.dumps(payload)
        return response


class PlaybookP0Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config=load_settings(ROOT/'config/settings.toml')
        self.config['database']=str(Path(tmp.name)/'v083-test.sqlite3')
        self.config['mode']='demo'
        self.config['llm']['model']='offline-scripted-only'
        self.plan=make_plan(self.config,project_id='test-v083',objective='Test repaired knowledge persistence',
                            max_simulations_per_batch=2)

    def run_script(self,outcome='mixed',deferred=False):
        script=MixedEvidenceScript(self.config['database'],outcome,deferred)
        return execute_batch(self.config,self.plan,adapter=script),script

    def store(self):
        mem=Memory(self.config['database'])
        self.addCleanup(mem.close)
        return mem,Playbook(mem)

    def test_mixed_completed_and_queued_evidence_are_reviewed_independently(self):
        report,script=self.run_script()
        self.assertEqual(report['new_trials'],2)
        self.assertEqual(len(report['knowledge_revisions']),2)
        self.assertEqual(len(report['knowledge_rejected']),2)
        mem,book=self.store()
        self.assertEqual(mem.db.execute("SELECT count(*) FROM v08_queue WHERE state='queued'").fetchone()[0],1)
        self.assertEqual(mem.db.execute("SELECT count(*) FROM v08_knowledge").fetchone()[0],2)
        self.assertEqual(mem.db.execute("SELECT count(*) FROM v08_knowledge_reviews").fetchone()[0],4)
        self.assertTrue(book.audit('test-v083')['event_chain_verified'])

    def test_raw_learn_still_rejects_unverified_queue_evidence(self):
        self.run_script()
        mem,book=self.store()
        queued=mem.db.execute("SELECT id FROM v08_queue WHERE state='queued'").fetchone()[0]
        source=mem.db.execute("SELECT body_json FROM v08_role_payloads WHERE role='playbook' LIMIT 1").fetchone()
        prototype=json.loads(source[0])['knowledge'][1]
        prototype['supporting_evidence_ids']=[queued]
        with self.assertRaisesRegex(ResearchError,'未经核实'):
            book.learn('test-v083',None,[prototype])

    def test_only_completed_persisted_trials_enter_playbook_prompt(self):
        _,script=self.run_script()
        evidence=script.playbook_contexts[0]['new_experiment_evidence']
        self.assertEqual(len(evidence),2)
        self.assertEqual(set(script.playbook_contexts[0]['allowed_evidence_ids']),
                         {x['evidence_id'] for x in evidence})
        self.assertTrue(all(x['state']=='evaluated' and x['trial_id'] for x in evidence))
        mem,book=self.store()
        whitelist=[json.loads(row[0]) for row in mem.db.execute(
            "SELECT payload FROM events WHERE kind='v08_playbook_evidence_whitelist'")]
        self.assertEqual(len(whitelist),1)
        self.assertEqual(whitelist[0]['queued_excluded'],1)
        self.assertEqual(set(whitelist[0]['eligible_evidence_ids']),
                         {x['evidence_id'] for x in evidence})

    def test_all_invalid_proposals_preserve_simulation_and_queue_without_knowledge(self):
        report,_=self.run_script('all_invalid')
        self.assertEqual(report['knowledge_revisions'],[])
        self.assertEqual(len(report['knowledge_rejected']),2)
        mem,book=self.store()
        self.assertEqual(mem.db.execute('SELECT count(*) FROM results').fetchone()[0],2)
        self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_knowledge').fetchone()[0],0)
        self.assertEqual(mem.db.execute('SELECT count(*) FROM v08_queue').fetchone()[0],3)
        self.assertEqual(book.status('test-v083')['status'],'active')
        self.assertTrue(book.audit('test-v083')['event_chain_verified'])

    def test_synthetic_scope_never_promotes_market_evidence(self):
        self.run_script()
        mem,book=self.store()
        self.assertEqual({row[0] for row in mem.db.execute('SELECT evidence_grade FROM v08_knowledge')},
                         {'synthetic_process'})
        self.assertTrue(all(not x['market_alpha_validated']
                            for x in book.memory_for('test-v083',None)))
        self.assertTrue(all(not e['market_alpha_validated']
                            for e in book.completed_evidence('test-v083')))

    def test_replayed_review_receipts_are_idempotent(self):
        self.run_script()
        mem,book=self.store()
        row=mem.db.execute("SELECT * FROM v08_role_payloads WHERE role='playbook'").fetchone()
        before=(mem.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],
                mem.db.execute('SELECT COUNT(*) FROM v08_knowledge_reviews').fetchone()[0],
                mem.db.execute('SELECT COUNT(*) FROM events').fetchone()[0])
        response=book.review_knowledge('test-v083',row['run_id'],row['request_id'],
                                       json.loads(row['body_json'])['knowledge'])
        after=(mem.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],
                mem.db.execute('SELECT COUNT(*) FROM v08_knowledge_reviews').fetchone()[0],
                mem.db.execute('SELECT COUNT(*) FROM events').fetchone()[0])
        self.assertEqual(before,after)
        self.assertEqual(len(response['accepted']),2)
        self.assertEqual(len(response['rejected']),2)

    def test_mismatched_source_payload_is_rejected_without_writes(self):
        self.run_script()
        mem,book=self.store()
        row=mem.db.execute("SELECT * FROM v08_role_payloads WHERE role='playbook'").fetchone()
        body=json.loads(row['body_json'])['knowledge']
        forged=copy.deepcopy(body)
        forged[1]['claim']='Forged knowledge payload'
        with self.assertRaisesRegex(ResearchError,'不一致'):
            book.review_knowledge('test-v083',row['run_id'],row['request_id'],forged)
        self.assertEqual(mem.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],2)

    def test_review_receipts_cannot_be_updated_or_deleted(self):
        self.run_script()
        mem,_=self.store()
        row=mem.db.execute('SELECT request_id,proposal_index FROM v08_knowledge_reviews LIMIT 1').fetchone()
        with self.assertRaises(sqlite3.DatabaseError):
            mem.db.execute('DELETE FROM v08_knowledge_reviews WHERE request_id=? AND proposal_index=?',tuple(row))
        with self.assertRaises(sqlite3.DatabaseError):
            mem.db.execute('UPDATE v08_knowledge_reviews SET reason=? WHERE request_id=? AND proposal_index=?',
                           ('forged',*tuple(row)))

    def test_database_write_fault_preserves_receipts_then_reconciles_after_resume(self):
        original=Playbook.event
        count={'accepted':0}
        def interrupt(book,run_id,kind,payload):
            if kind=='playbook_proposal_reviewed' and payload['decision']=='accepted':
                count['accepted']+=1
                if count['accepted']==2:
                    raise sqlite3.OperationalError('injected failure in knowledge receipt transaction')
            return original(book,run_id,kind,payload)
        with patch.object(Playbook,'event',interrupt):
            with self.assertRaises(sqlite3.OperationalError):
                self.run_script()
        mem,book=self.store()
        self.assertEqual(book.status('test-v083')['status'],'paused_fault')
        self.assertEqual(mem.db.execute('SELECT COUNT(*) FROM results').fetchone()[0],2)
        self.assertEqual(mem.db.execute('SELECT COUNT(*) FROM v08_knowledge').fetchone()[0],1)
        self.assertEqual(mem.db.execute('SELECT COUNT(*) FROM v08_knowledge_reviews').fetchone()[0],2)
        original_ids={row[0] for row in mem.db.execute('SELECT id FROM trials')}
        original_runs={row[0]:row[1] for row in mem.db.execute('SELECT id,simulations FROM runs')}
        mem.close()
        self.assertEqual(control(self.config,self.plan,'resume'),'active')
        report,_=self.run_script('all_invalid')
        mem2,book2=self.store()
        self.assertTrue(book2.audit('test-v083')['event_chain_verified'])
        original_req=mem2.db.execute("SELECT id FROM v08_requests WHERE role='playbook' ORDER BY created LIMIT 1").fetchone()[0]
        self.assertEqual(mem2.db.execute('SELECT COUNT(*) FROM v08_knowledge_reviews WHERE request_id=?',
                                         (original_req,)).fetchone()[0],4)
        self.assertEqual(mem2.db.execute('SELECT COUNT(*) FROM v08_knowledge WHERE project_id=?',
                                         ('test-v083',)).fetchone()[0],2)
        for trial_id in original_ids:
            self.assertEqual(mem2.db.execute('SELECT COUNT(*) FROM results WHERE trial_id=?',
                                             (trial_id,)).fetchone()[0],1)
        for run_id,spent in original_runs.items():
            self.assertEqual(mem2.db.execute('SELECT simulations FROM runs WHERE id=?',
                                             (run_id,)).fetchone()[0],spent)
        self.assertTrue(mem2.db.execute(
            "SELECT 1 FROM events WHERE kind='v08_playbook_reconciled'").fetchone())

    def test_pending_critic_revision_is_consumed_with_parent_lineage(self):
        first,script=self.run_script('all_valid',deferred=True)
        mem,book=self.store()
        old=book.pending_revisions('test-v083')
        self.assertEqual(len(old),1)
        rev_id=old[0]['revision_id']
        mem.close()
        second=execute_batch(self.config,self.plan,adapter=script)
        mem2,book2=self.store()
        status=mem2.db.execute('SELECT state FROM v08_revisions WHERE id=?',(rev_id,)).fetchone()[0]
        self.assertEqual(status,'resolved')
        parents=mem2.db.execute('SELECT parent_id FROM v08_hypotheses WHERE parent_id IS NOT NULL').fetchall()
        self.assertTrue(parents)
        self.assertTrue(book2.audit('test-v083')['event_chain_verified'])
        self.assertIn('memory_referenced',second)

    def test_unconfirmed_simulation_prevents_replay_and_leaves_budget_intact(self):
        self.run_script()
        mem,book=self.store()
        queued=mem.db.execute("SELECT id FROM v08_queue WHERE state='queued'").fetchone()[0]
        book.transition(None,queued,'queued','testing','simulate_unknown_failure')
        spent=mem.db.execute('SELECT SUM(simulations) FROM runs').fetchone()[0]
        count=mem.db.execute('SELECT COUNT(*) FROM results').fetchone()[0]
        mem.close()
        with self.assertRaisesRegex(ResearchError,'未知 Simulation'):
            self.run_script()
        mem2,book2=self.store()
        self.assertEqual(book2.status('test-v083')['status'],'paused_fault')
        self.assertEqual(mem2.db.execute('SELECT SUM(simulations) FROM runs').fetchone()[0],spent)
        self.assertEqual(mem2.db.execute('SELECT COUNT(*) FROM results').fetchone()[0],count)

    def test_historical_protocol_database_rejected_without_schema_mutation(self):
        # A v0.8.2 frozen project must stay on its original protocol/code.
        import sqlite3
        from alpharesearch.common import dumps
        file=Path(self.config['database'])
        with closing(sqlite3.connect(file)) as db:
            db.execute('CREATE TABLE v08_projects(project_id TEXT,plan_json TEXT,plan_sha256 TEXT,status TEXT,reason TEXT,created TEXT)')
            legacy=dict(self.plan,protocol='llm-autoresearch-v08')
            db.execute('INSERT INTO v08_projects VALUES(?,?,?,?,?,?)',
                       ('historical-v082',dumps(legacy),'legacy-frozen-identity',
                        'paused_fault','old_frozen_project','2026-10-26T00:00:00Z'))
            db.commit()
            original_schema=set(x[0] for x in db.execute('SELECT name FROM sqlite_master'))
        with self.assertRaisesRegex(ResearchError,'旧版本冻结研究项目'):
            self.run_script()
        with closing(sqlite3.connect(file)) as db:
            self.assertEqual(original_schema,{x[0] for x in db.execute('SELECT name FROM sqlite_master')})
            self.assertEqual(db.execute('SELECT status FROM v08_projects').fetchone()[0],'paused_fault')
            self.assertNotIn('runs',original_schema)

    def test_control_refuses_mismatched_plan_without_status_change(self):
        self.run_script()
        altered=copy.deepcopy(self.plan)
        altered['plan_sha256']='f'*64
        with self.assertRaisesRegex(ResearchError,'控制操作拒绝'):
            control(self.config,altered,'pause')
        mem,book=self.store()
        self.assertEqual(book.status('test-v083')['status'],'active')

    def test_memory_retrieved_referenced_applied_are_separate_receipts(self):
        first,script=self.run_script('all_valid')
        second=execute_batch(self.config,self.plan,adapter=script)
        mem,book=self.store()
        influences=[json.loads(row[0]) for row in mem.db.execute(
            "SELECT payload FROM events WHERE kind='v08_memory_influence' ORDER BY id")]
        self.assertEqual(len(influences),2)
        self.assertEqual(influences[1]['memory_retrieved'],second['memory_retrieved'])
        self.assertEqual(influences[1]['memory_referenced'],second['memory_referenced'])
        self.assertEqual(influences[1]['memory_applied'],second['memory_applied'])
        self.assertFalse(second['memory_effectiveness_causally_proven'])


if __name__=='__main__':
    unittest.main()
