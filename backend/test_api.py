import json
import pytest
from fastapi.testclient import TestClient
from .app import app

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('STRATEGY_API_TOKEN', 'test-api-token-not-for-production')
    monkeypatch.setenv('TRENDSPIDER_WEBHOOK_SECRET', 'test-webhook-secret-not-production')
    monkeypatch.setenv('STRATEGY_EVENT_DB', str(tmp_path / 'events.db'))
    monkeypatch.setenv('STRATEGY_RESEARCH_DIR', str(tmp_path / 'research'))
    with TestClient(app) as c: yield c

HEADERS = {'Authorization': 'Bearer test-api-token-not-for-production'}

def test_auth_state_control_and_validation(client):
    assert client.get('/health').json()['execution'] == 'disabled'
    assert client.get('/api/state').status_code == 401
    result = client.get('/api/state', headers=HEADERS)
    assert len(result.json()['snapshot']['bots']) == 5
    assert client.post('/api/control', json={'action': 'seek', 'index': 999999}, headers=HEADERS).status_code == 422
    assert client.post('/api/control', json={'action': 'scenario'}, headers=HEADERS).status_code == 422
    assert client.post('/api/control', json={'action': 'scenario', 'scenario_id': 'crisis'}, headers=HEADERS).json()['scenario_id'] == 'crisis'
    assert client.post('/api/control', json={'action': 'play'}, headers=HEADERS).json()['playing']
    assert not client.post('/api/control', json={'action': 'pause'}, headers=HEADERS).json()['playing']

def test_webhook_auth_dedupe_and_secret_redaction(client):
    payload = dict(secret='test-webhook-secret-not-production', strategy_id='sma', symbol='ORCL', action='buy', price=30., event_id='unique-1')
    assert client.post('/api/trendspider/webhook', json={**payload, 'secret': 'wrong-secret-16chars'}).status_code == 401
    first = client.post('/api/trendspider/webhook', json=payload).json()
    second = client.post('/api/trendspider/webhook', json=payload).json()
    assert not first['duplicate'] and second['duplicate']
    assert first['execution'] == 'not_submitted'
    events = client.get('/api/events', headers=HEADERS).json()
    assert len(events) == 1 and 'secret' not in events[0]
    assert client.post('/api/trendspider/webhook', json={**payload, 'price': -1}).status_code == 422

def test_websocket_initial_auth_and_five_bot_stream(client):
    with client.websocket_connect('/ws') as ws:
        ws.send_json({'token': 'test-api-token-not-for-production'})
        data = ws.receive_json()
        assert data['mode'] == 'historical_replay'
        assert len(data['snapshot']['bots']) == 5

def test_import_rejects_bad_csv(client):
    response = client.post('/api/backtests/import', headers=HEADERS, json=dict(csv='bad,data\n1,2', symbol='SPY', source_label='user CSV', start='2020-01-01', end='2021-01-01'))
    assert response.status_code == 422

def test_learning_routes_fail_closed_and_require_auth(client):
    assert client.get('/api/learning/status').status_code == 401
    status = client.get('/api/learning/status', headers=HEADERS).json()
    assert status['execution'] == 'disabled' and status['policy']['initial_capital_eur'] == 500
    assert status['training_labels'] == 0 and not status['promotion_gate']['eligible']
    assert client.post('/api/learning/promote', headers=HEADERS, json={'reviewed': True}).status_code == 422
    assert client.post('/api/learning/stage', headers=HEADERS, json={}).status_code == 422
    assert client.post('/api/learning/bars', headers=HEADERS, json={'csv': 'bad,data\n' * 3, 'source_label': 'test', 'roundtrip_cost_bps': {'T': 10}}).status_code == 422
    assert client.get('/api/decisions', headers=HEADERS).json() == []

def test_intraday_import_and_automatic_learning_pipeline(client):
    import pandas as pd
    from .test_research_service import bars
    end = pd.Timestamp.now(tz='UTC').floor('15min')
    frame = bars(start=(end - pd.Timedelta(hours=10)).isoformat())
    csv = frame.to_csv(index=False)
    response = client.post('/api/backtests/intraday', headers=HEADERS,
                           json={'csv': csv, 'source_label': 'synthetic unit test'})
    assert response.status_code == 200, response.text
    assert response.json()['execution'] == 'disabled'
    assert response.json()['result']['metrics']['initial_equity_eur'] == 500
    learned = client.post('/api/learning/bars', headers=HEADERS,
                          json={'csv': csv, 'source_label': 'synthetic unit test', 'roundtrip_cost_bps': {'TEST': 20}})
    assert learned.status_code == 200, learned.text
    assert learned.json()['queued_label_ids']
    assert all(d['order'] is None and len(d['roles']) == 4 for d in learned.json()['decisions'])
    assert client.get('/api/profile', headers=HEADERS).json()['capital_eur'] == 500
