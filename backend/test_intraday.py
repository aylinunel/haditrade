"""Synthetic invariant tests, not market backtests or evidence of profitability."""
import numpy as np
import pandas as pd
import pytest
from backend.intraday import (STRATEGY_IDS, IntradayConfig, QuantDeveloperAgent,
                              backtest_intraday, strategy_signals, validate_intraday_bars)


def bars(n=300, slope=0., symbol='TEST', start='2026-01-01T00:00:00Z'):
    price = 100 + np.arange(n) * slope
    return pd.DataFrame(dict(timestamp=pd.date_range(start, periods=n, freq='15min'),
        symbol=symbol, open=price, high=price + .02, low=price - .02, close=price,
        volume=100., fx_to_eur=1., asset_class='stock'))


def targets(frame, side=1, from_index=20):
    out = {(s, st): np.zeros(len(g), dtype=int) for s, g in frame.groupby('symbol') for st in STRATEGY_IDS}
    for s, _ in frame.groupby('symbol'): out[(s, 'sma')][from_index:] = side
    return out


def test_candidates_are_prefix_causal():
    df = bars(slope=.03)
    for strategy in STRATEGY_IDS:
        full = strategy_signals(df, strategy)
        assert np.array_equal(full[:80], strategy_signals(df.iloc[:80], strategy))
        changed = df.copy()
        changed.loc[80:, ['open', 'close']] = 80
        changed.loc[80:, 'high'] = 80.02
        changed.loc[80:, 'low'] = 79.98
        assert np.array_equal(full[:80], strategy_signals(changed, strategy)[:80])


def test_long_fills_next_open_and_never_exceeds_48h_when_tradable():
    df = bars()
    result = backtest_intraday(df, signal_overrides=targets(df))
    first = result['trades'][0]
    assert pd.Timestamp(first['entered_at']) == df.timestamp.iloc[21]
    assert first['reason'] == '48h_horizon_first_executable_bar'
    assert first['held_hours'] == 48
    assert first['beyond_48h'] is False
    assert first['entry_price_eur'] > 100
    assert result['metrics']['final_equity_eur'] < 500


def test_short_signed_accounting_and_all_costs_reconcile():
    df = bars(n=120, slope=-.01)
    result = backtest_intraday(df, signal_overrides=targets(df, -1))
    assert result['trades'][0]['side'] == 'short'
    assert result['trades'][0]['carry_eur'] > 0
    assert result['metrics']['final_equity_eur'] > 500
    assert np.isclose(result['metrics']['final_equity_eur'] - 500,
                      sum(t['pnl_eur'] for t in result['trades']))
    assert not result['unliquidated_positions']


def test_gap_horizon_closes_first_executable_bar_no_invented_fill():
    df = bars(n=240)
    df = df.drop(index=range(200, 225)).reset_index(drop=True)
    result = backtest_intraday(df, signal_overrides=targets(df))
    first = result['trades'][0]
    assert first['held_hours'] == 51
    assert first['beyond_48h']
    assert pd.Timestamp(first['exited_at']) == df.timestamp.iloc[200]


def test_stale_signals_dont_open_after_gap():
    df = bars(n=30)
    df.loc[21:, 'timestamp'] += pd.Timedelta(days=3)
    override = targets(df)
    for key in override: override[key][21:] = 0
    result = backtest_intraday(df, signal_overrides=override)
    assert not result['trades']
    assert not [d for d in result['decisions'] if d['action'] == 'open']


def test_fx_prices_and_fees_are_eur():
    df = bars(n=40)
    df['fx_to_eur'] = .8
    result = backtest_intraday(df, signal_overrides=targets(df))
    first = result['trades'][0]
    assert np.isclose(first['entry_price_eur'], 80 * 1.0005)
    assert np.isclose(first['entry_fee_eur'], first['quantity'] * first['entry_price_eur'] * .0005)


def test_multi_asset_shared_capital_and_symbol_limit():
    df = pd.concat([bars(n=40, symbol='A'), bars(n=40, symbol='B')]).sort_values(['timestamp', 'symbol'])
    override = targets(df)
    for key in override: override[key][20:] = 1
    result = backtest_intraday(df, signal_overrides=override)
    assert max(t['gross_exposure_eur'] for t in result['trace']) <= 150.1
    assert max(t['open_positions'] for t in result['trace']) <= 3
    assert all(t['gross_exposure_eur'] <= t['equity_eur'] + .1 for t in result['trace'])


def test_untradable_terminal_retains_position_without_fabricated_fill():
    df = bars(n=40)
    df.loc[39, 'volume'] = 0
    result = backtest_intraday(df, signal_overrides=targets(df))
    assert result['unliquidated_positions']
    assert not result['trades']
    assert result['trace'][-1]['open_positions'] == 1


