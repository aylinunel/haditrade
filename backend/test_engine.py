import numpy as np
import pandas as pd
import pytest
from .engine import load_bars, target_signals, STRATEGIES, simulate, build_report, wilder_rsi

@pytest.mark.parametrize('strategy', [s['id'] for s in STRATEGIES])
def test_future_data_cannot_change_past_signals(strategy):
    full = load_bars().iloc[:600].copy()
    original = target_signals(full, strategy)
    changed = full.copy()
    changed.loc[400:, ['Open', 'High', 'Low', 'Close']] *= 3
    np.testing.assert_array_equal(original[:400], target_signals(changed, strategy)[:400])
    np.testing.assert_array_equal(original[:400], target_signals(full.iloc[:400], strategy))

def test_next_open_fill_costs_and_terminal_liquidation():
    frame = pd.DataFrame(dict(Date=pd.date_range('2020-01-01', periods=3), Open=[100., 110., 130.], Close=[105., 120., 140.]))
    result = simulate(frame, np.array([1, 0, 0]), np.array([1, 2]), initial=1000, fee_bps=10, slippage_bps=20)
    entry, exit_ = result['trades']
    assert entry['date'] == '2020-01-02'
    assert entry['price'] == pytest.approx(110 * 1.002)
    assert exit_['price'] == pytest.approx(130 * .998)
    expected = (1000 / (110 * 1.002 * 1.001)) * (130 * .998) * .999
    assert result['metrics']['final_equity'] == pytest.approx(expected)
    assert result['trace'][-1]['shares'] == 0
    assert result['trace'][-1]['realized'] == pytest.approx(expected - 1000)

def test_flat_prices_lose_exact_roundtrip_costs():
    frame = pd.DataFrame(dict(Date=pd.date_range('2020-01-01', periods=3), Open=[100.] * 3, Close=[100.] * 3))
    result = simulate(frame, np.ones(3), np.array([1, 2]), initial=1000, fee_bps=5, slippage_bps=5)
    assert result['metrics']['final_equity'] == pytest.approx(1000 * .9995 * .9995 / (1.0005 * 1.0005))
    assert result['trades'][-1]['forced'] is True

def test_rsi_flat_and_increasing_edge_cases():
    assert wilder_rsi(pd.Series([10.] * 50)).iloc[-1] == 50
    assert wilder_rsi(pd.Series(np.arange(1., 51))).iloc[-1] == 100

@pytest.fixture(scope='module')
def report(): return build_report()

def test_all_three_reports_reconcile(report):
    assert len(report['scenarios']) == 3
    for scenario in report['scenarios']:
        assert scenario['bars'] > 500
        assert scenario['portfolio']['final_equity'] == pytest.approx(sum(s['metrics']['final_equity'] for s in scenario['strategies']))
        for snap in scenario['snapshots']:
            assert snap['equity'] == pytest.approx(sum(s['equity'] for s in snap['bots']))
            for bot in snap['bots']:
                assert bot['equity'] == pytest.approx(2000 + bot['realized'] + bot['unrealized'])
                assert bot['cash'] >= -1e-6 and bot['shares'] >= 0
        assert scenario['snapshots'][-1]['equity'] == pytest.approx(scenario['portfolio']['final_equity'])

def test_data_duplicate_is_rejected(tmp_path):
    frame = load_bars().iloc[:10]
    frame.loc[1, 'Date'] = frame.loc[0, 'Date']
    path = tmp_path / 'bad.csv'; frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match='unique'): load_bars(path)
