"""Bounded field search, evidence-linked notes and structural/economic family views."""
import json
import re
import uuid

from .common import ResearchError, dumps, now


def search_fields(catalog,query,limit=10,approved_only=True):
    if not isinstance(query,str) or not 1<=len(query.strip())<=300 or type(limit)is not int or not 1<=limit<=30:
        raise ResearchError('字段搜索需短查询和 1..30 的 limit')
    tokens=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',query.lower())
    results=[]
    for name,meta in catalog['fields'].items():
        if approved_only and not meta.get('point_in_time'): continue
        text=(name+' '+str(meta.get('description',''))).lower()
        score=sum(4*(token in name.lower())+(token in text) for token in tokens)
        if score: results.append({'id':name,'score':score,**meta})
    return sorted(results,key=lambda x:(-x['score'],x['id']))[:limit]


def add_note(engine,category,claim,evidence_ids,intent_id=None):
    if category not in {'hypothesis','observation','failure_constraint','success_pattern'}:
        raise ResearchError('未知记忆类型')
    if not isinstance(claim,str) or not 1<=len(claim)<=3000 or not isinstance(evidence_ids,list) or len(evidence_ids)>20 or not all(isinstance(x,str) for x in evidence_ids):
        raise ResearchError('结构化记忆格式错误')
    if category!='hypothesis' and not evidence_ids: raise ResearchError('非假设记忆必须链接实验依据')
    for trial_id in evidence_ids:
        if engine.memory.trial(trial_id)['context']!=engine.context: raise ResearchError('证据不属于当前研究范围')
    if intent_id and intent_id not in {i['id'] for i in engine.memory.intents(engine.run_id)}:
        raise ResearchError('intent_id 不属于当前 run')
    note_id=uuid.uuid4().hex[:16]
    # Explicit columns avoid implicit assumptions in future migrations.
    def save():
        count=engine.memory.db.execute('SELECT count(*) FROM research_notes WHERE run_id=?',(engine.run_id,)).fetchone()[0]
        if count>=engine.config.get('research',{}).get('max_notes_per_run',30): raise ResearchError('研究记忆写入预算耗尽')
        engine.memory.db.execute('INSERT INTO research_notes(id,context,run_id,intent_id,category,claim,evidence_ids,created) VALUES(?,?,?,?,?,?,?,?)',
            (note_id,engine.context,engine.run_id,intent_id,category,claim,dumps(evidence_ids),now()))
        engine.memory.event(engine.run_id,'note_created',{'note_id':note_id,'category':category,'evidence_ids':evidence_ids})
    engine.memory.transaction(save)
    return {'note_id':note_id,'category':category,'claim':claim,'evidence_ids':evidence_ids,'status':'research_note_not_verified_fact'}


def notes(engine,limit=30):
    rows=engine.memory.db.execute('SELECT * FROM research_notes WHERE context=? ORDER BY created DESC LIMIT ?',(engine.context,limit)).fetchall()
    return [{**dict(r),'evidence_ids':json.loads(r['evidence_ids']),'status':'research_note_not_verified_fact'} for r in rows]


def tag_family(engine,intent_id,tags,reason):
    if intent_id not in {i['id'] for i in engine.memory.intents(engine.run_id)}: raise ResearchError('未知研究假设')
    if not isinstance(tags,list) or not 1<=len(tags)<=4 or any(not isinstance(t,str) or not t.strip() or len(t)>120 for t in tags) or not isinstance(reason,str) or not 1<=len(reason)<=1000:
        raise ResearchError('标签必须为模型自行归纳的非空机制，附上依据')
    engine.memory.transaction(lambda:engine.memory.event(engine.run_id,'family_annotation',{'intent_id':intent_id,'tags':sorted(set(tags)),'reason':reason}))
    return {'intent_id':intent_id,'tags':tags,'economic_independence_verified':False}


def families(engine):
    groups={}
    annotations={}
    for row in engine.memory.db.execute("SELECT payload FROM events WHERE kind='family_annotation' ORDER BY id"):
        value=json.loads(row[0]);annotations[value['intent_id']]=value
    for intent in engine.memory.intents():
        trial=engine.memory.trial(intent['trial_id'])
        if trial['context']!=engine.context or not trial['family']: continue
        g=groups.setdefault(trial['family'],{'structural_family':trial['family'],'trial_ids':set(),'intent_ids':[],'tags':set(),'states':{}})
        g['trial_ids'].add(trial['id']);g['intent_ids'].append(intent['id'])
        g['tags'].update(annotations.get(intent['id'],{}).get('tags',[]))
        g['states'][intent['state']]=g['states'].get(intent['state'],0)+1
    return [{**g,'trial_ids':sorted(g['trial_ids']),'tags':sorted(g['tags']),'unique_formulas':len(g['trial_ids']),
             'economic_independence_verified':False} for g in groups.values()]
