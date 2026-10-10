from collections import namedtuple
from datetime import datetime, timedelta, timezone
import asyncio
import importlib.util
import json
import sys
import types

import httpx
import numpy as np
import pandas as pd
import pytest

from .edge import EdgeBook
from .intraday import BAR, strategy_signals, STRATEGY_IDS
from .ledger import PaperLedger
from .live import LiveLoop
from .markets import DEFAULT_PROFILES, profile_for
from .narrator import Narrator, template
from .replay import ReplayDriver, load_replay_data
from .runtime import TradingRuntime, first_blocker

Row = namedtuple('Row', 'timestamp symbol open high low close volume fx_to_eur asset_class')
T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
BTC = profile_for('BTCUSDT')


def row(i, o, h, l, c, symbol='BTCUSDT', volume=10.):
    return Row(pd.Timestamp(T0 + i * BAR), symbol, o, h, l, c, volume, 1., 'crypto')


def order(side='long', quantity=1., stop=.02):
    return dict(mode='paper', symbol='BTCUSDT', strategy_id='sma', side=side, quantity=quantity, stop_distance_pct=stop,
                entry_reference_price=100., notional_eur=100. * quantity, estimated_stop_risk_eur=2.5)


# ---------------- ledger ----------------
def test_ledger_fills_next_open_with_costs_and_exits_at_48h():
    ledger = PaperLedger()
    ledger.on_bar(row(0, 100, 101, 99, 100), BTC)
    ledger.submit(order(), decision_id='d1', created_at=T0 + BAR, profile=BTC)
    assert ledger.positions == [] and len(ledger.pending) == 1
    ledger.on_bar(row(1, 100, 101, 99.5, 100.5), BTC)
    position = ledger.positions[0]
    slip = (BTC.slippage_bps_per_side + BTC.spread_bps / 2) / 10000
    assert position['entry_price_eur'] == pytest.approx(100 * (1 + slip))
    assert ledger.cash == pytest.approx(500 - position['entry_price_eur'] - position['entry_fee_eur'])
    for i in range(2, 2 + 192):
        ledger.on_bar(row(i, 100.5, 101, 100, 100.5), BTC)
    assert not ledger.positions and ledger.trades[-1]['reason'] == '48h_horizon_first_executable_bar'
    assert ledger.trades[-1]['held_hours'] == pytest.approx(48)


def test_ledger_stop_fills_at_worse_of_gap_open_and_stop():
    ledger = PaperLedger()
    ledger.on_bar(row(0, 100, 101, 99, 100), BTC)
    ledger.submit(order(stop=.02), decision_id='d1', created_at=T0 + BAR, profile=BTC)
    ledger.on_bar(row(1, 100, 100.2, 99.9, 100), BTC)
    stop = ledger.positions[0]['stop_price_eur']
    ledger.on_bar(row(2, 95, 96, 94, 95), BTC)  # gap far below the stop
    trade = ledger.trades[-1]
    assert trade['reason'] == 'stop_loss_gap_or_intrabar'
    assert trade['exit_price_eur'] < stop and trade['exit_price_eur'] == pytest.approx(95 * (1 - (BTC.slippage_bps_per_side + BTC.spread_bps / 2) / 10000))


def test_short_accrues_borrow_and_profits_when_price_falls():
    ledger = PaperLedger()
    ledger.on_bar(row(0, 100, 101, 99, 100), BTC)
    ledger.submit(order(side='short', stop=.05), decision_id='d1', created_at=T0 + BAR, profile=BTC)
    for i in range(1, 100):
        ledger.on_bar(row(i, 98, 98.5, 97.5, 98), BTC)
    position = ledger.positions[0]
    assert position['quantity'] < 0 and position['carry_eur'] > 0
    assert ledger.equity() > 500 - 1  # short gains outweigh fees


def test_stale_orders_cancel_and_drawdown_halt_liquidates_and_blocks_until_acknowledged():
    ledger = PaperLedger()
    ledger.on_bar(row(0, 100, 101, 99, 100), BTC)
    ledger.submit(order(), decision_id='old', created_at=T0 + BAR, profile=BTC)
    ledger.on_bar(row(4, 100, 101, 99, 100), BTC)  # next bar 45 minutes later
    assert not ledger.positions and ledger.events[-1]['reason'] == 'no_executable_bar_within_30_minutes'
    ledger.submit(order(quantity=4.5, stop=.5), decision_id='big', created_at=T0 + 5 * BAR, profile=BTC)
    ledger.on_bar(row(5, 100, 100, 100, 100), BTC)
    ledger.on_bar(row(6, 80, 80, 79, 80), BTC)
    assert ledger.halted
    ledger.on_bar(row(7, 80, 81, 79, 80), BTC)
    assert not ledger.positions and ledger.trades[-1]['reason'] == 'drawdown_halt_liquidation'
    ledger.submit(order(), decision_id='blocked', created_at=T0 + 8 * BAR, profile=BTC)
    ledger.on_bar(row(8, 80, 81, 79, 80), BTC)
    assert not ledger.positions and ledger.events[-1]['reason'] == 'drawdown_halt'
    event = ledger.acknowledge_halt(T0 + 9 * BAR)
    assert not ledger.halted and event['peak_reset_to'] == pytest.approx(ledger.equity())
    restored = PaperLedger.from_state(json.loads(json.dumps(ledger.to_state())))
    assert restored.to_state() == ledger.to_state()


