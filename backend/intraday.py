"""Causal multi-strategy intraday PAPER research, with EUR accounting.

`timestamp` is the UTC bar OPEN; OHLC becomes known 15 minutes later. Orders
use the following executable bar's open. These are linear, fractional research
positions, not contract-aware derivatives or broker orders. Signals and execution
are deterministic; the agent does not claim trained AI or profitable strategies.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping
import numpy as np
import pandas as pd
from .engine import wilder_rsi

BAR = pd.Timedelta(minutes=15)
REQUIRED = ('timestamp', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'fx_to_eur', 'asset_class')
STRATEGY_IDS = ('sma', 'rsi', 'bollinger', 'atr_breakout', 'heikin')
STRATEGY_NAMES = dict(sma='SMA 10/30 trend', rsi='RSI 14 reversal', bollinger='Bollinger 20/2 reversal',
                      atr_breakout='20-bar range / ATR breakout', heikin='Heikin-Ashi confirmation')


@dataclass(frozen=True)
class IntradayConfig:
    """Policy rates ending _pct are fractions; e.g. .005 is 0.5%."""
    initial_eur: float = 500.
    commission_bps: float = 5.
    minimum_commission_eur: float = 0.
    slippage_bps: float = 5.
    short_borrow_annual_pct: float = .03
    funding_annual_pct: float = 0.
    risk_per_position_pct: float = .005
    max_position_exposure_pct: float = .10
    max_symbol_exposure_pct: float = .20
    max_gross_exposure_pct: float = 1.
    daily_loss_limit_pct: float = .015
    max_drawdown_pct: float = .08
    max_holding_hours: float = 48.
    max_signal_age_minutes: float = 30.
    allow_shorts: bool = True
    max_concurrent_positions: int = 3

    def __post_init__(self):
        if not isinstance(self.allow_shorts, bool):
            raise ValueError('allow_shorts must be boolean')
        numeric = [v for k, v in asdict(self).items() if k != 'allow_shorts']
        if any(isinstance(value, bool) for value in numeric):
            raise ValueError('Numeric configuration cannot be boolean')
        if not np.isfinite(numeric).all() or min(numeric) < 0:
            raise ValueError('Configuration must be finite and nonnegative')
        if self.initial_eur <= 0 or self.max_holding_hours <= 0:
            raise ValueError('Positive capital and holding horizon required')
        caps = dict(initial_eur=500., risk_per_position_pct=.005, max_position_exposure_pct=.20,
                    max_symbol_exposure_pct=.20, max_gross_exposure_pct=1., daily_loss_limit_pct=.015,
                    max_drawdown_pct=.08, max_holding_hours=48., max_signal_age_minutes=30.)
        for name, cap in caps.items():
            if not 0 < getattr(self, name) <= cap:
                raise ValueError(f'{name} must be positive and no greater than {cap}')
        if not isinstance(self.max_concurrent_positions, int) or not 1 <= self.max_concurrent_positions <= 3:
            raise ValueError('Maximum concurrent positions is 1 to 3')


def validate_intraday_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if not set(REQUIRED).issubset(frame.columns):
        raise ValueError(f'Required columns: {", ".join(REQUIRED)}')
    df = frame.loc[:, REQUIRED].copy()
    if df.empty:
        raise ValueError('Empty intraday data')
    already_aware = isinstance(df.timestamp.dtype, pd.DatetimeTZDtype)
    if not already_aware and not df.timestamp.map(lambda value: pd.Timestamp(value).tzinfo is not None).all():
        raise ValueError('Bar timestamps must explicitly include a timezone offset')
    df['timestamp'] = pd.to_datetime(df.timestamp, utc=True, errors='raise')
    if df.timestamp.isna().any() or (df.timestamp.astype('int64') % BAR.value != 0).any():
        raise ValueError('Bar timestamps must be valid and aligned to 15-minute boundaries')
    for col in ('symbol', 'asset_class'):
        if df[col].isna().any() or not all(isinstance(v, str) and bool(v.strip()) for v in df[col].unique()):
            raise ValueError(f'Nonempty {col} strings required')
    if df.duplicated(['symbol', 'timestamp']).any():
        raise ValueError('Duplicate symbol/bar timestamp')
    number_cols = ['open', 'high', 'low', 'close', 'volume', 'fx_to_eur']
    if not all(pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]) for c in number_cols):
        df[number_cols] = df[number_cols].apply(pd.to_numeric, errors='raise')
    values = df[number_cols].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values[:, [0, 1, 2, 3, 5]] <= 0).any() or (values[:, 4] < 0).any():
        raise ValueError('Finite positive prices/FX and nonnegative volume required')
    o, h, l, c = values[:, 0], values[:, 1], values[:, 2], values[:, 3]
    if (h < np.maximum(np.maximum(o, l), c)).any() or (l > np.minimum(np.minimum(o, h), c)).any():
        raise ValueError('Invalid OHLC bounds')
    # Duplicates were rejected above, so chronological order means strictly increasing per symbol.
    if (df.groupby('symbol', sort=False).timestamp.diff().dropna() <= pd.Timedelta(0)).any():
        raise ValueError('Each symbol must be chronological')
    if (df.groupby('symbol', sort=False).asset_class.nunique() != 1).any():
        raise ValueError('Symbol asset class cannot change')
    if {str(v).lower() for v in df.asset_class.unique()} - {'stock', 'stocks', 'equity', 'etf', 'crypto'}:
        raise ValueError('Contract-aware derivatives/forex execution is not implemented; only stock/equity/ETF/crypto research bars supported')
    return df.sort_values(['timestamp', 'symbol'], kind='stable').reset_index(drop=True)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    previous = frame.close.shift(1)
    tr = pd.concat([frame.high - frame.low, (frame.high - previous).abs(),
                    (frame.low - previous).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def strategy_signals(frame: pd.DataFrame, strategy: str) -> np.ndarray:
    """Targets observed after each completed bar; execution must shift one bar."""
    if strategy not in STRATEGY_IDS:
        raise ValueError('Unknown intraday strategy')
    c = frame.close
    targets = np.zeros(len(frame), dtype=int)
    if strategy == 'sma':
        fast, slow = c.rolling(10).mean(), c.rolling(30).mean()
        return np.where(slow.notna(), np.sign(fast - slow), 0).astype(int)
    close = c.to_numpy(dtype=float)
    if strategy == 'atr_breakout':
        # Current close is compared to a range containing PREVIOUS bars only.
        hi = frame.high.shift(1).rolling(20).max().to_numpy(dtype=float)
        lo = frame.low.shift(1).rolling(20).min().to_numpy(dtype=float)
        average_range = atr(frame).to_numpy(dtype=float)
        state = 0
        mid = (hi + lo) / 2
        for i in range(len(frame)):
            if not np.isfinite(hi[i]):
                continue
            if close[i] > hi[i] + .1 * average_range[i]: state = 1
            elif close[i] < lo[i] - .1 * average_range[i]: state = -1
            elif (state == 1 and close[i] < mid[i]) or (state == -1 and close[i] > mid[i]): state = 0
            targets[i] = state
        return targets
    if strategy in ('rsi', 'bollinger'):
        oscillator = wilder_rsi(c).to_numpy(dtype=float)
        mean = c.rolling(20).mean().to_numpy(dtype=float)
        sd = c.rolling(20).std(ddof=1).to_numpy(dtype=float)
        state = 0
        for i in range(len(frame)):
            if strategy == 'rsi':
                low, high = oscillator[i] < 30, oscillator[i] > 70
                exit_ = (state == 1 and oscillator[i] >= 50) or (state == -1 and oscillator[i] <= 50)
            else:
                low, high = close[i] < mean[i] - 2 * sd[i], close[i] > mean[i] + 2 * sd[i]
                exit_ = (state == 1 and close[i] >= mean[i]) or (state == -1 and close[i] <= mean[i])
            if exit_: state = 0
            elif state == 0:
                if low: state = 1
                elif high: state = -1
            targets[i] = state
        return targets
    hc = frame[['open', 'high', 'low', 'close']].mean(axis=1).to_numpy()
    ho = np.zeros(len(frame))
    if len(frame): ho[0] = (frame.open.iloc[0] + c.iloc[0]) / 2
    for i in range(1, len(frame)): ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    body = hc - ho
    for i in range(30, len(frame)):
        if body[i] > 0 and body[i - 1] > 0: targets[i] = 1
        elif body[i] < 0 and body[i - 1] < 0: targets[i] = -1
    return targets


class QuantDeveloperAgent:
    role = 'quant_developer'

    def evaluate(self, context: dict) -> dict:
        proposal = context.get('proposal', {})
        reasons = []
        if proposal.get('strategy_id') not in STRATEGY_IDS: reasons.append('Unknown strategy')
        if proposal.get('side') not in ('long', 'short'): reasons.append('Unknown side')
        if not proposal.get('symbol'): reasons.append('Missing symbol')
        try:
            observed = pd.Timestamp(proposal.get('observed_at'))
            now = pd.Timestamp(context.get('market_state', {}).get('now', context.get('now')))
            if observed.tzinfo is None or now.tzinfo is None: reasons.append('Timezone-aware observation and now required')
            elif observed > now or now - observed > pd.Timedelta(minutes=30): reasons.append('Future or stale observation')
            stop = float(proposal.get('stop_distance_pct', 0))
            if not np.isfinite(stop) or not 0 < stop <= .05: reasons.append('Stop distance must be a positive fraction no greater than .05')
        except (ValueError, TypeError): reasons.append('Invalid proposal timestamps or stop')
        bars = context.get('bars')
        if bars is not None and not reasons:
            candidates = self.propose(bars, observed_at=now)
            if not any(all(p.get(k) == proposal.get(k) for k in ('symbol', 'strategy_id', 'side', 'observed_at')) for p in candidates):
                reasons.append('Proposal did not reproduce from supplied completed bars')
        evidence = ['Deterministic causal signal implementation; no learned model claimed.',
                    'stop_distance_pct uses fractional units: .01 means 1%.',
                    'Signal independently regenerated from supplied bars.' if bars is not None else
                    'Proposal schema/provenance checked; bars were not supplied for independent signal reproduction.']
        return dict(role=self.role, approved=not reasons, status='pass' if not reasons else 'reject',
                    reasons=reasons, evidence=evidence)

    def propose(self, frame: pd.DataFrame, observed_at=None) -> list[dict]:
        df = validate_intraday_bars(frame)
        now = pd.Timestamp(observed_at) if observed_at is not None else df.timestamp.max() + BAR
        now = now.tz_localize('UTC') if now.tzinfo is None else now.tz_convert('UTC')
        df = df[df.timestamp + BAR <= now]
        proposals = []
        for symbol, group in df.groupby('symbol', sort=True):
            group = group.reset_index(drop=True)
            close_time = group.timestamp.iloc[-1] + BAR
            if now - close_time > pd.Timedelta(minutes=30) or group.volume.iloc[-1] <= 0:
                continue
            stop = max(.005, min(.05, float(2 * atr(group).iloc[-1] / group.close.iloc[-1])))
            for strategy in STRATEGY_IDS:
                targets = strategy_signals(group, strategy)
                signal = int(targets[-1])
                if signal == 0: continue
                if not np.isfinite(stop): continue
                proposals.append(dict(symbol=symbol, strategy_id=strategy, side='long' if signal > 0 else 'short',
                    confidence=.5, entry_reference_price=float(group.close.iloc[-1]), stop_distance_pct=stop,
                    observed_at=close_time.isoformat(),
                    fresh_signal=len(targets) < 2 or int(targets[-2]) != signal, evidence=[
                        f'{STRATEGY_NAMES[strategy]} generated a {"long" if signal > 0 else "short"} target using completed 15-minute bars.',
                        'Confidence 0.5 is an uncalibrated heuristic, not a probability of profit.',
                        'Entry reference is the latest close; a later executable price and risk approval are required.']))
        return proposals


def backtest_intraday(frame: pd.DataFrame, config: IntradayConfig | None = None,
                      signal_overrides: Mapping[tuple[str, str], np.ndarray] | None = None) -> dict:
    """Research simulation with shared capital, signed EUR marks and conservative stops.

    Gaps are never interpolated. Stale signals cannot create positions. An expired
    holding is closed at the first available positive-volume bar, possibly beyond
    48 hours; no fill is invented during a market closure. Overrides are for tests.
    """
    cfg = config or IntradayConfig()
    df = validate_intraday_bars(frame)
    groups = {s: g.reset_index(drop=True) for s, g in df.groupby('symbol', sort=True)}
    targets = {}
    ranges = {symbol: atr(group).to_numpy() for symbol, group in groups.items()}
    for symbol, group in groups.items():
        for strategy in STRATEGY_IDS:
            key = (symbol, strategy)
            signal = np.asarray(signal_overrides[key]) if signal_overrides is not None and key in signal_overrides else strategy_signals(group, strategy)
            if len(signal) != len(group) or not np.isin(signal, [-1, 0, 1]).all():
                raise ValueError('Targets must contain one -1/0/1 value per bar')
            targets[key] = signal
    cash, peak = cfg.initial_eur, cfg.initial_eur
    positions, marks, trades, decisions, trace = {}, {}, [], [], []
    last_accrual, day, day_open, hard_halt = None, None, cfg.initial_eur, False
    fee_rate, slip = cfg.commission_bps / 10000, cfg.slippage_bps / 10000
    row_index = {(s, row.timestamp): i for s, g in groups.items() for i, row in g.iterrows()}
    def equity(): return cash + sum(p['signed_quantity'] * marks[p['symbol']] for p in positions.values())
    def gross(symbol=None): return sum(abs(p['signed_quantity']) * marks[p['symbol']] for p in positions.values() if symbol is None or p['symbol'] == symbol)
    def close_position(key, price_eur, timestamp, reason):
        nonlocal cash
        p = positions.pop(key)
        if last_accrual is not None and timestamp > last_accrual:
            years = (timestamp - last_accrual).total_seconds() / (365 * 86400)
            rate = cfg.funding_annual_pct + (cfg.short_borrow_annual_pct if p['signed_quantity'] < 0 else 0)
            carry = abs(p['signed_quantity']) * marks[p['symbol']] * rate * years
            cash -= carry; p['carry_eur'] += carry
        direction = int(np.sign(p['signed_quantity']))
        execution = price_eur * (1 - direction * slip)
        notional = abs(p['signed_quantity']) * execution
        fee = max(cfg.minimum_commission_eur, notional * fee_rate)
        cash += p['signed_quantity'] * execution - fee
        pnl = p['signed_quantity'] * (execution - p['entry_price_eur']) - fee - p['entry_fee_eur'] - p['carry_eur']
        held = (timestamp - p['entered_at']).total_seconds() / 3600
        trades.append(dict(symbol=p['symbol'], strategy_id=p['strategy_id'], side='long' if direction > 0 else 'short',
            entered_at=p['entered_at'].isoformat(), exited_at=timestamp.isoformat(), held_hours=held,
            entry_price_eur=p['entry_price_eur'], exit_price_eur=execution, quantity=abs(p['signed_quantity']),
            entry_fee_eur=p['entry_fee_eur'], exit_fee_eur=fee, carry_eur=p['carry_eur'], pnl_eur=pnl,
            estimated_stop_risk_eur=p['estimated_stop_risk_eur'],
            reason=reason, beyond_48h=held > cfg.max_holding_hours + 1e-9))
        decisions.append(dict(timestamp=timestamp.isoformat(), symbol=p['symbol'], strategy_id=p['strategy_id'], action='close', reason=reason))
    for timestamp, batch in df.groupby('timestamp', sort=True):
        batch_rows = {row.symbol: row for row in batch.itertuples(index=False)}
        # Accrue carry for elapsed wall-clock time, including weekends and gaps.
        if last_accrual is not None:
            years = (timestamp - last_accrual).total_seconds() / (365 * 86400)
            for p in positions.values():
                rate = cfg.funding_annual_pct + (cfg.short_borrow_annual_pct if p['signed_quantity'] < 0 else 0)
                cost = abs(p['signed_quantity']) * marks[p['symbol']] * rate * years
                cash -= cost; p['carry_eur'] += cost
        last_accrual = timestamp
        for symbol, row in batch_rows.items(): marks[symbol] = float(row.open * row.fx_to_eur)
        open_equity = equity()
        if day != timestamp.date(): day, day_open = timestamp.date(), trace[-1]['equity_eur'] if trace else cfg.initial_eur
        peak = max(peak, open_equity)
        daily_halt = open_equity <= day_open * (1 - cfg.daily_loss_limit_pct)
        hard_halt = hard_halt or open_equity <= peak * (1 - cfg.max_drawdown_pct) or open_equity <= 0
        expired_keys = set()
        for key, p in list(positions.items()):
            symbol, strategy = key
            row = batch_rows.get(symbol)
            if row is None or row.volume <= 0: continue
            i = row_index[(symbol, timestamp)]
            desired = int(targets[key][i - 1]) if i else 0
            elapsed = timestamp - p['entered_at']
            reason = ('portfolio_drawdown_halt' if hard_halt else 'daily_loss_halt' if daily_halt else
                      '48h_horizon_first_executable_bar' if elapsed >= pd.Timedelta(hours=cfg.max_holding_hours) else
                      'signal_flat_or_reversal' if desired != int(np.sign(p['signed_quantity'])) else None)
            if reason:
                close_position(key, marks[symbol], timestamp, reason)
                if reason != 'signal_flat_or_reversal': expired_keys.add(key)
        for symbol, row in sorted(batch_rows.items()):
            group = groups[symbol]; i = row_index[(symbol, timestamp)]
            if i == 0: continue
            age = timestamp - (group.timestamp.iloc[i - 1] + BAR)
            if row.volume <= 0 or age > pd.Timedelta(minutes=cfg.max_signal_age_minutes): continue
            for strategy in STRATEGY_IDS:
                key = (symbol, strategy)
                direction = int(targets[key][i - 1])
                if not direction or key in positions or key in expired_keys: continue
                # Recheck realised entry/exit costs after EVERY same-open action.
                # Only current opening marks and previous observations are used.
                observed_equity = equity()
                daily_halt = observed_equity <= day_open * (1 - cfg.daily_loss_limit_pct)
                hard_halt = hard_halt or observed_equity <= peak * (1 - cfg.max_drawdown_pct)
                if hard_halt or daily_halt or len(positions) >= cfg.max_concurrent_positions or (direction < 0 and not cfg.allow_shorts):
                    decisions.append(dict(timestamp=timestamp.isoformat(), symbol=symbol, strategy_id=strategy, action='reject', reason='risk_halt_position_limit_or_short_disabled'))
                    continue
                stop_pct = float(max(.005, min(.05, 2 * ranges[symbol][i - 1] / group.close.iloc[i - 1])))
                if not np.isfinite(stop_pct): continue
                available_equity = max(0., equity())
                budget_base = min(available_equity, cfg.initial_eur)
                daily_loss = max(0., day_open - available_equity)
                daily_limit = min(day_open, cfg.initial_eur) * cfg.daily_loss_limit_pct
                reserved_risk = sum(p['estimated_stop_risk_eur'] for p in positions.values())
                available_daily_risk = max(0., daily_limit - daily_loss - reserved_risk)
                drawdown_remaining = max(0., peak * cfg.max_drawdown_pct - (peak - available_equity))
                risk_budget = min(budget_base * cfg.risk_per_position_pct, available_daily_risk, drawdown_remaining)
                cap = min(budget_base * cfg.max_position_exposure_pct,
                    max(0., available_equity * cfg.max_gross_exposure_pct - gross()),
                    max(0., available_equity * cfg.max_symbol_exposure_pct - gross(symbol)), max(0., cash))
                price = marks[symbol] * (1 + direction * slip)
                carry_rate = cfg.funding_annual_pct + (cfg.short_borrow_annual_pct if direction < 0 else 0)
                horizon_carry = carry_rate * cfg.max_holding_hours / (24 * 365)
                def roundtrip_cost(notional):
                    # Both sides' commissions, both slips, adverse short exit
                    # notional, and full-horizon carry are reserved before entry.
                    exit_notional = notional * (1 + stop_pct) * (1 + slip)
                    return (max(cfg.minimum_commission_eur, notional * fee_rate) +
                            max(cfg.minimum_commission_eur, exit_notional * fee_rate) +
                            notional * (2 * slip + horizon_carry * (1 + stop_pct)))
                lo, hi = 0., max(0., cap)
                for _ in range(60):
                    mid = (lo + hi) / 2
                    entry_fee = max(cfg.minimum_commission_eur, mid * fee_rate)
                    entry_slip_reserve = mid * slip
                    if (mid * stop_pct + roundtrip_cost(mid) <= risk_budget and
                            mid + entry_fee + entry_slip_reserve <= cash):
                        lo = mid
                    else:
                        hi = mid
                cap = lo
                fee = max(cfg.minimum_commission_eur, cap * fee_rate)
                estimated_cost = roundtrip_cost(cap)
                estimated_risk = cap * stop_pct + estimated_cost
                if cap <= 1e-9 or fee >= cap:
                    decisions.append(dict(timestamp=timestamp.isoformat(), symbol=symbol, strategy_id=strategy,
                        action='reject', reason='Stop plus round-trip costs cannot fit remaining immutable position/daily risk or cash budget.',
                        risk_budget_eur=risk_budget, reserved_position_risk_eur=reserved_risk,
                        available_daily_risk_eur=available_daily_risk))
                    continue
                quantity = cap / price * direction
                cash -= quantity * price + fee
                positions[key] = dict(symbol=symbol, strategy_id=strategy, signed_quantity=quantity, entry_price_eur=price,
                    entry_fee_eur=fee, carry_eur=0., entered_at=timestamp, stop_price_eur=price * (1 - direction * stop_pct),
                    estimated_stop_risk_eur=estimated_risk)
                decisions.append(dict(timestamp=timestamp.isoformat(), symbol=symbol, strategy_id=strategy, action='open',
                    reason='Prior completed-bar target; next-open fill; stop plus both commissions/slippage and 48h carry fit shared capital, position and remaining daily risk budgets.',
                    exposure_eur=cap, stop_distance_pct=stop_pct, risk_budget_eur=risk_budget,
                    estimated_stop_risk_eur=estimated_risk, estimated_roundtrip_cost_eur=estimated_cost,
                    reserved_position_risk_eur=reserved_risk, available_daily_risk_eur=available_daily_risk,
                    daily_loss_eur=daily_loss, observed_equity_eur=available_equity))
        # Stops use actual OHLC, with adverse opening gaps and no beneficial fill assumptions.
        for key, p in list(positions.items()):
            row = batch_rows.get(p['symbol'])
            if row is None or row.volume <= 0: continue
            long = p['signed_quantity'] > 0
            extreme = (row.low if long else row.high) * row.fx_to_eur
            crossed = extreme <= p['stop_price_eur'] if long else extreme >= p['stop_price_eur']
            if crossed:
                fill = min(marks[p['symbol']], p['stop_price_eur']) if long else max(marks[p['symbol']], p['stop_price_eur'])
                close_position(key, fill, timestamp + BAR, 'stop_loss_gap_or_intrabar')
        for symbol, row in batch_rows.items(): marks[symbol] = float(row.close * row.fx_to_eur)
        current = equity(); peak = max(peak, current)
        if current <= peak * (1 - cfg.max_drawdown_pct): hard_halt = True
        trace.append(dict(timestamp=(timestamp + BAR).isoformat(), equity_eur=current, cash_eur=cash,
            gross_exposure_eur=gross(), open_positions=len(positions),
            reserved_stop_risk_eur=sum(p['estimated_stop_risk_eur'] for p in positions.values()), drawdown_pct=(1 - current / peak) * 100,
            halted=hard_halt, stale_position_symbols=sorted({p['symbol'] for p in positions.values() if p['symbol'] not in batch_rows})))
    # Liquidate only at an available common terminal tradable CLOSE; otherwise retain marked positions.
    open_positions = []
    final_time = df.timestamp.max() + BAR
    for key, p in list(positions.items()):
        last = groups[p['symbol']].iloc[-1]
        if last.volume > 0 and last.timestamp + BAR == final_time:
            close_position(key, float(last.close * last.fx_to_eur), final_time, 'research_terminal_close')
        else:
            open_positions.append(dict(symbol=p['symbol'], strategy_id=p['strategy_id'], entered_at=p['entered_at'].isoformat(),
                reason='No tradable terminal bar; position remains marked, not fabricated liquidated.'))
    if trace:
        trace[-1].update(equity_eur=equity(), cash_eur=cash, gross_exposure_eur=gross(), open_positions=len(positions),
            reserved_stop_risk_eur=sum(p['estimated_stop_risk_eur'] for p in positions.values()))
    values = np.array([cfg.initial_eur] + [t['equity_eur'] for t in trace])
    dd = 1 - values / np.maximum.accumulate(values)
    return dict(config=asdict(cfg), config_rate_units='fractions; .01 means 1%', metric_pct_units='percentage points', metrics=dict(initial_equity_eur=cfg.initial_eur, final_equity_eur=float(values[-1]),
        return_pct=float((values[-1] / cfg.initial_eur - 1) * 100), max_drawdown_pct=float(dd.max() * 100),
        closed_trades=len(trades), win_rate_pct=float(np.mean([t['pnl_eur'] > 0 for t in trades]) * 100) if trades else None),
        trace=trace, trades=trades, decisions=decisions, unliquidated_positions=open_positions,
        limitations=['Research-only fractional stock/ETF/crypto positions; options/futures/forex/CFDs rejected until contract-aware models exist.',
            'Input FX must be point-in-time observations available at the bar open. All prices and costs are converted to EUR.',
            '48 hours is an elapsed-time exit target. Market closures/gaps delay exits to the first executable bar.',
            'Fees/slippage/borrow/funding are configurable assumptions; broker minimum fees may dominate EUR 500 accounts.',
            'Stops may exceed planned loss during gaps. No trained AI, market depth, corporate-action or borrow-availability model.',
            'Five indicator candidates use fixed bar windows; 48h is a holding horizon, not evidence of a 48h predictive pattern.',
            'All five candidates are research hypotheses; continuous learning must use approved out-of-sample promotion.'])
