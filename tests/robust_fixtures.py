"""Explicitly synthetic inputs; no real market calendar, returns, or account."""
import math
from datetime import date,timedelta

from alpharesearch.analysis_protocol import freeze_protocol
from alpharesearch.common import digest
from alpharesearch.return_contract import PROTOCOL,MANIFEST,verify_return_document


def document(candidate='SYNTHETIC_0',n=504,offset=0.):
    start=date(2020,1,2);sessions=[];day=start
    while len(sessions)<n:
        if day.weekday()<5:sessions.append(day.isoformat())
        day+=timedelta(days=1)
    values=[.0004+offset+.009*math.sin(i*.71)+.004*math.sin(i*.13) for i in range(n)]
    rows=[{'date':d,'value':v} for d,v in zip(sessions,values)]
    contract={'protocol':PROTOCOL,'source_type':'independent_pit_panel','source_id':'SYNTHETIC_GENERATOR',
        'population_id':'SYNTHETIC_DATASET','dataset_id':'SYNTHETIC_DATASET','pit_reviewed':True,
        'scope':{'region':'USA','universe':'SYNTHETIC_ONLY','delay':1},'sample_role':'research_is',
        'reviewed':True,'review_note':'TEST ONLY; no financial meaning','review_source_sha256':digest('TEST-ONLY'),
        'value_semantics':'daily_return','unit':'fraction','return_basis':'simple_return',
        'capital_basis':{'kind':'already_normalized','reviewed':True},
        'net_of_costs':False,'costs_reviewed':True,'cost_model_id':'SYNTHETIC_GROSS',
        'cost_review_note':'TEST ONLY; gross generated returns',
        'is_reviewed':True,'is_interval':{'start':sessions[0],'end':sessions[-1]},'excluded_intervals':[]}
    calendar={'id':'SYNTHETIC_WEEKDAYS_NOT_MARKET_CALENDAR','source':'TEST GENERATOR','reviewed':True,
        'region':'USA','review_note':'TEST ONLY; ignores actual market closures','sessions':sessions,
        'sha256':digest(sessions),'coverage_start':sessions[0],'coverage_end':sessions[-1]}
    return {'candidate_id':candidate,'synthetic':True,'contract':contract,'calendar':calendar,
            'observations':rows,'data_sha256':digest(rows)}


def protocol(docs,plateaus=None):
    return freeze_protocol({'study_id':'SYNTHETIC_STUDY','sample_role':'research_is',
        'frozen_before_observation':True,'registration_note':'TEST protocol; synthetic only',
        'candidate_universe':[d['candidate_id'] for d in docs],'is_interval':docs[0]['contract']['is_interval'],
        'annualization':252,'risk_free_return_per_day':0,'bootstrap':{'block_lengths':[5,10,20],'replicates':100,'seed':41,'confidence':.95},
        'stability_window_sessions':126,'min_stability_windows':4,
        'pbo':{'blocks':8,'metric':'daily_sharpe','ties':'unavailable','min_candidates':8,'min_observations':252},
        'plateaus':plateaus or [],'cost_scenarios':[{'id':'explicit_synthetic_scenario','kind':'incremental_cost','bps':2.}]})


def history(docs):
    verified=[verify_return_document(d) for d in docs]
    attempts=[{'attempt_id':'attempt_'+str(i),'candidate_id':d['candidate_id'],'family':'TEST_FAMILY',
               'variant_sha256':digest(str(i)),'status':'completed'} for i,d in enumerate(docs)]
    h={'reviewed':True,'complete':True,'includes_failed_invalid_unfinished':True,'review_note':'TEST ONLY',
       'source_sha256':digest('TEST ledger'),'attempts':attempts,'attempts_sha256':digest(attempts),'total_attempts':len(attempts)}
    h['dsr_calibration']={'reviewed':True,'review_note':'TEST ONLY; not an independence estimate',
        'source_sha256':digest('TEST calibration'),'history_sha256':digest(attempts),
        'independence_method':'SYNTHETIC_TEST_ASSUMPTION','effective_independent_trials':len(attempts),
        'sharpe_variance_daily':.002,'sharpe_unit':'daily_unannualized','includes_all_attempts':True,
        'comparability_sha256':verified[0].comparability_key,
        'effective_observations':{d['candidate_id']:{'value':len(d['observations']),'reviewed':True,
            'return_input_sha256':v.input_sha256,'method':'SYNTHETIC_TEST_ASSUMPTION'} for d,v in zip(docs,verified)}}
    return h


def manifest(count=4,n=504):
    docs=[document('SYNTHETIC_'+str(i),n,i*.0001) for i in range(count)]
    return {'format':MANIFEST,'series':docs,'history':history(docs)}
