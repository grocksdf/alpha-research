"""v0.8 autonomous LLM research CLI, using a NEW dedicated database.

No scripted candidate generators or tests can be selected via this CLI.
Operational research is Demo + a real, explicit tools-capable model.
Remote HTTPS model access requires a frozen time-limited approval AND a CLI opt-in.
Live BRAIN remains disabled pending a separate authorization and rate review.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .common import ResearchError, load_settings
from .autoresearch_v08 import make_plan, execute_batch, control, Playbook
from .memory import Memory


def _read_plan(file):
    data=json.loads(Path(file).read_text(encoding='utf-8'))
    if not isinstance(data,dict) or data.get('protocol')!='llm-autoresearch-v08':
        raise ResearchError('未提供 v0.8 冻结计划')
    return data


def _new_file(path,payload):
    destination=Path(path)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('x',encoding='utf-8') as out:
        out.write(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config/settings.toml')
    parser.add_argument('--database',required=True,help='Dedicated new v0.8 database; never historical state.db')
    sub=parser.add_subparsers(dest='command',required=True)
    new=sub.add_parser('plan')
    new.add_argument('--project-id',required=True)
    new.add_argument('--objective',required=True)
    new.add_argument('--output',required=True)
    new.add_argument('--max-simulations-per-batch',type=int,default=3)
    new.add_argument('--max-llm-calls-per-batch',type=int,default=6)
    new.add_argument('--max-wall-seconds',type=int,default=900)
    new.add_argument('--max-ideas-per-batch',type=int,default=4)
    new.add_argument('--max-tokens-per-batch',type=int,default=50000)
    new.add_argument('--max-known-usd-per-batch',type=float,default=None)
    new.add_argument('--max-worker-repairs-per-candidate',type=int,default=2)
    new.add_argument('--max-critic-defer-rounds',type=int,default=2)
    new.add_argument('--max-empty-batches',type=int,default=2)
    new.add_argument('--max-model-output-retries',type=int,default=1)
    new.add_argument('--max-campaign-llm-calls',type=int,default=200)
    new.add_argument('--max-campaign-tokens',type=int,default=500000)
    new.add_argument('--max-campaign-known-usd',type=float,default=100.0)
    new.add_argument('--research-materials',help='Locally reviewed source/description/text/license JSON list')
    provider_check=sub.add_parser('provider-check', help='Offline LLM endpoint policy check; no network')
    provider_check.add_argument('--allow-remote-llm',action='store_true',
                               help='Explicit opt-in required for reviewed remote HTTPS model access')
    for name in ('run','drive','inspect','pause','resume','terminate'):
        p=sub.add_parser(name)
        p.add_argument('--plan',required=True)
        if name in ('run','drive'):
            p.add_argument('--allow-remote-llm',action='store_true',
                help='Authorize sending reviewed research inputs to the approved external HTTPS LLM service')
        if name=='drive':
            p.add_argument('--batches',type=int,default=3)
            p.add_argument('--sleep-seconds',type=float,default=1.0)
        if name in ('pause','resume','terminate'):
            p.add_argument('--reason',default='operator')
    args=parser.parse_args(argv)
    try:
        config=load_settings(args.config)
        original=Path(config['database']).resolve()
        target=Path(args.database).resolve()
        if target==original or target.name==original.name or target.suffix not in ('.sqlite3','.db'):
            raise ResearchError('必须使用独立 v0.8 数据库路径；禁止覆盖历史实验库')
        if config['mode']!='demo' or config.get('safety',{}).get('live_research_enabled') or config.get('safety',{}).get('submission_enabled'):
            raise ResearchError('本版本只允许 Live 与 Submit 均关闭的 Demo 研究流程')
        config['database']=str(target)
        if args.command=='provider-check':
            from .llm_provider_policy import validate_llm_endpoint
            receipt=validate_llm_endpoint(config['llm'],operator_confirmed=args.allow_remote_llm)
            print(json.dumps({**receipt,'provider_authenticated':False,
                              'network_requests':0,'brain_requests':0},ensure_ascii=False))
            return 0
        if args.command=='plan':
            materials=(json.loads(Path(args.research_materials).read_text(encoding='utf-8'))
                       if args.research_materials else [])
            data=make_plan(config,project_id=args.project_id,objective=args.objective,
                 max_simulations_per_batch=args.max_simulations_per_batch,
                 max_llm_calls_per_batch=args.max_llm_calls_per_batch,
                 max_wall_seconds=args.max_wall_seconds,
                 max_ideas_per_batch=args.max_ideas_per_batch,
                 max_tokens_per_batch=args.max_tokens_per_batch,
                 max_known_usd_per_batch=args.max_known_usd_per_batch,
                 max_worker_repairs_per_candidate=args.max_worker_repairs_per_candidate,
                 max_critic_defer_rounds=args.max_critic_defer_rounds,
                 max_empty_batches=args.max_empty_batches,
                 max_model_output_retries=args.max_model_output_retries,
                 max_campaign_llm_calls=args.max_campaign_llm_calls,
                 max_campaign_tokens=args.max_campaign_tokens,
                 max_campaign_known_usd=args.max_campaign_known_usd,
                 research_materials=materials)
            _new_file(args.output,data)
            print(json.dumps({'plan':args.output,'plan_sha256':data['plan_sha256'],
                              'unbounded_campaign_simulations':True,'real_brain_requests':0}))
            return 0
        plan=_read_plan(args.plan)
        if args.command in ('pause','resume','terminate'):
            print(json.dumps({'status':control(config,plan,args.command,args.reason)}))
            return 0
        if args.command=='inspect':
            if not target.exists():
                raise ResearchError('v0.8 数据库尚未创建')
            m=Memory(target)
            try:
                b=Playbook(m)
                print(json.dumps({'status':b.status(plan['project_id']),
                    'latest_evidence':b.evidence(plan['project_id']),
                    'audit':b.audit(plan['project_id'])},ensure_ascii=False))
            finally:m.close()
            return 0
        # Network policy is checked before constructing any Engine or sending
        # any research materials. This is a host/model DATA EXPORT approval,
        # not permission to execute BRAIN simulations or Submit.
        from .llm_provider_policy import validate_llm_endpoint
        receipt=validate_llm_endpoint(config['llm'],operator_confirmed=args.allow_remote_llm)
        if not os.environ.get('LLM_API_KEY'):
            raise ResearchError('缺少真实 LLM_API_KEY；不会执行或回退固定 Mock')
        config['_v08_remote_operator_confirmed']=bool(args.allow_remote_llm)
        print(json.dumps({'llm_endpoint_policy':receipt['kind'],
                          'policy_sha256':receipt['policy_sha256'],
                          'mode':'demo','brain_requests':0},ensure_ascii=False),file=sys.stderr)
        rounds=1 if args.command=='run' else args.batches
        if type(rounds)is not int or not 1<=rounds<=1000:
            raise ResearchError('单次 CLI 必须具有有限 batch 运行保护')
        if args.command=='drive' and not 0<=args.sleep_seconds<=3600:
            raise ResearchError('sleep_seconds 非法')
        for _ in range(rounds):
            result=execute_batch(config,plan)
            print(json.dumps(result,ensure_ascii=False))
            if args.command=='drive':time.sleep(args.sleep_seconds)
        return 0
    except (ResearchError,ValueError,KeyError,OSError) as error:
        print('v0.8 error:',error,file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
