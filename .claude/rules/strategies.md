# Strategy Engine (validated strategies)

This is the bot's **current signal source**. Section `# ─── Strategy engine ───` in `main.py`, started from `lifespan()` as `strategy_engine()`. It replaced the 15m agents (`AGENT_MODE` now defaults to `off`, see agents.md), which had no edge in backtests.

Both strategies passed multi-period backtests. The evidence and caveats are in agents.md under "Findings". **Don't change their rules without re-running the backtest protocol** (fixed rules, several windows, fees included, judged on profit factor and not win rate).

## Strategies (both long-only)
| Strategy | Coins | Rule | Exit |
|---|---|---|---|
| Trend 4H | `TREND_COINS` (9) | Long while 4H EMA20 > EMA50 AND last closed daily close > daily SMA200 | When either condition fails |
| Funding capitulation | BTCUSDT | F = mean of last 3 funding prints; buy when F < 0 and F < 10th percentile of the prior 540 F values (180 days) | 72h time stop, or entry − 3×ATR(14, 4h) |

Expected from backtests: about 120 trades/yr in total. Trend wins ~30% of trades with PF ~2; capitulation wins ~55% with PF ~1.5–1.8 (BTC numbers are optimistic; it was weaker on ETH).

## Loop
`strategy_engine()` ticks every 60s:
- **Trend** runs once per 4H bucket, 30s after the candle closes (`last_4h`).
- **Capitulation** runs once per 8H bucket, 60s after the funding print (`last_funding`). Funding prints land at 00/08/16 UTC, which align with epoch 8H buckets.
- **Funding exits** (stop or 72h) are checked every tick using `live_price()`, which reads the BTC WebSocket cache and falls back to REST for other symbols.

Events from one tick are batched into a single Telegram message.

## State
`strategies.json` (git-ignored): `positions` keyed `trend:<SYM>` / `funding:BTCUSDT`, `trades` (each with pnl_pct after 0.1% fees, and pnl at `STRAT_LEVERAGE`), `last_4h`, `last_funding`. This is separate from the old `portfolio.json` sim and from `price_monitor`'s TP ladder.

## Modes (`STRATEGY_MODE` env var)
- `shadow` (default): paper positions + Telegram only. **No Binance orders.**
- `off`: engine does not start.
- Live order placement is **not built yet**. When it is, it must use isolated margin, ≤2x leverage (3x+ caused liquidations in backtests, 5x wiped out the portfolio), equal size per coin, and per-symbol quantity precision (the current `futures_order` rounds to 3 decimals, which only fits BTC).

## Gotchas
- `fapi/v1/fundingRate` returns at most 500 records unless `startTime` is set. `fetch_funding()` sets it to 215 days ago because capitulation needs ~545 prints. Without it the threshold is `None` and the strategy silently never fires.
- `capitulation_signal()` is verified against the backtest logic over full history (662 signal prints, 0 mismatches). Re-run that check if you touch it.
- `GET /strategies` shows open paper positions with unrealized %, a per-strategy summary, and the last 50 closed trades.
