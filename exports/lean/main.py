# QuantConnect / LEAN export of one ai-trading-agent signal family (research backtests only).
#
# Create a LEAN Python project, copy main.py and signals.py into it, and set the
# project parameter `family` (sma | rsi | bollinger | atr_breakout | heikin).
# Matches backend/intraday.py: completed 15-minute bars, entry on a fresh target
# change at the next bar, stop = clamp(2 * ATR14 / close, 0.5%, 5%), exit at
# 48 hours. Sizing mirrors the agent's caps: 0.5% equity risk per trade, at most
# 20% of equity per position, at most 3 positions. Costs come from LEAN's
# Binance fee model.
#
# NOT included: the skeptic's evidence/conflict vetoes and the researcher's
# empirical edge gate. Shorting needs a margin account; set allow_shorts False
# for a spot cash account.
from datetime import timedelta

import pandas as pd
from AlgorithmImports import *  # noqa: F401,F403  (LEAN convention)

from signals import FAMILIES, atr, targets


class FourAgentResearchAlgorithm(QCAlgorithm):
    def initialize(self):
        self.set_start_date(2025, 10, 1)
        self.set_account_currency('USDT')
        self.set_cash('USDT', 580)  # about EUR 500
        self.set_brokerage_model(BrokerageName.BINANCE, AccountType.MARGIN)
        self.family = self.get_parameter('family', 'sma')
        if self.family not in FAMILIES:
            raise ValueError(f'Unknown family {self.family}')
        self.allow_shorts = self.get_parameter('allow_shorts', 'true') == 'true'
        self.history_bars = {}
        self.entries = {}
        for ticker in ('BTCUSDT', 'ETHUSDT', 'SOLUSDT'):
            symbol = self.add_crypto(ticker, Resolution.MINUTE, Market.BINANCE).symbol
            self.history_bars[symbol] = []
            self.consolidate(symbol, timedelta(minutes=15), self.on_bar)
        self.set_warm_up(timedelta(days=5))

    def on_bar(self, bar):
        symbol = bar.symbol
        rows = self.history_bars[symbol]
        rows.append(dict(open=bar.open, high=bar.high, low=bar.low, close=bar.close))
        del rows[:-400]
        self.manage_exit(symbol)
        if self.is_warming_up or len(rows) < 60:
            return
        frame = pd.DataFrame(rows)
        target = targets(frame, self.family)
        if target[-1] == target[-2] or target[-1] == 0:
            return
        if self.portfolio[symbol].invested or symbol in self.entries:
            return
        if target[-1] < 0 and not self.allow_shorts:
            return
        if sum(1 for h in self.portfolio.values() if h.invested) >= 3:
            return
        stop = float(min(.05, max(.005, 2 * atr(frame).iloc[-1] / bar.close)))
        equity = self.portfolio.total_portfolio_value
        notional = min(equity * .005 / stop, equity * .20)
        quantity = notional / bar.close * (1 if target[-1] > 0 else -1)
        ticket = self.market_order(symbol, quantity, tag=f'{self.family} fresh {"long" if quantity > 0 else "short"}')
        price = bar.close
        stop_price = price * (1 - stop) if quantity > 0 else price * (1 + stop)
        self.entries[symbol] = dict(time=self.time, stop=self.stop_market_order(symbol, -quantity, stop_price, tag='agent stop'),
                                    entry=ticket)

    def manage_exit(self, symbol):
        entry = self.entries.get(symbol)
        if not entry:
            return
        if not self.portfolio[symbol].invested and entry['stop'].status == OrderStatus.FILLED:
            del self.entries[symbol]
        elif self.time - entry['time'] >= timedelta(hours=48):
            entry['stop'].cancel('48h horizon')
            self.liquidate(symbol, tag='48h horizon')
            del self.entries[symbol]
