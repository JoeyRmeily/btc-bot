# Strategy & TradingView

The V9 rules below are now implemented twice: in Pine Script on TradingView, and as the required (🔒) agents in `main.py` (see agents.md). Change both if the rules change.

## V9 Strategy (current best)
- **Signal**: EMA9/EMA21 crossover on 15m chart (fires once at the crossing bar)
- **1H filter** (relaxed): EMA9 > EMA21 + RSI > 50 + close > VWAP
- **4H filter** (strict): EMA9 > EMA21 > EMA50 + RSI > 50
- **15m confirm**: close > VWAP + RSI 45-70 + volume > 1.5× avg + close > EMA50

**Do not relax the 4H filter.** Every past version that relaxed it dropped win rate to ~33% and PF to ~1.3.

## Risk parameters (constants in main.py)
```
SL_PCT    = 1.5
TP1_PCT   = 2.0
TP2_PCT   = 3.5
TP3_PCT   = 5.0
TRAIL_PCT = 1.5
TRADE_SIZE = 0.20   # 20% of free sim balance per trade
LEVERAGE   = 2
```

## TradingView alert setup (optional now that agents generate signals)
- **Condition**: `alert() function calls only` (NOT "Order fills only")
- **Message** — must be this exact JSON, not the strategy name:
```json
{"secret":"btcbot2024","signal":"{{strategy.order.action}}","price":"{{close}}","symbol":"{{ticker}}","score":"6"}
```
- **Webhook URL**: `http://188.166.77.144/webhook`
- **Expiry**: set to 2030+ so it never expires silently

## Webhook signals
| signal value | Action |
|-------------|--------|
| `buy` | Open LONG |
| `sell` | Close all LONGs |
| `short` / `sell short` | Open SHORT |
| `short_close` / `buy to cover` | Close all SHORTs |

## Backtest results (Jan 1 – Apr 19, 2026)
- 39 trades, 43.59% win rate, 2.013 profit factor, 1.23% max drawdown
- Goal: 60% live win rate before scaling real capital

## Recent backtest (Apr 1 – Jul 28, 2026)
- 36 trades, 38.89% win rate, 1.383 profit factor. Weaker in the 2026 downtrend, which is why the macro agents were added.

## Improvements
Fear/Greed, 200DMA, halving cycle, Pi Cycle, MACD, Bollinger, OBV and ATR now exist as scoring agents (agents.md). Still planned: news/social sentiment and candlestick patterns.
