"""AlphaResearch test-only deterministic research fixtures.

This module is *not* a Live discovery implementation and is imported only
from guarded Demo code paths. It resides at package root so the existing
common.snapshot() glob('*.py') freezes its SHA alongside research code.
"""
from __future__ import annotations

class DemoDiscovery:
    """Deterministic fixtures to exercise the loop. This is never an AI baseline result."""
    def generate(self, plan, feedback, cycle):
        seeds = [('-rank(ts_delta(close, 5) / (ts_std_dev(returns, 20) + 0.01))', 'reversion'),
                 ('-rank(ts_corr(close, volume, 10))', 'liquidity'),
                 ('rank(ts_mean(returns, 60))', 'momentum'),
                 ('-rank(ts_std_dev(returns, 30))', 'volatility'),
                 ('-rank((vwap - close) / close)', 'reversion'),
                 ('rank(ts_delta(volume, 20) / (ts_mean(volume, 60) + 1))', 'liquidity')]
        start = (cycle-1)*plan['ideas_per_cycle']
        return [{'title': '合成流程样例 '+str(i+1), 'domain': 'price_volume',
                 'mechanism': '仅检验工程闭环；此固定模板不是 AI 研究结果。', 'expected_sign': 'conditional',
                 'horizon_days': 5, 'falsification': '在独立研究数据中无法复现时放弃；当前只运行合成数据。',
                 'expression': expr, 'source': 'DEMO deterministic fixture; no empirical evidence',
                 'family': family, 'parent_idea_id': None, 'change': '独立模板样例'}
                for i, (expr, family) in enumerate(seeds[start:start+plan['ideas_per_cycle']], start)]

from alpharesearch.discovery import approved_fields

class CampaignDemoDiscovery:
    """Diverse deterministic engineering fixtures; no empirical Alpha claims."""
    _forms = [
        ('rank(ts_mean(returns,{w}))', 'momentum'),
        ('-rank(ts_delta(close,{w}))', 'reversion'),
        ('-rank(ts_std_dev(returns,{w}))', 'volatility'),
        ('-rank(ts_corr(close,volume,{w}))', 'liquidity'),
        ('rank(ts_rank(volume,{w}))', 'momentum'),
        ('-rank(ts_zscore(close,{w}))', 'reversion'),
        ('rank(ts_sum(returns,{w}))', 'momentum'),
        ('-rank(ts_decay_linear(volume,{w}))', 'liquidity'),
    ]

    def __init__(self, config):
        self.config = config

    def generate(self, plan, feedback, cycle):
        approved = approved_fields(self.config)
        if 'price_volume' not in plan['domains'] or not all(
                name in approved for name in ('returns', 'close', 'volume')):
            return []
        maximum = self.config['rules']['max_window']
        count = plan['ideas_per_cycle']
        items = []
        start = (cycle - 1) * count
        for index in range(start, start + count):
            form, family = self._forms[index % len(self._forms)]
            window = 5 + index // len(self._forms)
            if window > maximum:
                break
            items.append({
                'title': 'Campaign synthetic fixture %d' % (index + 1),
                'domain': 'price_volume', 'mechanism': 'Only an offline scheduling fixture',
                'expected_sign': 'conditional', 'horizon_days': 5,
                'falsification': 'Independent empirical validation not performed',
                'expression': form.format(w=window),
                'source': 'DEMO synthetic fixture; not market evidence',
                'family': family, 'parent_idea_id': None,
                'change': 'Independent deterministic test formula'})
        return items
