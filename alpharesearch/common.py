from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


class ResearchError(Exception):
    pass


def dumps(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def research_context(config):
    catalog=config['catalog_data']
    semantic={'account_id':catalog.get('account_id'),'source':catalog.get('source'),
        'operators':sorted(catalog['operators']),
        'fields':{k:{a:v.get(a) for a in ('kind','point_in_time','min_delay')} for k,v in catalog['fields'].items()}}
    return digest({'scope':config['scope'],'catalog':semantic,'mode':config['mode']})


def load_settings(path):
    path = Path(path).resolve()
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    # config/settings.toml -> 项目根；也允许放在根目录。
    root = path.parent.parent if path.parent.name == "config" else path.parent
    for key in ("database", "catalog", "evaluation", "program"):
        config[key] = str((root / config[key]).resolve())
    config["root"] = str(root)
    validation=config.setdefault('validation',{})
    validation['holdout_registry']=str((root/validation.get('holdout_registry','state/holdout-registry.sqlite3')).resolve())
    rules_text = Path(config["program"]).read_text(encoding="utf-8")
    match = re.search(r"```toml\s*\n(.*?)```", rules_text, re.S)
    if not match:
        raise ResearchError("program.md 缺少可执行 TOML 规则")
    rules = tomllib.loads(match.group(1))
    if rules.get("automatic_submit") is not False or rules.get("protect_final_holdout") is not True:
        raise ResearchError("V0.1 必须人工提交并保护留出集")
    for key in ("max_simulations_per_run", "max_proposals_per_run", "max_llm_calls_per_run"):
        if type(rules.get(key)) is not int or rules[key] <= 0:
            raise ResearchError(f"无效预算: {key}")
    if config["scope"].get("delay", -1) < 1:
        raise ResearchError("V0.1 仅支持 delay >= 1")
    if config["mode"] not in ("demo", "live"):
        raise ResearchError("mode 必须为 demo/live")
    config["rules"] = rules
    config["policy"] = tomllib.loads(Path(config["evaluation"]).read_text(encoding="utf-8"))
    policy=config['policy']
    for name,minimum in (('stability_window_sessions',2),('min_stability_windows',2),('stability_annualization',1)):
        if type(policy.get(name)) is not int or policy[name]<minimum:raise ResearchError('缺少/无效的固定窗口策略 '+name+'；旧年度策略须显式迁移')
    for name in ('min_positive_window_fraction','min_window_sharpe'):
        if type(policy.get(name)) not in (int,float) or not math.isfinite(policy[name]):raise ResearchError('稳定性阈值必须为有限数字 '+name)
    if policy['stability_annualization']>366 or not 0<=policy['min_positive_window_fraction']<=1:raise ResearchError('稳定性频率/正窗口比例无效')
    config["catalog_data"] = json.loads(Path(config["catalog"]).read_text(encoding="utf-8"))
    config["program_text"] = rules_text
    return config


def snapshot(config):
    code_dir = Path(__file__).parent
    code = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(code_dir.glob("*.py"))}
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config["root"], stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "uncommitted"
    # 不读取或保存环境变量或认证信息。
    return {"code": code, "git_commit": commit, "runtime": {"python": sys.version, "sqlite": sqlite3.sqlite_version}, "scope": config["scope"],
            "policy": config["policy"], "rules": config["rules"],
            "program_hash": digest(config["program_text"]), "catalog": config["catalog_data"],
            "brain": config["brain"], "mode": config["mode"],
            'safety':config.get('safety',{}),'research':config.get('research',{}),'cost':config.get('cost',{}),
            "account_profile_verified": config["account_profile_verified"],
            "account_profile_note": config.get("account_profile_note", ""), "llm": config["llm"]}
