from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path

from .common import ResearchError, digest, dumps, now


def remote_result_complete(trial,payload,snap):
    from .brain import finite
    from .reconciliation import expression_matches,settings_match
    if not all(isinstance(value,dict) for value in (trial,payload,snap)):return False
    metrics=payload.get('metrics')
    if not isinstance(metrics,dict) or any(finite(metrics.get(k)) is None for k in ('sharpe','fitness','turnover')):
        return False
    if snap.get('mode')=='demo' and not trial.get('account_ref'):return True
    return (snap.get('mode')=='live' and payload.get('mode')=='live' and not payload.get('synthetic')
        and isinstance(payload.get('alpha_id'),str) and bool(payload['alpha_id'])
        and (not trial.get('alpha_id') or trial['alpha_id']==payload['alpha_id'])
        and expression_matches(payload.get('expression'),trial.get('expression'))
        and settings_match(payload.get('settings'),snap.get('scope')))


class Memory:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS runs(
          id TEXT PRIMARY KEY, created TEXT NOT NULL, snapshot TEXT NOT NULL,
          snapshot_hash TEXT NOT NULL, simulations INTEGER NOT NULL DEFAULT 0,
          proposals INTEGER NOT NULL DEFAULT 0, llm_calls INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS trials(
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
          expression TEXT NOT NULL, hypothesis TEXT NOT NULL, source TEXT NOT NULL,
          parent_id TEXT REFERENCES trials(id), kind TEXT NOT NULL,
          fingerprint TEXT, family TEXT, context TEXT NOT NULL,
          created TEXT NOT NULL, UNIQUE(context, fingerprint));
        CREATE TABLE IF NOT EXISTS tasks(
          trial_id TEXT PRIMARY KEY REFERENCES trials(id), state TEXT NOT NULL,
          location TEXT, error TEXT, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS results(
          id INTEGER PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id),
          created TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, created TEXT NOT NULL, run_id TEXT,
          kind TEXT NOT NULL, payload TEXT NOT NULL, previous TEXT NOT NULL, hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS submissions(
          alpha_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(id),
          state TEXT NOT NULL, review_hash TEXT NOT NULL, updated TEXT NOT NULL, result TEXT);
        CREATE TABLE IF NOT EXISTS candidate_promotions(
          run_id TEXT NOT NULL REFERENCES runs(id),
          trial_id TEXT NOT NULL REFERENCES trials(id),
          hypothesis TEXT NOT NULL, source TEXT NOT NULL, created TEXT NOT NULL,
          PRIMARY KEY(run_id, trial_id));
        CREATE TABLE IF NOT EXISTS research_intents(
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
          trial_id TEXT NOT NULL REFERENCES trials(id),
          hypothesis TEXT NOT NULL, source TEXT NOT NULL,
          parent_intent_id TEXT REFERENCES research_intents(id), created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intent_states(
          intent_id TEXT PRIMARY KEY REFERENCES research_intents(id),
          state TEXT NOT NULL, reason TEXT, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sensitivity_refs(
          intent_id TEXT NOT NULL REFERENCES research_intents(id),
          trial_id TEXT NOT NULL REFERENCES trials(id), design_hash TEXT NOT NULL,
          created TEXT NOT NULL, PRIMARY KEY(intent_id, trial_id, design_hash));
        CREATE TABLE IF NOT EXISTS research_notes(
          id TEXT PRIMARY KEY, context TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(id),
          intent_id TEXT REFERENCES research_intents(id), category TEXT NOT NULL,
          claim TEXT NOT NULL, evidence_ids TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS costs(
          id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), category TEXT NOT NULL,
          seconds REAL NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
          usd REAL, estimated INTEGER NOT NULL, detail TEXT NOT NULL, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS directed_studies(
          study_id TEXT PRIMARY KEY,run_id TEXT UNIQUE NOT NULL REFERENCES runs(id),
          plan_hash TEXT NOT NULL,created TEXT NOT NULL);
        """)
        self.migrate_tasks()
        for table in ("trials", "results", "events", "candidate_promotions", "research_intents", "sensitivity_refs", "research_notes", "costs",'directed_studies'):
            for op in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{op} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable research record'); END")
        self.migrate_intents()

    def migrate_tasks(self):
        def work():
            columns={row[1] for row in self.db.execute('PRAGMA table_info(tasks)')}
            for name in ('account_ref','api_base_url','dispatched_at','alpha_id','remote_state'):
                if name not in columns:self.db.execute('ALTER TABLE tasks ADD COLUMN '+name+' TEXT')
            if 'remote_state' not in columns:
                # Preserve historical snapshots/events/budgets. No Alpha binding
                # or local paused/failed marker proves a remote task has ended.
                self.db.execute('''UPDATE tasks SET remote_state=CASE
                    WHEN state='queued' AND location IS NULL AND alpha_id IS NULL THEN 'not_started'
                    WHEN state='rejected' THEN 'not_created'
                    WHEN state='completed' AND EXISTS(SELECT 1 FROM results r WHERE r.trial_id=tasks.trial_id) THEN 'completed'
                    ELSE 'unresolved' END''')
                rows=self.db.execute('''SELECT t.expression,q.trial_id,q.alpha_id,q.account_ref,r.snapshot,
                    (SELECT payload FROM results x WHERE x.trial_id=t.id ORDER BY id DESC LIMIT 1) AS payload
                    FROM tasks q JOIN trials t ON q.trial_id=t.id JOIN runs r ON t.run_id=r.id
                    WHERE q.state='completed' ''').fetchall()
                for row in rows:
                    try:complete=remote_result_complete(dict(row),json.loads(row['payload']),json.loads(row['snapshot']))
                    except (TypeError,ValueError):complete=False
                    if not complete:self.db.execute("UPDATE tasks SET remote_state='unresolved' WHERE trial_id=?",(row['trial_id'],))
        self.transaction(work)

    def migrate_intents(self):
        # Old tables remain intact. Recover known provenance; do not invent missing old hypotheses.
        def work():
            for row in self.db.execute("SELECT * FROM trials WHERE kind='candidate'").fetchall():
                if not self.db.execute("SELECT 1 FROM research_intents WHERE run_id=? AND trial_id=?", (row['run_id'], row['id'])).fetchone():
                    self._intent(row['run_id'], row['id'], row['hypothesis'], row['source'], None)
            for row in self.db.execute("SELECT * FROM candidate_promotions").fetchall():
                if not self.db.execute("SELECT 1 FROM research_intents WHERE run_id=? AND trial_id=?", (row['run_id'], row['trial_id'])).fetchone():
                    self._intent(row['run_id'], row['trial_id'], row['hypothesis'], row['source'], None)
        self.transaction(work)

    def _intent(self, run_id, trial_id, hypothesis, source, parent_intent_id):
        intent_id = uuid.uuid4().hex[:16]
        self.db.execute("INSERT INTO research_intents VALUES(?,?,?,?,?,?,?)", (intent_id,run_id,trial_id,hypothesis,source,parent_intent_id,now()))
        self.db.execute("INSERT INTO intent_states VALUES(?,?,?,?)", (intent_id,'active',None,now()))
        self.event(run_id,'intent_created',{'intent_id':intent_id,'trial_id':trial_id,'hypothesis':hypothesis,'source':source,'parent_intent_id':parent_intent_id})
        return intent_id

    def add_intent(self, run_id, trial_id, hypothesis, source, parent_intent_id=None):
        return self.transaction(lambda:self._intent(run_id,trial_id,hypothesis,source,parent_intent_id))

    def intents(self, run_id=None, trial_id=None):
        clauses, args = [], []
        for key,value in [('i.run_id',run_id),('i.trial_id',trial_id)]:
            if value is not None: clauses.append(key+'=?'); args.append(value)
        query='SELECT i.*,s.state,s.reason FROM research_intents i JOIN intent_states s ON s.intent_id=i.id'
        if clauses: query+=' WHERE '+' AND '.join(clauses)
        return [dict(x) for x in self.db.execute(query+' ORDER BY i.created,i.id',args)]

    def link_sensitivity(self, intent_id, trial_id, design_hash):
        def work():
            changed=self.db.execute('INSERT OR IGNORE INTO sensitivity_refs VALUES(?,?,?,?)',(intent_id,trial_id,design_hash,now())).rowcount
            if changed: self.event(None,'sensitivity_ref',{'intent_id':intent_id,'trial_id':trial_id,'design_hash':design_hash})
        self.transaction(work)

    def existing_trial(self, context, fingerprint):
        row=self.db.execute('SELECT id FROM trials WHERE context=? AND fingerprint=?',(context,fingerprint)).fetchone()
        return row[0] if row else None

    def cost(self, run_id, category, seconds=0, input_tokens=None, output_tokens=None, usd=None, estimated=False, detail=None):
        self.transaction(lambda:self.db.execute('INSERT INTO costs(run_id,category,seconds,input_tokens,output_tokens,usd,estimated,detail,created) VALUES(?,?,?,?,?,?,?,?,?)',
            (run_id,category,max(0,seconds),input_tokens,output_tokens,usd,int(estimated),dumps(detail or {}),now())))

    def cost_summary(self, run_id):
        rows=[dict(x) for x in self.db.execute('SELECT * FROM costs WHERE run_id=?',(run_id,))]
        priced=[r for r in rows if r['usd'] is not None]
        llm=[r for r in rows if r['category']=='llm']
        return {'measured_seconds':sum(r['seconds'] for r in rows),
            'input_tokens':sum(r['input_tokens'] or 0 for r in llm),
            'output_tokens':sum(r['output_tokens'] or 0 for r in llm),
            'token_usage_complete':bool(llm) and all(r['input_tokens'] is not None and r['output_tokens'] is not None for r in llm),
            'known_usd':sum(r['usd'] for r in priced) if priced else None,
            'all_costs_priced':bool(rows) and len(priced)==len(rows),
            'usd_is_estimate':any(r['estimated'] for r in priced),'categories':{k:sum(r['category']==k for r in rows) for k in sorted({r['category'] for r in rows})}}

    def close(self):
        self.db.close()

    def transaction(self, action):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            result = action()
            self.db.execute("COMMIT")
            return result
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def event(self, run_id, kind, payload):
        row = self.db.execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        previous = row[0] if row else ""
        created = now()
        body = dumps(payload)
        hashed = digest([created, run_id, kind, body, previous])
        self.db.execute("INSERT INTO events(created,run_id,kind,payload,previous,hash) VALUES(?,?,?,?,?,?)",
                        (created, run_id, kind, body, previous, hashed))

    def verify(self):
        previous = ""
        for row in self.db.execute("SELECT * FROM events ORDER BY id"):
            if row["previous"] != previous or row["hash"] != digest([row["created"], row["run_id"], row["kind"], row["payload"], previous]):
                raise ResearchError("审计链校验失败")
            previous = row["hash"]
        return {"events": self.db.execute("SELECT count(*) FROM events").fetchone()[0], "head": previous}

    def create_run(self, snapshot):
        run_id = uuid.uuid4().hex[:16]
        def work():
            self.db.execute("INSERT INTO runs(id,created,snapshot,snapshot_hash) VALUES(?,?,?,?)",
                            (run_id, now(), dumps(snapshot), digest(snapshot)))
            self.event(run_id, "run_created", snapshot)
        self.transaction(work)
        return run_id

    def run(self, run_id):
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise ResearchError("实验 run_id 不存在")
        return dict(row)

    def consume(self, run_id, counter, limit):
        if counter not in ("proposals", "llm_calls"):
            raise ResearchError("非法计数器")
        def work():
            changed = self.db.execute(f"UPDATE runs SET {counter}={counter}+1 WHERE id=? AND {counter}<?", (run_id, limit)).rowcount
            if not changed:
                raise ResearchError(f"{counter} 预算已用完")
        self.transaction(work)

    def add_trial(self, run_id, proposal, validated, context, kind="candidate", parent_id=None):
        trial_id = uuid.uuid4().hex[:16]
        def work():
            if validated:
                found = self.db.execute("SELECT id FROM trials WHERE context=? AND fingerprint=?", (context, validated.fingerprint)).fetchone()
                if found:
                    self.event(run_id, "duplicate", {"expression": proposal["expression"], "existing": found[0], "parent_id": parent_id, "requested_kind": kind})
                    original_kind = self.db.execute("SELECT kind FROM trials WHERE id=?", (found[0],)).fetchone()[0]
                    if kind == "candidate" and original_kind == "sensitivity":
                        existing = self.db.execute(
                            "SELECT 1 FROM candidate_promotions WHERE run_id=? AND trial_id=?",
                            (run_id, found[0])).fetchone()
                        if not existing:
                            self.db.execute("INSERT INTO candidate_promotions VALUES(?,?,?,?,?)",
                                            (run_id, found[0], proposal["hypothesis"], proposal["source"], now()))
                            self.event(run_id, "candidate_promoted", {
                                "trial_id": found[0], "hypothesis": proposal["hypothesis"],
                                "source": proposal["source"], "from": "sensitivity"})
                    return found[0], False
            self.db.execute("INSERT INTO trials VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                trial_id, run_id, proposal["expression"], proposal["hypothesis"], proposal["source"], parent_id, kind,
                validated.fingerprint if validated else None, validated.family if validated else None, context, now()))
            state = "queued" if validated else "rejected"
            self.db.execute("INSERT INTO tasks(trial_id,state,location,error,updated,remote_state) VALUES(?,?,?,?,?,?)",
                (trial_id,state,None,None,now(),'not_started' if validated else 'not_created'))
            self.event(run_id, "trial_created", {"trial_id": trial_id, **proposal, "parent_id": parent_id, "kind": kind, "state": state})
            return trial_id, True
        return self.transaction(work)

    def trial(self, trial_id):
        row = self.db.execute("SELECT t.*,q.state,q.location,q.error,q.account_ref,q.api_base_url,q.dispatched_at,q.alpha_id,q.remote_state FROM trials t JOIN tasks q ON q.trial_id=t.id WHERE t.id=?", (trial_id,)).fetchone()
        if not row:
            raise ResearchError("trial_id 不存在")
        return dict(row)

    def promotion(self, trial_id, run_id):
        row = self.db.execute(
            "SELECT hypothesis,source FROM candidate_promotions WHERE trial_id=? AND run_id=?",
            (trial_id, run_id)).fetchone()
        return dict(row) if row else None

    def trials(self, run_id):
        return [dict(r) for r in self.db.execute("SELECT t.*,q.state,q.location,q.error,q.account_ref,q.api_base_url,q.dispatched_at,q.alpha_id,q.remote_state FROM trials t JOIN tasks q ON t.id=q.trial_id WHERE run_id=? ORDER BY t.created", (run_id,))]

    def latest(self, trial_id):
        row = self.db.execute("SELECT payload FROM results WHERE trial_id=? ORDER BY id DESC LIMIT 1", (trial_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def result(self, trial_id, payload):
        trial = self.trial(trial_id)
        snap=json.loads(self.run(trial['run_id'])['snapshot'])
        resolved=remote_result_complete(trial,payload,snap)
        def work():
            self.db.execute("INSERT INTO results(trial_id,created,payload) VALUES(?,?,?)", (trial_id, now(), dumps(payload)))
            self.db.execute("UPDATE tasks SET state='completed',error=NULL,updated=? WHERE trial_id=?", (now(), trial_id))
            self.db.execute('UPDATE tasks SET remote_state=? WHERE trial_id=?',('completed' if resolved else 'unresolved',trial_id))
            self.event(trial["run_id"], "result_recorded", {"trial_id": trial_id, "result_hash": digest(payload)})
        self.transaction(work)

    def set_state(self, trial_id, state, location=None, error=None, remote_state=None):
        trial = self.trial(trial_id)
        if remote_state is not None and remote_state!='not_created':raise ResearchError('远程完成必须通过详情对账确认')
        if remote_state=='not_created' and state!='failed':raise ResearchError('仅明确拒绝创建可标记 not_created')
        def work():
            self.db.execute("UPDATE tasks SET state=?,location=COALESCE(?,location),error=?,updated=? WHERE trial_id=?",
                            (state, location, error, now(), trial_id))
            if state in ('dispatching','uncertain','polling'):
                self.db.execute("UPDATE tasks SET remote_state='unresolved' WHERE trial_id=?",(trial_id,))
            elif state=='paused':
                self.db.execute("UPDATE tasks SET remote_state='unresolved' WHERE trial_id=? AND (remote_state IS NULL OR remote_state='not_started')",(trial_id,))
            if remote_state:
                self.db.execute('UPDATE tasks SET remote_state=? WHERE trial_id=?',(remote_state,trial_id))
            self.event(trial["run_id"], "task_state", {"trial_id": trial_id, "state": state, "error": error,'remote_state':remote_state})
        self.transaction(work)

    def confirm_remote_completed(self,trial_id,value):
        """A verified detail can end remote occupancy while local evidence waits."""
        trial=self.trial(trial_id);snap=json.loads(self.run(trial['run_id'])['snapshot'])
        if (not remote_result_complete(trial,value,snap) or not trial.get('alpha_id')
            or value.get('mode')!='live' or value.get('alpha_id')!=trial['alpha_id']):
            raise ResearchError('详情不足以确认远程模拟完成；继续占用串行锁')
        def work():
            self.db.execute("UPDATE tasks SET remote_state='completed',state='paused',updated=? WHERE trial_id=?",(now(),trial_id))
            self.event(trial['run_id'],'remote_completed',{'trial_id':trial_id,'alpha_id':value['alpha_id'],'detail_sha256':digest(value)})
        self.transaction(work)

    def bind_alpha(self,trial_id,alpha_id,recovery=None):
        import re
        if not isinstance(alpha_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',alpha_id):raise ResearchError('无效 Alpha ID')
        trial=self.trial(trial_id)
        def work():
            existing=self.db.execute('SELECT alpha_id FROM tasks WHERE trial_id=?',(trial_id,)).fetchone()[0]
            if existing and existing!=alpha_id:raise ResearchError('任务已经绑定其他 Alpha；禁止覆盖')
            self.db.execute('UPDATE tasks SET alpha_id=?,updated=? WHERE trial_id=?',(alpha_id,now(),trial_id))
            self.event(trial['run_id'],'alpha_bound',{'trial_id':trial_id,'alpha_id':alpha_id,'recovery':recovery})
        self.transaction(work)

    def claim(self, trial_id, limit, account_ref=None, api_base_url=None):
        trial = self.trial(trial_id)
        snap=json.loads(self.run(trial['run_id'])['snapshot'])
        live=snap.get('mode')=='live' or account_ref is not None
        if live:
            import re
            from .scheduling import api_origin
            if not re.fullmatch(r'[0-9a-f]{64}',str(account_ref)) or api_origin(api_base_url) is None:
                raise ResearchError('真实创建必须绑定账户/API；不能省略身份绕过串行保护')
            expected=snap.get('catalog',{}).get('account_id')
            if expected and digest(str(expected))!=account_ref:raise ResearchError('创建账户与原快照冲突')
            if snap.get('mode')=='live' and api_origin(snap.get('brain',{}).get('base_url'))!=api_origin(api_base_url):
                raise ResearchError('创建 API 与原快照冲突；不能切换地址绕过串行保护')
        def work():
            if self.db.execute('SELECT state FROM tasks WHERE trial_id=?',(trial_id,)).fetchone()[0]!='queued':return False
            if live:
                from .scheduling import blocking_tasks
                blockers=blocking_tasks(self.db,account_ref,api_base_url,trial_id)
                if blockers:raise ResearchError('同账户仍有未解决的远程模拟/不确定创建；先 GET 对账，禁止新增 POST：'+blockers[0]['trial_id'])
            changed = self.db.execute("UPDATE tasks SET state='dispatching',remote_state='unresolved',updated=? WHERE trial_id=? AND state='queued'", (now(), trial_id)).rowcount
            if not changed:
                return False
            changed = self.db.execute("UPDATE runs SET simulations=simulations+1 WHERE id=? AND simulations<?", (trial["run_id"], limit)).rowcount
            if not changed:
                raise ResearchError("模拟预算已用完（含参数扰动和不确定写请求）")
            dispatched_at=now()
            self.db.execute('UPDATE tasks SET account_ref=?,api_base_url=?,dispatched_at=? WHERE trial_id=?',
                (account_ref,api_base_url,dispatched_at,trial_id))
            self.event(trial["run_id"], "simulation_reserved", {"trial_id": trial_id,'dispatched_at':dispatched_at,
                'account_ref':account_ref,'api_base_url':api_base_url})
            return True
        return self.transaction(work)

    def feedback(self, context, limit=30):
        # Read-only and explicit allowlist; no raw result/holdout access for the model.
        rows = self.db.execute("SELECT t.id,t.expression,i.hypothesis,i.source,t.family,i.run_id,i.id AS intent_id,s.state AS hypothesis_state,q.state,q.error FROM research_intents i JOIN trials t ON t.id=i.trial_id JOIN intent_states s ON s.intent_id=i.id JOIN tasks q ON t.id=q.trial_id WHERE context=? ORDER BY i.created DESC LIMIT ?", (context, limit)).fetchall()
        feedback = []
        for row in rows:
            payload = self.latest(row["id"]) or {}
            feedback.append({**dict(row), "metrics": payload.get("metrics", {})})
        return feedback

    def reserve_submission(self, trial_id, alpha_id, review_hash):
        trial = self.trial(trial_id)
        def work():
            if self.db.execute("SELECT 1 FROM submissions WHERE alpha_id=?", (alpha_id,)).fetchone():
                raise ResearchError("此 Alpha 已有提交意图；只能恢复查询，不能重复 POST")
            self.db.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?)", (alpha_id, trial_id, "dispatching", review_hash, now(), None))
            self.event(trial["run_id"], "submission_approved", {"alpha_id": alpha_id, "trial_id": trial_id, "review_hash": review_hash})
        self.transaction(work)

    def submission_state(self, alpha_id, state, payload):
        def work():
            self.db.execute("UPDATE submissions SET state=?,updated=?,result=? WHERE alpha_id=?", (state, now(), dumps(payload), alpha_id))
            self.event(None, "submission_state", {"alpha_id": alpha_id, "state": state, "payload": payload})
        self.transaction(work)
