"""Persistent EUR paper ledger. Simulation only; it never contacts a broker.

Approved paper orders fill at the NEXT completed bar's open with adverse
slippage and half-spread. Stops trigger on bar high/low and fill at the worse of
the stop and the open (gaps are adverse). Positions exit at the first bar opening
at or after 48 elapsed hours. Short borrow/funding accrues per elapsed time.
A drawdown halt liquidates at the next open and blocks new orders until a
human acknowledges it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Mapping
from uuid import uuid4

import pandas as pd

from .markets import MarketProfile

BAR = timedelta(minutes=15)
MAX_FILL_DELAY = timedelta(minutes=30)


def _time(value: str | datetime | pd.Timestamp) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError('Ledger timestamps require timezone')
    return dt.astimezone(timezone.utc)


class PaperLedger:
    def __init__(self, initial_eur: float = 500.):
        if not 0 < initial_eur <= 500:
            raise ValueError('Paper capital must be positive and at most EUR 500')
        self.initial_eur = float(initial_eur)
        self.cash = float(initial_eur)
        self.peak = float(initial_eur)
        self.day: str | None = None
        self.day_start_equity = float(initial_eur)
        self.halted = False
        self.halt_reason: str | None = None
        self.max_drawdown_pct = .08
        self.positions: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.marks: dict[str, float] = {}
        self.trades: list[dict[str, Any]] = []
        self.stats: dict[str, Any] = dict(closed=0, wins=0, pnl_eur=0., fees_eur=0., carry_eur=0., by_strategy={})
        self.curve: list[list[Any]] = []
        self.events: list[dict[str, Any]] = []
        self.clock: str | None = None

    # ---- valuation ----
    def equity(self) -> float:
        return self.cash + sum(p['quantity'] * self.marks.get(p['symbol'], p['entry_price_eur']) for p in self.positions)

    def gross(self) -> float:
        return sum(abs(p['quantity']) * self.marks.get(p['symbol'], p['entry_price_eur']) for p in self.positions)

    def _event(self, kind: str, at: datetime, **data) -> None:
        self.events.append(dict(type=kind, at=at.isoformat(), **data))
        del self.events[:-300]

    # ---- order intake ----
    def submit(self, order: Mapping[str, Any], *, decision_id: str, created_at: datetime, profile: MarketProfile) -> dict:
        if order.get('mode') != 'paper':
            raise ValueError('Only paper orders are accepted')
        pending = dict(id=str(uuid4()), decision_id=decision_id, symbol=order['symbol'], strategy_id=order['strategy_id'],
                       side=order['side'], quantity=float(order['quantity']), stop_distance_pct=float(order['stop_distance_pct']),
                       reference_price=float(order['entry_reference_price']), notional_eur=float(order['notional_eur']),
                       estimated_stop_risk_eur=float(order['estimated_stop_risk_eur']),
                       created_at=_time(created_at).isoformat(), quantity_step=profile.quantity_step)
        self.pending.append(pending)
        self._event('order_queued', _time(created_at), symbol=pending['symbol'], strategy_id=pending['strategy_id'],
                    side=pending['side'], notional_eur=pending['notional_eur'], decision_id=decision_id)
        return pending

    # ---- bar processing ----
    def on_bar(self, row, profile: MarketProfile) -> None:
        opened = _time(row.timestamp)
        closed = opened + BAR
        fx = float(row.fx_to_eur)
        o, h, l, c = (float(getattr(row, k)) * fx for k in ('open', 'high', 'low', 'close'))
        symbol = row.symbol
        day = opened.date().isoformat()
        if self.day != day:
            self.day, self.day_start_equity = day, self.equity()
        self._accrue(symbol, opened, profile)
        tradable = float(row.volume) > 0
        if tradable:
            self._fill_pending(symbol, opened, o, profile)
            for position in [p for p in self.positions if p['symbol'] == symbol]:
                if self.halted:
                    self._close(position, o, opened, 'drawdown_halt_liquidation', profile)
                elif opened >= _time(position['entered_at']) + timedelta(hours=48):
                    self._close(position, o, opened, '48h_horizon_first_executable_bar', profile)
            for position in [p for p in self.positions if p['symbol'] == symbol]:
                long = position['quantity'] > 0
                stop = position['stop_price_eur']
                if (long and l <= stop) or (not long and h >= stop):
                    fill = min(o, stop) if long else max(o, stop)
                    self._close(position, fill, closed, 'stop_loss_gap_or_intrabar', profile)
        self.marks[symbol] = c
        equity = self.equity()
        self.peak = max(self.peak, equity)
        if not self.halted and equity <= self.peak * (1 - self.max_drawdown_pct):
            self.halted, self.halt_reason = True, f'Equity {equity:.2f} fell {self.max_drawdown_pct:.0%} below peak {self.peak:.2f}'
            self._event('drawdown_halt', closed, equity_eur=equity, peak_eur=self.peak)
        stamp = closed.isoformat()
        if self.curve and self.curve[-1][0] == stamp:
            self.curve[-1][1] = equity
        else:
            self.curve.append([stamp, equity])
        if len(self.curve) > 6000:
            half = len(self.curve) // 2
            self.curve = self.curve[:half:2] + self.curve[half:]
        self.clock = stamp

    def _accrue(self, symbol: str, until: datetime, profile: MarketProfile) -> None:
        for p in self.positions:
            if p['symbol'] != symbol:
                continue
            start = _time(p['accrued_to'])
            if until <= start:
                continue
            rate = profile.funding_rate_annual + (profile.borrow_rate_annual if p['quantity'] < 0 else 0.)
            carry = abs(p['quantity']) * self.marks.get(symbol, p['entry_price_eur']) * rate * (until - start).total_seconds() / (365 * 86400)
            self.cash -= carry
            p['carry_eur'] += carry
            p['accrued_to'] = until.isoformat()

    def _fill_pending(self, symbol: str, opened: datetime, open_eur: float, profile: MarketProfile) -> None:
        keep = []
        for order in self.pending:
            if order['symbol'] != symbol:
                keep.append(order)
                continue
            created = _time(order['created_at'])
            if opened < created:
                keep.append(order)
                continue
            reason = None
            if self.halted:
                reason = 'drawdown_halt'
            elif opened - created > MAX_FILL_DELAY:
                reason = 'no_executable_bar_within_30_minutes'
            elif any(p['symbol'] == symbol for p in self.positions):
                reason = 'symbol_already_has_position'
            if reason:
                self._event('order_cancelled', opened, symbol=symbol, strategy_id=order['strategy_id'], reason=reason,
                            decision_id=order['decision_id'])
                continue
            direction = 1 if order['side'] == 'long' else -1
            price = open_eur * (1 + direction * (profile.slippage_bps_per_side + profile.spread_bps / 2) / 10000)
            quantity = order['quantity']
            fee_rate = profile.fee_bps_per_side / 10000
            if direction > 0:
                affordable = max(0., self.cash) / (price * (1 + fee_rate))
                if quantity > affordable:
                    step = order['quantity_step']
                    quantity = math.floor(affordable / step) * step
            notional = quantity * price
            fee = max(profile.minimum_fee_eur_per_side, notional * fee_rate)
            if quantity <= 0 or notional < profile.minimum_order_eur:
                self._event('order_cancelled', opened, symbol=symbol, strategy_id=order['strategy_id'],
                            reason='unaffordable_or_below_minimum_at_fill', decision_id=order['decision_id'])
                continue
            self.cash -= direction * quantity * price + fee
            position = dict(id=order['id'], decision_id=order['decision_id'], symbol=symbol, strategy_id=order['strategy_id'],
                            side=order['side'], quantity=direction * quantity, entry_price_eur=price, entry_fee_eur=fee,
                            carry_eur=0., entered_at=opened.isoformat(), accrued_to=opened.isoformat(),
                            stop_distance_pct=order['stop_distance_pct'],
                            stop_price_eur=price * (1 - direction * order['stop_distance_pct']),
                            notional_eur=notional, estimated_stop_risk_eur=order['estimated_stop_risk_eur'])
            self.positions.append(position)
            self.marks[symbol] = open_eur
            self._event('filled', opened, symbol=symbol, strategy_id=order['strategy_id'], side=order['side'],
                        price_eur=price, quantity=quantity, fee_eur=fee, decision_id=order['decision_id'])
        self.pending = keep

    def _close(self, position: dict, price_eur: float, at: datetime, reason: str, profile: MarketProfile) -> None:
        direction = 1 if position['quantity'] > 0 else -1
        execution = price_eur * (1 - direction * (profile.slippage_bps_per_side + profile.spread_bps / 2) / 10000)
        quantity = position['quantity']
        fee = max(profile.minimum_fee_eur_per_side, abs(quantity) * execution * profile.fee_bps_per_side / 10000)
        self.cash += quantity * execution - fee
        pnl = quantity * (execution - position['entry_price_eur']) - position['entry_fee_eur'] - fee - position['carry_eur']
        held = (at - _time(position['entered_at'])).total_seconds() / 3600
        trade = dict(id=position['id'], decision_id=position['decision_id'], symbol=position['symbol'],
                     strategy_id=position['strategy_id'], side=position['side'], quantity=abs(quantity),
                     entered_at=position['entered_at'], exited_at=at.isoformat(), held_hours=held,
                     entry_price_eur=position['entry_price_eur'], exit_price_eur=execution,
                     fees_eur=position['entry_fee_eur'] + fee, carry_eur=position['carry_eur'], pnl_eur=pnl,
                     return_pct=pnl / position['notional_eur'] * 100 if position['notional_eur'] else 0., reason=reason)
        self.positions.remove(position)
        self.trades.append(trade)
        del self.trades[:-500]
        s = self.stats
        s['closed'] += 1
        s['wins'] += int(pnl > 0)
        s['pnl_eur'] += pnl
        s['fees_eur'] += trade['fees_eur']
        s['carry_eur'] += trade['carry_eur']
        by = s['by_strategy'].setdefault(position['strategy_id'], dict(trades=0, wins=0, pnl_eur=0.))
        by['trades'] += 1
        by['wins'] += int(pnl > 0)
        by['pnl_eur'] += pnl
        self.marks[position['symbol']] = price_eur
        self._event('closed', at, symbol=position['symbol'], strategy_id=position['strategy_id'], side=position['side'],
                    reason=reason, pnl_eur=pnl, decision_id=position['decision_id'])

    def acknowledge_halt(self, at: datetime) -> dict:
        if not self.halted:
            raise ValueError('No drawdown halt is active')
        if self.positions or self.pending:
            raise ValueError('Halt can be acknowledged only after liquidation completes')
        event = dict(previous_reason=self.halt_reason, peak_reset_from=self.peak, peak_reset_to=self.equity())
        self.halted, self.halt_reason, self.peak = False, None, self.equity()
        self._event('halt_acknowledged', _time(at), **event)
        return event

    # ---- views ----
    def portfolio(self) -> dict:
        reserved = [dict(symbol=o['symbol'], notional_eur=o['notional_eur'], estimated_stop_risk_eur=o['estimated_stop_risk_eur'],
                         opened_at=o['created_at']) for o in self.pending]
        held = [dict(symbol=p['symbol'], notional_eur=abs(p['quantity']) * self.marks.get(p['symbol'], p['entry_price_eur']),
                     estimated_stop_risk_eur=p['estimated_stop_risk_eur'], opened_at=p['entered_at']) for p in self.positions]
        equity = self.equity()
        return dict(equity_eur=equity, cash_eur=self.cash, peak_equity_eur=max(self.peak, equity),
                    day_start_equity_eur=self.day_start_equity,
                    gross_exposure_eur=self.gross() + sum(o['notional_eur'] for o in self.pending),
                    open_positions=held + reserved)

    def summary(self) -> dict:
        equity = self.equity()
        positions = [dict(p, mark_eur=self.marks.get(p['symbol']),
                          unrealized_eur=p['quantity'] * (self.marks.get(p['symbol'], p['entry_price_eur']) - p['entry_price_eur'])
                          - p['entry_fee_eur'] - p['carry_eur']) for p in self.positions]
        return dict(initial_eur=self.initial_eur, equity_eur=equity, cash_eur=self.cash, peak_eur=self.peak,
                    return_pct=(equity / self.initial_eur - 1) * 100,
                    drawdown_pct=(1 - equity / self.peak) * 100 if self.peak else 0.,
                    day_start_equity_eur=self.day_start_equity, gross_exposure_eur=self.gross(),
                    halted=self.halted, halt_reason=self.halt_reason, clock=self.clock,
                    positions=positions, pending=self.pending[:], stats=self.stats,
                    win_rate_pct=self.stats['wins'] / self.stats['closed'] * 100 if self.stats['closed'] else None)

    # ---- persistence ----
    _FIELDS = ('initial_eur', 'cash', 'peak', 'day', 'day_start_equity', 'halted', 'halt_reason', 'max_drawdown_pct',
               'positions', 'pending', 'marks', 'trades', 'stats', 'curve', 'events', 'clock')

    def to_state(self) -> dict:
        return {k: getattr(self, k) for k in self._FIELDS}

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> 'PaperLedger':
        ledger = cls(state['initial_eur'])
        for k in cls._FIELDS:
            setattr(ledger, k, state[k])
        if not all(math.isfinite(v) for v in (ledger.cash, ledger.peak, ledger.day_start_equity)):
            raise ValueError('Invalid ledger checkpoint')
        return ledger
