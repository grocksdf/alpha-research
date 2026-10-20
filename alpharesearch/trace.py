"""Shareable shape-only API evidence: never log headers, request bodies or values."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .common import now

PUBLIC_KEYS=set('id name email username password cookie token authorization is os prod test train checks result limit value records schema properties type description progress alpha regular code status message detail count results next previous date pnl year sharpe fitness turnover returns drawdown margin max min settings delay neutralization truncation decay instrumentType region universe language pasteurization unitHandling nanHandling visualization startDate endDate bookSize longCount shortCount _non_json inquiry'.split())
PUBLIC_COLUMNS=set('date pnl year sharpe fitness turnover returns drawdown margin longCount shortCount'.split())


def shape(value, depth=0):
    if depth > 8: return {'type':'truncated'}
    if value is None: return {'type':'null'}
    if isinstance(value, bool): return {'type':'boolean'}
    if isinstance(value, (int,float)): return {'type':'number'}
    if isinstance(value, str): return {'type':'string'}
    if isinstance(value, list):
        unique={json.dumps(shape(v,depth+1),sort_keys=True):shape(v,depth+1) for v in value[:8]}
        return {'type':'array','length':len(value),'sample_shapes':list(unique.values())}
    if isinstance(value, dict):
        result={'type':'object','properties':{k if k in PUBLIC_KEYS else '<redacted-key-'+str(i)+'>':shape(v,depth+1) for i,(k,v) in enumerate(list(value.items())[:80])}}
        schema=value.get('schema')
        if isinstance(schema,dict) and isinstance(schema.get('properties'),list):
            result['recordset_columns']=[p.get('name') if isinstance(p,dict) and p.get('name') in PUBLIC_COLUMNS else '<unknown-column>' for p in schema['properties'][:80]]
        return result
    return {'type':'unknown'}


class ShapeRecorder:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)

    def __call__(self,method,url,response):
        parts=urlsplit(url).path.split('/')
        for i,part in enumerate(parts):
            if i>0 and parts[i-1] in ('alphas','simulations') and part: parts[i]='{id}'
        entry={'observed_at':now(),'method':method,'path':'/'.join(parts),
            'http_status':response.status,'has_location':'location' in response.headers,
            'has_retry_after':'retry-after' in response.headers,'response_shape':shape(response.data),
            'values_redacted':True}
        fd=os.open(self.path,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
        with os.fdopen(fd,'a',encoding='utf-8') as out:
            out.write(json.dumps(entry,ensure_ascii=False)+'\n');out.flush();os.fsync(out.fileno())
