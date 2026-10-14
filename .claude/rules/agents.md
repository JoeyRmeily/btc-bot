# Agent Signal Generator

Built into `main.py` (section `# ─── Agent signal generator ───`). It replaces TradingView as the signal source. The `/webhook` endpoint still works, so a TradingView alert and the agents can run side by side, but never point both at live trading at once or every signal doubles.

## Loop
`signal_generator()` wakes 10s after every 15m candle close, runs `analyze()` for each symbol in `AGENT_SYMBOLS`, and dedupes by candle open time (`agent_state["last_bar"]`). Telegram gets a full agent report **only when the EMA crossover fires**, which happens a few times a day at most.

## Modes (`AGENT_MODE` env var)
| Mode | Behavior |
|------|----------|
| `off` | Generator does not start |
| `shadow` (default) | Sends the analysis to Telegram, never trades |
| `live` | Calls `process_signal()` (same path as the webhook): sim trade, plus a Binance order if `LIVE_TRADING=true` |

Keep new agents or threshold changes in `shadow` until there are enough logged signals to judge them.

## Two kinds of agents
Every agent returns `vote(v, reason, weight, required)` with `v` in {-1, 0, +1}.

**Required (🔒): the V9 rules.** All must vote +1 or the trade is blocked. These encode the validated V9 strategy. **Do not loosen them** (same reasoning as the 4H filter rule in strategy.md).
- EMA Cross: EMA9/EMA21 crossover on the last closed 15m bar (the trigger)
- MTF: 1H EMA9>EMA21, RSI>50, close>VWAP; 4H EMA9>EMA21>EMA50, RSI>50 (mirrored for SHORT)
- RSI 15m: 45-70 LONG, 30-55 SHORT
- VWAP 15m: close above (LONG) / below (SHORT)
- Volume: last bar ≥ 1.5× the 20-bar average
- EMA50 trend: close above/below 15m EMA50

**Scoring (weighted):** summed and compared to `MIN_AGENT_SCORE` (default 2). Max is +9.
| Agent | Weight | Logic |
|-------|--------|-------|
| MACD (12,26,9) | 1 | Histogram on the trade's side and expanding |
| Bollinger (20,2) | 1 | -1 if beyond the outer band (overextended), +1 if past the midline |
| OBV | 1 | OBV above/below its EMA20 |
| ATR(14) | 1 | -1 if < 0.15% of price (too quiet to reach TP1), +1 if ≥ 0.30% |
| 200DMA | 2 | With the trend +1, against -1; reports the Mayer Multiple |
| Pi Cycle | 1 | 0 normally; when 111DMA ≥ 2×350DMA, -1 LONG / +1 SHORT |
| Halving cycle | 1 | Phase from `LAST_HALVING` (2024-04-20): bull +1 LONG, bear +1 SHORT |
| Fear/Greed | 1 | 0 normally; -1 LONG at ≥80, -1 SHORT at ≤20 (contrarian at extremes only) |

## Implementation rules
- Indicator math mirrors Pine so numbers line up with TradingView: SMA-seeded EMA, Wilder (RMA) RSI and ATR, VWAP anchored at 00:00 UTC.
- `fetch_klines()` drops Binance's last kline because it is still forming. Never compute signals on an open candle.
- Daily candles and Fear/Greed are cached for 1h (`cached()`).
- To add an agent: write `agent_x(...) -> vote(...)`, add it to the dict in `analyze()`, then add a row to the table above.
- Telegram uses `parse_mode=HTML`. Never put a bare `&`, `<` or `>` in an agent name or reason. Telegram rejects the message and `telegram()` fails silently.
- `/agents` (GET) runs a fresh analysis and never trades. `?force=LONG` or `?force=SHORT` runs every agent without a crossover, which is the easiest way to check them.

## Backtesting
`python backtest.py --months 12` replays history through `evaluate()` (the same function the live loop uses, so keep agent logic inside `evaluate()` and never fork it). Exits mirror live: full close at TP1, SL checked first when a candle touches both, 0.1% round-trip fees. Data is cached in `.backtest_cache/` and every crossover is written to `backtest_results.csv`.

### Findings (2026-10-14, 3 years / 4,933 crossovers, train 24 months / test 12 months)
- Unfiltered win rate tracks the random-walk value `SL / (TP + SL)` for every TP/SL pair, and every pair has negative expectancy after fees. **15m EMA crossovers carry no edge on BTC.**
- V9 rules (12 months): 43.1% WR, PF 0.90. The 4H filter did worse when it passed (39%) than when it failed (43%).
- Greedy filter search reached 65–84% WR on train and collapsed to coin-flip or zero trades on test. That is overfitting.
- Adaptive exits (ATR-sized stop with 2–5R target, ATR trailing, swing-low stops) on V9-gated trades looked profitable on 2023–26 train and test (PF 1.26–1.36), then **failed on unseen 2020–23** (PF 0.61–1.00). That was a false positive from picking the best of ~40 configs.
- Support/resistance (1H swing pivots, 30-day lookback; bounce and breakout, long and short, ±trend filter, target = next level with ≥2R): 24–29% WR against a ~33% break-even, PF 0.5–0.9 in nearly every 2-year window from 2020 to 2026. No edge.
- Time-of-day long 22:00–00:00 UTC (published seasonality): slightly positive gross, loses in every window after 0.1% fees.
- Delta-neutral funding carry (long spot + short perp): positive every year since 2019, 75–100% of weeks profitable, but only 2–8%/yr on notional in calm years and close to zero in 2026.
- **Funding-capitulation long: first edge that survives every year.** Rules: F = mean of last 3 funding prints; go long at the next 1h open when F < 0 AND F < its rolling 180-day 10th percentile; exit after 72h or at entry − 3×ATR(14, 4h); one position at a time; funding received while long. Over 2020-03 to 2026-10: 144 trades, 56.9% WR, PF 1.84, profitable in all 7 calendar years (worst 2022: PF 1.09; 2026 YTD PF 1.38). Caveat: the 72h horizon came from exploring the same data, so this isn't fully out-of-sample. Paper-trade it before risking money. Data: `fapi.binance.com/fapi/v1/fundingRate` (full history from 2019-09).
- Open-interest history: the `/futures/data/*` REST endpoints only return 30 days. Use `data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/` (5m rows from 2020-09) for backtests.
- **Conclusion so far:** price-only rules on BTC 15m (EMA, RSI, VWAP, volume, MACD, BB, OBV, ATR, S/R, any TP/SL) show no durable edge. Next research needs different information: derivatives positioning (funding rate, open interest) or higher-timeframe trend following.
- **Rules:** judge any change on expectancy and profit factor, not win rate, because WR can be bought by shrinking TP. Always validate out of sample. Do not report in-sample results as improvements.

## Not built yet
- News/social agents (CryptoPanic, LunarCrush).
- Candlestick-pattern agent.
- Higher-timeframe and derivatives-data signals (funding rate, open interest). This is the most promising research direction given the findings above.
