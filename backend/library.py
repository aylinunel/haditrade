"""Read-only access to the bundled historical research (quant_backtests + legacy ORCL report)."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
QB = ROOT / 'research' / 'quant_backtests' / 'results_csv'


def _records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient='records'))


@lru_cache(maxsize=1)
def summary() -> dict:
    results = pd.read_csv(QB / 'results.csv')
    return dict(markets=_records(pd.read_csv(QB / 'markets.csv')), results=_records(results),
                macd_years=_records(pd.read_csv(QB / 'macd_years.csv')),
                macd_robust=_records(pd.read_csv(QB / 'macd_robust.csv')),
                caveats=['Daily (and some hourly) historical backtests with fixed costs per side; not the 15-minute agent.',
                         'Futures windows cover about one year or less and cannot support conclusions.',
                         'On SPY every tested strategy trailed buy-and-hold over 1995-2026.',
                         'Past performance does not predict future results.'])


@lru_cache(maxsize=8)
def curves(market: str) -> dict:
    frame = pd.read_csv(QB / 'curves.csv')
    frame = frame[frame.market == market]
    if frame.empty:
        raise ValueError('Unknown market')
    return {strategy: group[['week', 'growth_of_1']].values.tolist() for strategy, group in frame.groupby('strategy')}


@lru_cache(maxsize=1)
def legacy_orcl() -> dict:
    report = json.loads((ROOT / 'research' / 'backtests.json').read_text())
    return dict(provenance=report.get('provenance'), scenarios=[
        dict(id=s['id'], name=s['name'], label=s['label'], symbol=s['symbol'], portfolio=s['portfolio'],
             benchmark=s['benchmark'],
             strategies=[{k: bot[k] for k in ('id', 'name', 'family', 'metrics')} for bot in s['strategies']])
        for s in report['scenarios']])
