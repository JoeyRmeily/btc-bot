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

## Not built yet
- News/social agents (CryptoPanic, LunarCrush): planned Phase 3.
- Candlestick-pattern agent.
- A backtester that replays history through `analyze()` to calibrate `MIN_AGENT_SCORE`. Until it exists, the threshold is a guess, so tune it from shadow-mode reports.
