from copy import deepcopy
from datetime import datetime, timezone
import sqlite3

import pytest

from .decision_team import DecisionJournal, DecisionTeam

NOW = datetime(2026, 10, 9, 23, 0, tzinfo=timezone.utc)


class Role:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def evaluate(self, context):
        self.calls.append(deepcopy(context))
        return deepcopy(self.result)


@pytest.fixture
def proposal():
    return {'symbol': 'EXAMPLE', 'strategy_id': 'sma', 'side': 'long', 'confidence': .6,
            'entry_reference_price': 10, 'stop_distance_pct': .02,
            'evidence': {'signal': 'Test-only explicit signal'}, 'observed_at': NOW.isoformat()}


@pytest.fixture
def market():
    return {'data_valid': True, 'observed_at': NOW.isoformat(),
            'short_supported': False, 'borrow_available': False}


@pytest.fixture
def team(tmp_path):
    return DecisionTeam(DecisionJournal(tmp_path / 'decisions.sqlite'),
                        developer=Role({'eligible': True, 'reasons': ['Test developer validation']}),
                        researcher=Role({'eligible': True, 'edge_supported': True,
                                         'expected_advantage_bps': 25, 'roundtrip_cost_bps': 10,
                                         'reasons': ['Test-only independent after-cost estimate']}),
                        trader=Role({'approved': True, 'outcome': 'paper_only',
                                     'order': {'symbol': 'EXAMPLE', 'notional_eur': 100},
                                     'reasons': ['Test-only portfolio limits passed']}))


def test_named_roles_paper_only_and_durable_journal(team, proposal, market):
    result = team.evaluate(proposal, market, {'equity_eur': 500}, now=NOW)
    assert result['outcome'] == 'paper_only_approved'
    assert result['live_orders_enabled'] is False
    assert [role['role'] for role in result['roles']] == ['developer', 'researcher', 'senior skeptic', 'trader']
    assert result['journal_persisted'] is True
    stored = team.journal.records()[0]
    assert stored['roles'][2]['decision']['evidence']['expected_net_advantage_bps'] == 15
    assert stored['decision_id'] == result['decision_id']


@pytest.mark.parametrize('changes,reason', [
    ({'observed_at': '2026-10-09T22:50:00+00:00'}, 'timestamp'),
    ({'observed_at': '2026-10-09T23:00:00'}, 'timestamp'),
    ({'observed_at': '2026-10-09T23:00:10+00:00'}, 'timestamp'),
    ({'entry_reference_price': float('nan')}, 'reference price'),
    ({'confidence': True}, 'Confidence'),
    ({'stop_distance_pct': 2}, 'Stop distance'),
    ({'evidence': {}}, 'evidence'),
])
def test_invalid_proposal_fails_before_trader(team, proposal, market, changes, reason):
    proposal.update(changes)
    result = team.evaluate(proposal, market, {}, now=NOW)
    assert result['outcome'] == 'rejected'
    assert any(reason in item for item in result['roles'][2]['decision']['reasons'])
    assert len(team.trader.calls) == 1


def test_opposite_proposals_both_rejected(team, proposal, market):
    opposing = {**proposal, 'strategy_id': 'rsi', 'side': 'short'}
    result = team.evaluate_batch([proposal, opposing], {'EXAMPLE': market}, {}, now=NOW)
    assert all(row['outcome'] == 'rejected' for row in result)
    assert len(team.journal.records()) == 2


def test_short_rejected_without_explicit_support_and_borrow(team, proposal, market):
    proposal['side'] = 'short'
    result = team.evaluate(proposal, market, {}, now=NOW)
    assert result['outcome'] == 'rejected'
    market.update(short_supported=True, borrow_available=True)
    assert team.evaluate(proposal, market, {}, now=NOW)['outcome'] == 'paper_only_approved'


@pytest.mark.parametrize('research', [
    {'eligible': True, 'edge_supported': True, 'expected_advantage_bps': 10, 'roundtrip_cost_bps': 10},
    {'eligible': True, 'edge_supported': True, 'expected_advantage_bps': 5, 'roundtrip_cost_bps': 10},
    {'eligible': True, 'confidence': .99},
    {'eligible': True, 'edge_supported': True, 'expected_advantage_bps': 25, 'roundtrip_cost_bps': -1},
    {'eligible': True, 'edge_supported': False, 'expected_advantage_bps': 25, 'roundtrip_cost_bps': 10},
])
def test_missing_or_nonpositive_after_cost_edge_rejected(team, proposal, market, research):
    team.researcher = Role(research)
    assert team.evaluate(proposal, market, {}, now=NOW)['outcome'] == 'rejected'
    assert len(team.trader.calls) == 1


def test_trader_risk_veto_is_final(team, proposal, market):
    team.trader = Role({'approved': False, 'reasons': ['Daily loss budget exhausted.']})
    result = team.evaluate(proposal, market, {}, now=NOW)
    assert result['outcome'] == 'rejected' and result['order'] is None


def test_default_unconfigured_team_truthfully_rejects(tmp_path, proposal, market):
    team = DecisionTeam(DecisionJournal(tmp_path / 'audit.sqlite'))
    result = team.evaluate(proposal, market, {}, now=NOW)
    assert result['outcome'] == 'rejected'
    assert 'not configured' in result['roles'][0]['decision']['reasons'][0]


def test_sensitive_fields_not_recorded_and_hash_tampering_detected(team, proposal, market):
    proposal['api_key'] = 'must-not-be-written'
    proposal['evidence']['nested_token'] = 'also-private'
    team.evaluate(proposal, market, {}, now=NOW)
    raw = team.journal.path.read_bytes()
    assert b'must-not-be-written' not in raw and b'also-private' not in raw
    with sqlite3.connect(team.journal.path) as connection:
        connection.execute("UPDATE decisions SET payload='{}' WHERE sequence=1")
    with pytest.raises(ValueError, match='hash chain'):
        team.journal.records()


def test_audit_write_failure_closes_gate(team, proposal, market):
    def failed(record):
        raise OSError('Test unavailable disk')
    team.journal.append = failed
    result = team.evaluate(proposal, market, {}, now=NOW)
    assert result['outcome'] == 'rejected'
    assert result['order'] is None and result['journal_persisted'] is False


def test_batch_cannot_spend_same_capital_twice(team, proposal, market):
    second = {**proposal, 'symbol': 'OTHER'}
    results = team.evaluate_batch([proposal, second], {'EXAMPLE': market, 'OTHER': market}, {}, now=NOW)
    assert [result['outcome'] for result in results] == ['paper_only_approved', 'rejected']
    assert len(team.trader.calls) == 2


def test_agent_mutation_cannot_override_hard_gate(team, proposal, market):
    class MutatingRole:
        def evaluate(self, context):
            context['proposal']['observed_at'] = NOW.isoformat()
            context['market_state']['data_valid'] = True
            return {'eligible': True}
    team.developer = MutatingRole()
    market['data_valid'] = False
    assert team.evaluate(proposal, market, {}, now=NOW)['outcome'] == 'rejected'
