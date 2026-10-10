"""Empirical edge evidence from matured, prospective 48-hour outcomes.

A bucket is (strategy, side, asset class). Only outcomes of *fresh* signals
(the bar where a strategy's target changed, which is what the team may trade)
are counted, and only one non-overlapping 48h sample per bucket and symbol.
Every sample matured strictly before it is used, so the estimate is causal.

Support requires a minimum sample count and a positive one-sided lower bound on
the mean return after the *current* round-trip cost estimate, with a
Bonferroni-adjusted z across all buckets tested. This is a transparent
statistical screen, not a guarantee: samples across BTC/ETH/SOL are correlated,
regimes change, and the normal approximation is crude for fat-tailed returns.
"""
from __future__ import annotations

from datetime import datetime
import math
from statistics import NormalDist
from typing import Any, Mapping

from .intraday import STRATEGY_IDS

SIDES = ('long', 'short')
FAMILY_ALPHA = .05


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def bucket_key(strategy_id: str, side: str, asset_class: str) -> str:
    return f'{strategy_id}:{side}:{asset_class}'


class EdgeBook:
    def __init__(self, minimum_samples: int = 30, window: int = 120):
        if minimum_samples < 20 or not minimum_samples <= window <= 1000:
            raise ValueError('Edge evidence needs at least 20 samples and a window >= minimum')
        self.minimum_samples = minimum_samples
        self.window = window
        self.buckets: dict[str, dict[str, Any]] = {}

    def _bucket(self, key: str) -> dict[str, Any]:
        return self.buckets.setdefault(key, dict(samples=[], last_end={}, seen=0))

    def record(self, outcome: Mapping[str, Any], asset_class: str) -> bool:
        """Count a matured outcome if it is fresh and does not overlap the bucket/symbol's previous sample."""
        if not outcome.get('fresh') or outcome.get('side') not in SIDES or outcome.get('strategy_id') not in STRATEGY_IDS:
            return False
        bucket = self._bucket(bucket_key(outcome['strategy_id'], outcome['side'], asset_class))
        bucket['seen'] += 1
        symbol = outcome['symbol']
        previous_end = bucket['last_end'].get(symbol)
        if previous_end and _time(outcome['observed_at']) < _time(previous_end):
            return False
        bucket['last_end'][symbol] = outcome['label_at']
        bucket['samples'].append(dict(symbol=symbol, observed_at=outcome['observed_at'], label_at=outcome['label_at'],
                                      gross_bps=float(outcome['gross_return']) * 10000))
        del bucket['samples'][:-self.window]
        return True

    def tested_buckets(self) -> int:
        return max(1, sum(1 for b in self.buckets.values() if len(b['samples']) >= self.minimum_samples))

    def estimate(self, strategy_id: str, side: str, asset_class: str, cost_bps: float) -> dict[str, Any]:
        key = bucket_key(strategy_id, side, asset_class)
        samples = [s['gross_bps'] for s in self.buckets.get(key, {}).get('samples', [])]
        n = len(samples)
        tests = self.tested_buckets()
        z = NormalDist().inv_cdf(1 - FAMILY_ALPHA / tests)
        result = dict(bucket=key, samples=n, minimum_samples=self.minimum_samples, window=self.window,
                      roundtrip_cost_bps=cost_bps, tested_buckets=tests, z=z,
                      mean_gross_bps=None, sd_bps=None, mean_net_bps=None, lower_net_bps=None, supported=False,
                      source='prospective matured fresh-signal 48h outcomes, one non-overlapping sample per bucket and symbol')
        if n == 0:
            result['reason'] = 'No matured fresh-signal outcomes for this bucket yet.'
            return result
        mean = sum(samples) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in samples) / (n - 1)) if n > 1 else None
        net = mean - cost_bps
        lower = net - z * sd / math.sqrt(n) if sd is not None else None
        result.update(mean_gross_bps=mean, sd_bps=sd, mean_net_bps=net, lower_net_bps=lower)
        if n < self.minimum_samples:
            result['reason'] = f'Only {n} of {self.minimum_samples} required non-overlapping samples.'
        elif lower is None or lower <= 0:
            result['reason'] = f'Lower bound of mean net return ({lower:.1f} bps) is not positive after costs and multiple-testing adjustment.'
        else:
            result.update(supported=True, reason=f'{n} samples; adjusted lower bound {lower:.1f} bps > 0 after {cost_bps:.1f} bps costs.')
        return result

    def summary(self, costs_by_asset: Mapping[str, float]) -> list[dict[str, Any]]:
        rows = []
        for asset_class, cost in costs_by_asset.items():
            for strategy in STRATEGY_IDS:
                for side in SIDES:
                    estimate = self.estimate(strategy, side, asset_class, cost)
                    estimate.update(strategy_id=strategy, side=side, asset_class=asset_class,
                                    outcomes_seen=self.buckets.get(estimate['bucket'], {}).get('seen', 0))
                    rows.append(estimate)
        return rows

    def to_state(self) -> dict[str, Any]:
        return dict(minimum_samples=self.minimum_samples, window=self.window, buckets=self.buckets)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> 'EdgeBook':
        book = cls(int(state['minimum_samples']), int(state['window']))
        book.buckets = {k: dict(samples=list(v['samples']), last_end=dict(v['last_end']), seen=int(v.get('seen', 0)))
                        for k, v in state['buckets'].items()}
        for bucket in book.buckets.values():
            if any(not math.isfinite(float(s['gross_bps'])) for s in bucket['samples']):
                raise ValueError('Invalid edge checkpoint')
        return book
