from datetime import datetime, timedelta, timezone
import json

import numpy as np
import pytest

from .learning import DIM, PastNormalizer, QuantResearcherAgent

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def proposal(at=BASE, side='long', symbol='TEST'):
    return dict(symbol=symbol, strategy_id='sma', side=side, confidence=.7,
                entry_reference_price=100., stop_distance_pct=.01, evidence=['Causal signal'], observed_at=at.isoformat())


def train(agent, count=100):
    for i in range(count):
        at = BASE + timedelta(hours=50 * i)
        agent.observe(proposal(at), {'momentum_48h': .01})
        agent.reveal('TEST', at + timedelta(hours=48), 102., 20.)
    return at + timedelta(hours=49)


def test_no_training_before_48h_and_neutral_champion_stays_frozen():
    agent = QuantResearcherAgent()
    agent.observe(proposal())
    assert agent.reveal('TEST', BASE + timedelta(hours=47), 102, 20) == []
    assert agent.trainer.updates == 0
    result = agent.reveal('TEST', BASE + timedelta(hours=48), 102, 20)
    assert result[0]['label'] == 1
    assert agent.trainer.updates == 1
    assert agent.champion.updates == 0
    assert agent.evaluate(proposal(BASE + timedelta(hours=49)))['probability'] == .5


def test_short_label_is_signed_and_includes_costs():
    agent = QuantResearcherAgent()
    agent.observe(proposal(side='short'))
    # 0.1% short profit is negative after 0.2% total costs.
    outcome = agent.reveal('TEST', BASE + timedelta(hours=48), 99.9, 20)[0]
    assert outcome['label'] == 0
    assert outcome['net_return'] == pytest.approx(-.001)


def test_past_only_normalization_and_immutable_saved_features():
    n = PastNormalizer()
    row = np.ones(DIM) * 2
    assert np.array_equal(n.transform(row), row)
    n.update(row)
    assert np.array_equal(n.transform(row), np.zeros(DIM))
    agent = QuantResearcherAgent()
    agent.observe(proposal())
    saved = agent.pending[0]['features'].copy()
    agent.observe(proposal(BASE + timedelta(hours=1)), {'momentum_48h': 5})
    assert agent.pending[0]['features'] == saved
    assert saved[0] == .7


def test_hindsight_observation_rejected_even_across_symbols():
    agent = QuantResearcherAgent()
    agent.observe(proposal())
    agent.reveal('TEST', BASE + timedelta(hours=48), 102, 20)
    with pytest.raises(ValueError, match='clock'):
        agent.observe(proposal(BASE + timedelta(hours=1), symbol='OTHER'))
    with pytest.raises(ValueError, match='clock'):
        agent.reveal('OTHER', BASE + timedelta(hours=47), 101, 20)


def test_duplicate_and_bad_feature_rejected():
    agent = QuantResearcherAgent()
    agent.observe(proposal())
    with pytest.raises(ValueError, match='Duplicate'):
        agent.observe(proposal())
    with pytest.raises(ValueError, match='finite'):
        agent.observe(proposal(BASE + timedelta(hours=1)), {'momentum_48h': float('nan')})
    with pytest.raises(ValueError, match='Only'):
        agent.evaluate(proposal(), {'future_return': .5})
    with pytest.raises(ValueError, match='UTC offset'):
        agent.evaluate({**proposal(), 'observed_at': '2026-01-01T00:00:00'})


def test_checkpoint_roundtrip_preserves_pending_and_replay_results(tmp_path):
    agent = QuantResearcherAgent()
    agent.observe(proposal())
    path = tmp_path / 'learner.json'
    agent.save(path)
    restored = QuantResearcherAgent.load(path)
    assert restored.to_state() == agent.to_state()
    at = BASE + timedelta(hours=48)
    assert restored.reveal('TEST', at, 102, 20) == agent.reveal('TEST', at, 102, 20)
    assert restored.to_state() == agent.to_state()
    state = json.loads(path.read_text())
    state['trainer']['weights'] = [0]
    with pytest.raises(ValueError, match='checkpoint'):
        QuantResearcherAgent.from_state(state)


def test_minimum_samples_and_frozen_shadow_model():
    agent = QuantResearcherAgent()
    with pytest.raises(ValueError, match='100'):
        agent.start_shadow(BASE)
    at = train(agent)
    agent.start_shadow(at)
    frozen = agent.challenger.state()
    agent.observe(proposal(at))
    agent.reveal('TEST', at + timedelta(hours=48), 103, 20)
    assert agent.challenger.state() == frozen
    assert agent.trainer.updates == 101
    assert not agent.promotion_gate()['eligible']
    with pytest.raises(ValueError, match='reviewed'):
        agent.promote(at + timedelta(hours=48), reviewed=True)


def test_shadow_disallows_overlapping_sample_inflation():
    agent = QuantResearcherAgent()
    at = train(agent)
    agent.start_shadow(at)
    agent.observe(proposal(at))
    agent.observe(proposal(at + timedelta(hours=1)))
    agent.reveal('TEST', at + timedelta(hours=49), 102, 20)
    assert agent.trainer.updates == 102
    assert len(agent.shadow_records) == 1


def test_prospective_promotion_needs_explicit_review_and_changes_only_champion():
    agent = QuantResearcherAgent()
    at = train(agent)
    agent.start_shadow(at)
    old = agent.champion.state()
    for i in range(60):
        stamp = at + timedelta(hours=50 * i)
        agent.observe(proposal(stamp), {'momentum_48h': .01})
        agent.reveal('TEST', stamp + timedelta(hours=48), 102, 20)
    gate = agent.promotion_gate()
    assert gate['eligible'], gate
    assert gate['samples'] == 60
    assert gate['mean_net_return'] == pytest.approx(.018)
    assert agent.champion.state() == old
    with pytest.raises(ValueError, match='reviewed'):
        agent.promote(stamp + timedelta(hours=49))
    result = agent.promote(stamp + timedelta(hours=49), reviewed=True)
    assert result['version'] == 1
    assert agent.champion.updates == 100  # frozen staged model, not newest trainer
    assert agent.trainer.updates == 160
    assert agent.challenger is None
    assert agent.evaluate(proposal(stamp + timedelta(hours=49)))['eligible_for_live'] is False


def test_costs_and_drift_can_block_promotion():
    agent = QuantResearcherAgent()
    at = train(agent)
    agent.start_shadow(at)
    for i in range(60):
        stamp = at + timedelta(hours=50 * i)
        agent.observe(proposal(stamp), {'momentum_48h': .01})
        agent.reveal('TEST', stamp + timedelta(hours=48), 100.1, 20)
    assert agent.drift_flag
    assert not agent.promotion_gate()['eligible']
    assert any('positive after' in reason for reason in agent.promotion_gate()['reasons'])
    with pytest.raises(ValueError, match='Review drift'):
        agent.start_shadow(stamp + timedelta(hours=49))
    agent.start_shadow(stamp + timedelta(hours=49), acknowledge_drift=True)
    assert not agent.drift_flag
    assert agent.shadow_records == []
    assert not agent.promotion_gate()['eligible']


def test_label_waits_until_next_quote_and_reports_actual_holding_period():
    agent = QuantResearcherAgent()
    agent.observe(proposal())
    outcome = agent.reveal('TEST', BASE + timedelta(hours=72), 102, 20)[0]
    assert outcome['holding_hours'] == 72
    assert not agent.pending
