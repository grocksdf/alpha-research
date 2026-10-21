"""Generate explicitly synthetic, offline v0.5 evidence examples; never call APIs."""
from __future__ import annotations

import argparse
import math
from datetime import date,timedelta
from pathlib import Path

from alpharesearch.analysis_protocol import freeze_protocol
from alpharesearch.common import digest
from alpharesearch.probe import _write_fresh
from alpharesearch.research_report import research_report
from alpharesearch.return_contract import MANIFEST,PROTOCOL,verify_return_document


def demo_inputs():
    sessions=[];day=date(2020,1,2)
    while len(sessions)<504:
        if day.weekday()<5:sessions.append(day.isoformat())
        day+=timedelta(days=1)
    docs=[];turnovers={}
    for k in range(4):
        rows=[{'date':d,'value':.0003+k*.0001+.009*math.sin(i*.71)+.004*math.sin(i*.13)}
              for i,d in enumerate(sessions)]
        contract={'protocol':PROTOCOL,'source_type':'independent_pit_panel','source_id':'SYNTHETIC_GENERATOR',
            'population_id':'SYNTHETIC_ONLY','dataset_id':'SYNTHETIC_ONLY','pit_reviewed':True,
            'scope':{'region':'USA','universe':'SYNTHETIC_ONLY','delay':1},'sample_role':'research_is',
            'reviewed':True,'review_note':'合成测试；没有金融含义','review_source_sha256':digest('synthetic generator'),
            'value_semantics':'daily_return','unit':'fraction','return_basis':'simple_return',
            'capital_basis':{'kind':'already_normalized','reviewed':True},'net_of_costs':False,
            'costs_reviewed':True,'cost_model_id':'SYNTHETIC_GROSS','cost_review_note':'合成毛收益，没有 BRAIN 成本假设',
            'is_reviewed':True,'is_interval':{'start':sessions[0],'end':sessions[-1]},'excluded_intervals':[]}
        calendar={'id':'SYNTHETIC_WEEKDAYS_NOT_EXCHANGE_CALENDAR','source':'synthetic generator','reviewed':True,
            'region':'USA','review_note':'合成星期序列；忽略市场休市日，禁止用于真实研究','sessions':sessions,
            'sha256':digest(sessions),'coverage_start':sessions[0],'coverage_end':sessions[-1]}
        doc={'candidate_id':'SYNTHETIC_'+str(k),'synthetic':True,'contract':contract,'calendar':calendar,
             'observations':rows,'data_sha256':digest(rows)}
        docs.append(doc);series=verify_return_document(doc)
        turns=[{'date':d,'value':.3-k*.04} for d in sessions]
        turnovers[series.candidate_id]={'candidate_id':series.candidate_id,'return_input_sha256':series.input_sha256,
            'reviewed':True,'review_note':'合成换手率，非 BRAIN 指标','source_sha256':digest('synthetic turnover '+str(k)),
            'unit':'one_way_traded_notional_over_same_capital','cost_basis':'incremental_only',
            'observations':turns,'data_sha256':digest(turns)}
    spec={'study_id':'SYNTHETIC_DEMO_NOT_FINANCIAL_EVIDENCE','sample_role':'research_is',
        'frozen_before_observation':True,'registration_note':'合成示例；不声称真实事前注册或独立验证',
        'candidate_universe':[d['candidate_id'] for d in docs],'is_interval':docs[0]['contract']['is_interval'],
        'annualization':252,'risk_free_return_per_day':0,'bootstrap':{'block_lengths':[5,10,20],'replicates':200,'seed':20261020,'confidence':.95},
        'stability_window_sessions':126,'min_stability_windows':4,
        'pbo':{'blocks':8,'metric':'daily_sharpe','ties':'unavailable','min_candidates':8,'min_observations':252},
        'plateaus':[{'family':'SYNTHETIC_EXAMPLE_NOT_A_STRATEGY','dimension':'hypothetical_window',
            'candidate_ids':['SYNTHETIC_0','SYNTHETIC_1','SYNTHETIC_2'],'parameter_values':[5,10,20],
            'baseline_id':'SYNTHETIC_1','minimum_sharpe':0.,'minimum_fraction_of_baseline':.8}],
        'cost_scenarios':[{'id':'illustrative_zero_increment','kind':'incremental_cost','bps':0},
                          {'id':'illustrative_two_bps_increment','kind':'incremental_cost','bps':2}]}
    manifest={'format':MANIFEST,'series':docs,'turnover_contracts':turnovers,'history':None,
              'notice':'全部收益、日历和成本输入均为合成。真实收益、BRAIN API 与 AI 能力未核实。'}
    return manifest,spec


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args();folder=Path(args.output_dir)
    names=['synthetic-returns-manifest.json','analysis-spec.json','frozen-analysis-protocol.json','robustness-report.json']
    if any((folder/name).exists() for name in names):parser.error('输出已存在；使用新目录保留历史')
    manifest,spec=demo_inputs();protocol=freeze_protocol(spec)
    for name,value in zip(names,[manifest,spec,protocol,research_report(manifest,protocol)]):_write_fresh(folder/name,value)
    print('Saved synthetic examples. Real BRAIN returns remain unverified.')


if __name__=='__main__':main()
