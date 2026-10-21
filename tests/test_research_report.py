import copy
import io
import json
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from robust_fixtures import manifest,protocol,document
from test_system import Base,FixtureSimulator
import test_v04
from alpharesearch.analysis_protocol import freeze_protocol,read_json
from alpharesearch.cli import main
from alpharesearch.common import ResearchError,digest
from alpharesearch.engine import Engine
from alpharesearch.evidence_audit import audit_existing_alphas
from alpharesearch.memory import Memory
from alpharesearch.research_report import analyze_manifest,research_report
from alpharesearch.return_contract import EvidenceError,verify_return_document


class ResearchReportTests(Base):
    def review(self,candidate,ready=False):
        snapshot={'policy':{'required_checks':['LOW_SHARPE']},'scope':candidate.get('settings',{})}
        value={'run_id':'TEST_RUN','mode':'live','snapshot':snapshot,'snapshot_hash':digest(snapshot),
            'counts':{'simulations':2,'proposals':3,'llm_calls':0},'costs':{'known_usd':None},
            'candidates':[candidate|{'evaluation':{'ready':ready,'missing':[] if ready else ['missing_neighbors'],'rejections':[]}}],
            'trials':[{'id':'TEST_FAILED','state':'failed'},{'id':'TEST_INVALID','state':'rejected'},
                      {'id':'TEST_PENDING','state':'uncertain'}]}
        value['review_hash']=digest(value);return value

    def test_diagnostics_do_not_promote_an_incomplete_engine_evaluation(self):
        m=manifest(1,126);p=protocol(m['series']);c={'alpha_id':m['series'][0]['candidate_id'],
            'checks':[{'name':'SELF_CORRELATION','result':'PASS'},{'name':'LOW_SHARPE','result':'PASS'}]}
        r=research_report(m,p,self.review(c))
        self.assertFalse(r['series'][0]['ready_to_submit']);self.assertEqual(r['series'][0]['candidate_status'],'evidence_incomplete')
        self.assertEqual([t['state'] for t in r['trials']],['failed','rejected','uncertain'])
        self.assertFalse(r['multiplicity_history_complete_from_single_run']);self.assertIsNone(r['historical_submit_posts'])

    def test_pending_self_check_or_synthetic_returns_cannot_be_ready(self):
        m=manifest(1,126);p=protocol(m['series']);d=m['series'][0];scope={'region':'USA','universe':'SYNTHETIC_ONLY','delay':1}
        c={'alpha_id':d['candidate_id'],'expression':'rank(close)','settings':scope,'evidence_source':{'account_ref':'a'*64},
           'checks':[{'name':'SELF_CORRELATION','result':'PENDING'},{'name':'LOW_SHARPE','result':'PASS'}]}
        d['contract'].update(source_type='brain_pnl',source_id=d['candidate_id'],population_id='a'*64,account_ref='a'*64,
            expression_sha256=digest(c['expression']),settings_sha256=digest(scope))
        for synthetic,pending in ((True,False),(False,True)):
            d['synthetic']=synthetic;c['checks'][0]['result']='PENDING' if pending else 'PASS'
            r=research_report(m,p,self.review(c,True));self.assertNotEqual(r['series'][0]['candidate_status'],'ready_for_human_review')
            self.assertFalse(r['series'][0]['ready_to_submit'])

    def test_original_ready_decision_still_requires_identity_reconciliation(self):
        m=manifest(1,126);p=protocol(m['series']);d=m['series'][0];scope=d['contract']['scope'];c={'alpha_id':d['candidate_id'],
            'expression':'rank(close)','settings':scope,'evidence_source':{'account_ref':'a'*64},
            'checks':[{'name':'SELF_CORRELATION','result':'PASS'},{'name':'LOW_SHARPE','result':'PASS'}]}
        d['synthetic']=False;d['contract'].update(source_type='brain_pnl',source_id=d['candidate_id'],population_id='a'*64,
            account_ref='a'*64,settings_sha256=digest(scope),expression_sha256=digest('rank(volume)'))
        r=research_report(m,p,self.review(c,True));self.assertFalse(r['series'][0]['return_identity_reconciled'])
        d['contract']['expression_sha256']=digest(c['expression']);r=research_report(m,p,self.review(c,True))
        self.assertEqual(r['series'][0]['candidate_status'],'ready_for_human_review')
        self.assertFalse(r['submission_enabled']);self.assertFalse(r['series'][0]['ready_to_submit'])

    def test_tampered_source_review_does_not_get_reassessed_as_ready(self):
        m=manifest(1,126);p=protocol(m['series']);review=self.review({'alpha_id':'SYNTHETIC_0'})
        review['counts']['simulations']=0
        with self.assertRaises(EvidenceError):research_report(m,p,review)

    def test_all_analysis_cli_are_local_no_engine_memory_holdout_or_http(self):
        m=manifest(1,126);p=protocol(m['series']);folder=Path(self.tmp.name)
        path=folder/'returns.json';path.write_text(json.dumps(m),encoding='utf-8')
        frozen=folder/'protocol.json';frozen.write_text(json.dumps(p),encoding='utf-8')
        spec=folder/'spec.json';spec.write_text(json.dumps({k:v for k,v in p.items() if k not in ('protocol_sha256','registered_at')}),encoding='utf-8')
        commands=[['analysis-freeze','--spec',str(spec)],['verify-returns','--manifest',str(path)]]
        commands += [[name,'--manifest',str(path),'--protocol',str(frozen)] for name in ('robustness-audit','selection-bias-audit','research-report')]
        with (patch('alpharesearch.cli.load_settings',return_value=self.config),
              patch('alpharesearch.cli.credentials',side_effect=AssertionError('login')),
              patch('alpharesearch.cli.Engine',side_effect=AssertionError('Engine')),
              patch('alpharesearch.cli.Memory',side_effect=AssertionError('Memory')),
              patch('alpharesearch.cli.worker_lock',side_effect=AssertionError('worker')),
              patch('alpharesearch.local_validation.final_holdout',side_effect=AssertionError('holdout')),
              patch('alpharesearch.http.JsonTransport.request',side_effect=AssertionError('HTTP')) as request,
              redirect_stdout(io.StringIO())):
            for i,args in enumerate(commands):main(args+['--output',str(folder/('out'+str(i)+'.json'))])
        request.assert_not_called();self.assertFalse(Path(self.config['database']).exists())
        self.assertEqual(m,json.loads(path.read_text(encoding='utf-8')))

    def test_offline_analysis_preserves_old_database_snapshots_history_and_budget(self):
        engine=Engine(self.config,simulator=FixtureSimulator())
        engine.propose('rank(close)','TEST','TEST');memory=engine.memory
        rows_before=list(memory.db.iterdump());tables_before=[r[0] for r in memory.db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        m=manifest(1,126);p=protocol(m['series']);research_report(m,p)
        self.assertEqual(rows_before,list(memory.db.iterdump()));memory.close()
        # v0.5 adds no DB tables or migrations, so the v0.4.2 schema is unchanged.
        memory=Memory(self.config['database'])
        try:
            self.assertEqual(tables_before,[r[0] for r in memory.db.execute("SELECT name FROM sqlite_master WHERE type='table'")])
            self.assertEqual(rows_before,list(memory.db.iterdump()))
        finally:memory.close()

    def test_duplicate_json_fields_fail_closed_and_outputs_do_not_overwrite(self):
        folder=Path(self.tmp.name);path=folder/'duplicate.json';path.write_text('{"x":1,"x":2}',encoding='utf-8')
        with self.assertRaises(ResearchError):read_json(path)
        m=manifest(1,126);p=protocol(m['series']);data=folder/'m.json';frozen=folder/'p.json';out=folder/'out.json'
        data.write_text(json.dumps(m),encoding='utf-8');frozen.write_text(json.dumps(p),encoding='utf-8');out.write_text('original',encoding='utf-8')
        with patch('alpharesearch.cli.load_settings',return_value=self.config),redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):main(['robustness-audit','--manifest',str(data),'--protocol',str(frozen),'--output',str(out)])
        self.assertEqual(out.read_text(encoding='utf-8'),'original')

    def test_four_missing_alphas_keep_upstream_reasons_without_fabricated_data(self):
        m=manifest(4,126);p=protocol(m['series'])
        m['series']=[{'candidate_id':d['candidate_id'],'contract':None,
                      'verification':{'reasons':['account_authorization_missing']}} for d in m['series']]
        r=analyze_manifest(m,p);self.assertEqual(r['status'],'insufficient_evidence')
        self.assertFalse(r['real_return_contracts_verified'])
        for item in r['series']:
            self.assertIn('account_authorization_missing',item['evidence']['reasons'])
            self.assertIsNone(item['robustness']);self.assertIsNone(item['dsr'])


class ExistingAlphaExportTests(Base):
    simulator=test_v04.RecoveryTests.simulator
    def test_export_four_existing_alpha_evidence_is_get_only_no_runs_or_simulations(self):
        sim,http=self.simulator();http.alphas={aid:http.detail(aid) for aid in ('one','two','three','four')}
        folder=Path(self.tmp.name);path=folder/'alphas.json';output=folder/'audit.json';returns=folder/'returns.json'
        path.write_text(json.dumps([{'alpha_id':aid,'expression':'rank(close)','settings':self.config['scope']} for aid in http.alphas]),encoding='utf-8')
        value=audit_existing_alphas(sim,path,output,returns)
        self.assertTrue(all(method=='GET' for method,_,_ in http.calls));self.assertFalse(Path(self.config['database']).exists())
        export=json.loads(returns.read_text(encoding='utf-8'));self.assertEqual(len(export['series']),4)
        for row in export['series']:
            self.assertIsNone(row['contract']);self.assertNotIn('observations',row)
        self.assertEqual(export['simulation_posts'],0);self.assertEqual(export['submit_posts'],0)
        self.assertFalse(value['real_evidence_acceptance_completed'])
