"""Deterministic portfolio sizing. No AI-generated order can relax these limits.

All rates ending in ``pct`` are fractions (0.005 = 0.5%). Prices are quote
currency; portfolio money is EUR. Market cost/edge estimates must come from
validated observations, never proposal confidence. This module cannot trade live.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from math import isfinite
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class RiskPolicy:
    initial_capital_eur: float = 500.0
    risk_per_trade_pct: float = 0.005
    daily_loss_pct: float = 0.015
    max_drawdown_pct: float = 0.08
    max_gross_exposure_pct: float = 1.0
    max_position_pct: float = 0.20
    max_concurrent_positions: int = 3
    max_holding_hours: float = 48.0
    max_market_age_seconds: float = 60.0
    max_signal_age_seconds: float = 3600.0
    minimum_edge_samples: int = 20
    leverage_enabled: bool = False
    live_shorting_enabled: bool = False
    live_derivatives_enabled: bool = False
    execution_mode: str = 'paper'

    def __post_init__(self):
        # User changes can tighten this policy, never silently relax it.
        defaults = {'initial_capital_eur': 500., 'risk_per_trade_pct': .005,
                    'daily_loss_pct': .015, 'max_drawdown_pct': .08,
                    'max_gross_exposure_pct': 1., 'max_position_pct': .2,
                    'max_holding_hours': 48., 'max_market_age_seconds': 60.,
                    'max_signal_age_seconds': 3600.}
        for key, limit in defaults.items():
            value = getattr(self, key)
            if isinstance(value, bool) or not isfinite(value) or not 0 < value <= limit:
                raise ValueError(f'{key} must be positive and no greater than {limit}')
        if not isinstance(self.max_concurrent_positions, int) or not 1 <= self.max_concurrent_positions <= 3:
            raise ValueError('max_concurrent_positions must be 1 to 3')
        if not isinstance(self.minimum_edge_samples, int) or self.minimum_edge_samples < 20:
            raise ValueError('minimum_edge_samples must be at least 20')
        if any((self.leverage_enabled, self.live_shorting_enabled, self.live_derivatives_enabled)) or self.execution_mode != 'paper':
            raise ValueError('Only unleveraged paper execution is implemented')

    def snapshot(self) -> dict:
        return asdict(self)


DEFAULT_POLICY = RiskPolicy()


class PaperExecutionAdapter(Protocol):
    """A future simulator boundary; never pass a live brokerage adapter here."""
    mode: str

    def submit_paper_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def close_paper_position(self, symbol: str, reason: str) -> Mapping[str, Any]: ...


def _number(data, key, *, minimum=0.0, maximum=None):
    value = data[key]
    if isinstance(value, bool):
        raise ValueError(f'{key} is not a number')
    value = float(value)
    if not isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f'{key} is outside its valid range')
    return value


def _time(value):
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamps must include timezone')
    return result.astimezone(timezone.utc)


class QuantTraderAgent:
    role = 'quant_trader'

    def __init__(self, policy: RiskPolicy = DEFAULT_POLICY):
        self.policy = policy

    def evaluate(self, proposal: Mapping, account: Mapping | None = None,
                 market: Mapping | None = None) -> dict:
        """Return an explainable paper order, or fail closed.

        Also accepts a team context containing proposal, portfolio/account and
        market_state/market. Cost estimates model both entry and exit. The risk
        budget includes stop loss plus estimated round-trip costs, but cannot
        guarantee a realised loss cap when prices gap or liquidity disappears.
        """
        if account is None and market is None and isinstance(proposal, Mapping):
            context = proposal
            proposal = context.get('proposal', {})
            account = context.get('account', context.get('portfolio', {}))
            market = context.get('market', context.get('market_state', {}))
        account, market = account or {}, market or {}
        result = {'role': self.role, 'approved': False, 'eligible': False,
                  'outcome': 'rejected', 'reasons': [], 'order': None,
                  'audit': {'policy': self.policy.snapshot()}}
        reasons, audit, policy = result['reasons'], result['audit'], self.policy
        try:
            if not all(isinstance(value, Mapping) for value in (proposal, account, market)):
                raise ValueError('proposal, account and market must be mappings')
            now = _time(market['now'])
            for value, cap, label in [(market['observed_at'], policy.max_market_age_seconds, 'market'),
                                      (proposal['observed_at'], policy.max_signal_age_seconds, 'signal')]:
                age = (now - _time(value)).total_seconds()
                if age < 0 or age > cap:
                    reasons.append(f'{label}_timestamp_stale_or_future')
            if market.get('data_valid') is not True:
                reasons.append('market_data_not_validated')
            if market.get('market_open') is not True:
                reasons.append('market_not_open')
            symbol, strategy = proposal['symbol'], proposal['strategy_id']
            if not isinstance(symbol, str) or not symbol.strip() or not isinstance(strategy, str) or not strategy.strip():
                reasons.append('missing_symbol_or_strategy')
            if not proposal.get('evidence'):
                reasons.append('missing_proposal_evidence')
            side = proposal['side']
            if side not in ('long', 'short'):
                reasons.append('unsupported_side')
            _number(proposal, 'confidence', maximum=1.)
            stop = _number(proposal, 'stop_distance_pct', minimum=1e-9, maximum=1.)
            reference = _number(proposal, 'entry_reference_price', minimum=1e-9)
            price = _number(market, 'price', minimum=1e-9)
            if abs(price / reference - 1) > stop / 2:
                reasons.append('price_moved_too_far_from_signal')
            fx = _number(market, 'currency_to_eur', minimum=1e-9)
            multiplier = _number(market, 'contract_multiplier', minimum=1e-9)
            asset = market['asset_class']
            if asset not in ('equity', 'etf', 'crypto', 'forex', 'future', 'cfd', 'option'):
                reasons.append('unsupported_asset_class')
            if asset == 'option':
                reasons.append('options_require_payoff_aware_risk_model')
            if asset in ('future', 'cfd') and market.get('linear_contract') is not True:
                reasons.append('derivative_requires_linear_contract_metadata')
            horizon = _number(market, 'holding_hours', minimum=1e-9, maximum=policy.max_holding_hours)
            if side == 'short':
                if market.get('short_supported') is not True or market.get('borrow_available') is not True:
                    reasons.append('short_support_or_borrow_not_verified')
                borrow = _number(market, 'borrow_rate_annual')
                funding = _number(market, 'funding_rate_annual')
                carry = (borrow + funding) * horizon / (24 * 365)
            else:
                carry = _number(market, 'funding_rate_annual') * horizon / (24 * 365)
            fee_rate = _number(market, 'fee_bps_per_side') / 10000
            minimum_fee = _number(market, 'minimum_fee_eur_per_side')
            spread = _number(market, 'spread_bps') / 10000
            slippage = _number(market, 'slippage_bps_per_side') / 10000
            fx_cost = _number(market, 'fx_cost_bps_roundtrip') / 10000
            min_order = _number(market, 'minimum_order_eur', minimum=1e-9)
            step = _number(market, 'quantity_step', minimum=1e-12)
            expectancy = _number(market, 'expected_return_pct', minimum=-1., maximum=1.)
            samples = _number(market, 'expected_return_sample_count', minimum=0)
            if samples < policy.minimum_edge_samples or not market.get('expected_return_source'):
                reasons.append('expected_edge_not_supported_by_enough_observations')
            if market.get('expected_return_validated') is not True:
                reasons.append('expected_edge_not_validated')
            equity = _number(account, 'equity_eur', minimum=1e-9)
            cash = _number(account, 'cash_eur')
            peak = _number(account, 'peak_equity_eur', minimum=equity)
            day_start = _number(account, 'day_start_equity_eur', minimum=1e-9)
            gross = _number(account, 'gross_exposure_eur')
            positions = account['open_positions']
            if not isinstance(positions, list) or any(not isinstance(position, Mapping) for position in positions):
                raise ValueError('open_positions must be a list of mappings')
            if len(positions) >= policy.max_concurrent_positions:
                reasons.append('max_concurrent_positions_reached')
            if any(p.get('symbol') == symbol for p in positions):
                reasons.append('existing_symbol_position_requires_netting_or_exit')
            position_gross = sum(_number(p, 'notional_eur') for p in positions)
            gross = max(gross, position_gross)
            for position in positions:
                if (now - _time(position['opened_at'])).total_seconds() >= policy.max_holding_hours * 3600:
                    reasons.append('existing_position_requires_48h_exit')
                    break
            daily_loss = max(0., day_start - equity)
            drawdown = (peak - equity) / peak
            # Fixed initial-capital ceilings avoid loosening budgets after a winning day.
            budget_base = min(equity, policy.initial_capital_eur)
            day_limit = min(day_start, policy.initial_capital_eur) * policy.daily_loss_pct
            daily_remaining = max(0., day_limit - daily_loss)
            if daily_remaining <= 1e-9:
                reasons.append('daily_loss_limit_reached')
            if drawdown >= policy.max_drawdown_pct - 1e-12:
                reasons.append('drawdown_halt')
            reserved_risk = sum(_number(p, 'estimated_stop_risk_eur') for p in positions)
            available_daily_risk = max(0., daily_remaining - reserved_risk)
            risk_budget = min(budget_base * policy.risk_per_trade_pct, available_daily_risk,
                              max(0., peak * policy.max_drawdown_pct - (peak - equity)))
            gross_remaining = max(0., equity * policy.max_gross_exposure_pct - gross)
            cap = min(budget_base * policy.max_position_pct, gross_remaining, cash)
            def costs(notional):
                # Price at an adverse stop can increase exit fees on a short.
                exit_notional = notional * (1 + stop)
                return (max(minimum_fee, notional * fee_rate) +
                        max(minimum_fee, exit_notional * fee_rate) +
                        notional * (spread + 2 * slippage + fx_cost + carry))
            # Monotonic bisection handles minimum commissions and cash affordability.
            lo, hi = 0., max(0., cap)
            for _ in range(64):
                mid = (lo + hi) / 2
                entry_cost = max(minimum_fee, mid * fee_rate) + mid * (spread / 2 + slippage + fx_cost / 2)
                if mid * stop + costs(mid) <= risk_budget and mid + entry_cost <= cash:
                    lo = mid
                else:
                    hi = mid
            unit_notional = price * fx * multiplier
            if not isfinite(unit_notional) or unit_notional <= 0:
                raise ValueError('invalid EUR unit notional')
            quantity = float((Decimal(str(lo / unit_notional)) / Decimal(str(step))).to_integral_value(rounding=ROUND_FLOOR) * Decimal(str(step)))
            notional = quantity * price * fx * multiplier
            cost = costs(notional)
            edge = notional * expectancy - cost
            audit.update(risk_budget_eur=risk_budget, daily_loss_eur=daily_loss,
                         daily_remaining_eur=daily_remaining, reserved_position_risk_eur=reserved_risk,
                         available_daily_risk_eur=available_daily_risk, drawdown_pct=drawdown,
                         gross_remaining_eur=gross_remaining, roundtrip_cost_eur=cost,
                         expected_gross_edge_eur=notional * expectancy,
                         expected_net_edge_eur=edge, estimated_stop_risk_eur=notional * stop + cost,
                         expected_return_source=market.get('expected_return_source'),
                         expected_return_sample_count=samples,
                         note='Stop risk is estimated; gaps and execution failure can exceed limits.')
            if quantity <= 0 or notional + 1e-9 < min_order:
                reasons.append('order_below_minimum_or_unaffordable_after_fees')
            if edge <= 0:
                reasons.append('expected_edge_does_not_cover_roundtrip_costs')
            if not reasons:
                result.update(approved=True, eligible=True, outcome='paper_only',
                    reasons=['within_immutable_risk_limits', 'expected_edge_exceeds_estimated_costs'],
                    order={'symbol': symbol, 'strategy_id': strategy, 'side': side,
                           'quantity': quantity, 'notional_eur': notional, 'mode': 'paper',
                           'entry_reference_price': price, 'stop_distance_pct': stop,
                           'max_holding_hours': horizon, 'expected_net_edge_eur': edge,
                           'estimated_roundtrip_cost_eur': cost,
                           'estimated_stop_risk_eur': notional * stop + cost,
                           'evaluated_at': now.isoformat()})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            reasons.append(f'incomplete_or_invalid_risk_inputs:{exc}')
        return result

    def execute_paper(self, decision: Mapping, adapter: PaperExecutionAdapter) -> Mapping:
        """Explicit simulation only. Never used automatically by evaluate()."""
        if adapter.mode != 'paper' or decision.get('approved') is not True or decision.get('order', {}).get('mode') != 'paper':
            raise ValueError('Only an approved paper order and paper adapter are permitted')
        return adapter.submit_paper_order(decision['order'])
