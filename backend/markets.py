"""Per-instrument paper-trading assumptions: costs, sessions, shortability.

Every number here is an editable research assumption, not a verified broker
quote. Costs are basis points of notional. Values feed both the learner's label
costs and the trader's sizing, so the edge it learns is net of the same costs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo('America/New_York')


@dataclass(frozen=True)
class MarketProfile:
    symbol: str
    display: str
    asset_class: str  # 'crypto' | 'etf' | 'equity'
    quote_currency: str
    venue: str
    session: str  # 'always' | 'us_equity'
    fee_bps_per_side: float
    spread_bps: float
    slippage_bps_per_side: float
    fx_cost_bps_roundtrip: float
    minimum_fee_eur_per_side: float
    minimum_order_eur: float
    quantity_step: float
    paper_short_allowed: bool
    borrow_rate_annual: float
    funding_rate_annual: float = 0.

    def __post_init__(self):
        if self.asset_class not in ('crypto', 'etf', 'equity') or self.session not in ('always', 'us_equity'):
            raise ValueError('Unsupported asset class or session')
        numbers = [self.fee_bps_per_side, self.spread_bps, self.slippage_bps_per_side, self.fx_cost_bps_roundtrip,
                   self.minimum_fee_eur_per_side, self.borrow_rate_annual, self.funding_rate_annual]
        if any(not 0 <= v < 10000 for v in numbers) or self.minimum_order_eur <= 0 or self.quantity_step <= 0:
            raise ValueError('Costs must be nonnegative; order minimum and quantity step positive')

    def is_open(self, at: datetime) -> bool:
        if self.session == 'always':
            return True
        local = at.astimezone(NEW_YORK)
        # Regular session only; exchange holidays and half days are NOT modeled.
        return local.weekday() < 5 and time(9, 30) <= local.time() < time(16, 0)

    def roundtrip_cost_bps(self, holding_hours: float = 48., *, include_borrow: bool = True) -> float:
        carry = self.funding_rate_annual + (self.borrow_rate_annual if include_borrow and self.paper_short_allowed else 0.)
        return (2 * self.fee_bps_per_side + self.spread_bps + 2 * self.slippage_bps_per_side
                + self.fx_cost_bps_roundtrip + carry * 10000 * holding_hours / (24 * 365))

    def snapshot(self) -> dict:
        return {**asdict(self), 'roundtrip_cost_bps_48h': self.roundtrip_cost_bps()}


DEFAULT_PROFILES: dict[str, MarketProfile] = {p.symbol: p for p in [
    MarketProfile('BTCUSDT', 'Bitcoin', 'crypto', 'USDT', 'Binance spot (public data)', 'always',
                  fee_bps_per_side=10, spread_bps=1, slippage_bps_per_side=3, fx_cost_bps_roundtrip=0,
                  minimum_fee_eur_per_side=0, minimum_order_eur=5, quantity_step=1e-5,
                  paper_short_allowed=True, borrow_rate_annual=.10),
    MarketProfile('ETHUSDT', 'Ether', 'crypto', 'USDT', 'Binance spot (public data)', 'always',
                  fee_bps_per_side=10, spread_bps=1, slippage_bps_per_side=3, fx_cost_bps_roundtrip=0,
                  minimum_fee_eur_per_side=0, minimum_order_eur=5, quantity_step=1e-4,
                  paper_short_allowed=True, borrow_rate_annual=.10),
    MarketProfile('SOLUSDT', 'Solana', 'crypto', 'USDT', 'Binance spot (public data)', 'always',
                  fee_bps_per_side=10, spread_bps=2, slippage_bps_per_side=5, fx_cost_bps_roundtrip=0,
                  minimum_fee_eur_per_side=0, minimum_order_eur=5, quantity_step=1e-3,
                  paper_short_allowed=True, borrow_rate_annual=.12),
    MarketProfile('SPY', 'S&P 500 ETF', 'etf', 'USD', 'Alpaca IEX (requires your keys)', 'us_equity',
                  fee_bps_per_side=0, spread_bps=1, slippage_bps_per_side=2, fx_cost_bps_roundtrip=4,
                  minimum_fee_eur_per_side=0, minimum_order_eur=1, quantity_step=1e-4,
                  paper_short_allowed=False, borrow_rate_annual=0),
    MarketProfile('QQQ', 'Nasdaq-100 ETF', 'etf', 'USD', 'Alpaca IEX (requires your keys)', 'us_equity',
                  fee_bps_per_side=0, spread_bps=1, slippage_bps_per_side=2, fx_cost_bps_roundtrip=4,
                  minimum_fee_eur_per_side=0, minimum_order_eur=1, quantity_step=1e-4,
                  paper_short_allowed=False, borrow_rate_annual=0),
]}


def profile_for(symbol: str, shorts_enabled: bool = True) -> MarketProfile:
    if symbol not in DEFAULT_PROFILES:
        raise ValueError(f'No market profile for {symbol}; add one in backend/markets.py')
    profile = DEFAULT_PROFILES[symbol]
    return profile if shorts_enabled or not profile.paper_short_allowed else replace(profile, paper_short_allowed=False)


def market_state(profile: MarketProfile, *, observed_at: str, now: datetime, price: float, fx_to_eur: float) -> dict:
    """Trader/skeptic market snapshot. Expected-return fields are added per proposal."""
    return dict(data_valid=True, observed_at=observed_at, now=now.astimezone(timezone.utc).isoformat(), price=price,
                currency_to_eur=fx_to_eur, contract_multiplier=1., asset_class=profile.asset_class,
                holding_hours=48., market_open=profile.is_open(now),
                short_supported=profile.paper_short_allowed, borrow_available=profile.paper_short_allowed,
                borrow_rate_annual=profile.borrow_rate_annual, funding_rate_annual=profile.funding_rate_annual,
                fee_bps_per_side=profile.fee_bps_per_side, minimum_fee_eur_per_side=profile.minimum_fee_eur_per_side,
                spread_bps=profile.spread_bps, slippage_bps_per_side=profile.slippage_bps_per_side,
                fx_cost_bps_roundtrip=profile.fx_cost_bps_roundtrip, minimum_order_eur=profile.minimum_order_eur,
                quantity_step=profile.quantity_step,
                short_assumption='paper margin borrow assumed available' if profile.paper_short_allowed else 'shorting disabled')
