"""Offline-only Live policy validation and fail-closed per-account rate governor.

This module deliberately has NO HTTP, BrainSimulator or Submit imports. Passing
its policy checks is NOT account entitlement. The active campaign runner still
rejects live execution; user-authorized BRAIN integration requires a separate
release, account inspection and audited approval.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .common import ResearchError,digest,now

def validate_live_policy(config,manifest,*,account_ref=None):
    """Validate a source-attributed operator-reviewed policy; do not authorize POST."""
    if config["mode"]!="live":
        raise ResearchError("仅用于真实研究模式的离线策略验收")
    safety=config.get("safety",{})
    if safety.get("submission_enabled") is not False:
        raise ResearchError("campaign 禁止任何自动 Submit 能力")
    if safety.get("live_research_enabled") is not True:
        raise ResearchError("真实研究授权开关关闭")
    catalog=config["catalog_data"]
    if (catalog.get("source")!="brain-account-api" or
        not isinstance(catalog.get("account_id"),str) or
        not catalog["account_id"]):
        raise ResearchError("缺少账户绑定字段目录")
    known_account=digest(str(catalog["account_id"]))
    if account_ref!=known_account:
        raise ResearchError("已认证账户身份与冻结字段目录不符")
    if not isinstance(manifest,dict) or set(manifest)!={
            "protocol","account_ref","scope","approved_max_concurrent",
            "approved_requests_per_minute","observed_at","expires_at",
            "source","reviewed_by","review_note","review_source_sha256"}:
        raise ResearchError("真实限流策略缺少全部审核字段")
    if manifest["protocol"]!="brain-research-rate-policy-v1" or manifest["account_ref"]!=known_account:
        raise ResearchError("真实请求限制策略与账户不一致")
    expected_scope={k:config["scope"][k] for k in (
        "instrumentType","region","universe","delay")}
    if manifest["scope"] != expected_scope:
        raise ResearchError("真实限流策略与研究 scope 不一致")
    if manifest["source"] not in ("account_observation","platform_documentation"):
        raise ResearchError("限流来源必须可归属")
    for name in ("reviewed_by","review_note"):
        if not isinstance(manifest[name],str) or not manifest[name].strip():
            raise ResearchError("缺少账户限流人工审核记录："+name)
    if not re.fullmatch(r"[0-9a-f]{64}",str(manifest["review_source_sha256"])):
        raise ResearchError("限流来源缺少原始审核凭据 SHA256")
    concurrent=manifest["approved_max_concurrent"]
    per_minute=manifest["approved_requests_per_minute"]
    if type(concurrent) is not int or concurrent!=1:
        raise ResearchError("尚无实际平台并发证明时只允许最多一个在途模拟")
    if type(per_minute) is not int or not 1<=per_minute<=120:
        raise ResearchError("请求频率须基于审核资料明确给出；不得猜测")
    # Strict UTC ISO dates, no future-dated observations or expired permissions.
    from datetime import datetime,timezone
    try:
        observed=datetime.fromisoformat(manifest["observed_at"])
        expires=datetime.fromisoformat(manifest["expires_at"])
        if observed.tzinfo is None or expires.tzinfo is None:
            raise ValueError("timezone missing")
        current=datetime.now(timezone.utc)
        if not observed<=current<expires or (expires-observed).days>90:
            raise ValueError("stale policy")
    except (TypeError,ValueError,OverflowError) as error:
        raise ResearchError("真实限流观察过期、未来日期或缺少时区") from error
    # Operational smoke receipt remains an extra local requirement, but is NOT
    # an entitlement attestation; this function does not read provider secrets.
    from .probe import require_smoke_receipt
    require_smoke_receipt(config,catalog["account_id"])
    return {"status":"offline_policy_preflight_pass",
            "actual_platform_quotas_verified_by_this_run":False,
            "live_simulation_authorized_by_this_function":False,
            "automatic_submit":False,
            "rate_policy_sha256":digest(manifest),
            "max_concurrent":1,"requests_per_minute":per_minute}

class OfflineRateGovernor:
    """Durable 60-second fixed window and single in-flight slot.

    The caller MUST hold the existing global worker lock. Reservations never
    expire automatically. A lost HTTP response keeps the slot occupied until
    identity-checked read-only reconciliation. No retries or remote operations.
    """
    def __init__(self,memory):
        self.memory=memory
        self.db=memory.db
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS campaign_rate_reservations(
              operation_id TEXT PRIMARY KEY,account_ref TEXT NOT NULL,
              rate_policy_hash TEXT NOT NULL,reserved_at REAL NOT NULL,
              state TEXT NOT NULL,updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS campaign_rate_requests(
              id INTEGER PRIMARY KEY,account_ref TEXT NOT NULL,
              operation_id TEXT NOT NULL,created_epoch REAL NOT NULL,
              rate_policy_hash TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS campaign_rate_window ON
              campaign_rate_requests(account_ref,created_epoch);
            CREATE TRIGGER IF NOT EXISTS campaign_rate_no_delete BEFORE DELETE
              ON campaign_rate_reservations
              BEGIN SELECT RAISE(ABORT,'immutable remote reservation'); END;
            CREATE TRIGGER IF NOT EXISTS campaign_rate_request_no_update
              BEFORE UPDATE ON campaign_rate_requests
              BEGIN SELECT RAISE(ABORT,'immutable rate request'); END;
            CREATE TRIGGER IF NOT EXISTS campaign_rate_request_no_delete
              BEFORE DELETE ON campaign_rate_requests
              BEGIN SELECT RAISE(ABORT,'immutable rate request'); END;
        """)

    def reserve(self,account_ref,operation_id,policy_hash,requests_per_minute,epoch=None):
        if not re.fullmatch(r"[0-9a-f]{64}",str(account_ref)):
            raise ResearchError("无效的限流账户标识")
        if not isinstance(operation_id,str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}",operation_id):
            raise ResearchError("无效远程操作标识")
        if type(requests_per_minute)is not int or not 1<=requests_per_minute<=120:
            raise ResearchError("限流策略没有合规上限")
        if not re.fullmatch(r"[0-9a-f]{64}",str(policy_hash)):
            raise ResearchError("缺少冻结限流策略摘要")
        epoch=time.time() if epoch is None else epoch
        if type(epoch) not in (int,float) or epoch < 0:
            raise ResearchError("时钟无效")
        def write():
            if self.db.execute(
                    "SELECT 1 FROM campaign_rate_reservations WHERE operation_id=?",
                    (operation_id,)).fetchone():
                raise ResearchError("相同操作 ID 已预留，禁止再次发起 POST")
            if self.db.execute(
                    "SELECT 1 FROM campaign_rate_reservations WHERE account_ref=? "
                    "AND state IN ('reserved','uncertain','polling')",
                    (account_ref,)).fetchone():
                raise ResearchError("同账户有未解决的远程任务")
            n=self.db.execute(
                    "SELECT count(*) FROM campaign_rate_requests "
                    "WHERE account_ref=? AND created_epoch>? AND created_epoch<=?",
                    (account_ref,epoch-60,epoch)).fetchone()[0]
            if n>=requests_per_minute:
                raise ResearchError("平台请求率限额已用尽；不得自动重发")
            self.db.execute("INSERT INTO campaign_rate_reservations VALUES(?,?,?,?,?,?)",
                            (operation_id,account_ref,policy_hash,epoch,"reserved",now()))
            self.db.execute("INSERT INTO campaign_rate_requests("
                "account_ref,operation_id,created_epoch,rate_policy_hash) VALUES(?,?,?,?)",
                (account_ref,operation_id,epoch,policy_hash))
            self.memory.event(None,"campaign_live_rate_reserved",{
                "account_ref":account_ref,"operation_id":operation_id,
                "rate_policy_sha256":policy_hash})
        self.memory.transaction(write)
        return {"operation_id":operation_id,"status":"reserved",
                "remote_simulation_created":False}

    def mark_uncertain(self,operation_id):
        def write():
            row=self.db.execute(
                "SELECT account_ref,state FROM campaign_rate_reservations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if not row or row["state"]!="reserved":
                raise ResearchError("只有预留请求可转不确定状态")
            self.db.execute("UPDATE campaign_rate_reservations SET state='uncertain',updated=? "
                            "WHERE operation_id=?", (now(),operation_id))
            self.memory.event(None,"campaign_live_rate_state",
                              {"operation_id":operation_id,"state":"uncertain"})
        self.memory.transaction(write)

    def reconcile(self,operation_id,trial_id):
        """Release only after ORIGINAL Engine has persisted verified live result.

        Cannot be unlocked using a caller-supplied JSON assertion. Fail closed
        unless remote_result_complete() and account/source checks already pass.
        """
        if operation_id != trial_id:
            raise ResearchError("远程操作 ID 必须对应唯一原实验 ID")
        row=self.db.execute("SELECT account_ref,state FROM campaign_rate_reservations "
                            "WHERE operation_id=?",(operation_id,)).fetchone()
        if not row or row["state"] not in ("reserved","uncertain","polling"):
            raise ResearchError("远程操作未占用或已终结")
        trial=self.memory.trial(trial_id)
        payload=self.memory.latest(trial_id)
        snap=json.loads(self.memory.run(trial["run_id"])["snapshot"])
        from .memory import remote_result_complete
        source=payload.get("evidence_source",{}) if isinstance(payload,dict) else {}
        if (snap.get("mode")!="live" or trial["state"]!="completed" or
                trial.get("remote_state")!="completed" or
                trial.get("account_ref")!=row["account_ref"] or
                source.get("transport")!="account_http" or
                source.get("account_ref")!=row["account_ref"] or
                not remote_result_complete(trial,payload,snap)):
            raise ResearchError("只有已保存且核实账户/公式/设置的真实远程终态可释放并发占位")
        result_events=self.db.execute(
            "SELECT payload FROM events WHERE kind='result_recorded' AND run_id=?",
            (trial["run_id"],)).fetchall()
        if not any(json.loads(x[0]).get("trial_id")==trial_id for x in result_events):
            raise ResearchError("缺少原 Run 的不可覆盖结果审计")
        def write():
            self.db.execute("UPDATE campaign_rate_reservations SET state='terminal',updated=? "
                            "WHERE operation_id=?", (now(),operation_id))
            self.memory.event(None,"campaign_live_rate_reconciled",{
                "operation_id":operation_id,"trial_id":trial_id,
                "evidence_sha256":digest(payload),
                "source":"persisted_account_bound_verified_result"})
        self.memory.transaction(write)
