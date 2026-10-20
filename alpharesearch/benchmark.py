"""Compare frozen protocols and accounting; never infer superiority from a demo."""
from .common import digest


def compare_reports(left,right):
    reasons=[]
    a,b=left['snapshot'],right['snapshot']
    for key in ('scope','catalog','policy','rules','code','brain','research','cost'):
        if a.get(key)!=b.get(key):reasons.append('protocol_mismatch:'+key)
    if left['mode']!='live' or right['mode']!='live':reasons.append('synthetic_or_nonlive_evidence')
    labels={r.get('research_arm',{}).get('agent') for r in (left,right)}
    if labels!={'baseline','llm'}:reasons.append('arms_not_labeled_baseline_and_llm')
    actual=[r['counts']['simulations'] for r in (left,right)]
    if actual[0]!=actual[1] or not actual[0]:reasons.append('unequal_or_zero_simulation_spend')
    for report in (left,right):
        if any(c.get('original_run_id',report['run_id'])!=report['run_id'] for c in report['candidates']):reasons.append('cross_run_cached_evidence')
    def metrics(report):
        total=report['counts']['simulations'];good=report['shortlist'];trials=report.get('trials',[])
        return {'run_id':report['run_id'],'simulations':total,'hypotheses':len(report.get('research_intents',[])),
            'qualified_formulas':len(good),'qualified_per_simulation':len(good)/total if total else None,
            'structural_families':len({x['family'] for x in good}),
            'completed_simulation_fraction':sum(t['state']=='completed' for t in trials)/total if total else None,
            'costs':report.get('costs',{}),'independent_holdout_validated':False}
    return {'protocol_comparable':not reasons,'reasons':sorted(set(reasons)),
        'left':metrics(left),'right':metrics(right),'protocol_hash':digest({k:a.get(k) for k in ('scope','catalog','policy','rules','code')}),
        'ai_superiority_established':False,'note':'描述性研究效率比较；单轮、非独立验证结果不能证明 AI 有效性'}
