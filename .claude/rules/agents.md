# Agent Signal Generator (retired) and research log

**Retired:** `AGENT_MODE` defaults to `off` because backtests found no edge. The live signal source is now the strategy engine (strategies.md). The agent code stays because `backtest.py` uses `evaluate()`. The "Findings" section below is the research log behind the current strategies.

Built into `main.py` (section `# ─── Agent signal generator ───`). The `/webhook` endpoint still works, so a TradingView alert and the agents can run side by side, but never point both at live trading at once or every signal doubles.

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
- **4H trend with daily filter (long-only): robust edge, low win rate.** Rules: long while 4H EMA20 > 4H EMA50 AND the last closed daily close > daily SMA200; otherwise flat; act at the next 4H open; no stop or target. Profitable in all 4 windows from 2018 to 2026 (+63–89%, +123%, +70%, +68%), with max drawdown 18–40% vs buy-and-hold's 53–81%. 27/27 parameter combinations at ±20% stay profitable in every window. WR ~31%, PF ~2.4, ~11 trades/yr. Trails buy-and-hold in strong bull runs; its value is sitting out bear markets. Short versions were worse in every family. Verified independently.
- **ETH cross-check (rules frozen from BTC):** the 4H trend rule held, profitable in 3 of 4 windows and −7% in 2022–23 vs ETH buy-and-hold −38%, so the edge generalizes. Funding capitulation was much weaker on ETH (149 trades, 49% WR, PF 1.12, 2024 −11%). Treat the BTC capitulation stats as optimistic and don't run it on ETH.
- **Shorts: no reliable short strategy found** (9 coins, 2019–2026, 4H data, fees + funding received). Four pre-registered ideas, average return per coin by window (2019-20 / 2021-22 / 2023-24 / 2025-26):
  - Trend short in bear regime (below SMA200, 4H EMA20<50): −17 / +24 / −23 / −17%
  - 20-day breakdown short, cover at 10-day high: +2 / +11 / −43 / +14%
  - Funding top decile + close < 4H EMA50, 72h, 3×ATR stop: 0 / −5 / −48 / +3%
  - Fade 4H RSI>70 in bear regime, 72h: −21 / −5 / −26 / −3%

  Small, inconsistent gains in bear windows; heavy losses in bull windows. Bear-market protection comes from the long strategies going flat.
- **Second short round (2026-10-24), also no edge.** Pre-registered, fees + real per-coin funding, 4 windows, perturbation grids:
  - Cross-sectional momentum, long top 2 / short bottom 2 by 30d return, weekly: L/S +1001 / −53 / −6 / −68%; short leg alone lost in every window (179/180 grid combo-windows). Ruined at 2x in Jan 2021 (AVAX +107% in 6 days).
  - Short the 2 weakest alts (30d) when BTC < SMA200 and SMA50 < SMA200, 7d hold, 2.5×ATR stop: −57 / +105 / −60 / +10%; hedged with BTC long −9 / +32 / −14 / +2%. 0/48 combos positive in every window.
  - Short highest 30d volatility or MAX coins (Lee & Wang 2024 effect), with or without long low-vol leg or bear gate: lost in 3 of 4 windows, won only in 2025-26 when the whole market fell. 0/36 combos for every variant.
  - BTC funding top 10% (mirror of the capitulation long): PF 0.94 / 0.42 / 0.84. Adding OI falling ≥3% from 72h max: PF 0.05 in 2023-24.
  - OI +≥10% over 7d with price < +2% and top-trader L/S ratio in top 20%, 72h / 3×ATR: pooled over 9 coins 208 trades, PF 1.08 / 1.14 / 1.45, but PF 1.04 without the top 5 trades (mostly the Oct 10 2025 crash), PF > 1 in all windows on 1/9 coins. Only 8/27 grid configs pass every window. Alt metrics start 2021-12 and the L/S ratio is missing for 2022, so it is untested in 2022. Paper-track at most.
  - Liquidation-flush long (OI −10% and price −8% in 24h, 72h, 3×ATR stop, 9 coins): pooled PF 0.80 / 1.89 / 1.35 (2022 / 2023-24 / 2025-26). Fails in the 2022 bear market (Jan, LUNA and FTX crashes hit 19–25 coin-trades each). Buying any 24h −8% drop gets most of the same result, so OI adds little. It is a bull-market dip-buy that overlaps Trend 4H. A version gated by SMA200 is untested and would need fresh data.
  - Idle capital while Trend 4H is flat (idle 63–79% of the time): a 4%/yr stablecoin yield lifts 2019-26 CAGR from 55.8% to 60.7% with no added drawdown. Delta-neutral carry gated on 3-print funding > 0 lost 6–9 points/yr to switching costs (the gate flipped about 100 times in 2022); always-on carry is about breakeven.
  - Literature check: no peer-reviewed net-of-cost short edge on large caps. Short-side evidence exists only for small/new tokens: post-listing drift (median −11.5% at 7d over 470 Binance listings), token unlocks, delistings. None fit the 9-coin universe.
