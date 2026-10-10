"""The trading agent runtime: four roles + learner + edge book + paper ledger.

One `TradingRuntime` owns one directory (checkpoint + journal). The live feed
and the historical replay each get their own runtime, so a replay can never
contaminate the live model, ledger or journal. Execution is paper only.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
from itertools import count
import pickle
from pathlib import Path
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

import numpy as np
import pandas as pd

from .edge import EdgeBook
from .intraday import BAR, STRATEGY_IDS, STRATEGY_NAMES, atr, strategy_signals
from .engine import wilder_rsi
from .ledger import PaperLedger
from .markets import DEFAULT_PROFILES, market_state, profile_for
from .research_service import ResearchService
from .risk import QuantTraderAgent, RiskPolicy

RISK_KEYS = ('risk_per_trade_pct', 'daily_loss_pct', 'max_drawdown_pct', 'max_position_pct',
             'max_gross_exposure_pct', 'max_concurrent_positions')
DEFAULT_SETTINGS = dict(risk={k: getattr(RiskPolicy(), k) for k in RISK_KEYS},
                        shorts_enabled=True, minimum_edge_samples=30, edge_window=120,
                        conflict_policy='evidence')
# 'evidence': an opposite signal conflicts when it is fresh this bar or has supported edge evidence.
# 'strict': any active opposite signal conflicts (trend and reversal strategies then rarely agree).
CONFLICT_POLICIES = ('evidence', 'strict')


def learner_cost_bps(symbol: str) -> float:
    """Fixed per-symbol label cost (includes short borrow; conservative for longs)."""
    return round(profile_for(symbol).roundtrip_cost_bps(), 4)


class Work:
    """Mutable runtime state copied per ingest and committed only after a durable save."""
    def __init__(self, ledger: PaperLedger, edge: EdgeBook, settings: dict, audit: list,
                 asset_classes: dict, recent_outcomes: list, active_signals: list, counters: dict | None = None):
        self.ledger, self.edge, self.settings, self.audit = ledger, edge, settings, audit
        self.asset_classes, self.recent_outcomes, self.active_signals = asset_classes, recent_outcomes, active_signals
        # 'SYMBOL|strategy' -> [matured labels, positive labels, sum of net returns]
        self.counters = counters if counters is not None else {}

    @classmethod
    def fresh(cls) -> 'Work':
        settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        return cls(PaperLedger(500.), EdgeBook(settings['minimum_edge_samples'], settings['edge_window']),
                   settings, [], {}, [], [])

    def to_state(self) -> dict:
        return dict(ledger=self.ledger.to_state(), edge=self.edge.to_state(), settings=self.settings,
                    audit=self.audit, asset_classes=self.asset_classes, recent_outcomes=self.recent_outcomes,
                    active_signals=self.active_signals, counters=self.counters)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> 'Work':
        copied = json.loads(json.dumps(state, allow_nan=False))
        return cls(PaperLedger.from_state(copied['ledger']), EdgeBook.from_state(copied['edge']), copied['settings'],
                   copied['audit'], copied['asset_classes'], copied['recent_outcomes'], copied['active_signals'],
                   copied.get('counters', {}))

    def policy(self) -> RiskPolicy:
        return RiskPolicy(**self.settings['risk'])


class EdgeResearcher:
    """Quant researcher: model probability plus empirical after-cost edge evidence."""
    def __init__(self, learner, features, work: Work):
        self.learner, self.features, self.work = learner, features, work

    def evaluate(self, context):
        proposal = context['proposal']
        result = self.learner.evaluate(proposal, self.features.get(proposal['symbol']))
        profile = profile_for(proposal['symbol'], self.work.settings['shorts_enabled'])
        cost = profile.roundtrip_cost_bps(include_borrow=proposal['side'] == 'short')
        estimate = self.work.edge.estimate(proposal['strategy_id'], proposal['side'], profile.asset_class, cost)
        reasons = []
        if not estimate['supported']:
            reasons.append(estimate['reason'])
        if self.learner.champion_version > 0 and result['probability'] < self.learner.decision_threshold:
            reasons.append(f'Promoted champion v{self.learner.champion_version} probability '
                           f'{result["probability"]:.2f} is below the {self.learner.decision_threshold:.2f} threshold.')
        result.update(eligible=not reasons, edge_supported=estimate['supported'],
                      expected_advantage_bps=estimate['mean_gross_bps'] if estimate['supported'] else None,
                      roundtrip_cost_bps=cost, edge_estimate=estimate, eligible_for_live=False,
                      reasons=reasons or [f'Empirical edge supported: {estimate["reason"]}'])
        result['evidence'] = result['evidence'] + [
            f'Edge bucket {estimate["bucket"]}: {estimate["samples"]} matured non-overlapping samples '
            f'(window {estimate["window"]}, Bonferroni over {estimate["tested_buckets"]} tested buckets).']
        return result


class TraderAdapter:
    """Quant trader with per-proposal expectancy taken from the researcher's provenance."""
    def __init__(self, policy: RiskPolicy):
        self.trader = QuantTraderAgent(policy)
        self.policy = policy

    def evaluate(self, context):
        researcher = context.get('researcher', {})
        estimate = researcher.get('edge_estimate') or {}
        supported = researcher.get('edge_supported') is True
        gross = researcher.get('expected_advantage_bps')
        market = dict(context['market_state'])
        market.update(expected_return_pct=float(np.clip(gross / 10000, -1, 1)) if supported and gross is not None else 0.,
                      expected_return_sample_count=estimate.get('samples', 0),
                      expected_return_source=estimate.get('source') if supported else None,
                      expected_return_validated=supported)
        return self.trader.evaluate(context['proposal'], context['portfolio'], market)


