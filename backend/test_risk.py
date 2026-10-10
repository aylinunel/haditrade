from dataclasses import FrozenInstanceError
import pytest
from backend.risk import QuantTraderAgent, RiskPolicy


def inputs():
    proposal = dict(symbol='BTC/EUR', strategy_id='rsi', side='long', confidence=.8,
                    entry_reference_price=100., stop_distance_pct=.01,
                    evidence=['Completed bar mean-reversion setup'], observed_at='2026-10-10T00:00:00Z')
    account = dict(equity_eur=500., cash_eur=500., peak_equity_eur=500., day_start_equity_eur=500.,
                   gross_exposure_eur=0., open_positions=[])
    market = dict(now='2026-10-10T00:00:00Z', observed_at='2026-10-10T00:00:00Z',
                  data_valid=True, market_open=True, price=100., currency_to_eur=1.,
                  asset_class='crypto', contract_multiplier=1., holding_hours=48.,
                  fee_bps_per_side=10., minimum_fee_eur_per_side=0., spread_bps=2.,
                  slippage_bps_per_side=2., fx_cost_bps_roundtrip=0.,
                  funding_rate_annual=0., minimum_order_eur=5., quantity_step=.001,
                  expected_return_pct=.03, expected_return_source='walk-forward-v1',
                  expected_return_sample_count=100, expected_return_validated=True)
    return proposal, account, market


def test_defaults_and_immutability():
    policy = RiskPolicy()
    assert policy.risk_per_trade_pct * 500 == 2.5
    assert policy.daily_loss_pct * 500 == 7.5
    assert policy.max_drawdown_pct * 500 == 40
    with pytest.raises(FrozenInstanceError):
        policy.risk_per_trade_pct = 1
    with pytest.raises(ValueError):
        RiskPolicy(risk_per_trade_pct=.01)
    with pytest.raises(ValueError):
        RiskPolicy(execution_mode='live')


def test_size_and_costs_are_transparent():
    decision = QuantTraderAgent().evaluate(*inputs())
    assert decision['approved']
    assert decision['order']['notional_eur'] <= 100
    assert decision['audit']['estimated_stop_risk_eur'] <= 2.5
    assert decision['order']['expected_net_edge_eur'] > 0
    assert decision['order']['mode'] == 'paper'


def test_context_contract():
    p, a, m = inputs()
    assert QuantTraderAgent().evaluate(dict(proposal=p, portfolio=a, market_state=m))['approved']


@pytest.mark.parametrize('key,value,reason', [
    ('observed_at', '2026-10-09T23:00:00Z', 'market_timestamp'),
    ('observed_at', '2026-10-10T00:01:00Z', 'market_timestamp'),
    ('expected_return_pct', .00001, 'expected_edge_does_not_cover'),
    ('expected_return_sample_count', 19, 'expected_edge_not_supported'),
    ('expected_return_validated', False, 'expected_edge_not_validated'),
    ('minimum_fee_eur_per_side', 1.5, 'order_below_minimum'),
    ('holding_hours', 49, 'incomplete_or_invalid'),
    ('price', 105, 'price_moved_too_far'),
    ('data_valid', False, 'market_data_not_validated'),
])
def test_fail_closed(key, value, reason):
    p, a, m = inputs()
    m[key] = value
    decision = QuantTraderAgent().evaluate(p, a, m)
    assert not decision['approved']
    assert any(reason in entry for entry in decision['reasons'])


def test_drawdown_and_daily_loss_halt():
    p, a, m = inputs()
    a['equity_eur'] = 492.5
    assert 'daily_loss_limit_reached' in QuantTraderAgent().evaluate(p, a, m)['reasons']
    a['equity_eur'] = a['day_start_equity_eur'] = 460.
    assert 'drawdown_halt' in QuantTraderAgent().evaluate(p, a, m)['reasons']


def test_remaining_daily_budget_includes_costs():
    p, a, m = inputs()
    a['equity_eur'] = 493.
    decision = QuantTraderAgent().evaluate(p, a, m)
    assert decision['approved']
    assert decision['order']['estimated_stop_risk_eur'] <= .5


def test_short_requires_verified_borrow_and_costs():
    p, a, m = inputs()
    p['side'] = 'short'
    decision = QuantTraderAgent().evaluate(p, a, m)
    assert not decision['approved']
    m.update(short_supported=True, borrow_available=True, borrow_rate_annual=.10)
    decision = QuantTraderAgent().evaluate(p, a, m)
    assert decision['approved']
    assert decision['audit']['roundtrip_cost_eur'] > .26


def test_lot_size_and_derivatives_cannot_fake_affordability():
    p, a, m = inputs()
    m.update(asset_class='future', linear_contract=True, contract_multiplier=100., quantity_step=1.)
    assert not QuantTraderAgent().evaluate(p, a, m)['approved']
    m.update(asset_class='option', contract_multiplier=1., quantity_step=.001)
    assert 'options_require_payoff_aware_risk_model' in QuantTraderAgent().evaluate(p, a, m)['reasons']


def test_concurrent_positions_and_overdue_exit():
    p, a, m = inputs()
    a['open_positions'] = [dict(symbol=f'OTHER{i}', notional_eur=50., estimated_stop_risk_eur=1., opened_at='2026-10-07T00:00:00Z') for i in range(3)]
    result = QuantTraderAgent().evaluate(p, a, m)
    assert not result['approved']
    assert 'max_concurrent_positions_reached' in result['reasons']
    assert 'existing_position_requires_48h_exit' in result['reasons']


def test_cash_and_portfolio_gross_limits():
    p, a, m = inputs()
    a['cash_eur'] = 10.
    decision = QuantTraderAgent().evaluate(p, a, m)
    assert decision['approved']
    assert decision['order']['notional_eur'] + .01 <= 10.
    a['gross_exposure_eur'] = 500.
    assert not QuantTraderAgent().evaluate(p, a, m)['approved']


def test_missing_cost_is_not_zero_cost():
    p, a, m = inputs()
    del m['fx_cost_bps_roundtrip']
    assert not QuantTraderAgent().evaluate(p, a, m)['approved']


def test_live_adapter_refused_without_submission():
    class Live:
        mode = 'live'
        def submit_paper_order(self, order):
            raise AssertionError('Must not be called')
    trader = QuantTraderAgent()
    with pytest.raises(ValueError):
        trader.execute_paper(trader.evaluate(*inputs()), Live())


def test_existing_risk_is_reserved():
    p, a, m = inputs()
    a['equity_eur'] = 494.
    a['open_positions'] = [dict(symbol='OTHER', notional_eur=50., estimated_stop_risk_eur=1.48,
                                opened_at='2026-10-09T23:00:00Z')]
    decision = QuantTraderAgent().evaluate(p, a, m)
    assert not decision['approved']  # 2 cents available cannot afford the minimum order risk.
    assert decision['audit']['available_daily_risk_eur'] == pytest.approx(.02)


def test_malformed_nested_inputs_fail_closed():
    p, a, m = inputs()
    a['open_positions'] = ['invalid']
    assert not QuantTraderAgent().evaluate(p, a, m)['approved']
    assert not QuantTraderAgent().evaluate(None)['approved']
