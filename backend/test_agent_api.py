import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .app import app

TOKEN = 'test-api-token-not-for-production'
HEADERS = {'Authorization': f'Bearer {TOKEN}'}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('STRATEGY_API_TOKEN', TOKEN)
    monkeypatch.setenv('STRATEGY_EVENT_DB', str(tmp_path / 'events.db'))
    monkeypatch.setenv('STRATEGY_RESEARCH_DIR', str(tmp_path / 'research'))
    monkeypatch.setenv('TRADING_RUNTIME_DIR', str(tmp_path / 'agent'))
    monkeypatch.delenv('TRADING_LIVE_AUTOSTART', raising=False)
    monkeypatch.delenv('TRADING_NARRATOR', raising=False)
    with TestClient(app) as c:
        yield c


def test_agent_routes_require_auth_and_report_paper_only(client):
    assert client.get('/health').json()['broker_connected'] is False
    for path in ('/api/agent/live/status', '/api/agent/replay/model', '/api/library/summary', '/api/exports'):
        assert client.get(path).status_code == 401
    assert client.get('/api/agent/paper/status', headers=HEADERS).status_code == 422
    status = client.get('/api/agent/live/status', headers=HEADERS).json()
    assert status['execution'] == 'disabled' and status['driver']['execution'] == 'paper_only'
    assert status['ledger']['equity_eur'] == 500 and not status['narrator']['llm_enabled']


def test_replay_step_produces_decisions_ledger_bars_and_model(client):
    response = client.post('/api/replay/control', headers=HEADERS, json={'action': 'step', 'steps': 30})
    assert response.status_code == 200, response.text
    assert response.json()['position'] > 60
    decisions = client.get('/api/agent/replay/decisions?limit=20', headers=HEADERS).json()
    assert decisions and all(len(d['roles']) == 4 and d['live_orders_enabled'] is False for d in decisions)
    narration = client.post(f"/api/agent/replay/decisions/{decisions[-1]['decision_id']}/narrate", headers=HEADERS).json()
    assert narration['source']['kind'] == 'template'
    bars = client.get('/api/agent/replay/bars/BTCUSDT', headers=HEADERS).json()
    assert len(bars['time']) == len(bars['close']) and set(bars['targets']) == {'sma', 'rsi', 'bollinger', 'atr_breakout', 'heikin'}
    assert client.get('/api/agent/replay/bars/NOPE', headers=HEADERS).status_code == 404
    model = client.get('/api/agent/replay/model', headers=HEADERS).json()
    assert len(model['feature_names']) == len(model['trainer']['weights']) and len(model['edge']) == 10
    ledger = client.get('/api/agent/replay/ledger', headers=HEADERS).json()
    assert ledger['execution'] == 'paper_only' and ledger['curve']
    assert client.post('/api/agent/replay/promote', headers=HEADERS, json={'reviewed': True}).status_code == 422
    reset = client.post('/api/replay/control', headers=HEADERS, json={'action': 'reset'}).json()
    assert reset['position'] == 0


def test_settings_tighten_only_through_api(client):
    current = client.get('/api/agent/live/settings', headers=HEADERS).json()
    assert current['hard_caps']['risk_per_trade_pct'] == .005
    loosen = client.put('/api/agent/live/settings', headers=HEADERS, json={'risk': {'max_position_pct': .5}})
    assert loosen.status_code == 422
    assert client.put('/api/agent/live/settings', headers=HEADERS, json={'leverage': 2}).status_code == 422
    ok = client.put('/api/agent/live/settings', headers=HEADERS, json={'risk': {'max_position_pct': .1}, 'conflict_policy': 'strict'})
    assert ok.status_code == 200 and ok.json()['after']['risk']['max_position_pct'] == .1
    assert client.get('/api/agent/live/settings', headers=HEADERS).json()['settings']['conflict_policy'] == 'strict'


def test_library_exports_and_agent_websocket(client):
    summary = client.get('/api/library/summary', headers=HEADERS).json()
    assert any(r['market'] == 'BTC' for r in summary['results'])
    assert 'Buy & hold' in client.get('/api/library/curves/BTC', headers=HEADERS).json()
    assert len(client.get('/api/library/orcl', headers=HEADERS).json()['scenarios']) == 3
    assert 'class FourAgentResearchStrategy' in client.get('/api/exports/freqtrade', headers=HEADERS).text
    assert client.get('/api/exports/../../etc/passwd', headers=HEADERS).status_code == 404
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'token': 'wrong', 'runtime': 'live'})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    client.post('/api/replay/control', headers=HEADERS, json={'action': 'step', 'steps': 2})
    with client.websocket_connect('/ws/agent') as ws:
        ws.send_json({'token': TOKEN, 'runtime': 'replay'})
        kinds = {ws.receive_json()['type'] for _ in range(2)}
        assert kinds <= {'events', 'heartbeat'} and 'events' in kinds


def test_root_serves_website_or_build_instructions(client):
    response = client.get('/')
    assert response.status_code == 200
    assert 'Strategy City' in response.text
