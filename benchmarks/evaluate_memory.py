"""Independent, read-only A/B evaluator; NEVER used as a candidate generator.

Input JSONL rows must come from paired, pre-registered research trials with
known evidence grades. Null or unequal budget/task coverage yields INCOMPLETE,
not a superiority claim. No trading or BRAIN requests are performed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

ARMS=('rule_random_baseline','llm_no_memory','llm_full_playbook','llm_shuffled_memory')


def evaluate(records):
    if not isinstance(records,list) or not records:
        return {'status':'INCOMPLETE','reason':'no_real_paired_records'}
    by={arm:{} for arm in ARMS}
    seen=set()
    for row in records:
        required={'arm','task_id','authorized_catalog_sha256','budget_sha256',
                  'evidence_grade','attempted','legal','duplicates','revisions',
                  'simulation_count','independent_validation_passed','source_run_id',
                  'mechanism_labels','platform_checks_passed'}
        if not isinstance(row,dict) or set(row)!=required or row['arm'] not in ARMS:
            raise ValueError('Invalid frozen A/B trial record')
        key=(row['task_id'],row['arm'])
        if key in seen:raise ValueError('duplicate arm/task id')
        seen.add(key)
        if not row['source_run_id'] or row['evidence_grade'] not in (
                'synthetic_process','research_is','verified_market'):
            raise ValueError('invalid provenance grade')
        for number in ('attempted','legal','duplicates','revisions',
                       'simulation_count','platform_checks_passed'):
            if type(row[number]) is not int or row[number]<0:
                raise ValueError('invalid sample count')
        if not 0<=row['legal']<=row['attempted'] or row['simulation_count']>row['attempted']:
            raise ValueError('invalid accounting relation')
        if (not isinstance(row['mechanism_labels'],list) or
                len(row['mechanism_labels'])>row['attempted'] or
                any(not isinstance(x,str) or not x or len(x)>120
                    for x in row['mechanism_labels'])):
            raise ValueError('invalid LLM-generated mechanism labels')
        if row['platform_checks_passed']>row['simulation_count']:
            raise ValueError('invalid number of platform checks')
        by[row['arm']][row['task_id']]=row
    tasks=set(by[ARMS[0]])
    if not tasks or any(set(by[arm])!=tasks for arm in ARMS):
        return {'status':'INCOMPLETE','reason':'unpaired_tasks_or_missing_arm',
                'observed_task_counts':{arm:len(rows) for arm,rows in by.items()}}
    for task in tasks:
        descriptors={(by[arm][task]['authorized_catalog_sha256'],
                      by[arm][task]['budget_sha256']) for arm in ARMS}
        if len(descriptors)!=1:
            return {'status':'INCOMPLETE','reason':'unequal_frozen_resources_or_fields'}
    output={}
    validated=True
    for arm in ARMS:
        samples=list(by[arm].values())
        n_attempts=sum(x['attempted'] for x in samples)
        output[arm]={
            'attempted':n_attempts,
            'valid_rate':sum(x['legal'] for x in samples)/n_attempts if n_attempts else None,
            'duplicate_rate':sum(x['duplicates'] for x in samples)/n_attempts if n_attempts else None,
            'feedback_revisions':sum(x['revisions'] for x in samples),
            'simulation_count':sum(x['simulation_count'] for x in samples),
            'mechanism_diversity':len(set(label for x in samples
                        for label in x['mechanism_labels'])),
            'legal_candidates_per_simulation':(
                sum(x['legal'] for x in samples)/sum(x['simulation_count'] for x in samples)
                if sum(x['simulation_count'] for x in samples) else None),
            'platform_checks_passed':sum(x['platform_checks_passed'] for x in samples),
            'independent_passes':sum(x['independent_validation_passed'] is True for x in samples),
            'evidence_grades':sorted(set(x['evidence_grade'] for x in samples)),
        }
        if any(x['evidence_grade']!='verified_market' for x in samples):validated=False
    return {'status':'PAIRED_DESCRIPTIVE_ONLY' if validated else 'ENGINEERING_ONLY',
            'tasks':len(tasks),'arms':output,
            'memory_improvement_proven':False,
            'real_alpha_effectiveness_proven':False,
            'note':'No significance claim without actual independent test and predeclared statistical correction.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('records',help='Previously frozen JSONL records; no simulations')
    args=parser.parse_args()
    rows=[json.loads(line) for line in Path(args.records).read_text(encoding='utf-8').splitlines() if line.strip()]
    print(json.dumps(evaluate(rows),indent=2,ensure_ascii=False))

if __name__=='__main__':main()
