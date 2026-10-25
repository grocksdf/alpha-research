"""Immutable legacy fixed-study verification only; NO formula generation.

The published protocol checksum protects historical holdout evidence from
post-hoc formula substitution. It never emits or proposes a candidate.
"""
from .common import ResearchError, digest

LEGACY_DIRECTED_V1_CANDIDATE_SHA256 = "2ad2f67a1acba859b60e62f340ad95d82fc5f84e93e1ad8f387ce11f109e3d43"

def verify_historical_fixed_design(candidates):
    if digest(candidates) != LEGACY_DIRECTED_V1_CANDIDATE_SHA256:
        raise ResearchError("历史定向验证公式设计或比较对不一致；冻结证据不可更改")
    return True
