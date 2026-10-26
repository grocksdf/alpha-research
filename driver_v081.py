"""v0.8.1 真实 LLM 三轮自主研究驱动器。

与 verification/v08_genuine_llm_acceptance.py 的唯一差异：max_ideas_per_batch=2
（验收脚本内部硬用默认值 4）。原因：v08_pricing.py:107 把单次输出硬上限设为
min(configured_max, 2048, 剩余)，而本服务商免费模型的结构化输出在该上限下不稳定。
此为资源参数调节，不触碰任何安全门禁、不以任何方式放宽验收规则。
"""
import sys, json, traceback
from pathlib import Path
sys.path.insert(0,'.')
from alpharesearch.common import load_settings
from alpharesearch.v08_pricing import reviewed_model_price
from alpharesearch.llm_provider_policy import validate_llm_endpoint, _model_set
from alpharesearch.autoresearch_v08 import make_plan, execute_batch

TARGET = Path('state/v081-real-llm-fresh.sqlite3')
IDEA = int(sys.argv[1]) if len(sys.argv) > 1 else 2

cfg = load_settings('config/v08-provider.local.toml')
if TARGET.exists(): TARGET.unlink()
cfg['database'] = str(TARGET.resolve())
cfg['_v08_remote_operator_confirmed'] = True

policy = validate_llm_endpoint(cfg['llm'], operator_confirmed=True)
prices = [reviewed_model_price(cfg, m) for m in _model_set(cfg['llm'])]
print('policy:', policy['kind'], '| prices:', [(p['model'], p['status']) for p in prices], flush=True)

plan = make_plan(cfg, project_id='v081-genuine-research',
    objective='Autonomously discover diverse and falsifiable Alpha hypotheses using authorized data fields and operators, then revise them using experimental evidence without predetermined formula templates',
    max_simulations_per_batch=2, max_llm_calls_per_batch=6, max_wall_seconds=1800,
    max_ideas_per_batch=IDEA, max_tokens_per_batch=40000, max_known_usd_per_batch=2.0)
print('plan_sha256:', plan['plan_sha256'], '| max_ideas:', IDEA, flush=True)

outcomes = []
for b in range(1, 4):
    print(f'--- batch {b} start ---', flush=True)
    try:
        out = execute_batch(cfg, plan, adapter=None)
        outcomes.append(out)
        print(f'--- batch {b} OK ---', json.dumps(out, ensure_ascii=False)[:300], flush=True)
    except Exception as e:
        tb = traceback.format_exc().strip().splitlines()[-1]
        print(f'--- batch {b} FAULT: {type(e).__name__}: {str(e)[:160]}', flush=True)
        outcomes.append({'batch': b, 'fault': type(e).__name__, 'reason': str(e)[:300]})
print('=== DONE ===', flush=True)