# ---------------- edge book ----------------
def outcome(i, gross, fresh=True, symbol='BTCUSDT', side='long', hours=48):
    start = T0 + timedelta(hours=hours * i)
    return dict(symbol=symbol, strategy_id='sma', side=side, fresh=fresh, gross_return=gross,
                observed_at=start.isoformat(), label_at=(start + timedelta(hours=48)).isoformat())


def test_edge_book_counts_only_fresh_nonoverlapping_samples_and_requires_positive_lower_bound():
    book = EdgeBook(minimum_samples=20)
    assert not book.record(outcome(0, .02, fresh=False), 'crypto')
    assert book.record(outcome(0, .02), 'crypto')
    assert not book.record(outcome(.5, .02), 'crypto')  # overlaps the previous 48h sample
    rng = np.random.default_rng(1)
    for i in range(1, 25):
        book.record(outcome(i, .02 + rng.normal(0, .005)), 'crypto')
    good = book.estimate('sma', 'long', 'crypto', cost_bps=30)
    assert good['supported'] and good['samples'] == 25 and good['lower_net_bps'] > 0
    assert not book.estimate('sma', 'long', 'crypto', cost_bps=400)['supported']
    assert not book.estimate('sma', 'short', 'crypto', cost_bps=30)['supported']
    noisy = EdgeBook(minimum_samples=20)
    for i in range(30):
        noisy.record(outcome(i, rng.normal(.001, .05)), 'crypto')
    assert not noisy.estimate('sma', 'long', 'crypto', cost_bps=30)['supported']
    assert EdgeBook.from_state(json.loads(json.dumps(book.to_state()))).to_state() == book.to_state()


def test_bonferroni_z_increases_with_more_tested_buckets():
    book = EdgeBook(minimum_samples=20)
    for i in range(20):
        book.record(outcome(i, .01), 'crypto')
    single = book.estimate('sma', 'long', 'crypto', 0)['z']
    for i in range(20):
        book.record(outcome(i, .01, side='short'), 'crypto')
    assert book.estimate('sma', 'long', 'crypto', 0)['z'] > single
    with pytest.raises(ValueError):
        EdgeBook(minimum_samples=5)


# ---------------- runtime ----------------
@pytest.fixture(scope='module')
def btc():
    data = load_replay_data(symbols=('BTCUSDT',))
    return data.iloc[:3000].reset_index(drop=True)


def first_clean_fresh_long(frame, strategy='sma'):
    """Index of a bar where `strategy` turns long and no other strategy turns short on the same bar."""
    sig = {s: strategy_signals(frame, s) for s in STRATEGY_IDS}
    for i in range(400, len(frame)):
        if sig[strategy][i] == 1 and sig[strategy][i - 1] != 1:
            if not any(sig[s][i] == -1 and sig[s][i - 1] != -1 for s in STRATEGY_IDS):
                return i
    raise AssertionError('no clean signal in fixture')


def seed_edge(runtime, strategy='sma', side='long', gross=.03):
    rng = np.random.default_rng(7)
    for i in range(40):
        runtime.extra.edge.record(outcome(i - 200, gross + rng.normal(0, .004), side=side) | {'strategy_id': strategy}, 'crypto')


def ingest_until(runtime, frame, end):
    part = frame.iloc[:end + 1]
    now = (part.timestamp.iloc[-1] + BAR + pd.Timedelta(seconds=1)).to_pydatetime()
    return runtime.ingest_bars(part, 'fixture: Binance 15m', now=now)


def test_unsupported_edge_is_rejected_by_researcher_and_nothing_trades(tmp_path, btc):
    runtime = TradingRuntime(tmp_path, name='test')
    i = first_clean_fresh_long(btc)
    result = ingest_until(runtime, btc, i)
    fresh = [d for d in result['decisions'] if d['proposal']['strategy_id'] == 'sma']
    assert fresh and all(d['outcome'] == 'rejected' for d in result['decisions'])
    assert first_blocker(fresh[0]) == 'researcher'
    assert 'No matured fresh-signal outcomes' in fresh[0]['roles'][1]['decision']['reasons'][0]
    assert not runtime.extra.ledger.pending and runtime.status()['execution'] == 'disabled'