class TradingRuntime(ResearchService):
    def __init__(self, directory: str | Path, *, name: str, checkpoint_every: int = 1):
        self.name = name
        self.checkpoint_every = max(1, int(checkpoint_every))
        self._unsaved = 0
        self._dirty = False
        self.events: deque[dict] = deque(maxlen=500)
        self._sequence = count(1)
        self.event_lock = RLock()
        super().__init__(directory)

    # ---- hook implementations ----
    def _initial_extra(self):
        return Work.fresh()

    def _restore_extra(self, state):
        return Work.from_state(state)

    def _copy_extra(self, extra):
        return pickle.loads(pickle.dumps(extra, protocol=pickle.HIGHEST_PROTOCOL))

    def _extra_state(self, extra):
        return extra.to_state() if extra is not None else None

    def _profile(self, work: Work, symbol: str):
        return profile_for(symbol, work.settings['shorts_enabled'])

    def _on_bar(self, work, row, costs):
        profile = self._profile(work, row.symbol)
        work.asset_classes[row.symbol] = profile.asset_class
        work.ledger.max_drawdown_pct = work.settings['risk']['max_drawdown_pct']
        work.ledger.on_bar(row, profile)

    def _on_outcomes(self, work, outcomes, history):
        for outcome in outcomes:
            asset = work.asset_classes.get(outcome['symbol']) or self._profile(work, outcome['symbol']).asset_class
            counted = work.edge.record(outcome, asset)
            tally = work.counters.setdefault(f"{outcome['symbol']}|{outcome['strategy_id']}", [0, 0, 0.])
            tally[0] += 1
            tally[1] += outcome['label']
            tally[2] += outcome['net_return']
            work.recent_outcomes.append({k: outcome[k] for k in ('symbol', 'strategy_id', 'side', 'observed_at', 'label_at',
                                                                 'gross_return', 'net_return', 'label', 'fresh')} | {'edge_sample': counted})
        del work.recent_outcomes[:-300]

    def _select_for_decision(self, work, proposals):
        work.active_signals = [{k: p[k] for k in ('symbol', 'strategy_id', 'side', 'observed_at', 'fresh_signal', 'stop_distance_pct')}
                               for p in proposals]
        # Only a strategy's fresh transition is actionable; persisting targets are learned from, not re-traded.
        return [p for p in proposals if p.get('fresh_signal')]

    def _peers(self, work, proposals):
        if work.settings.get('conflict_policy', 'evidence') == 'strict':
            return proposals
        def supported(p):
            profile = self._profile(work, p['symbol'])
            cost = profile.roundtrip_cost_bps(include_borrow=p['side'] == 'short')
            return work.edge.estimate(p['strategy_id'], p['side'], profile.asset_class, cost)['supported']
        return [p for p in proposals if p.get('fresh_signal') or supported(p)]

    def _researcher_adapter(self, learner, features, work):
        return EdgeResearcher(learner, features, work)

    def _trader(self, work):
        return TraderAdapter(work.policy())

    def _markets(self, combined, now, work):
        markets = {}
        for symbol, group in combined.groupby('symbol'):
            last = group.iloc[-1]
            markets[symbol] = market_state(self._profile(work, symbol), observed_at=(last.timestamp + BAR).isoformat(),
                                           now=now, price=float(last.close), fx_to_eur=float(last.fx_to_eur))
        return markets

    def _portfolio(self, work, now):
        return work.ledger.portfolio()

    def _on_decisions(self, work, decisions, combined, now):
        for decision in decisions:
            if decision['outcome'] == 'paper_only_approved' and decision.get('order'):
                order = decision['order']
                work.ledger.submit(order, decision_id=decision['decision_id'],
                                   created_at=pd.Timestamp(decision['proposal']['observed_at']).to_pydatetime(),
                                   profile=self._profile(work, order['symbol']))

    def _status_extra(self):
        work = self.extra
        summary = work.ledger.summary()
        summary.pop('positions', None)
        return dict(runtime=self.name, settings=work.settings, ledger=summary,
                    edge_supported_buckets=sum(1 for r in self.edge_rows() if r['supported']),
                    active_signals=work.active_signals, decision_count=self.journal.count(),
                    symbols=sorted(work.asset_classes), outcome_counters=work.counters,
                    clock=self.researcher.last_processed_at)

    # ---- persistence ----
    def _save(self, researcher, history, sources, costs, extra=None):
        self._unsaved += 1
        if self._unsaved >= self.checkpoint_every:
            super()._save(researcher, history, sources, costs, extra)
            self._unsaved = 0
            self._dirty = False
        else:
            self._dirty = True

    def flush(self):
        with self.lock:
            if self._dirty or not self.path.exists():
                super()._save(self.researcher, self.history, self.sources, self.costs, self.extra)
                self._unsaved, self._dirty = 0, False

    def stage(self, **kwargs):
        event = super().stage(**kwargs)
        self.flush()
        self.publish('lifecycle', event)
        return event

    def promote(self, **kwargs):
        event = super().promote(**kwargs)
        self.flush()
        self.publish('lifecycle', event)
        return event

    # ---- events for the web UI ----
    def publish(self, kind: str, payload: Mapping[str, Any]) -> None:
        with self.event_lock:
            event = {k: v for k, v in payload.items() if k not in ('seq', 'type', 'runtime', 'published_at')}
            event.update(seq=next(self._sequence), type=kind, runtime=self.name,
                         published_at=datetime.now(timezone.utc).isoformat())
            self.events.append(event)

    def events_after(self, seq: int) -> list[dict]:
        with self.event_lock:
            return [e for e in self.events if e['seq'] > seq]

    def ingest_bars(self, frame: pd.DataFrame, source_label: str, *, now: datetime | None = None) -> dict:
        costs = {symbol: learner_cost_bps(symbol) for symbol in frame.symbol.unique()}
        result = self.ingest(frame, source_label, costs, now=now)
        compact = [dict(decision_id=d['decision_id'], symbol=d['proposal'].get('symbol'),
                        strategy_id=d['proposal'].get('strategy_id'), side=d['proposal'].get('side'),
                        outcome=d['outcome'], blocked_by=first_blocker(d)) for d in result['decisions']]
        ledger = self.extra.ledger
        self.publish('ingest', dict(clock=self.researcher.last_processed_at, accepted_bars=result['accepted_bars'],
                                    outcomes=len(result['outcomes']),
                                    edge_samples=sum(1 for o in result['outcomes'] if o.get('fresh')),
                                    training_labels=self.researcher.trainer.updates, decisions=compact,
                                    equity_eur=ledger.equity(), positions=len(ledger.positions),
                                    pending_orders=len(ledger.pending), halted=ledger.halted,
                                    active_signals=self.extra.active_signals))
        return result

    # ---- settings ----
    def update_settings(self, changes: Mapping[str, Any], *, now: datetime | None = None) -> dict:
        at = now or datetime.now(timezone.utc)
        with self.lock:
            settings = json.loads(json.dumps(self.extra.settings))
            if 'risk' in changes:
                unknown = set(changes['risk']) - set(RISK_KEYS)
                if unknown:
                    raise ValueError(f'Unknown risk settings: {sorted(unknown)}')
                settings['risk'].update(changes['risk'])
                RiskPolicy(**settings['risk'])  # hard caps: tighten only, never loosen
            if 'shorts_enabled' in changes:
                if not isinstance(changes['shorts_enabled'], bool):
                    raise ValueError('shorts_enabled must be boolean')
                settings['shorts_enabled'] = changes['shorts_enabled']
            for key in ('minimum_edge_samples', 'edge_window'):
                if key in changes:
                    settings[key] = int(changes[key])
            EdgeBook(settings['minimum_edge_samples'], settings['edge_window'])
            if 'conflict_policy' in changes:
                if changes['conflict_policy'] not in CONFLICT_POLICIES:
                    raise ValueError(f'conflict_policy must be one of {CONFLICT_POLICIES}')
                settings['conflict_policy'] = changes['conflict_policy']
            unknown = set(changes) - {'risk', 'shorts_enabled', 'minimum_edge_samples', 'edge_window', 'conflict_policy'}
            if unknown:
                raise ValueError(f'Unknown settings: {sorted(unknown)}')
            work = self._copy_extra(self.extra)
            before = work.settings
            work.settings = settings
            work.edge.minimum_samples, work.edge.window = settings['minimum_edge_samples'], settings['edge_window']
            event = dict(action='settings_change', at=at.isoformat(), before=before, after=settings,
                         note='Applies to new paper decisions only; open positions keep their stops.')
            work.audit.append(event)
            del work.audit[:-200]
            self.journal.append(dict(decision_id=f'settings-{uuid4()}', kind='settings_change', evaluated_at=at.isoformat(),
                                     mode='paper', live_orders_enabled=False, outcome='settings_change', settings=event))
            super()._save(self.researcher, self.history, self.sources, self.costs, work)
            self.extra = work
            self._unsaved, self._dirty = 0, False
        self.publish('settings', event)
        return event

    def acknowledge_halt(self, *, now: datetime | None = None) -> dict:
        with self.lock:
            work = self._copy_extra(self.extra)
            event = work.ledger.acknowledge_halt(now or datetime.now(timezone.utc))
            super()._save(self.researcher, self.history, self.sources, self.costs, work)
            self.extra = work
        self.publish('lifecycle', dict(action='halt_acknowledged', **event))
        return event

    # ---- read models for the UI ----
    def edge_rows(self) -> list[dict]:
        costs = {}
        for symbol, asset in (self.extra.asset_classes or {}).items():
            costs.setdefault(asset, profile_for(symbol, self.extra.settings['shorts_enabled']).roundtrip_cost_bps(include_borrow=False))
        return self.extra.edge.summary(costs or {'crypto': DEFAULT_PROFILES['BTCUSDT'].roundtrip_cost_bps(include_borrow=False)})

    def model_view(self) -> dict:
        from .learning import FEATURE_NAMES
        learner = self.researcher
        def model(m, n):
            if m is None:
                return None
            scale = np.sqrt(np.maximum(n.m2 / max(1, n.count - 1), .01)) if n.count > 1 else np.ones(len(FEATURE_NAMES))
            return dict(weights=m.weights.tolist(), bias=m.bias, updates=m.updates,
                        normalizer=dict(count=n.count, mean=n.mean.tolist(), scale=scale.tolist()))
        example = learner.pending[-1] if learner.pending else None
        outcomes = self.extra.recent_outcomes
        return dict(feature_names=list(FEATURE_NAMES), decision_threshold=learner.decision_threshold,
                    trainer=model(learner.trainer, learner.normalizer),
                    champion=model(learner.champion, learner.champion_normalizer),
                    challenger=model(learner.challenger, learner.challenger_normalizer),
                    champion_version=learner.champion_version, shadow_epoch=learner.shadow_epoch,
                    shadow_records=len(learner.shadow_records), promotion_gate=learner.promotion_gate(),
                    drift_flag=learner.drift_flag, drift_reasons=learner.drift_reasons,
                    audit=learner.audit[-20:], example=dict(symbol=example['symbol'], strategy_id=example.get('strategy_id'),
                                                            side=example['side'], observed_at=example['observed_at'],
                                                            transformed_features=example['features']) if example else None,
                    recent_outcomes=outcomes[-60:],
                    recent_success_rate=float(np.mean([o['label'] for o in outcomes])) if outcomes else None,
                    edge=self.edge_rows())

    def bars_view(self, symbol: str) -> dict:
        if self.history is None or symbol not in set(self.history.symbol):
            raise ValueError('No bars for that symbol yet')
        group = self.history[self.history.symbol == symbol].reset_index(drop=True)
        close = group.close
        mean, sd = close.rolling(20).mean(), close.rolling(20).std(ddof=1)
        ha_close = group[['open', 'high', 'low', 'close']].mean(axis=1).to_numpy()
        ha_open = np.zeros(len(group))
        if len(group):
            ha_open[0] = (group.open.iloc[0] + close.iloc[0]) / 2
        for i in range(1, len(group)):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2
        def series(values):
            return [None if not np.isfinite(v) else float(v) for v in np.asarray(values, dtype=float)]
        targets = {s: strategy_signals(group, s).tolist() for s in STRATEGY_IDS}
        ledger = self.extra.ledger
        trades = [t for t in ledger.trades if t['symbol'] == symbol][-100:]
        positions = [p for p in ledger.positions if p['symbol'] == symbol]
        return dict(symbol=symbol, profile=profile_for(symbol).snapshot(), strategy_names=STRATEGY_NAMES,
                    time=[int((t + BAR).timestamp()) for t in group.timestamp],
                    open=series(group.open), high=series(group.high), low=series(group.low), close=series(close),
                    volume=series(group.volume), sma10=series(close.rolling(10).mean()), sma30=series(close.rolling(30).mean()),
                    bb_mid=series(mean), bb_upper=series(mean + 2 * sd), bb_lower=series(mean - 2 * sd),
                    rsi=series(wilder_rsi(close)), atr=series(atr(group)),
                    range_high=series(group.high.shift(1).rolling(20).max()), range_low=series(group.low.shift(1).rolling(20).min()),
                    ha_open=series(ha_open), ha_close=series(ha_close), targets=targets, trades=trades, positions=positions)


def first_blocker(decision: Mapping[str, Any]) -> str | None:
    if decision.get('outcome') == 'paper_only_approved':
        return None
    for role in decision.get('roles', []):
        verdict = role['decision']
        if verdict.get('approved', verdict.get('eligible')) is not True:
            return role['role']
    return 'journal'
