from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from .research_service import ResearchService


def bars(start='2026-01-01T00:00:00Z', count=40, symbol='TEST'):
    prices = 100 + np.arange(count) * .25
    return pd.DataFrame(dict(timestamp=pd.date_range(start, periods=count, freq='15min'),
                             symbol=symbol, open=prices, high=prices + 1,
                             low=prices - 1, close=prices + .1, volume=1000 + np.arange(count),
                             fx_to_eur=1., asset_class='equity'))


def close_time(frame):
    return (frame.timestamp.max() + pd.Timedelta(minutes=15)).to_pydatetime()


def test_four_roles_continuous_delayed_learning_and_restart(tmp_path):
    service = ResearchService(tmp_path)
    first = bars()
    result = service.ingest(first, 'synthetic unit fixture', {'TEST': 20}, now=close_time(first))
    assert result['queued_label_ids'] and result['status']['training_labels'] == 0
    assert all(d['outcome'] == 'rejected' and len(d['roles']) == 4 for d in result['decisions'])
    assert all(d['journal_persisted'] and d['order'] is None for d in result['decisions'])
    assert service.history is not None
    pending = len(service.researcher.pending)
    # Nothing is trained before the full elapsed 48 hours.
    later = bars(start=(first.timestamp.max() + pd.Timedelta(hours=48)).isoformat(), count=1)
    later.loc[:, ['open', 'close']] = [115., 115.1]
    later.loc[:, ['high', 'low']] = [116., 114.]
    result = service.ingest(later, 'synthetic unit fixture', {'TEST': 20}, now=close_time(later))
    assert len(result['outcomes']) == pending
    assert service.researcher.trainer.updates == pending
    restored = ResearchService(tmp_path)
    assert restored.researcher.to_state() == service.researcher.to_state()
    assert len(restored.journal.records()) == len(service.journal.records())
    assert restored.status()['champion_version'] == 0


def test_duplicate_bar_idempotency_and_revision_rejection(tmp_path):
    service = ResearchService(tmp_path)
    frame = bars()
    service.ingest(frame, 'fixture', {'TEST': 20}, now=close_time(frame))
    before = service.researcher.to_state()
    duplicate = service.ingest(frame, 'fixture', {'TEST': 20}, now=close_time(frame))
    assert duplicate['duplicate_bars'] == 40 and duplicate['accepted_bars'] == 0
    assert not duplicate['decisions'] and service.researcher.to_state() == before
    frame.loc[0, 'close'] += .2
    with pytest.raises(ValueError, match='revised silently'):
        service.ingest(frame, 'fixture', {'TEST': 20}, now=close_time(frame))
    assert service.researcher.to_state() == before


def test_cost_changes_future_bars_and_hindsight_rejected(tmp_path):
    service = ResearchService(tmp_path)
    frame = bars()
    with pytest.raises(ValueError, match='completed'):
        service.ingest(frame, 'fixture', {'TEST': 20}, now=frame.timestamp.max().to_pydatetime())
    with pytest.raises(ValueError, match='finite'):
        service.ingest(frame, 'fixture', {'TEST': float('nan')}, now=close_time(frame))
    service.ingest(frame, 'fixture', {'TEST': 20}, now=close_time(frame))
    with pytest.raises(ValueError, match='cost assumption'):
        service.ingest(frame, 'fixture', {'TEST': 21}, now=close_time(frame))
    other = bars(count=10, symbol='OLD')
    with pytest.raises(ValueError, match='clock'):
        service.ingest(other, 'fixture', {'OLD': 20}, now=close_time(frame))


def test_checkpoint_failure_does_not_change_in_memory_learning(tmp_path, monkeypatch):
    service = ResearchService(tmp_path)
    original = service.researcher.to_state()
    def fail(*args):
        raise OSError('test checkpoint unavailable')
    monkeypatch.setattr(service, '_save', fail)
    frame = bars()
    with pytest.raises(OSError):
        service.ingest(frame, 'fixture', {'TEST': 20}, now=close_time(frame))
    assert service.researcher.to_state() == original and service.history is None


def test_async_symbol_close_is_not_scored_with_future_learned_weights(tmp_path):
    service = ResearchService(tmp_path)
    current, older = bars(symbol='NEW'), bars(count=39, symbol='OLDER')
    result = service.ingest(pd.concat([current, older]), 'fixture', {'NEW': 20, 'OLDER': 20}, now=close_time(current))
    assert all(d['proposal']['symbol'] == 'NEW' for d in result['decisions'])
    assert all(p['symbol'] == 'NEW' for p in service.researcher.pending)
