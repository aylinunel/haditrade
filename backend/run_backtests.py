import json
from pathlib import Path
from .engine import build_report

def main():
    root = Path(__file__).resolve().parents[1]
    report = build_report()
    output = root / 'research' / 'backtests.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, allow_nan=False, separators=(',', ':')))
    summary = {k: report[k] for k in ['version', 'mode', 'provenance', 'methodology', 'strategies']}
    summary['scenarios'] = []
    for s in report['scenarios']:
        obj = {k: s[k] for k in ['id', 'name', 'label', 'symbol', 'start_actual', 'end_actual', 'bars', 'portfolio', 'benchmark', 'return_correlations']}
        obj['strategies'] = [{k: v for k, v in bot.items() if k not in ('trace', 'trades')} for bot in s['strategies']]
        step = max(1, len(s['snapshots']) // 180)
        obj['curve'] = [dict(date=t['date'], equity=round(t['equity'], 4), benchmark_equity=round(t['benchmark_equity'], 4))
                        for i, t in enumerate(s['snapshots']) if i % step == 0 or i == len(s['snapshots']) - 1]
        summary['scenarios'].append(obj)
    asset = root / 'apps' / 'mobile' / 'assets' / 'research-summary.json'
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text(json.dumps(summary, separators=(',', ':'), allow_nan=False))
    for s in report['scenarios']:
        p, b = s['portfolio'], s['benchmark']
        print(f"{s['name']}: {s['start_actual']} to {s['end_actual']}, {s['bars']} bars, portfolio {p['return_pct']:.2f}%, buy/hold {b['return_pct']:.2f}%, max DD {p['max_drawdown_pct']:.2f}%, Sharpe {p['sharpe']:.2f}")
        for bot in s['strategies']:
            print(f"  {bot['name']}: {bot['metrics']['return_pct']:.2f}%, {bot['metrics']['trades']} closed trades")

if __name__ == '__main__': main()