- **2026-10-24 improvement round (4 parallel agents, rules fixed before testing):**
  - **BTC-trend filter for alt Trend 4H trades (V1–V4): rejected.** It cut 2025-26 alt losses (ADA −61→−51%, XRP −46→−35%) but halved bull-window returns (2021-22 portfolio +870% → +355–395%) and wrecked 2019-20 (BNB +60% → −22%). V4 (BTC trend breaking also forces alt exits) lowered drawdown in every window but failed the profit test in 2019–22. Alt losses come from each alt's own trend failing, not from BTC's regime.
  - **Trend 4H exit and filter tweaks: all rejected** (34 coin-windows; baseline median PF 2.68, 29/34 profitable, 31.6% WR). ADX>20/25: PF 2.36–2.37, worse at every threshold. Sell half at +2 ATR: WR 45% but PF 1.90 and bull returns −65%. Breakeven stop: 20/34 profitable. Catastrophic 3×ATR stop: PF 1.91, BTC 2021-22 +6% → −27%. Clear-break filter: neutral (PF 2.68). The edge is holding winners with no stop and no target.
  - **Open interest on funding capitulation: rejected.** OI −10%/24h shows 75% WR and PF 5.1, but on only 16 trades in 6 years. It's unstable at 7–8% thresholds (57% WR) and disappears when OI is measured in coins instead of USD. A random 16-trade draw from B0 matches it 3.4% of the time, so with ~14 configs tried it's expected by chance. No variant beat B0 in most years. B0 trades cluster: 93/133 start within 7 days of the previous exit.
  - **Position sizing: ADOPT volatility targeting for live trading.** Notional per sleeve = L × (equity/10) × min(2, 0.25 / the coin's 30-day annualized realized daily vol), with a gross-exposure cap of 2× equity. 2021–2026 at 2x, ex-2021: CAGR 22%, max DD 24%, Sharpe 1.02, vs equal-size 2x at 26% / 52% / 0.73 (2025: −3% vs −30%). Stable across 15/30/60/90-day vol lookbacks; no liquidations. A 20% drawdown brake and a 5-position cap both did worse. Code was checked: vol uses only days closed before entry, and fees are charged both sides. Script: `scratchpad/sizing/sizing.py` (not in the repo).
- Open-interest history: the `/futures/data/*` REST endpoints only return 30 days. Use `data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/` (5m rows from 2020-09) for backtests.
- **Conclusion so far:** price-only rules on BTC 15m (EMA, RSI, VWAP, volume, MACD, BB, OBV, ATR, S/R, any TP/SL) show no durable edge. Next research needs different information: derivatives positioning (funding rate, open interest) or higher-timeframe trend following.
- **Rules:** judge any change on expectancy and profit factor, not win rate, because WR can be bought by shrinking TP. Always validate out of sample. Do not report in-sample results as improvements.

## Not built yet
- News/social agents (CryptoPanic, LunarCrush).
- Candlestick-pattern agent.
- Higher-timeframe and derivatives-data signals (funding rate, open interest). This is the most promising research direction given the findings above.
