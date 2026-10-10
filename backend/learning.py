"""Bounded, past-only research learning. Frozen champions never learn in place."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
import uuid

import numpy as np

FEATURE_NAMES = ('signal_confidence', 'is_short', 'stop_distance_pct',
                 'momentum_48h', 'volatility_48h', 'volume_zscore',
                 'sma', 'rsi', 'bollinger', 'atr_breakout', 'heikin')
STRATEGIES = FEATURE_NAMES[-5:]
DIM = len(FEATURE_NAMES)


def _time(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('Timestamps require a UTC offset')
    return dt.astimezone(timezone.utc)


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{name} must be finite')
    return number


def _features(proposal: Mapping[str, Any], features: Mapping[str, float] | None) -> np.ndarray:
    if proposal.get('side') not in ('long', 'short'):
        raise ValueError('side must be long or short')
    if proposal.get('strategy_id') not in STRATEGIES:
        raise ValueError('Unknown strategy_id')
    if not isinstance(proposal.get('symbol'), str) or not proposal['symbol'].strip():
        raise ValueError('symbol is required')
    confidence = _finite(proposal['confidence'], 'confidence')
    stop = _finite(proposal['stop_distance_pct'], 'stop_distance_pct')
    entry = _finite(proposal['entry_reference_price'], 'entry_reference_price')
    if not 0 <= confidence <= 1 or not 0 < stop <= 1 or entry <= 0:
        raise ValueError('confidence must be 0..1; stop fraction and entry price must be positive')
    _time(proposal['observed_at'])
    extra = features or {}
    if set(extra) - set(FEATURE_NAMES[3:6]):
        raise ValueError('Only momentum_48h, volatility_48h and volume_zscore extra features are accepted')
    return np.asarray([confidence, float(proposal['side'] == 'short'), stop,
                       *[_finite(extra.get(k, 0), k) for k in FEATURE_NAMES[3:6]],
                       *[float(proposal['strategy_id'] == k) for k in STRATEGIES]], dtype=float)


@dataclass
class PastNormalizer:
    count: int = 0
    mean: np.ndarray = field(default_factory=lambda: np.zeros(DIM))
    m2: np.ndarray = field(default_factory=lambda: np.zeros(DIM))

    def transform(self, values: np.ndarray) -> np.ndarray:
        # The current row is never incorporated before its feature transform.
        scale = np.sqrt(np.maximum(self.m2 / max(1, self.count - 1), .01)) if self.count > 1 else np.ones(DIM)
        return np.clip((values - self.mean) / scale, -5, 5)

    def update(self, values: np.ndarray) -> None:
        self.count += 1
        delta = values - self.mean
        self.mean += delta / self.count
        self.m2 += delta * (values - self.mean)


@dataclass
class LogisticModel:
    weights: np.ndarray = field(default_factory=lambda: np.zeros(DIM))
    bias: float = 0.
    updates: int = 0

    def probability(self, features: np.ndarray) -> float:
        logit = float(np.clip(self.weights @ features + self.bias, -30, 30))
        return 1. / (1. + math.exp(-logit))

    def train(self, features: np.ndarray, label: int) -> None:
        error = self.probability(features) - label
        rate = .05 / math.sqrt(1 + self.updates / 100)
        self.weights = np.clip(self.weights - rate * (error * features + .001 * self.weights), -5, 5)
        self.bias = float(np.clip(self.bias - rate * error, -5, 5))
        self.updates += 1

    def state(self) -> dict[str, Any]:
        return {'weights': self.weights.tolist(), 'bias': self.bias, 'updates': self.updates}

    @classmethod
    def restore(cls, state: Mapping[str, Any]) -> 'LogisticModel':
        weights = np.asarray(state['weights'], dtype=float)
        bias, updates = _finite(state['bias'], 'bias'), int(state['updates'])
        if weights.shape != (DIM,) or not np.isfinite(weights).all() or updates < 0:
            raise ValueError('Invalid model checkpoint')
        return cls(weights.copy(), bias, updates)


class QuantResearcherAgent:
    """Online logistic researcher, not an LLM and not a broker executor.

    Probability means a model estimate of positive signed 48h return after costs,
    not a calibrated success guarantee. The champion and its normalizer are
    frozen. Only a trainer learns; explicitly staged challengers are also frozen.
    """
    def __init__(self, horizon_hours: int = 48, minimum_training_samples: int = 100,
                 minimum_shadow_samples: int = 60, minimum_shadow_days: int = 60,
                 improvement_margin: float = .0005, decision_threshold: float = .55):
        if horizon_hours != 48:
            raise ValueError('This research profile requires a 48h horizon')
        if minimum_training_samples < 100 or minimum_shadow_samples < 60 or minimum_shadow_days < 60:
            raise ValueError('Promotion floors: 100 training labels, 60 shadow samples and 60 days')
        if not 0 < improvement_margin < 1 or not .5 < decision_threshold < 1:
            raise ValueError('Invalid promotion margin or decision threshold')
        self.horizon_hours = horizon_hours
        self.minimum_training_samples = minimum_training_samples
        self.minimum_shadow_samples = minimum_shadow_samples
        self.minimum_shadow_days = minimum_shadow_days
        self.improvement_margin = improvement_margin
        self.decision_threshold = decision_threshold
        self.normalizer = PastNormalizer()
        self.trainer = LogisticModel()
        self.champion = LogisticModel()
        self.champion_normalizer = PastNormalizer()
        self.champion_version = 0
        self.challenger: LogisticModel | None = None
        self.challenger_normalizer: PastNormalizer | None = None
        self.shadow_started_at: str | None = None
        self.shadow_epoch = 0
        self.shadow_records: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.last_observation: dict[str, str] = {}
        self.last_quote: dict[str, str] = {}
        self.last_processed_at: str | None = None
        self.last_independent_end: str | None = None
        self.drift_flag = False
        self.drift_reasons: list[str] = []
        self.recent_labels: deque[int] = deque(maxlen=40)
        self.audit: list[dict[str, Any]] = []

    def evaluate(self, proposal: Mapping[str, Any], features: Mapping[str, float] | None = None) -> dict[str, Any]:
        raw = _features(proposal, features)
        probability = self.champion.probability(self.champion_normalizer.transform(raw))
        confidence = min(1., self.champion.updates / 200) * abs(probability - .5) * 2
        evidence = [f'Frozen champion v{self.champion_version}; {self.champion.updates} matured training labels.',
                    'Estimated probability concerns positive signed return after configured costs at the 48h label; stops are not modeled.',
                    'Confidence is a sample-size/margin heuristic, not a calibrated probability.',
                    'No LLM credentials are configured; this is bounded online logistic learning.']
        if self.champion_version == 0:
            evidence.append('Unpromoted neutral champion: research only; no learned model is deployed.')
        if not features:
            evidence.append('Market feature inputs absent; optional features are zero. No market history was inferred.')
        if self.drift_flag:
            evidence.append('Drift flag is active; challenger promotion is paused.')
        return dict(agent='quant_researcher', probability=probability, confidence=confidence,
                    model_version=self.champion_version, drift_flag=self.drift_flag,
                    eligible=False, edge_supported=False, eligible_for_live=False,
                    reasons=['Positive expected net return has not been independently calibrated; success probability alone is insufficient.'],
                    evidence=evidence)

    def observe(self, proposal: Mapping[str, Any], features: Mapping[str, float] | None = None) -> str:
        raw = _features(proposal, features)
        observed = _time(proposal['observed_at'])
        symbol = proposal['symbol']
        if self.last_processed_at and observed < _time(self.last_processed_at):
            raise ValueError('Shared model input clock cannot move backwards across symbols')
        previous = self.last_observation.get(symbol)
        latest_quote = self.last_quote.get(symbol)
        if (previous and observed < _time(previous)) or (latest_quote and observed < _time(latest_quote)):
            raise ValueError('Observations must not precede previously processed data for the symbol')
        key = (symbol, proposal['strategy_id'], proposal['side'], observed.isoformat())
        if any(tuple(p['key']) == key for p in self.pending):
            raise ValueError('Duplicate pending proposal')
        transformed = self.normalizer.transform(raw)
        if self.normalizer.count >= 100 and np.mean(np.abs(transformed) >= 5) > .25:
            self._flag_drift('Large feature deviation from past-only training distribution')
        identifier = str(uuid.uuid4())
        self.pending.append(dict(id=identifier, key=list(key), symbol=symbol, side=proposal['side'],
            strategy_id=proposal['strategy_id'], fresh=bool(proposal.get('fresh_signal', False)),
            observed_at=observed.isoformat(), matures_at=(observed + timedelta(hours=48)).isoformat(),
            entry_price=float(proposal['entry_reference_price']), features=transformed.tolist(),
            champion_probability=self.champion.probability(self.champion_normalizer.transform(raw)),
            challenger_probability=self.challenger.probability(self.challenger_normalizer.transform(raw)) if self.challenger else None,
            shadow_epoch=self.shadow_epoch))
        self.normalizer.update(raw)
        self.last_observation[symbol] = observed.isoformat()
        self.last_processed_at = observed.isoformat()
        return identifier

    def reveal(self, symbol: str, observed_at: str | datetime, price: float,
               round_trip_cost_bps: float) -> list[dict[str, Any]]:
        now, price = _time(observed_at), _finite(price, 'price')
        costs = _finite(round_trip_cost_bps, 'round_trip_cost_bps')
        if self.last_processed_at and now < _time(self.last_processed_at):
            raise ValueError('Shared model outcome clock cannot move backwards across symbols')
        if price <= 0 or not 0 <= costs <= 10000:
            raise ValueError('Positive price and nonnegative total costs up to 10000bps required')
        if symbol in self.last_quote and now <= _time(self.last_quote[symbol]):
            raise ValueError('Outcome quotes must be strictly increasing for each symbol')
        if symbol in self.last_observation and now < _time(self.last_observation[symbol]):
            raise ValueError('Outcome quote cannot precede an already processed observation')
        self.last_quote[symbol] = now.isoformat()
        self.last_processed_at = now.isoformat()
        outcomes, remaining = [], []
        for record in self.pending:
            if record['symbol'] != symbol or now < _time(record['matures_at']):
                remaining.append(record)
                continue
            signed_return = (price / record['entry_price'] - 1) * (1 if record['side'] == 'long' else -1)
            net_return = signed_return - costs / 10000
            label = int(net_return > 0)
            # Predictions were stored at observation, before either label or update.
            outcome = dict(id=record['id'], observed_at=record['observed_at'], label_at=now.isoformat(),
                           holding_hours=(now - _time(record['observed_at'])).total_seconds() / 3600,
                           symbol=symbol, strategy_id=record.get('strategy_id', record['key'][1]),
                           side=record['side'], fresh=record.get('fresh', False),
                           gross_return=signed_return, net_return=net_return, cost_bps=costs, label=label)
            self.trainer.train(np.asarray(record['features']), label)
            self.recent_labels.append(label)
            if len(self.recent_labels) == 40:
                labels = list(self.recent_labels)
                if abs(np.mean(labels[:20]) - np.mean(labels[20:])) >= .4:
                    self._flag_drift('Recent 20-label success rate changed by at least 40 percentage points')
            # Conservative global non-overlap, across all symbols/strategies.
            independent = self.last_independent_end is None or _time(record['observed_at']) >= _time(self.last_independent_end)
            if (record['challenger_probability'] is not None and record['shadow_epoch'] == self.shadow_epoch
                    and self.shadow_started_at and _time(record['observed_at']) >= _time(self.shadow_started_at) and independent):
                self.shadow_records.append(dict(**outcome,
                    champion_return=net_return if record['champion_probability'] >= self.decision_threshold else 0.,
                    challenger_return=net_return if record['challenger_probability'] >= self.decision_threshold else 0.,
                    champion_probability=record['champion_probability'], challenger_probability=record['challenger_probability']))
                self.last_independent_end = now.isoformat()
            outcomes.append(outcome)
        self.pending = remaining
        return outcomes

    def _flag_drift(self, reason: str) -> None:
        self.drift_flag = True
        if reason not in self.drift_reasons:
            self.drift_reasons.append(reason)

    def start_shadow(self, observed_at: str | datetime, *, acknowledge_drift: bool = False) -> dict[str, Any]:
        now = _time(observed_at)
        if self.trainer.updates < self.minimum_training_samples:
            raise ValueError('Need at least 100 matured training labels before staging a challenger')
        latest = list(self.last_observation.values()) + list(self.last_quote.values())
        if latest and now < max(_time(t) for t in latest):
            raise ValueError('Shadow start must not be backdated')
        if self.drift_flag and not acknowledge_drift:
            raise ValueError('Review drift and explicitly acknowledge before a new shadow epoch')
        if acknowledge_drift:
            self.drift_flag, self.drift_reasons = False, []
            self.recent_labels.clear()
        self.challenger = LogisticModel.restore(self.trainer.state())
        self.challenger_normalizer = PastNormalizer(self.normalizer.count, self.normalizer.mean.copy(), self.normalizer.m2.copy())
        self.shadow_started_at = now.isoformat()
        self.shadow_epoch += 1
        self.shadow_records, self.last_independent_end = [], None
        event = dict(action='stage_frozen_challenger', at=now.isoformat(), shadow_epoch=self.shadow_epoch)
        self.audit.append(event)
        return event

    def promotion_gate(self) -> dict[str, Any]:
        records, failures = self.shadow_records, []
        if self.challenger is None:
            failures.append('No frozen challenger staged')
        if self.drift_flag:
            failures.append('Drift flag pauses promotion')
        if len(records) < self.minimum_shadow_samples:
            failures.append(f'Need {self.minimum_shadow_samples} nonoverlapping prospective samples')
        days = ((_time(records[-1]['label_at']) - _time(records[0]['observed_at'])).total_seconds() / 86400) if records else 0.
        if days < self.minimum_shadow_days:
            failures.append(f'Need {self.minimum_shadow_days} days of prospective shadow observations')
        challenger = np.asarray([r['challenger_return'] for r in records], dtype=float)
        champion = np.asarray([r['champion_return'] for r in records], dtype=float)
        trades = int(np.count_nonzero([r['challenger_probability'] >= self.decision_threshold for r in records]))
        if trades < 30:
            failures.append('Need at least 30 cost-adjusted challenger shadow trades')
        mean = float(challenger.mean()) if len(records) else 0.
        advantage = float((challenger - champion).mean()) if len(records) else 0.
        if mean <= 0:
            failures.append('Challenger mean net return must be positive after supplied total costs')
        if advantage < self.improvement_margin:
            failures.append('Challenger mean improvement below preset margin')
        if len(records) > 1:
            differences = challenger - champion
            lower = advantage - 1.96 * float(differences.std(ddof=1)) / math.sqrt(len(records))
        else:
            lower = -1.
        if lower <= 0:
            failures.append('Approximate 95% mean-improvement lower bound must be positive')
        return dict(eligible=not failures, reasons=failures, samples=len(records), shadow_days=days,
                    challenger_trades=trades, mean_net_return=mean, mean_improvement=advantage,
                    approximate_improvement_lower_bound=lower, mode='research_paper_only')

    def promote(self, observed_at: str | datetime, *, reviewed: bool = False) -> dict[str, Any]:
        gate = self.promotion_gate()
        if not reviewed or not gate['eligible']:
            raise ValueError('Promotion requires explicit reviewed=True and all prospective gates passed')
        now = _time(observed_at)
        latest = list(self.last_observation.values()) + list(self.last_quote.values())
        if latest and now < max(_time(t) for t in latest):
            raise ValueError('Promotion must not be backdated')
        self.champion = LogisticModel.restore(self.challenger.state())
        n = self.challenger_normalizer
        self.champion_normalizer = PastNormalizer(n.count, n.mean.copy(), n.m2.copy())
        self.champion_version += 1
        event = dict(action='promote_paper_champion', at=now.isoformat(), version=self.champion_version, gate=gate)
        self.audit.append(event)
        self.challenger, self.challenger_normalizer = None, None
        return event

    def to_state(self) -> dict[str, Any]:
        def normalization(n):
            return dict(count=n.count, mean=n.mean.tolist(), m2=n.m2.tolist()) if n else None
        return dict(schema_version=1, feature_names=list(FEATURE_NAMES),
            config={k: getattr(self, k) for k in ('horizon_hours', 'minimum_training_samples', 'minimum_shadow_samples', 'minimum_shadow_days', 'improvement_margin', 'decision_threshold')},
            trainer=self.trainer.state(), champion=self.champion.state(),
            challenger=self.challenger.state() if self.challenger else None,
            normalizer=normalization(self.normalizer), champion_normalizer=normalization(self.champion_normalizer),
            challenger_normalizer=normalization(self.challenger_normalizer),
            **{k: getattr(self, k) for k in ('champion_version', 'shadow_started_at', 'shadow_epoch', 'shadow_records', 'pending', 'last_observation', 'last_quote', 'last_processed_at', 'last_independent_end', 'drift_flag', 'drift_reasons', 'audit')},
            recent_labels=list(self.recent_labels))

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> 'QuantResearcherAgent':
        if state.get('schema_version') != 1 or state.get('feature_names') != list(FEATURE_NAMES):
            raise ValueError('Unsupported checkpoint schema/features')
        obj = cls(**state['config'])
        def normalization(values):
            if values is None:
                return None
            mean, m2 = np.asarray(values['mean'], dtype=float), np.asarray(values['m2'], dtype=float)
            count = int(values['count'])
            if mean.shape != (DIM,) or m2.shape != (DIM,) or not np.isfinite(mean).all() or not np.isfinite(m2).all() or count < 0 or np.any(m2 < -1e-9):
                raise ValueError('Invalid normalization checkpoint')
            return PastNormalizer(count, mean.copy(), np.maximum(m2, 0))
        obj.trainer = LogisticModel.restore(state['trainer'])
        obj.champion = LogisticModel.restore(state['champion'])
        obj.challenger = LogisticModel.restore(state['challenger']) if state['challenger'] else None
        for k in ('normalizer', 'champion_normalizer', 'challenger_normalizer'):
            setattr(obj, k, normalization(state[k]))
        if obj.normalizer is None or obj.champion_normalizer is None or bool(obj.challenger) != bool(obj.challenger_normalizer):
            raise ValueError('Missing checkpoint normalizer')
        # JSON round-trip prevents accidental shared mutable state.
        copied = json.loads(json.dumps(state, allow_nan=False))
        for k in ('champion_version', 'shadow_started_at', 'shadow_epoch', 'shadow_records', 'pending', 'last_observation', 'last_quote', 'last_processed_at', 'last_independent_end', 'drift_flag', 'drift_reasons', 'audit'):
            setattr(obj, k, copied[k])
        for record in obj.pending:
            features = np.asarray(record['features'], dtype=float)
            if features.shape != (DIM,) or not np.isfinite(features).all() or _time(record['matures_at']) != _time(record['observed_at']) + timedelta(hours=48):
                raise ValueError('Invalid pending checkpoint')
        obj.recent_labels = deque(copied['recent_labels'], maxlen=40)
        return obj

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=target.parent, delete=False, encoding='utf-8') as handle:
            temp = Path(handle.name)
            try:
                json.dump(self.to_state(), handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                temp.unlink(missing_ok=True)
                raise
        os.replace(temp, target)

    @classmethod
    def load(cls, path: str | Path) -> 'QuantResearcherAgent':
        return cls.from_state(json.loads(Path(path).read_text(encoding='utf-8')))
