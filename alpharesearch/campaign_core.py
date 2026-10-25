"""Campaign v2: durable, audited research projects. No network and no submission tools.

Designed as an ADDITIVE extension to AlphaResearch v0.6.1. Legacy plans,
autonomous_studies, frozen hashes, submit() and return contracts are untouched.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .common import ResearchError, digest, dumps, now, snapshot, research_context
from .discovery import DOMAINS, coverage

PROTOCOL = "alpha-campaign-v2"
STATES = frozenset({"generated", "validated", "queued", "testing",
                    "evaluated", "deferred", "rejected", "promising"})
TRANSITIONS = {
    "generated": {"validated", "rejected"},
    "validated": {"queued", "deferred", "rejected"},
    "queued": {"testing", "deferred", "rejected"},
    "testing": {"evaluated", "promising", "deferred", "rejected"},
    "evaluated": {"testing", "promising", "deferred", "rejected"},
    "promising": {"deferred"},
    "deferred": {"queued", "rejected"},
    "rejected": set(),
}
STATUS = frozenset({"active", "paused_user", "paused_fault", "paused_resource",
                    "terminated", "completed"})
ALLOWED_SPEC = {"campaign_id", "objective", "domains", "generator", "research_notes",
                "lifecycle", "max_total_simulations", "quality_goal_count",
                "ideas_per_cycle", "queue_low_watermark", "breadth_before_depth",
                "max_depth_per_batch", "max_sensitivity_variants", "family_failure_limit",
                "family_cooldown_cycles", "max_return_correlation",
                "external_libraries", "max_total_llm_calls", "max_total_tokens",
                "max_total_known_usd", "demo_engineering_goal_count",
                "walk_forward_train", "walk_forward_test", "walk_forward_step",
                "walk_forward_min_folds", "walk_forward_embargo",
                "llm_max_output_tokens", "llm_max_prompt_chars"}
ALLOWED_LIMITS = {"max_new_simulations", "max_llm_calls", "max_steps", "max_wall_seconds",
                  "max_input_output_tokens", "max_known_usd"}

def _positive(value, name, maximum=None, allow_zero=False):
    low = 0 if allow_zero else 1
    if type(value) is not int or value < low or (maximum is not None and value > maximum):
        raise ResearchError("无效的 campaign 正整数预算：" + name)
    return value

def create_campaign_plan(config, spec):
    if not isinstance(spec, dict) or set(spec) - ALLOWED_SPEC:
        raise ResearchError("campaign 计划有未知字段")
    data = {
        "generator": "llm", "domains": list(DOMAINS), "research_notes": "",
        "lifecycle": "continuous", "max_total_simulations": None,
        "quality_goal_count": None, "ideas_per_cycle": 3, "queue_low_watermark": 2,
        "breadth_before_depth": 3, "max_depth_per_batch": 1,
        "max_sensitivity_variants": 2, "family_failure_limit": 4,
        "family_cooldown_cycles": 2, "max_return_correlation": 0.7,
        "external_libraries": [], "max_total_llm_calls": None,
        "max_total_tokens": None, "max_total_known_usd": None,
        "demo_engineering_goal_count": None,
        "walk_forward_train": 504, "walk_forward_test": 126,
        "walk_forward_step": 126, "walk_forward_min_folds": 3,
        "walk_forward_embargo": 5,
        "llm_max_output_tokens": 2048,
        "llm_max_prompt_chars": 100000,
    }
    data.update(spec)
    if not isinstance(data.get("campaign_id"), str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,80}", data["campaign_id"]):
        raise ResearchError("campaign_id 无效")
    if not isinstance(data.get("objective"), str) or not 1 <= len(data["objective"].strip()) <= 2000:
        raise ResearchError("必须给出研究目标")
    if data["lifecycle"] not in ("continuous", "finite"):
        raise ResearchError("lifecycle 必须为 continuous 或 finite")
    total = data["max_total_simulations"]
    if data["lifecycle"] == "continuous":
        if total is not None:
            raise ResearchError("持续模式必须使用 null，不得以大整数模拟无限预算")
    else:
        _positive(total, "max_total_simulations")
    goal = data["quality_goal_count"]
    if goal is not None:
        _positive(goal, "quality_goal_count")
    for key in ("max_total_llm_calls", "max_total_tokens",
                "demo_engineering_goal_count"):
        if data[key] is not None:
            _positive(data[key],key)
    for key in ("walk_forward_train", "walk_forward_test",
                "walk_forward_step", "walk_forward_min_folds"):
        _positive(data[key],key)
    if data["walk_forward_min_folds"] < 2:
        raise ResearchError("walk-forward 至少需要两个样本外研究窗口")
    _positive(data["walk_forward_embargo"], "walk_forward_embargo",
              maximum=200, allow_zero=True)
    if data["walk_forward_step"] < data["walk_forward_test"]:
        raise ResearchError("walk-forward 测试窗口不能重叠")
    _positive(data["llm_max_output_tokens"],"llm_max_output_tokens",16000)
    _positive(data["llm_max_prompt_chars"],"llm_max_prompt_chars",1000000)
    lifetime_usd = data["max_total_known_usd"]
    if lifetime_usd is not None and (type(lifetime_usd) not in (int,float) or
            not 0 < lifetime_usd < 10000000):
        raise ResearchError("max_total_known_usd 必须为正数或 null")
    if not isinstance(data["domains"], list) or not data["domains"] or (
            len(set(data["domains"])) != len(data["domains"])
            or any(x not in DOMAINS for x in data["domains"])):
        raise ResearchError("研究方向不合法")
    if data["generator"] not in ("demo", "llm") or (
            data["generator"] == "demo" and config["mode"] != "demo"):
        raise ResearchError("demo 生成器仅用于 demo")
    for key, maximum in (("ideas_per_cycle", 8), ("queue_low_watermark", 1000),
                         ("breadth_before_depth", 100),
                         ("max_depth_per_batch", 10),
                         ("max_sensitivity_variants", config["rules"]["sensitivity_variants"]),
                         ("family_failure_limit", 100),
                         ("family_cooldown_cycles", 100)):
        _positive(data[key], key, maximum)
    if not isinstance(data["research_notes"], str) or len(data["research_notes"]) > 12000:
        raise ResearchError("研究资料不得超过 12000 字")
    corr = data["max_return_correlation"]
    if type(corr) not in (int, float) or not 0 < corr < 1:
        raise ResearchError("相关性阈值必须在 (0,1) 内")
    libs = data["external_libraries"]
    if (not isinstance(libs, list) or len(libs) > 24 or
            any(not isinstance(p, str) or not p or len(p) > 2048 for p in libs) or
            len(libs) != len(set(libs))):
        raise ResearchError("外部库必须是显式、互不重复的只读路径")
    library_hashes = {}
    for path in libs:
        file = Path(path).expanduser()
        if not file.is_file() or file.resolve() == Path(config["database"]).resolve():
            raise ResearchError("外部库不存在或指向主库：" + path)
        # Only explicitly frozen, quiescent SQLite copies are allowed. An
        # active SQLite WAL is not a stable cross-library dedup reference.
        if Path(str(file)+"-wal").exists() or Path(str(file)+"-shm").exists():
            raise ResearchError("跨库去重必须提供停止写入并 checkpoint 后的只读库快照："+path)
        hasher = hashlib.sha256()
        with file.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024*1024), b""):
                hasher.update(chunk)
        library_hashes[path] = hasher.hexdigest()
    data.update(external_library_sha256=library_hashes, 
        protocol=PROTOCOL, mode=config["mode"], context=research_context(config),
        snapshot_sha256=digest(snapshot(config)),
        database_ref=digest(str(Path(config["database"]).resolve())),
        domain_coverage=coverage(config, data["domains"]), automatic_submit=False)
    data["plan_sha256"] = digest(data)
    return data

def validate_campaign_plan(config, plan):
    if not isinstance(plan, dict) or plan.get("protocol") != PROTOCOL:
        raise ResearchError("campaign 计划协议不符")
    spec = {k: v for k, v in plan.items() if k in ALLOWED_SPEC}
    if create_campaign_plan(config, spec) != plan:
        raise ResearchError("campaign 冻结计划/快照已改变；须显式建立新项目并追加迁移证据")
    return plan

def validate_limits(config, limits):
    if not isinstance(limits, dict) or set(limits) - ALLOWED_LIMITS:
        raise ResearchError("本次运行资源配置包含未知项")
    rules = config["rules"]
    values = {"max_new_simulations": min(3, rules["max_simulations_per_run"]),
              "max_llm_calls": min(2, rules["max_llm_calls_per_run"]),
              "max_steps": 12, "max_wall_seconds": 900,
              "max_input_output_tokens": 50000, "max_known_usd": None}
    values.update(limits)
    for name, high in (("max_new_simulations", rules["max_simulations_per_run"]),
                       ("max_llm_calls", rules["max_llm_calls_per_run"]),
                       ("max_steps", 1000), ("max_wall_seconds", 86400),
                       ("max_input_output_tokens", 10000000)):
        _positive(values[name], name, high)
    usd = values["max_known_usd"]
    if usd is not None and (type(usd) not in (int, float) or not 0 < usd <= 100000):
        raise ResearchError("max_known_usd 必须是有限正数或 null")
    return values

class CampaignStore:
    """Persistent project queue on the SAME database, with an audit replay check.

    Existing immutable events are chained by Memory.event(). Mutations of the
    queue and project status are always paired with a chained event in one TX.
    The schema never rewrites old runs or event hashes.
    """
    def __init__(self, memory):
        self.memory = memory
        self.db = memory.db
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS campaign_projects(
          campaign_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL,
          plan_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
          status TEXT NOT NULL, reason TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaign_queue(
          id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign_projects(campaign_id),
          original_id TEXT, idea_json TEXT NOT NULL, fingerprint TEXT, family TEXT,
          state TEXT NOT NULL, phase TEXT NOT NULL, parent_id TEXT, trial_id TEXT,
          reason TEXT NOT NULL, priority INTEGER NOT NULL, cycle INTEGER NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL,
          UNIQUE(campaign_id,fingerprint));
        CREATE INDEX IF NOT EXISTS campaign_queue_ready ON campaign_queue(campaign_id,state,phase,priority,cycle);
        CREATE TABLE IF NOT EXISTS campaign_duplicates(
          attempt_id TEXT PRIMARY KEY,
          campaign_id TEXT NOT NULL REFERENCES campaign_projects(campaign_id),
          existing_idea_id TEXT NOT NULL REFERENCES campaign_queue(id),
          original_id TEXT,
          idea_json TEXT NOT NULL,
          fingerprint TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state='rejected'),
          reason TEXT NOT NULL,
          created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaign_runs(
          run_id TEXT PRIMARY KEY REFERENCES runs(id), campaign_id TEXT NOT NULL,
          batch_no INTEGER NOT NULL, limits_json TEXT NOT NULL, created TEXT NOT NULL,
          UNIQUE(campaign_id,batch_no));
        CREATE TABLE IF NOT EXISTS campaign_generations(
          campaign_id TEXT NOT NULL, cycle INTEGER NOT NULL, run_id TEXT NOT NULL,
          status TEXT NOT NULL, request_hash TEXT NOT NULL, updated TEXT NOT NULL,
          PRIMARY KEY(campaign_id,cycle));
        CREATE TABLE IF NOT EXISTS campaign_depth(
          campaign_id TEXT NOT NULL, idea_id TEXT NOT NULL, formula_hash TEXT NOT NULL,
          trial_id TEXT, status TEXT NOT NULL, created TEXT NOT NULL,
          PRIMARY KEY(campaign_id,idea_id,formula_hash));
        """)
        self.db.executescript("""
        CREATE TRIGGER IF NOT EXISTS campaign_frozen_plan
        BEFORE UPDATE OF plan_hash,plan_json,snapshot_hash ON campaign_projects
        BEGIN SELECT RAISE(ABORT,'immutable campaign plan'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_project_delete
        BEFORE DELETE ON campaign_projects
        BEGIN SELECT RAISE(ABORT,'immutable campaign project'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_idea_delete
        BEFORE DELETE ON campaign_queue
        BEGIN SELECT RAISE(ABORT,'immutable campaign hypothesis'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_idea_created_change
        BEFORE UPDATE OF created ON campaign_queue
        BEGIN SELECT RAISE(ABORT,'immutable hypothesis creation time'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_project_created_change
        BEFORE UPDATE OF created ON campaign_projects
        BEGIN SELECT RAISE(ABORT,'immutable campaign creation time'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_generation_identity_change
        BEFORE UPDATE OF campaign_id,cycle,run_id,request_hash ON campaign_generations
        BEGIN SELECT RAISE(ABORT,'immutable generation identity'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_depth_update
        BEFORE UPDATE ON campaign_depth
        BEGIN SELECT RAISE(ABORT,'immutable depth evidence'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_duplicate_update
        BEFORE UPDATE ON campaign_duplicates
        BEGIN SELECT RAISE(ABORT,'immutable duplicate attempt'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_duplicate_delete
        BEFORE DELETE ON campaign_duplicates
        BEGIN SELECT RAISE(ABORT,'immutable duplicate attempt'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_generation_delete
        BEFORE DELETE ON campaign_generations
        BEGIN SELECT RAISE(ABORT,'immutable generation evidence'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_no_depth_delete
        BEFORE DELETE ON campaign_depth
        BEGIN SELECT RAISE(ABORT,'immutable depth evidence'); END;
        CREATE TRIGGER IF NOT EXISTS campaign_frozen_idea
        BEFORE UPDATE OF idea_json,fingerprint,family,original_id,cycle,priority,parent_id
        ON campaign_queue
        BEGIN SELECT RAISE(ABORT,'immutable campaign hypothesis'); END;
        """)
        for table in ("campaign_runs",):
            for op in ("UPDATE", "DELETE"):
                self.db.execute("CREATE TRIGGER IF NOT EXISTS immut_%s_%s BEFORE %s ON %s "
                                "BEGIN SELECT RAISE(ABORT,'immutable campaign execution'); END"
                                % (table, op.lower(), op, table))

    def _log(self, cid, action, payload, run_id=None):
        self.memory.event(run_id, "campaign_" + action, {"campaign_id": cid, **payload})

    def get(self, cid):
        row = self.db.execute("SELECT * FROM campaign_projects WHERE campaign_id=?", (cid,)).fetchone()
        if row is None:
            raise ResearchError("campaign 不存在：" + cid)
        return dict(row)

    def create(self, plan):
        cid = plan["campaign_id"]
        record = self.db.execute("SELECT plan_hash FROM campaign_projects WHERE campaign_id=?", (cid,)).fetchone()
        if record:
            if record[0] != plan["plan_sha256"]:
                raise ResearchError("相同 campaign_id 已绑定其他冻结计划")
            return False
        def work():
            self.db.execute("INSERT INTO campaign_projects VALUES(?,?,?,?,?,?,?,?)",
                            (cid, plan["plan_sha256"], dumps(plan), plan["snapshot_sha256"],
                             "active", "created", now(), now()))
            self._log(cid, "created", {"plan_hash": plan["plan_sha256"],
                                      "plan": plan, "status": "active"})
        self.memory.transaction(work)
        return True

    def control(self, cid, action, reason="operator"):
        allowed = {
            "pause": {"active"}, "resume": {"paused_user", "paused_fault", "paused_resource"},
            "terminate": {"active", "paused_user", "paused_fault", "paused_resource"},
            "fault": {"active"}, "resource": {"active"}, "complete": {"active"},
        }
        targets = {"pause": "paused_user", "resume": "active", "terminate": "terminated",
                   "fault": "paused_fault", "resource": "paused_resource",
                   "complete": "completed"}
        if action not in targets:
            raise ResearchError("未知的 campaign 控制命令")
        before = self.get(cid)["status"]
        if before not in allowed[action]:
            raise ResearchError("不允许从 %s 执行 %s" % (before, action))
        # Intentionally fail closed: a Demo Sharpe/fitness score is not an
        # independently attested quality achievement, and the external holdout
        # attestation protocol is NOT wired into this offline adapter.
        if action == "complete":
            if reason == "demo_engineering_goal":
                frozen = json.loads(self.get(cid)["plan_json"])
                goal = frozen.get("demo_engineering_goal_count")
                count = self.db.execute(
                    "SELECT count(*) FROM campaign_queue "
                    "WHERE campaign_id=? AND state='promising' AND phase='l3'", (cid,)
                ).fetchone()[0]
                if frozen.get("mode") != "demo" or goal is None or count < goal:
                    raise ResearchError("Demo 工程目标尚未满足，禁止完成")
            elif reason != "finite_lifecycle_cap":
                # Real quality goals require a separate independently verified
                # holdout protocol; no Demo score may satisfy this.
                raise ResearchError("质量目标完成缺乏独立留出签署证据，禁止完成")
        if not isinstance(reason, str) or not reason or len(reason) > 500:
            raise ResearchError("控制原因无效")
        def work():
            self.db.execute("UPDATE campaign_projects SET status=?,reason=?,updated=? WHERE campaign_id=?",
                            (targets[action], reason, now(), cid))
            self._log(cid, "control", {"from": before, "to": targets[action], "reason": reason})
        self.memory.transaction(work)
        return targets[action]

    def rows(self, cid, *, state=None, phase=None):
        query = "SELECT * FROM campaign_queue WHERE campaign_id=?"
        args = [cid]
        if state:
            query += " AND state=?"; args.append(state)
        if phase:
            query += " AND phase=?"; args.append(phase)
        query += " ORDER BY priority ASC, cycle ASC, id ASC"
        result = [dict(r) for r in self.db.execute(query, args)]
        for item in result:
            item["idea"] = json.loads(item.pop("idea_json"))
        return result

    def one(self, idea_id):
        row = self.db.execute("SELECT * FROM campaign_queue WHERE id=?", (idea_id,)).fetchone()
        if not row:
            raise ResearchError("未知 campaign idea_id")
        item = dict(row)
        item["idea"] = json.loads(item.pop("idea_json"))
        return item

    def add(self, cid, idea, cycle, *, validated=None, reason=None, original_id=None):
        cidrow = self.get(cid)
        if cidrow["status"] != "active":
            raise ResearchError("暂停或终止的 campaign 不得接收新假设")
        if not isinstance(idea, dict):
            idea = {"unstructured": repr(idea)[:1000]}
            reason = reason or "invalid_hypothesis_schema"
        ident = digest([cid, cycle, idea, original_id])[:24]
        fingerprint = validated.fingerprint if validated is not None else None
        family = validated.family if validated is not None else None
        priority = 0 if idea.get("parent_idea_id") else 1
        if fingerprint:
            old = self.db.execute(
                "SELECT id FROM campaign_queue WHERE campaign_id=? AND fingerprint=?",
                (cid, fingerprint)).fetchone()
            if old:
                def record_duplicate():
                    changed = self.db.execute(
                        "INSERT OR IGNORE INTO campaign_duplicates VALUES(?,?,?,?,?,?,?,?,?)",
                        (ident, cid, old[0], original_id, dumps(idea), fingerprint,
                         "rejected", "duplicate_ast_fingerprint", now())).rowcount
                    if changed:
                        self._log(cid, "duplicate", {
                            "attempt_id": ident, "existing_idea_id": old[0],
                            "fingerprint": fingerprint, "original_id": original_id,
                            "state": "rejected", "reason": "duplicate_ast_fingerprint"})
                self.memory.transaction(record_duplicate)
                return old[0], False
        status = "validated" if validated else "rejected"
        def work():
            self.db.execute(
                "INSERT INTO campaign_queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ident, cid, original_id, dumps(idea), fingerprint, family, "generated",
                 "l1", idea.get("parent_idea_id"), None, "", priority, cycle, now(), now()))
            self._log(cid, "idea", {"idea_id": ident, "state": "generated",
                                   "original_id": original_id, "idea_sha256": digest(idea)})
            self._move(ident, status, "l1", reason or "AST_and_economic_validation")
        self.memory.transaction(work)
        return ident, True

    def _move(self, idea_id, state, phase, reason, *, trial_id=None):
        row = self.db.execute("SELECT campaign_id,state,phase,trial_id FROM campaign_queue WHERE id=?",
                              (idea_id,)).fetchone()
        if not row or state not in TRANSITIONS.get(row["state"], set()):
            raise ResearchError("不合法的候选状态迁移")
        if phase not in ("l1", "l2", "l3"):
            raise ResearchError("阶段必须为 l1 / l2 / l3")
        if trial_id is not None and row["trial_id"] is not None and row["trial_id"] != trial_id:
            raise ResearchError("候选 trial_id 不能重绑")
        tid = trial_id if trial_id is not None else row["trial_id"]
        self.db.execute("UPDATE campaign_queue SET state=?,phase=?,reason=?,trial_id=?,updated=? WHERE id=?",
                        (state, phase, str(reason)[:1000], tid, now(), idea_id))
        self._log(row["campaign_id"], "transition", {
            "idea_id": idea_id, "from": row["state"], "to": state, "phase": phase,
            "previous_phase": row["phase"], "trial_id": tid, "reason": str(reason)[:1000]})

    def move(self, idea_id, state, phase, reason, *, trial_id=None):
        return self.memory.transaction(lambda: self._move(
            idea_id, state, phase, reason, trial_id=trial_id))

    def begin_generation(self, cid, run_id, request_hash):
        if self.db.execute("SELECT 1 FROM campaign_generations WHERE campaign_id=? AND status='started'",
                           (cid,)).fetchone():
            raise ResearchError("上一轮模型调用状态未知；操作员确认后才能继续")
        cycle = self.db.execute("SELECT COALESCE(MAX(cycle),0)+1 FROM campaign_generations "
                                "WHERE campaign_id=?", (cid,)).fetchone()[0]
        def work():
            self.db.execute("INSERT INTO campaign_generations VALUES(?,?,?,?,?,?)",
                            (cid, cycle, run_id, "started", request_hash, now()))
            self._log(cid, "generation", {"cycle": cycle, "status": "started",
                                          "request_hash": request_hash}, run_id)
        self.memory.transaction(work)
        return cycle

    def end_generation(self, cid, cycle, status, count):
        if status not in ("completed", "failed", "aborted"):
            raise ResearchError("非法 generation 状态")
        row = self.db.execute("SELECT status,run_id FROM campaign_generations WHERE campaign_id=? AND cycle=?",
                              (cid, cycle)).fetchone()
        if not row or row["status"] != "started":
            raise ResearchError("重复或无效的生成终结")
        def work():
            self.db.execute("UPDATE campaign_generations SET status=?,updated=? "
                            "WHERE campaign_id=? AND cycle=?", (status, now(), cid, cycle))
            self._log(cid, "generation", {"cycle": cycle, "status": status, "ideas": count}, row["run_id"])
        self.memory.transaction(work)

    def abort_unknown_generations(self, cid):
        rows = self.db.execute("SELECT cycle FROM campaign_generations WHERE campaign_id=? AND status='started'",
                               (cid,)).fetchall()
        for row in rows:
            self.end_generation(cid, row["cycle"], "aborted", 0)
        return len(rows)

    def begin_run(self, cid, run_id, limits):
        batch_no = self.db.execute("SELECT count(*)+1 FROM campaign_runs WHERE campaign_id=?",
                                   (cid,)).fetchone()[0]
        def work():
            self.db.execute("INSERT INTO campaign_runs VALUES(?,?,?,?,?)",
                            (run_id, cid, batch_no, dumps(limits), now()))
            self._log(cid, "execution", {"run_id": run_id, "batch": batch_no,
                                         "limits": limits}, run_id)
        self.memory.transaction(work)
        return batch_no

    def cumulative(self, cid):
        return self.db.execute(
            "SELECT COALESCE(SUM(r.simulations),0) FROM runs r "
            "JOIN campaign_runs b ON b.run_id=r.id WHERE b.campaign_id=?", (cid,)).fetchone()[0]

    def observed_llm_cost(self, run_id):
        llm = [dict(x) for x in self.db.execute(
            "SELECT input_tokens,output_tokens,usd,detail FROM costs "
            "WHERE run_id=? AND category='llm'", (run_id,))]
        unknown_tokens = any(x["input_tokens"] is None or x["output_tokens"] is None for x in llm)
        unknown_usd = any(x["usd"] is None for x in llm)
        return {"calls": len(llm),
                "known_tokens": sum((x["input_tokens"] or 0) + (x["output_tokens"] or 0) for x in llm),
                "known_usd": sum(x["usd"] or 0 for x in llm),
                "unknown_tokens": unknown_tokens, "unknown_usd": unknown_usd}

    def cumulative_resources(self, cid):
        """Cross-batch resource accounting. Unknown vendor usage stays unknown."""
        records = [dict(x) for x in self.db.execute(
            "SELECT c.input_tokens,c.output_tokens,c.usd,c.category,c.seconds "
            "FROM costs c JOIN campaign_runs r ON r.run_id=c.run_id "
            "WHERE r.campaign_id=?", (cid,))]
        llm = [r for r in records if r["category"] == "llm"]
        llm_calls = self.db.execute(
            "SELECT COALESCE(SUM(u.llm_calls),0) FROM runs u JOIN campaign_runs r "
            "ON u.id=r.run_id WHERE r.campaign_id=?", (cid,)).fetchone()[0]
        return {"llm_calls": llm_calls,
                "known_tokens": sum((r["input_tokens"] or 0)+(r["output_tokens"] or 0)
                                    for r in llm),
                "known_usd": sum(r["usd"] or 0.0 for r in llm),
                "unknown_token_calls": sum(r["input_tokens"] is None or
                                           r["output_tokens"] is None for r in llm),
                "unknown_usd_calls": sum(r["usd"] is None for r in llm),
                "measured_operation_seconds": sum(r["seconds"] for r in records),
                "total_simulations": self.cumulative(cid)}

    def external_duplicate(self, paths, context, fingerprint, expected_hashes=None):
        """No external writes, no cross-context reuse, and frozen source bytes."""
        for name in paths:
            file = Path(name).expanduser().resolve()
            if not file.is_file():
                raise ResearchError("跨库去重源消失，禁止继续：" + str(file))
            if Path(str(file)+"-wal").exists() or Path(str(file)+"-shm").exists():
                raise ResearchError("跨库数据库发生 WAL 活跃写入；停止去重")
            if expected_hashes is not None:
                expected = expected_hashes.get(name)
                if not isinstance(expected, str):
                    raise ResearchError("冻结外部库来源缺少文件摘要")
                hasher=hashlib.sha256()
                with file.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024*1024), b""):
                        hasher.update(chunk)
                if hasher.hexdigest() != expected:
                    raise ResearchError("外部库文件相对冻结计划发生改变："+name)
            try:
                uri = file.as_uri() + "?mode=ro"
                with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
                    db.execute("PRAGMA query_only=ON")
                    row = db.execute(
                        "SELECT t.id,q.state FROM trials t JOIN tasks q ON q.trial_id=t.id "
                        "WHERE t.context=? AND t.fingerprint=? LIMIT 1",
                        (context, fingerprint)).fetchone()
                    if row:
                        return {"library_sha256": digest(str(file)), "trial_id": row[0],
                                "state": row[1], "reusable": False}
            except sqlite3.DatabaseError as error:
                raise ResearchError("外部公式索引不可用；禁止忽略错误后重复测试") from error
        return None

    def audit_queue(self, cid):
        """Replay immutable transition facts and detect accidental direct SQL edits."""
        recorded = {}
        project_state = None
        project_reason = None
        project_created_count = 0
        # Both the persistent queue and the campaign status must be supported
        # by a valid append-only event history, not merely by mutable rows.
        for event in self.db.execute(
                "SELECT kind,payload FROM events WHERE kind IN "
                "('campaign_created','campaign_control') ORDER BY id"):
            entry = json.loads(event["payload"])
            if entry.get("campaign_id") != cid:
                continue
            if event["kind"] == "campaign_created":
                project_created_count += 1
                if project_created_count != 1:
                    raise ResearchError("campaign 重复建档事件")
                if entry.get("plan_hash") != self.get(cid)["plan_hash"]:
                    raise ResearchError("冻结计划与创建审计事件不符")
                project_state = entry["status"]
                project_reason = "created"
            else:
                if project_state is None or entry["from"] != project_state:
                    raise ResearchError("campaign 控制审计不连续")
                project_state = entry["to"]
                project_reason = entry["reason"]
        if (project_created_count != 1 or project_state != self.get(cid)["status"]
                or project_reason != self.get(cid)["reason"]):
            raise ResearchError("campaign 状态或原因未经审计直接篡改")
        for row in self.db.execute("SELECT kind,payload FROM events WHERE kind IN "
                                   "('campaign_idea','campaign_transition') ORDER BY id"):
            p = json.loads(row["payload"])
            if p.get("campaign_id") != cid:
                continue
            if row["kind"] == "campaign_idea":
                recorded[p["idea_id"]] = ("generated", "l1", None, "")
            else:
                old = recorded.get(p["idea_id"])
                if old is None or old[0] != p["from"]:
                    raise ResearchError("campaign 迁移历史不连续")
                recorded[p["idea_id"]] = (p["to"], p["phase"], p["trial_id"], p["reason"])
        observed = {}
        for row in self.db.execute("SELECT id,state,phase,trial_id,reason FROM campaign_queue WHERE campaign_id=?",
                                   (cid,)):
            observed[row["id"]] = (
                row["state"], row["phase"], row["trial_id"], row["reason"])
        if recorded != observed:
            raise ResearchError("campaign 队列被绕过事件链直接修改或删除")
        # Independently replay generation, batch and duplicate records. SQLite
        # triggers alone do NOT prevent a privileged operator from rewriting
        # arbitrary database bytes; external anchoring is still recommended.
        generations = {}
        runs = {}
        duplicates = {}
        for row in self.db.execute(
                "SELECT kind,payload FROM events WHERE kind IN "
                "('campaign_generation','campaign_execution','campaign_duplicate') "
                "ORDER BY id"):
            ev = json.loads(row["payload"])
            if ev.get("campaign_id") != cid:
                continue
            if row["kind"] == "campaign_generation":
                cycle = ev["cycle"]
                if ev["status"] == "started":
                    if cycle in generations:
                        raise ResearchError("重复的生成开始审计")
                elif generations.get(cycle) != "started":
                    raise ResearchError("生成结束没有对应开始事件")
                generations[cycle] = ev["status"]
            elif row["kind"] == "campaign_execution":
                run = ev["run_id"]
                if run in runs:
                    raise ResearchError("重复的 batch 审计")
                runs[run] = (ev["batch"], ev["limits"])
            else:
                attempt = ev["attempt_id"]
                if attempt in duplicates:
                    raise ResearchError("重复的 duplicate 审计")
                duplicates[attempt] = (ev["existing_idea_id"], ev["fingerprint"])
        db_generations = {
            x["cycle"]: x["status"] for x in self.db.execute(
                "SELECT cycle,status FROM campaign_generations WHERE campaign_id=?", (cid,))}
        if generations != db_generations:
            raise ResearchError("生成历史与当前 checkpoint 状态不一致")
        db_runs = {
            x["run_id"]: (x["batch_no"], json.loads(x["limits_json"]))
            for x in self.db.execute(
                "SELECT run_id,batch_no,limits_json FROM campaign_runs WHERE campaign_id=?", (cid,))}
        if runs != db_runs:
            raise ResearchError("执行批次记录与审计历史不一致")
        db_duplicates = {
            x["attempt_id"]: (x["existing_idea_id"],x["fingerprint"])
            for x in self.db.execute(
                "SELECT attempt_id,existing_idea_id,fingerprint FROM campaign_duplicates "
                "WHERE campaign_id=?", (cid,))}
        if duplicates != db_duplicates:
            raise ResearchError("重复假设尝试记录与审计不一致")
        return {"campaign_ideas": len(recorded),
                "campaign_batches":len(runs),
                "generation_cycles":len(generations),
                "duplicate_attempts":len(duplicates),
                "audit_chain": self.memory.verify()}

    def summary(self, cid):
        info = self.get(cid)
        items = self.rows(cid)
        counts = {s: sum(v["state"] == s for v in items) for s in sorted(STATES)}
        return {"campaign_id": cid, "status": info["status"], "reason": info["reason"],
                "plan_sha256": info["plan_hash"], "simulations": self.cumulative(cid),
                "cumulative_resources": self.cumulative_resources(cid),
                "counts": counts, "duplicate_attempts": self.db.execute(
                    "SELECT count(*) FROM campaign_duplicates WHERE campaign_id=?", (cid,)
                ).fetchone()[0],
                "total_generated_attempts": len(items)+self.db.execute(
                    "SELECT count(*) FROM campaign_duplicates WHERE campaign_id=?", (cid,)
                ).fetchone()[0],
                "queue": [
                    {"idea_id": x["id"], "state": x["state"], "phase": x["phase"],
                     "expression": x["idea"].get("expression"), "trial_id": x["trial_id"],
                     "reason": x["reason"], "parent_id": x["parent_id"]}
                    for x in items],
                "automatic_submit": False,
                "independent_holdout_validated": False,
                "empirical_alpha_discovery_validated": False}