def test_supported_edge_is_approved_sized_queued_and_filled_next_open(tmp_path, btc):
    runtime = TradingRuntime(tmp_path, name='test')
    seed_edge(runtime)
    i = first_clean_fresh_long(btc)
    result = ingest_until(runtime, btc, i)
    approved = [d for d in result['decisions'] if d['outcome'] == 'paper_only_approved']
    assert len(approved) == 1, [first_blocker(d) for d in result['decisions']]
    decision = approved[0]
    assert decision['live_orders_enabled'] is False and decision['order']['mode'] == 'paper'
    assert decision['order']['notional_eur'] <= 100 + 1e-6  # 20% of EUR 500
    trader = decision['roles'][3]['decision']['audit']
    assert trader['estimated_stop_risk_eur'] <= 2.5 + 1e-6 and trader['expected_net_edge_eur'] > 0
    assert len(runtime.extra.ledger.pending) == 1
    nxt = btc.iloc[[i + 1]]
    runtime.ingest_bars(nxt, 'fixture', now=(nxt.timestamp.iloc[0] + BAR + pd.Timedelta(seconds=1)).to_pydatetime())
    position = runtime.extra.ledger.positions[0]
    assert position['decision_id'] == decision['decision_id']
    assert position['entered_at'] == nxt.timestamp.iloc[0].isoformat()
    restarted = TradingRuntime(tmp_path, name='test')
    assert restarted.extra.ledger.to_state() == runtime.extra.ledger.to_state()
    assert restarted.journal.find(decision['decision_id'])['outcome'] == 'paper_only_approved'


def test_promoted_champion_disagreement_vetoes_even_with_edge(tmp_path, btc):
    runtime = TradingRuntime(tmp_path, name='test')
    seed_edge(runtime)
    runtime.researcher.champion_version = 1
    runtime.researcher.champion.bias = -5.  # champion strongly predicts failure
    result = ingest_until(runtime, btc, first_clean_fresh_long(btc))
    assert all(d['outcome'] == 'rejected' for d in result['decisions'])
    sma = next(d for d in result['decisions'] if d['proposal']['strategy_id'] == 'sma')
    assert 'below the 0.55 threshold' in ' '.join(sma['roles'][1]['decision']['reasons'])


def test_strict_conflict_policy_vetoes_persisting_opposite_signals(tmp_path, btc):
    sig = {s: strategy_signals(btc, s) for s in STRATEGY_IDS}
    i = next(i for i in range(400, len(btc)) if sig['sma'][i] == 1 and sig['sma'][i - 1] != 1
             and any(sig[s][i] == -1 for s in STRATEGY_IDS)
             and not any(sig[s][i] == -1 and sig[s][i - 1] != -1 for s in STRATEGY_IDS))
    evidence = TradingRuntime(tmp_path / 'evidence', name='e')
    seed_edge(evidence)
    strict = TradingRuntime(tmp_path / 'strict', name='s')
    seed_edge(strict)
    strict.update_settings({'conflict_policy': 'strict'})
    assert any(d['outcome'] == 'paper_only_approved' for d in ingest_until(evidence, btc, i)['decisions'])
    vetoed = ingest_until(strict, btc, i)['decisions']
    assert all(d['outcome'] == 'rejected' for d in vetoed)
    assert any('conflict' in r for d in vetoed for r in d['roles'][2]['decision']['reasons'])


def test_settings_can_tighten_but_never_loosen_and_are_journaled(tmp_path):
    runtime = TradingRuntime(tmp_path, name='test')
    with pytest.raises(ValueError, match='no greater than'):
        runtime.update_settings({'risk': {'risk_per_trade_pct': .02}})
    with pytest.raises(ValueError):
        runtime.update_settings({'risk': {'max_concurrent_positions': 5}})
    with pytest.raises(ValueError, match='Unknown'):
        runtime.update_settings({'leverage': 3})
    event = runtime.update_settings({'risk': {'risk_per_trade_pct': .0025}, 'shorts_enabled': False})
    assert event['after']['risk']['risk_per_trade_pct'] == .0025
    assert runtime.status()['policy']['risk_per_trade_pct'] == .0025
    assert runtime.journal.tail(5)[-1]['kind'] == 'settings_change'
    assert TradingRuntime(tmp_path, name='test').extra.settings['shorts_enabled'] is False


def test_failed_checkpoint_rolls_back_ledger_and_learning(tmp_path, btc, monkeypatch):
    runtime = TradingRuntime(tmp_path, name='test')
    seed_edge(runtime)
    before_ledger, before_learner = runtime.extra.ledger.to_state(), runtime.researcher.to_state()
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(TradingRuntime, '_save', fail)
    with pytest.raises(OSError):
        ingest_until(runtime, btc, first_clean_fresh_long(btc))
    assert runtime.extra.ledger.to_state() == before_ledger and runtime.researcher.to_state() == before_learner


