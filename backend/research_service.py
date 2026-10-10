"""Persistent event-driven research. No network fetches, broker orders or LLM calls.

`ResearchService` keeps the original research-only behaviour. Subclasses (see
`runtime.TradingRuntime`) override the hook methods to add a paper ledger,
empirical edge evidence and market profiles without changing the ingest order.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import pickle
from pathlib import Path
import tempfile
from threading import RLock

import pandas as pd
import numpy as np

from .decision_team import DecisionJournal, DecisionTeam
from .intraday import BAR, QuantDeveloperAgent, validate_intraday_bars
from .learning import QuantResearcherAgent
from .risk import QuantTraderAgent


class ResearcherAdapter:
    def __init__(self, researcher, features):
        self.researcher = researcher
        self.features = features

    def evaluate(self, context):
        result = self.researcher.evaluate(context['proposal'], self.features.get(context['proposal']['symbol']))
        # Probability of a positive return cannot establish its magnitude.
        result.update(eligible=False, edge_supported=False,
                      reasons=['No independently validated expected return for this instrument, strategy and cost model.'])
        return result


def trailing_features(combined: pd.DataFrame) -> dict[str, dict[str, float]]:
    features = {}
    for symbol, group in combined.groupby('symbol'):
        at = group.timestamp.iloc[-1] + BAR
        trailing = group[group.timestamp + BAR >= at - pd.Timedelta(hours=48)]
        euro_close = trailing.close * trailing.fx_to_eur
        returns = euro_close.pct_change().dropna()
        previous_volume = trailing.volume.iloc[:-1]
        scale = previous_volume.std(ddof=1)
        features[symbol] = dict(momentum_48h=float(euro_close.iloc[-1] / euro_close.iloc[0] - 1),
                                volatility_48h=float(returns.std(ddof=1)) if len(returns) > 1 else 0.,
                                volume_zscore=float((trailing.volume.iloc[-1] - previous_volume.mean()) / scale)
                                if len(previous_volume) > 1 and scale > 0 else 0.)
    return features


class ResearchService:
    """One worker: new completed bars reveal old outcomes and queue new signals.

    Data source and all-in costs are caller-supplied research assumptions. A
    licensed feed adapter must send bars; this class does not create a feed.
    Atomic checkpoints preserve learning and bounded history across restarts.
    """
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'research-state.json'
        self.lock = RLock()
        self.developer = QuantDeveloperAgent()
        self.trader = QuantTraderAgent()
        self.journal = DecisionJournal(self.directory / 'decisions.sqlite')
        self.history = None
        self.sources = {}
        self.costs = {}
        self.researcher = QuantResearcherAgent()
        self.extra = self._initial_extra()
        if self.path.exists():
            state = json.loads(self.path.read_text())
            if state.get('schema_version') != 1:
                raise ValueError('Unsupported research checkpoint')
            self.researcher = QuantResearcherAgent.from_state(state['learner'])
            self.history = validate_intraday_bars(pd.DataFrame(state['bars'])) if state['bars'] else None
            self.sources, self.costs = state['sources'], state['costs']
            if state.get('extra') is not None:
                self.extra = self._restore_extra(state['extra'])

    # ---- hooks: the base class is research-only and keeps every proposal rejected ----
    def _initial_extra(self):
        return None

    def _restore_extra(self, state):
        return state

    def _copy_extra(self, extra):
        return json.loads(json.dumps(extra, allow_nan=False)) if extra is not None else None

    def _extra_state(self, extra):
        return extra

    def _on_bar(self, work, row, costs):
        """Called for each new completed bar, in chronological order, before proposals."""

    def _on_outcomes(self, work, outcomes, history):
        """Called with matured labels revealed by this batch."""

    def _select_for_decision(self, work, proposals):
        return proposals

    def _peers(self, work, proposals):
        """Signals the skeptic weighs for direction conflicts."""
        return proposals

    def _researcher_adapter(self, learner, features, work):
        return ResearcherAdapter(learner, features)

    def _trader(self, work):
        return self.trader

    def _markets(self, combined, now, work):
        return {symbol: dict(data_valid=True, observed_at=(group.timestamp.iloc[-1] + BAR).isoformat(),
                             now=now.isoformat(), price=float(group.close.iloc[-1]),
                             market_open=False, short_supported=False, borrow_available=False)
                for symbol, group in combined.groupby('symbol')}

    def _portfolio(self, work, now):
        return dict(equity_eur=500., cash_eur=500., peak_equity_eur=500.,
                    day_start_equity_eur=500., gross_exposure_eur=0., open_positions=[])

    def _on_decisions(self, work, decisions, combined, now):
        """Called after the four roles have evaluated and journaled this batch."""

    def _status_extra(self):
        return {}

    # ---- core ----
    def status(self):
        with self.lock:
            return dict(mode='research_paper_only', execution='disabled',
                        continuous_learning='event_driven_when_authorized_bars_arrive',
                        feed_connected=False, horizon_hours=48,
                        training_labels=self.researcher.trainer.updates,
                        pending_labels=len(self.researcher.pending),
                        champion_version=self.researcher.champion_version,
                        drift_flag=self.researcher.drift_flag,
                        drift_reasons=self.researcher.drift_reasons[:],
                        promotion_gate=self.researcher.promotion_gate(),
                        policy=self._trader(self.extra).policy.snapshot(),
                        sources=self.sources.copy(), **self._status_extra())

    def _save(self, researcher, history, sources, costs, extra=None):
        bars = json.loads(history.to_json(orient='records', date_format='iso')) if history is not None else []
        state = dict(schema_version=1, learner=researcher.to_state(), bars=bars,
                     sources=sources, costs=costs, extra=self._extra_state(extra))
        with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                json.dump(state, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def ingest(self, frame, source_label, roundtrip_cost_bps, *, now=None):
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError('Timezone-aware receipt time required')
        incoming = validate_intraday_bars(frame)
        if len(incoming) > 10000 or incoming.symbol.nunique() > 50:
            raise ValueError('Batch limit: 10000 bars and 50 symbols')
        if (incoming.timestamp + BAR > pd.Timestamp(now)).any():
            raise ValueError('Only completed bars may enter learning')
        if set(incoming.symbol) - set(roundtrip_cost_bps):
            raise ValueError('Every symbol needs an explicit all-in round-trip cost estimate')
        if any(not np.isfinite(v) or not 0 <= v <= 10000 for v in roundtrip_cost_bps.values()):
            raise ValueError('Costs must be finite basis points between 0 and 10000')
        with self.lock:
            # In-process working copy (our own objects, never external data); committed only after a durable save.
            learner = pickle.loads(pickle.dumps(self.researcher, protocol=pickle.HIGHEST_PROTOCOL))
            work = self._copy_extra(self.extra)
            sources, costs = self.sources.copy(), self.costs.copy()
            old = self.history
            fresh = []
            for symbol, group in incoming.groupby('symbol', sort=False):
                if symbol in costs and costs[symbol] != roundtrip_cost_bps[symbol] and any(p['symbol'] == symbol for p in learner.pending):
                    raise ValueError('Cannot change a symbol cost assumption while labels are pending')
                costs[symbol] = float(roundtrip_cost_bps[symbol])
                sources[symbol] = source_label
                previous = old[old.symbol == symbol].set_index('timestamp') if old is not None else None
                for row in group.itertuples(index=False):
                    if previous is not None and row.timestamp in previous.index:
                        cached = previous.loc[row.timestamp]
                        if any(cached[k] != getattr(row, k) for k in ('open', 'high', 'low', 'close', 'volume', 'fx_to_eur', 'asset_class')):
                            raise ValueError('Previously accepted bars cannot be revised silently')
                        continue
                    if symbol in learner.last_quote and row.timestamp + BAR <= pd.Timestamp(learner.last_quote[symbol]):
                        raise ValueError('New bars must follow previously processed data')
                    if learner.last_processed_at and row.timestamp + BAR < pd.Timestamp(learner.last_processed_at):
                        raise ValueError('Shared model input clock cannot move backwards across symbols')
                    fresh.append(row._asdict())
            outcomes = []
            new = pd.DataFrame(fresh, columns=incoming.columns)
            if not new.empty:
                new = validate_intraday_bars(new)
                for row in new.itertuples(index=False):
                    self._on_bar(work, row, costs)
                    outcomes.extend(learner.reveal(row.symbol, (row.timestamp + BAR).isoformat(),
                                                  row.close * row.fx_to_eur, costs[row.symbol]))
            combined = pd.concat([old, incoming] if old is not None else [incoming], ignore_index=True)
            combined = combined.drop_duplicates(['symbol', 'timestamp']).sort_values(['timestamp', 'symbol'])
            if combined.symbol.nunique() > 50:
                raise ValueError('Research workspace limit: 50 tracked symbols')
            combined = combined.groupby('symbol', group_keys=False).tail(400).reset_index(drop=True)
            self._on_outcomes(work, outcomes, combined)
            proposals = self.developer.propose(combined, observed_at=now) if fresh else []
            # Never score an older signal with weights learned from newer quotes.
            proposals = [p for p in proposals if not learner.last_processed_at or
                         pd.Timestamp(p['observed_at']) >= pd.Timestamp(learner.last_processed_at)]
            features = trailing_features(combined)
            team = DecisionTeam(self.journal, developer=self.developer,
                                researcher=self._researcher_adapter(learner, features, work),
                                trader=self._trader(work))
            markets = self._markets(combined, now, work)
            portfolio = self._portfolio(work, now)
            decisions = team.evaluate_batch(self._select_for_decision(work, proposals), markets, portfolio,
                                            now=now, peers=self._peers(work, proposals))
            self._on_decisions(work, decisions, combined, now)
            observed_ids = []
            pending_keys = {tuple(p['key']) for p in learner.pending}
            last_fx = combined.groupby('symbol').fx_to_eur.last()
            for proposal in proposals:
                # Learning labels are EUR returns; order proposals retain quote prices.
                learning_proposal = {**proposal, 'entry_reference_price': proposal['entry_reference_price'] * float(last_fx[proposal['symbol']])}
                key = (proposal['symbol'], proposal['strategy_id'], proposal['side'], pd.Timestamp(proposal['observed_at']).isoformat())
                if key in pending_keys:
                    continue
                pending_keys.add(key)
                observed_ids.append(learner.observe(learning_proposal, features[proposal['symbol']]))
            self._save(learner, combined, sources, costs, work)
            self.researcher, self.history, self.sources, self.costs, self.extra = learner, combined, sources, costs, work
            return dict(accepted_bars=len(fresh), duplicate_bars=len(incoming) - len(fresh),
                        source=source_label, outcomes=outcomes, queued_label_ids=observed_ids,
                        decisions=decisions, status=self.status(),
                        assumptions=['All-in costs are caller estimates; not verified broker fees.',
                                     'Labels use the first supplied completed close at or after 48 hours.',
                                     'Outcome labels concern EUR signed returns; they do not simulate stops.',
                                     'Features use observed bars in the trailing 48 elapsed hours; sparse history and market gaps are not filled.',
                                     'No execution adapter or account is connected.'])

    def stage(self, *, acknowledge_drift=False, now=None):
        with self.lock:
            learner = QuantResearcherAgent.from_state(self.researcher.to_state())
            event = learner.start_shadow(now or datetime.now(timezone.utc), acknowledge_drift=acknowledge_drift)
            self._save(learner, self.history, self.sources, self.costs, self.extra)
            self.researcher = learner
            return event

    def promote(self, *, reviewed=False, now=None):
        with self.lock:
            learner = QuantResearcherAgent.from_state(self.researcher.to_state())
            event = learner.promote(now or datetime.now(timezone.utc), reviewed=reviewed)
            self._save(learner, self.history, self.sources, self.costs, self.extra)
            self.researcher = learner
            return event
