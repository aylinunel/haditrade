"""Convert finom/static-klines weekly Binance 15m JSON into gzip CSV replay files.

Usage: python scripts/build_replay_data.py /path/to/static-klines [--start 2025-10-01]
Only fully closed bars (close_time < now) are written. Prices are USDT quotes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path

SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT')
OUT = Path(__file__).resolve().parents[1] / 'data' / 'replay'


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('--start', default='2025-10-01')
    args = parser.parse_args()
    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    OUT.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        rows = {}
        for path in sorted((args.source / '.klines-cache' / symbol / '15m').glob('*.json')):
            for k in json.loads(path.read_text()):
                open_ms, close_ms = int(k[0]), int(k[6])
                if open_ms >= start_ms and close_ms < now_ms and open_ms % 900_000 == 0:
                    rows[open_ms] = k[1:6]
        target = OUT / f'{symbol}_15m.csv.gz'
        with gzip.open(target, 'wt', newline='') as handle:
            handle.write('timestamp,open,high,low,close,volume\n')
            for open_ms in sorted(rows):
                stamp = datetime.fromtimestamp(open_ms / 1000, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')
                handle.write(stamp + ',' + ','.join(str(float(v)) for v in rows[open_ms]) + '\n')
        first, last = min(rows), max(rows)
        print(symbol, len(rows), datetime.fromtimestamp(first / 1000, timezone.utc), datetime.fromtimestamp(last / 1000, timezone.utc))


if __name__ == '__main__':
    main()
