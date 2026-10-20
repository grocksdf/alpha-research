"""Account-scoped remote simulation occupancy, shared by claim and previews."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

from .common import digest


def api_origin(value):
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        return ('https',parsed.hostname.lower(),parsed.port or 443)
    except (TypeError,ValueError):
        return None


def blocking_tasks(db,account_ref,api_base_url,exclude=None):
    """Must be called under BEGIN IMMEDIATE before granting a live claim.

    Legacy paused/failed tasks lack a remote completion receipt and stay locked.
    Clear demo records and distinct known accounts/origins do not share a slot.
    """
    columns={r[1] for r in db.execute('PRAGMA table_info(tasks)')}
    remote='q.remote_state' if 'remote_state' in columns else 'NULL'
    identity='q.account_ref' if 'account_ref' in columns else 'NULL'
    base='q.api_base_url' if 'api_base_url' in columns else 'NULL'
    rows=db.execute(f'''SELECT t.id,t.run_id,q.state,{remote} AS remote_state,
        {identity} AS account_ref,{base} AS api_base_url,r.snapshot
        FROM tasks q JOIN trials t ON q.trial_id=t.id JOIN runs r ON t.run_id=r.id
        WHERE t.id<>? AND (q.state IN ('dispatching','uncertain','polling')
            OR (q.state='paused' AND COALESCE({remote},'unresolved') NOT IN ('completed','not_created'))
            OR {remote}='unresolved'
            OR ({remote} IS NULL AND q.state IN ('failed','abandoned')))''',(exclude or '',)).fetchall()
    result=[]
    for row in rows:
        # Works with both row_factory=Row and plain read-only sqlite tuples.
        identifier,run_id,state,remote_state,account,base_url,raw=row
        if state not in ('dispatching','uncertain','polling'):
            if remote_state in ('completed','not_created','not_started') and state!='paused':continue
            if state=='paused' and remote_state in ('completed','not_created'):continue
            if remote_state is None and state not in ('paused','failed','abandoned'):continue
        try:snap=json.loads(raw)
        except (TypeError,ValueError):snap={}
        if not account and snap.get('mode')=='demo':continue
        if not account and snap.get('catalog',{}).get('account_id'):
            account=digest(str(snap['catalog']['account_id']))
        if account and account!=account_ref:continue
        base_url=base_url or snap.get('brain',{}).get('base_url')
        origin=api_origin(base_url)
        if origin and origin!=api_origin(api_base_url):continue
        result.append({'trial_id':identifier,'run_id':run_id,'state':state,
            'remote_state':remote_state or 'legacy_unresolved'})
    return sorted(result,key=lambda r:r['trial_id'])
