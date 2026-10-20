"""Generate reproducible TEST data to exercise the protocol, not to discover alpha."""
import argparse
import csv
import json
import math
from datetime import date,timedelta
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='state/local-demo');args=parser.parse_args()
    root=Path(args.output);root.mkdir(parents=True,exist_ok=True)
    with (root/'panel.csv').open('x',newline='') as file:
        writer=csv.writer(file);writer.writerow(['date','asset','available_date','eligible','open','high','low','close','volume','vwap','cap','returns','subindustry','session'])
        prices=[20+5*j for j in range(12)]
        for t in range(260):
            day=(date(2020,1,1)+timedelta(days=t)).isoformat()
            for j in range(12):
                old=prices[j];ret=0.001+0.01*math.sin(t*.17+j*.9);prices[j]*=1+ret;close=prices[j]
                writer.writerow([day,f'TEST_{j:02}',day,1,old,max(old,close)*1.005,min(old,close)*.995,close,
                    100000*(1.5+math.sin(t*.1+j)),(old+close)/2,close*1e7,ret,'TEST_GROUP_'+str(j%3),t])
    plan={'study_id':'synthetic-protocol-demo','dataset_id':'synthetic-panel-generator-v1','data_permission_confirmed':True,'data_source':'generated synthetic data, NOT market data',
        'point_in_time_review':'synthetic daily snapshots; no real data validity claim','synthetic':True,
        'discovery_start':'2020-01-01','holdout_start':'2020-07-19','holdout_end':'2020-09-16',
        'train_min_days':60,'test_days':30,'embargo_days':2,'delay':1,'min_assets':6,'min_train_observations':40,
        'fee_bps':5,'min_train_ic':-1,'min_train_sharpe':-100,'max_train_turnover':3,'min_train_coverage':0.5,
        'label_horizon_days':1,'annualization':252}
    with (root/'plan.json').open('x') as file:json.dump(plan,file,indent=2)
    print(root)


if __name__=='__main__':main()