def test_synthetic_short_gap_loss_and_drawdown_halt():
    df = bars(n=70)
    df.loc[35:, ['open', 'close']] = 150
    df.loc[35:, 'high'] = 150.02
    df.loc[35:, 'low'] = 149.98
    cfg = IntradayConfig(max_drawdown_pct=.03)
    result = backtest_intraday(df, cfg, targets(df, -1))
    assert any(t['halted'] for t in result['trace'])
    assert not any(d['action'] == 'open' and pd.Timestamp(d['timestamp']) > df.timestamp.iloc[35] for d in result['decisions'])
    assert result['trades'][0]['pnl_eur'] < -20


def test_invalid_contracts_duplicates_prices_and_leverage_rejected():
    df = bars(n=40)
    with pytest.raises(ValueError): validate_intraday_bars(pd.concat([df, df.iloc[:1]]))
    df.loc[0, 'fx_to_eur'] = 0
    with pytest.raises(ValueError): validate_intraday_bars(df)
    df = bars(n=40); df['asset_class'] = 'options'
    with pytest.raises(ValueError, match='Contract-aware'): backtest_intraday(df)
    with pytest.raises(ValueError): IntradayConfig(max_gross_exposure_pct=1.01)


def test_proposer_requires_completed_fresh_bars_and_fraction_stop():
    df = bars(n=60, slope=.03)
    agent = QuantDeveloperAgent()
    now = df.timestamp.iloc[-1] + pd.Timedelta(minutes=15)
    proposals = agent.propose(df, now)
    assert proposals
    assert all(0 < p['stop_distance_pct'] <= .05 for p in proposals)
    assert not agent.propose(df, now + pd.Timedelta(hours=1))
    assert agent.evaluate(dict(proposal=proposals[0], bars=df, market_state={'now': now}))['approved']
    future = dict(proposals[0], observed_at=(now + pd.Timedelta(hours=1)).isoformat())
    assert not agent.evaluate(dict(proposal=future, market_state={'now': now}))['approved']


def test_naive_timestamps_and_relaxed_risk_caps_rejected():
    df = bars(n=40)
    df['timestamp'] = df.timestamp.dt.tz_localize(None)
    with pytest.raises(ValueError, match='timezone'): validate_intraday_bars(df)
    for kwargs in ({'risk_per_position_pct': .006}, {'daily_loss_limit_pct': .02},
                   {'max_holding_hours': 49}, {'max_concurrent_positions': 4}):
        with pytest.raises(ValueError): IntradayConfig(**kwargs)


def test_incomplete_future_bars_do_not_generate_proposals():
    df = bars(n=60, slope=.03)
    assert not QuantDeveloperAgent().propose(df, df.timestamp.iloc[0] - pd.Timedelta(hours=1))


def test_stop_risk_reserves_two_fees_slips_and_full_horizon_carry():
    df = bars(n=40)
    cfg = IntradayConfig(minimum_commission_eur=.7, slippage_bps=100,
                         short_borrow_annual_pct=.2, funding_annual_pct=.05)
    result = backtest_intraday(df, cfg, targets(df, -1))
    opened = next(d for d in result['decisions'] if d['action'] == 'open')
    n, stop = opened['exposure_eur'], opened['stop_distance_pct']
    expected_cost = (.7 + .7 + n * (2 * .01 + .25 * 48 / (24 * 365) * (1 + stop)))
    assert np.isclose(opened['estimated_roundtrip_cost_eur'], expected_cost)
    assert np.isclose(opened['estimated_stop_risk_eur'], n * stop + expected_cost)
    assert opened['estimated_stop_risk_eur'] <= 2.5 + 1e-9
    assert n < 50  # Old stop-only sizing would spend the full EUR 50 cap.


def test_same_open_entries_share_daily_risk_and_observe_paid_fees():
    df = pd.concat([bars(n=40, symbol=s) for s in ('A', 'B', 'C')]).sort_values(['timestamp', 'symbol'])
    cfg = IntradayConfig(minimum_commission_eur=.7, slippage_bps=100)
    result = backtest_intraday(df, cfg, targets(df))
    opens = [d for d in result['decisions'] if d['action'] == 'open']
    initial_opens = [d for d in opens if d['timestamp'] == opens[0]['timestamp']]
    assert len(initial_opens) == 2
    assert initial_opens[1]['observed_equity_eur'] < initial_opens[0]['observed_equity_eur']
    assert initial_opens[1]['daily_loss_eur'] > 0
    for event in opens:
        assert event['estimated_stop_risk_eur'] <= 2.5 + 1e-9
        assert event['reserved_position_risk_eur'] + event['estimated_stop_risk_eur'] + event['daily_loss_eur'] <= 7.5 + 1e-9
    assert any(d['action'] == 'reject' and 'round-trip costs' in d['reason'] for d in result['decisions'])
