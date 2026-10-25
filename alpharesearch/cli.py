from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from .brain import BrainSimulator
from .common import ResearchError, load_settings
from .engine import Engine
from .memory import Memory


@contextmanager
def worker_lock(database):
    """Single local worker. Kernel releases lock on crash; no stale lock-file guesswork."""
    path = Path(database + ".worker.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        os.chmod(path, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise ResearchError("此数据库已有任务执行器运行") from e
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def save_json(path,value):
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def credentials():
    if not os.getenv("BRAIN_EMAIL"):
        os.environ["BRAIN_EMAIL"] = input("BRAIN Email: ").strip()
    if not os.getenv("BRAIN_PASSWORD"):
        os.environ["BRAIN_PASSWORD"] = getpass.getpass("BRAIN Password（不回显，不写文件）: ")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AlphaResearch V0.6.1 自主研究与完整收益契约门禁")
    parser.add_argument("--config", default="config/settings.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="离线配置检查；不登录")
    autonomous_plan = sub.add_parser('autonomy-plan', help='冻结自主发现范围、总预算及模型调用上限；不联网')
    autonomous_plan.add_argument('--spec', required=True)
    autonomous_plan.add_argument('--output', required=True)
    autonomous_run = sub.add_parser('autonomy-run', help='在本次确认预算内自主生成、规划、模拟、分析及改进；同计划断点续跑')
    autonomous_run.add_argument('--plan', required=True)
    autonomous_run.add_argument('--report', default='state/autonomy-review.json')
    autonomous_run.add_argument('--dry-run', action='store_true')
    autonomous_run.add_argument('--max-new-simulations', type=int)
    autonomous_run.add_argument('--confirm-preview')
    autonomous_run.add_argument('--steps', type=int, help='完成指定候选数后暂停；不重置总预算')
    autonomous_recover = sub.add_parser('autonomy-recover', help='自主计划只读 GET 恢复；不调用模型、不新建模拟')
    autonomous_recover.add_argument('--plan', required=True)
    autonomous_recover.add_argument('--report', default='state/autonomy-review.json')
    sub.add_parser('migrate-state',help='离线迁移已有任务状态，保留快照、历史与预算，不访问网络')
    probe = sub.add_parser("probe-api", help="账户接口能力探测；默认只读取元数据")
    probe.add_argument("--one-simulation", action="store_true", help="明确创建一次真实单 Alpha 模拟")
    probe.add_argument("--expression", help="单模拟探测所用 Fast Expression")
    probe.add_argument("--output", default="state/permission-probe.json", help="写请求前的唯一审计记录路径")
    probe.add_argument('--trace',help='仅保存脱敏响应结构 JSONL，默认与 output 相邻')
    evidence=sub.add_parser('probe-evidence',help='GET 对账已有单 Alpha、PnL 语义/日历覆盖，不创建模拟')
    evidence.add_argument('record',help='已完成的单 Alpha 冒烟记录')
    evidence.add_argument('--output',default='state/evidence-probe.json')
    evidence.add_argument('--trace',help='仅保存脱敏响应结构')
    resume_probe = sub.add_parser("resume-probe", help="只恢复访问探测的 GET 轮询，不重新创建模拟")
    resume_probe.add_argument("record", help="probe-api --one-simulation 写入的 JSON")
    resume_probe.add_argument('--alpha-id',help='人工确认已有 ID；仍须账户列表成员与详情对账，不创建模拟')
    audit=sub.add_parser('audit-alphas',help='GET-only 对账已有 Alpha 清单；不创建模拟或提交')
    audit.add_argument('--manifest',required=True,help='JSON 数组：alpha_id、expression、settings')
    audit.add_argument('--output',default='state/alphas-evidence-audit.json')
    audit.add_argument('--trace',help='脱敏结构追踪文件')
    audit.add_argument('--returns-output',help='仅导出契约已验证的 IS 收益输入；缺证据项保留阻断原因')
    analysis_freeze=sub.add_parser('analysis-freeze',help='冻结离线统计协议；只写指定 JSON，不接触账户或数据库')
    analysis_freeze.add_argument('--spec',required=True);analysis_freeze.add_argument('--output',required=True)
    verify_returns=sub.add_parser('verify-returns',help='离线复核收益契约；不计算绩效，不读最终留出')
    verify_returns.add_argument('--manifest',required=True);verify_returns.add_argument('--output',required=True)
    attempts=sub.add_parser('attempt-history',help='只读导出全部本地尝试、失败与复用；不冒充账户完整历史')
    attempts.add_argument('--output',required=True)
    for command in ('robustness-audit','selection-bias-audit','research-report'):
        analysis=sub.add_parser(command,help='离线只读分析已有研究 IS；只写新的报告，不改变提交资格')
        analysis.add_argument('--manifest',required=True);analysis.add_argument('--protocol',required=True)
        analysis.add_argument('--output',required=True)
        if command=='research-report':analysis.add_argument('--review',help='已有 Engine 评审包；只引用原准入结论')
    reconcile_probe=sub.add_parser('reconcile-probe',help='人工确认服务端已创建后绑定模拟 Location；不重发 POST')
    reconcile_probe.add_argument('record');reconcile_probe.add_argument('--location',required=True)
    sync = sub.add_parser("sync-catalog", help="登录并同步当前账户目录，不运行模拟")
    sync.add_argument("--output", default="config/catalog.live.json")
    run = sub.add_parser("run", help="旧单 Agent 路径：仅授权 LLM 或 Demo 显式文件")
    run.add_argument("--agent", choices=("llm", "file"), default="llm")
    run.add_argument("--candidates", help="JSON batch for --agent file; every candidate is validated")
    run.add_argument("--papers", help="显式提供的 UTF-8 本地论文摘要/研究文本")
    run.add_argument("--report", default="state/latest-review.json")
    run.add_argument('--dry-run',action='store_true',help='离线预览；LLM 只显示预算上界，不调用模型')
    run.add_argument('--max-new-simulations',type=int)
    run.add_argument('--confirm-preview')
    resume = sub.add_parser("resume", help="恢复 GET 轮询，不重复不确定 POST")
    resume.add_argument("run_id")
    resume.add_argument('--continue-research',action='store_true',help='显式允许处理排队任务和新增参数扰动；默认仅 GET 恢复')
    resume.add_argument('--dry-run',action='store_true',help='只读预览恢复队列及可能新增的扰动，不登录')
    resume.add_argument('--max-new-simulations',type=int,help='本次继续研究最多新建数，含失败和不确定响应')
    resume.add_argument('--confirm-preview',help='确认当前 resume --continue-research 预览哈希')
    report = sub.add_parser("report", help="导出当前 run 的评审包")
    report.add_argument("run_id")
    report.add_argument("--output", default="state/latest-review.json")
    refresh=sub.add_parser('refresh-evidence',help='只用 GET 刷新已有 Alpha 的缺失证据，不创建模拟')
    refresh.add_argument('run_id');refresh.add_argument('trial_id');refresh.add_argument('--report',default='state/latest-review.json')
    reconcile = sub.add_parser("reconcile", help="本人从平台对账后绑定已创建的模拟 Location")
    reconcile.add_argument("run_id")
    reconcile.add_argument("trial_id")
    reconcile.add_argument("--location", required=True)
    recover=sub.add_parser('recover-trial',help='用账户 Alpha 列表与详情只读恢复不确定任务')
    recover.add_argument('run_id');recover.add_argument('trial_id');recover.add_argument('--alpha-id')
    submit = sub.add_parser("submit", help="精确批准一个已通过评审的 live Alpha")
    submit.add_argument("run_id")
    submit.add_argument("trial_id")
    submit.add_argument("--review", required=True)
    submit.add_argument("--approve-alpha-id", required=True)
    poll = sub.add_parser("resume-submit", help="只查已记录提交的最终状态，绝不重复 POST")
    poll.add_argument("alpha_id")
    sub.add_parser("verify-audit", help="验证实验审计链")
    fields=sub.add_parser('field-search',help='搜索当前字段目录')
    fields.add_argument('query');fields.add_argument('--limit',type=int,default=10);fields.add_argument('--all-fields',action='store_true')
    papers=sub.add_parser('paper-search',help='有界 arXiv 元数据检索，不下载全文')
    papers.add_argument('query');papers.add_argument('--limit',type=int,default=5);papers.add_argument('--output',default='state/papers.json')
    memory=sub.add_parser('research-memory',help='发现期记忆、家族与费用汇总')
    memory.add_argument('run_id')
    compare=sub.add_parser('compare',help='检查两轮研究的可比性并比较效率')
    compare.add_argument('left');compare.add_argument('right');compare.add_argument('--output',default='state/comparison.json')
    cost=sub.add_parser('record-cost',help='追加负责人核实的人工时间或其他费用')
    cost.add_argument('run_id');cost.add_argument('--category',choices=['human','data','compute'],required=True)
    cost.add_argument('--seconds',type=float,default=0);cost.add_argument('--usd',type=float);cost.add_argument('--note',default='owner-reported')
    freeze=sub.add_parser('validation-freeze',help='冻结本地面板、公式、规则与最终留出')
    freeze.add_argument('--panel',required=True);freeze.add_argument('--candidates',required=True);freeze.add_argument('--plan',required=True)
    freeze.add_argument('--ledger',default='state/validation.sqlite3');freeze.add_argument('--output',default='state/frozen-validation.json')
    legacy=sub.add_parser('validation-import-legacy',help='将旧版已暴露留出登记到独立账本，不重新计算')
    legacy.add_argument('--ledger',required=True);legacy.add_argument('--dataset-id',required=True)
    for command in ('walk-forward','final-holdout'):
        part=sub.add_parser(command,help='独立于 Research Agent 的本地时间验证')
        part.add_argument('--manifest',required=True);part.add_argument('--ledger',default='state/validation.sqlite3');part.add_argument('--output',default='state/'+command+'.json')
    result=sub.add_parser('validation-result',help='读取已归档最终报告，不重新计算留出')
    result.add_argument('study_id');result.add_argument('--ledger',default='state/validation.sqlite3');result.add_argument('--output',default='state/final-holdout.json')
    register=sub.add_parser('track-register',help='冻结候选并从未来日期开始追踪')
    register.add_argument('--review',required=True);register.add_argument('--trial-id',required=True);register.add_argument('--start',required=True)
    register.add_argument('--timezone',default='America/Los_Angeles');register.add_argument('--ledger',default='state/forward.sqlite3')
    update=sub.add_parser('track-update',help='追加前瞻观测；不会重新模拟历史')
    update.add_argument('monitor_id');choice=update.add_mutually_exclusive_group(required=True)
    choice.add_argument('--csv');choice.add_argument('--brain',action='store_true');update.add_argument('--source',default='owner-provided returns')
    update.add_argument('--ledger',default='state/forward.sqlite3')
    track=sub.add_parser('track-report',help='读取隔离的前瞻追踪报告')
    track.add_argument('monitor_id');track.add_argument('--ledger',default='state/forward.sqlite3')
    args = parser.parse_args(argv)
    engine = None
    try:
        config = load_settings(args.config)
        if config['mode']=='live' and args.command in ('autonomy-plan','autonomy-run'):
            raise ResearchError('v0.8 正式 Live Autoresearch 禁止旧的封闭研究域计划；仅接受独立 LLM-only 授权研究入口')
        if args.command == 'autonomy-plan':
            from .autonomy import create_plan
            from .probe import _write_fresh
            value = create_plan(config, json.loads(Path(args.spec).read_text(encoding='utf-8')))
            _write_fresh(args.output, value)
            print_json({'output': args.output, 'plan_sha256': value['plan_sha256'], 'domain_coverage': value['domain_coverage']})
            return
        if args.command in ('autonomy-run', 'autonomy-recover'):
            from .autonomy import preview_plan, execute_plan, recover_plan
            plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
            estimate = preview_plan(config, plan)
            if args.command == 'autonomy-recover' and not estimate['run_id']:
                raise ResearchError('计划尚未运行，无需恢复或登录')
            if args.command == 'autonomy-run':
                if args.steps is not None and args.steps < 1:
                    raise ResearchError('steps 必须为正整数')
                if args.dry_run:
                    print_json(estimate); return
                from .preview import confirm
                confirm(config, estimate, args.max_new_simulations, args.confirm_preview)
                if config['mode'] == 'live' and not config.get('safety', {}).get('live_research_enabled'):
                    raise ResearchError('真实研究开关关闭；未登录')
            with worker_lock(config['database']):
                simulator = None
                if config['mode'] == 'live':
                    credentials(); simulator = BrainSimulator(config)
                    identity = simulator.login()
                    if identity['user_id'] != config['catalog_data'].get('account_id'):
                        raise ResearchError('登录账户与字段目录不一致')
                if args.command == 'autonomy-recover':
                    print_json(recover_plan(config, plan, args.report, simulator))
                else:
                    value = execute_plan(config, plan, args.report, simulator, args.max_new_simulations,
                                         args.confirm_preview, args.steps)
                    print_json({'run_id': value['run_id'], 'state': value['state'],
                                'counts': value['counts'], 'report': args.report,
                                'shortlisted': len(value['shortlist'])})
            return
        if args.command in ('analysis-freeze','verify-returns','attempt-history','robustness-audit','selection-bias-audit',
                            'research-report'):
            from .analysis_protocol import read_json,load_manifest,freeze_protocol
            from .probe import _write_fresh
            if args.command=='analysis-freeze':value=freeze_protocol(read_json(args.spec))
            elif args.command=='attempt-history':
                from .attempt_history import inventory
                value=inventory(config['database'])
            elif args.command=='verify-returns':
                from .return_contract import review_return_document
                value={'mode':'offline_return_contract_review','series':[review_return_document(d)[1] for d in load_manifest(args.manifest)['series']],
                       'network_requests':0,'simulation_posts':0,'submit_posts':0,'database_mutated':False}
            else:
                from .research_report import analyze_manifest,research_report
                manifest=load_manifest(args.manifest);protocol=read_json(args.protocol)
                if args.command=='research-report':value=research_report(manifest,protocol,read_json(args.review) if args.review else None)
                else:
                    value=analyze_manifest(manifest,protocol)
                    if args.command=='selection-bias-audit':
                        value={k:v for k,v in value.items() if k!='series'}|{'dsr':[{'candidate_id':s['candidate_id'],'dsr':s['dsr']} for s in value['series']]}
            if getattr(args,'output',None):_write_fresh(args.output,value)
            print_json(value);return
        estimate=None;allowed=None
        if args.command=='resume':
            if not args.continue_research and (args.max_new_simulations is not None or args.confirm_preview is not None):
                raise ResearchError('新建确认参数需要 --continue-research；普通恢复只用 GET')
            if args.dry_run or args.continue_research:
                from .preview import preview_resume,confirm
                estimate=preview_resume(config,args.run_id,args.continue_research)
                if args.dry_run:print_json(estimate);return
                confirm(config,estimate,args.max_new_simulations,args.confirm_preview)
        if args.command=='run':
            from .preview import preview,confirm,research_candidates
            estimate=preview(config,research_candidates(config,args.agent,args.candidates),papers_path=args.papers)
            if args.dry_run:print_json(estimate);return
            allowed=confirm(config,estimate,args.max_new_simulations,args.confirm_preview)
        if args.command=='field-search':
            from .research import search_fields
            print_json(search_fields(config['catalog_data'],args.query,args.limit,not args.all_fields));return
        if args.command=='paper-search':
            from .papers import PaperSearch
            if not config.get('research',{}).get('paper_search_enabled'):raise ResearchError('请先在 [research] 开启 paper_search_enabled')
            value=PaperSearch(Path(config['database']).parent/'papers.sqlite3').search(args.query,args.limit)
            save_json(args.output,value);print_json({'output':args.output,'papers':len(value['papers']),'cache_hit':value['cache_hit']});return
        if args.command=='compare':
            from .benchmark import compare_reports
            value=compare_reports(json.loads(Path(args.left).read_text(encoding="utf-8")),json.loads(Path(args.right).read_text(encoding="utf-8")))
            save_json(args.output,value);print_json(value);return
        if args.command=='validation-import-legacy':
            from .holdout_registry import import_legacy
            print_json(import_legacy(args.ledger,config['validation']['holdout_registry'],args.dataset_id));return
        if args.command in ('validation-freeze','walk-forward','final-holdout','validation-result'):
            from . import local_validation as local
            if args.command=='validation-freeze':value=local.freeze(args.panel,args.candidates,args.plan,config,args.ledger,args.output)
            elif args.command=='validation-result':value=local.saved_final_report(args.study_id,args.ledger)
            elif args.command=='walk-forward':value=local.walk_forward(args.manifest,args.ledger)
            else:value=local.final_holdout(args.manifest,args.ledger)
            if args.command!='validation-freeze':save_json(args.output,value)
            print_json({'output':args.output,'study_id':value.get('study_id',value.get('plan',{}).get('study_id')),
                'mode':value.get('mode','frozen'),'synthetic':value.get('synthetic'),'metrics':value.get('metrics'),
                'independent_holdout_validated':False});return
        if args.command.startswith('track-'):
            from . import tracking
            if args.command=='track-register':value=tracking.register(args.review,args.trial_id,args.start,args.ledger,args.timezone)
            elif args.command=='track-report':value=tracking.tracking_report(args.ledger,args.monitor_id)
            elif args.brain:
                credentials();simulator=BrainSimulator(config);simulator.login()
                value=tracking.update_from_brain(args.ledger,args.monitor_id,simulator)
            else:value=tracking.ingest_csv(args.ledger,args.monitor_id,args.csv,args.source)
            print_json(value);return
        if args.command == "doctor":
            catalog = config["catalog_data"]
            print_json({"mode": config["mode"], "scope": config["scope"],
                "approved_fields": sum(v.get("point_in_time") is True for v in catalog["fields"].values()),
                "account_profile_verified": config["account_profile_verified"],
                "brain_credentials_in_environment": bool(os.getenv("BRAIN_EMAIL") and os.getenv("BRAIN_PASSWORD")),
                "llm_key_in_environment": bool(os.getenv("LLM_API_KEY")),
                "max_simulations_including_sensitivity": config["rules"]["max_simulations_per_run"],
                "automatic_submission": False, "network_tested": False,
                'live_research_enabled':config.get('safety',{}).get('live_research_enabled',False),
                'submission_enabled':config.get('safety',{}).get('submission_enabled',False),
                'is_date_contract_reviewed':config['brain'].get('discovery_evidence',{}).get('schema_reviewed',False),
                'pnl_semantics_reviewed':config['brain'].get('pnl_evidence',{}).get('reviewed',False),
                'pnl_calendar_reviewed':config['brain'].get('pnl_evidence',{}).get('calendar_reviewed',False),
                'stability_window_sessions':config['policy']['stability_window_sessions'],
                'min_stability_windows':config['policy']['min_stability_windows'],
                'paper_search_enabled':config.get('research',{}).get('paper_search_enabled',False)})
            return
        with worker_lock(config["database"]):
            if args.command=='migrate-state':
                if not Path(config['database']).is_file():raise ResearchError('没有已有数据库，无需迁移')
                memory=Memory(config['database'])
                try:
                    rows=memory.db.execute('SELECT remote_state,COUNT(*) FROM tasks GROUP BY remote_state').fetchall()
                    print_json({'network_requests':0,'remote_states':{r[0]:r[1] for r in rows},'audit':memory.verify()})
                finally:memory.close()
                return
            if args.command=='audit-alphas':
                from .evidence_audit import audit_existing_alphas
                from .trace import ShapeRecorder
                if Path(args.output).exists():raise ResearchError('审计报告已存在；指定新输出，保留历史')
                credentials();simulator=BrainSimulator(config)
                simulator.api.observer=ShapeRecorder(args.trace or args.output+'.shapes.jsonl')
                simulator.login()
                print_json(audit_existing_alphas(simulator,args.manifest,args.output,args.returns_output));return
            if args.command=='probe-evidence':
                from .probe import inspect_alpha_evidence
                from .trace import ShapeRecorder
                if Path(args.output).exists():raise ResearchError('证据验收文件已存在，保留原记录并指定新输出')
                credentials();simulator=BrainSimulator(config)
                simulator.api.observer=ShapeRecorder(args.trace or args.output+'.shapes.jsonl')
                simulator.login()
                print_json(inspect_alpha_evidence(simulator,args.record,args.output));return
            if args.command == "probe-api":
                from .probe import inspect_access, one_simulation_probe,_write_fresh
                from .trace import ShapeRecorder
                if Path(args.output).exists():raise ResearchError('探测记录已存在；恢复使用 resume-probe，不重发创建请求')
                credentials()
                simulator = BrainSimulator(config)
                simulator.api.observer=ShapeRecorder(args.trace or args.output+'.shapes.jsonl')
                simulator.login()
                inspection = inspect_access(simulator)
                print_json(inspection)
                if args.one_simulation:
                    if not args.expression:
                        raise ResearchError("--one-simulation requires --expression")
                    if not (inspection["operators"]["available"] and inspection["fields"]["available"]):
                        raise ResearchError("Read-only field/operator discovery failed; no write probe sent")
                    print_json({"write_probe": "explicitly_requested", "output": args.output})
                    print_json(one_simulation_probe(simulator, args.expression, args.output))
                elif args.expression:
                    raise ResearchError("--expression requires --one-simulation")
                else:_write_fresh(args.output,inspection)
                return
            if args.command in ("resume-probe",'reconcile-probe'):
                from .probe import resume_simulation_probe,reconcile_simulation_probe
                credentials()
                simulator = BrainSimulator(config)
                from .trace import ShapeRecorder
                simulator.api.observer=ShapeRecorder(args.record+'.shapes.jsonl')
                simulator.login()
                print_json(resume_simulation_probe(simulator, args.record,args.alpha_id) if args.command=='resume-probe' else reconcile_simulation_probe(simulator,args.record,args.location))
                return
            if args.command=='record-cost':
                from .brain import finite
                if finite(args.seconds) is None or args.seconds<0 or (args.usd is not None and (finite(args.usd) is None or args.usd<0)):raise ResearchError('时间/费用必须为非负有限值')
                memory=Memory(config['database'])
                try:
                    memory.run(args.run_id);memory.cost(args.run_id,args.category,args.seconds,usd=args.usd,detail={'note':args.note})
                    print_json(memory.cost_summary(args.run_id))
                finally:memory.close()
                return
            if args.command == "verify-audit":
                memory = Memory(config["database"])
                try:
                    print_json(memory.verify())
                finally:
                    memory.close()
                return
            simulator = None
            if args.command == "sync-catalog" or (config["mode"] == "live" and args.command in ("run", "resume", "submit", "resume-submit",'refresh-evidence','recover-trial')):
                credentials()
                simulator = BrainSimulator(config)
                identity = simulator.login()
                if args.command == "sync-catalog":
                    catalog = simulator.sync_catalog()
                    catalog["account_id"] = identity["user_id"]
                    output = Path(args.output)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
                    print_json({"catalog": str(output), "fields": len(catalog["fields"]), "note": "字段默认禁用，审核后使用"})
                    return
                if config["catalog_data"].get("account_id") != identity["user_id"]:
                    raise ResearchError("登录账户与字段目录账户不一致，请重新同步目录")
                if args.command in ('run',):
                    from .probe import require_smoke_receipt
                    require_smoke_receipt(config,identity['user_id'])
            if args.command=='recover-trial':
                if config['mode']!='live':raise ResearchError('仅用于真实任务的 GET 对账')
                from .reconciliation import recover_saved_trial
                print_json(recover_saved_trial(config,args.run_id,args.trial_id,simulator,args.alpha_id));return
            if args.command == "resume-submit":
                if not simulator:
                    raise ResearchError("DEMO 不支持提交")
                memory = Memory(config["database"])
                try:
                    if not memory.db.execute("SELECT 1 FROM submissions WHERE alpha_id=?", (args.alpha_id,)).fetchone():
                        raise ResearchError("没有此 Alpha 的本地提交记录")
                    result = simulator.poll_submission(args.alpha_id)
                    memory.submission_state(args.alpha_id, "confirmed", result)
                    print_json(result)
                finally:
                    memory.close()
                return
            engine = Engine(config, getattr(args, "run_id", None), simulator,simulation_limit=allowed)
            print_json({"run_id": engine.run_id, "mode": config["mode"]})
            if args.command == "run":
                if config['mode']=='live' and not config.get('safety',{}).get('live_research_enabled'):raise ResearchError('先完成单 Alpha 冒烟验收，live_research_enabled 默认关闭')
                engine.memory.transaction(lambda:engine.memory.event(engine.run_id,'research_arm',{'agent':args.agent,'papers_path':args.papers}))
                # Recheck after authentication, before the first remote create.
                current=preview(config,research_candidates(config,args.agent,args.candidates),papers_path=args.papers)
                allowed=confirm(config,current,args.max_new_simulations,args.confirm_preview)
                engine.simulation_limit=allowed
                engine.memory.transaction(lambda:engine.memory.event(engine.run_id,'creation_budget_confirmed',
                    {'preview_sha256':current['preview_sha256'],'max_new_simulations':allowed}))
                if args.agent == "llm":
                    from .agent import ResearchAgent
                    papers = Path(args.papers).read_text(encoding="utf-8") if args.papers else "No papers supplied; explain priors and do not invent citations."
                    print_json(ResearchAgent(engine).run(papers))
                elif args.agent == "file" and config['mode'] == 'demo':
                    # Explicit user file for isolated Demo testing only. No
                    # built-in fallback, baseline, fixed pool or seed source.
                    from .batch import read_candidates
                    if not args.candidates:
                        raise ResearchError('--agent file requires --candidates PATH')
                    for candidate in read_candidates(args.candidates):
                        run_counters = engine.memory.run(engine.run_id)
                        if run_counters["proposals"] >= config["rules"]["max_proposals_per_run"]:
                            break
                        if run_counters['simulations']>=engine.simulation_limit and not engine.memory.existing_trial(
                                engine.context,engine.validator.validate(candidate['expression']).fingerprint):
                            continue
                        print_json(engine.propose(**candidate))
                else:
                    raise ResearchError('正式运行只能使用已授权的真实 LLM Agent')
                review = engine.write_report(args.report)
                print_json({"report": args.report, "shortlisted": len(review["shortlist"]), "counts": review["counts"]})
            elif args.command == "resume":
                print_json(engine.resume(args.continue_research,args.max_new_simulations,args.confirm_preview))
            elif args.command=='refresh-evidence':
                print_json(engine.refresh_evidence(args.trial_id));engine.write_report(args.report)
            elif args.command == "report":
                review = engine.write_report(args.output)
                print_json({"report": args.output, "shortlisted": len(review["shortlist"]), "counts": review["counts"]})
            elif args.command=='research-memory':
                from .research import notes,families
                print_json({'experiments':engine.memory.feedback(engine.context),'notes':notes(engine),'families':families(engine),'costs':engine.memory.cost_summary(engine.run_id)})
            elif args.command == "reconcile":
                trial = engine.memory.trial(args.trial_id)
                if trial["run_id"] != engine.run_id or trial["state"] not in ("uncertain", "dispatching"):
                    raise ResearchError("仅对账当前 run 的不确定任务")
                from .http import ApiClient
                location = ApiClient(config["brain"]["base_url"]).url(args.location) if config["mode"] == "live" else args.location
                engine.memory.set_state(args.trial_id, "polling", location=location, error="人工对账绑定，恢复时请核对公式/设置")
                print_json({"trial_id": args.trial_id, "state": "polling"})
            elif args.command == "submit":
                print_json(engine.submit(args.trial_id, args.review, args.approve_alpha_id))
    except (ResearchError, OSError, ValueError) as e:
        print_json({"error": str(e), **({"run_id": engine.run_id} if engine else {})})
        sys.exit(2)
    finally:
        if engine:
            engine.memory.close()
