from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "project": {
        "name": "AlphaResearch",
        "database": "data/research.db",
        "program": "program.md",
    },
    "brain": {
        "base_url": "https://api.worldquantbrain.com",
        "timeout_seconds": 45,
        "poll_timeout_seconds": 900,
        "max_retries": 5,
        "simulation": {
            "instrumentType": "EQUITY",
            "region": "USA",
            "universe": "TOP3000",
            "delay": 1,
            "decay": 4,
            "neutralization": "SECTOR",
            "truncation": 0.08,
            "pasteurization": "ON",
            "unitHandling": "VERIFY",
            "nanHandling": "ON",
            "language": "FASTEXPR",
            "visualization": False,
        },
    },
    "research": {
        "model": "gpt-5-mini",
        "max_simulations_per_run": 15,
        "candidates_per_iteration": 5,
        "memory_limit": 20,
    },
    "validation": {
        "max_nodes": 80,
        "max_depth": 12,
        "max_duplicate_subtree": 3,
        "max_window": 756,
        "forbidden_operators": ["lead", "ts_lead", "future_return"],
    },
    "evaluation": {
        "min_sharpe": 1.25,
        "min_fitness": 1.0,
        "min_turnover": 0.01,
        "max_turnover": 0.70,
        "max_drawdown": 0.35,
        "min_years": 3,
        "min_positive_year_fraction": 0.60,
        "max_self_correlation": 0.70,
        "max_production_correlation": 0.70,
        "weights": {
            "sharpe": 0.25,
            "fitness": 0.20,
            "turnover": 0.10,
            "drawdown": 0.10,
            "year_stability": 0.20,
            "novelty": 0.15,
        },
    },
    "submission": {"automatic_submit": False, "human_approval_required": True},
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    selected = Path(path or os.getenv("ALPHARESEARCH_CONFIG", "config.yaml"))
    user: dict[str, Any] = {}
    if selected.exists():
        user = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
    cfg = _merge(DEFAULTS, user)
    cfg["brain"]["base_url"] = os.getenv("BRAIN_BASE_URL", cfg["brain"]["base_url"]).rstrip("/")
    cfg["research"]["model"] = os.getenv("ALPHARESEARCH_MODEL", cfg["research"]["model"])
    return cfg