# ---------------- replay ----------------
def test_replay_steps_recovers_position_after_restart_and_resets(tmp_path, btc):
    data = btc.iloc[:200]
    factory = lambda d: TradingRuntime(d, name='replay', checkpoint_every=5)
    driver = ReplayDriver(factory, tmp_path / 'replay', data=data)
    assert driver.step(10) == 10 and driver.position() == ReplayDriver.WARMUP_BARS + 9
    driver.runtime.flush()
    again = ReplayDriver(factory, tmp_path / 'replay', data=data)
    assert again.position() == driver.position()
    asyncio.run(again.reset())
    assert again.position() == 0 and again.status()['total'] == 200


# ---------------- live feed (mocked exchange) ----------------
def kline(start, price):
    open_ms = int(start.timestamp() * 1000)
    return [open_ms, str(price), str(price * 1.001), str(price * .999), str(price), '5', open_ms + 899_999]


def binance_transport(now, bars=40):
    def handler(request):
        symbol = request.url.params['symbol']
        base = {'EURUSDT': 1.17, 'BTCUSDT': 90000, 'ETHUSDT': 3000, 'SOLUSDT': 150}[symbol]
        start = pd.Timestamp(now).floor('15min') - BAR * bars
        rows = [kline(start + BAR * k, base * (1 + .001 * np.sin(k))) for k in range(bars + 1)]
        return httpx.Response(200, json=rows)  # last row is still forming and must be ignored
    return httpx.MockTransport(handler)


def test_live_loop_ingests_completed_bars_once_with_point_in_time_fx(tmp_path, monkeypatch):
    monkeypatch.delenv('ALPACA_API_KEY_ID', raising=False)
    runtime = TradingRuntime(tmp_path, name='live')
    now = datetime(2026, 3, 2, 12, 7, tzinfo=timezone.utc)
    loop = LiveLoop(runtime, transport=binance_transport(now))
    async def run():
        async with httpx.AsyncClient(transport=loop.transport) as client:
            first = await loop.poll_once(client, now=now)
            second = await loop.poll_once(client, now=now + timedelta(seconds=20))
            return first, second
    first, second = asyncio.run(run())
    assert first['accepted_bars'] == 120 and second is None
    history = runtime.history
    assert set(history.symbol) == {'BTCUSDT', 'ETHUSDT', 'SOLUSDT'}
    assert (history.timestamp + BAR <= pd.Timestamp(now)).all()
    assert history.fx_to_eur.iloc[-1] == pytest.approx(1 / (1.17 * (1 + .001 * np.sin(40 - 1))), rel=1e-6)
    assert any('SPY/QQQ disabled' in n for n in loop.state['notes'])
    assert loop.status()['execution'] == 'paper_only'


# ---------------- narrator ----------------
def test_template_narration_and_llm_disabled_by_default(tmp_path, btc, monkeypatch):
    monkeypatch.delenv('TRADING_NARRATOR', raising=False)
    runtime = TradingRuntime(tmp_path, name='test')
    record = ingest_until(runtime, btc, first_clean_fresh_long(btc))['decisions'][0]
    narration = Narrator(tmp_path).narrate(record)
    assert narration['source']['kind'] == 'template' and 'Nothing was traded' in narration['text']
    assert runtime.journal.find(record['decision_id']) == runtime.journal.tail(100)[[r['decision_id'] for r in runtime.journal.tail(100)].index(record['decision_id'])]
    assert 'queued' in template({'proposal': {}, 'roles': [], 'outcome': 'paper_only_approved', 'order': {'notional_eur': 50}})


# ---------------- exports ----------------
def load_export(path, name):
    strategy = types.ModuleType('freqtrade.strategy')
    strategy.IStrategy = object
    strategy.stoploss_from_open = lambda *a, **k: 0.
    sys.modules.setdefault('freqtrade', types.ModuleType('freqtrade'))
    sys.modules['freqtrade.strategy'] = strategy
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exported_signals_match_agent_signals_on_real_bars(btc):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'exports'
    freqtrade = load_export(root / 'freqtrade' / 'FourAgentResearchStrategy.py', 'ft_export')
    lean = load_export(root / 'lean' / 'signals.py', 'lean_signals')
    frame = btc.iloc[:2000].reset_index(drop=True)
    for family in STRATEGY_IDS:
        expected = strategy_signals(frame, family)
        assert (freqtrade.targets(frame, family) == expected).all(), family
        assert (lean.targets(frame, family) == expected).all(), family
