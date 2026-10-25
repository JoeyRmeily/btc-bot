# Architecture

Single-file FastAPI app (`main.py`). No modules, no tests.

## Request flow
```
strategy_engine (60s tick) → Trend 4H on 9 coins + BTC funding capitulation
                           → paper positions in strategies.json + Telegram (see strategies.md)
telegram_commands (long-polls getUpdates) → /summary /market /positions /help → reply via telegram()
signal_generator (retired, AGENT_MODE=off) → analyze() → agents vote (see agents.md)
TradingView alert → POST /webhook → process_signal()
process_signal() → open/close sim + live position → Telegram
price_monitor (1s loop) → checks price_cache against TP/SL levels
                        → calls futures_order() for live closes
ws_price_feed → streams btcusdt@aggTrade → price_cache["BTCUSDT"]
```

Four background tasks are started in `lifespan()`: `ws_price_feed`, `price_monitor`, `signal_generator`, plus `init_futures()` once when live. `process_signal(data)` holds all the entry and exit logic. The `/webhook` route only parses JSON and calls it, so internal callers use `process_signal` directly instead of POSTing to localhost.

## Telegram commands
`telegram_commands()` long-polls `getUpdates` (30s timeout). The server only has plain HTTP on an IP address, and Telegram webhooks require HTTPS, so polling is used instead.
- It answers **only** messages from `TELEGRAM_CHAT_ID`. Messages sent before startup are ignored, so a restart doesn't replay old commands.
- Commands are registered with `setMyCommands` at startup, so they appear in Telegram's menu.
- Reports are built by `build_summary()` / `build_market()` / `build_positions()`. `/status` and `/start` alias `/summary`; anything unknown returns `/help`.
- Only one process may poll a bot token at a time. A second `getUpdates` consumer (e.g. a local test) steals updates from the server, and a webhook set on the bot makes polling fail with 409. Test the builders locally by calling `handle_command()` directly, never the poller.
- To add a command: write a `build_x()` that returns HTML (no bare `&`, `<`, `>`), add it to `TG_COMMANDS` and to the `builders` map in `handle_command()`.

## State
`portfolio.json` is the only store — loaded and saved on every webhook and monitor tick. No locking. After a redeploy, restore with `POST /admin/seed` using `seed_trades.json`.

## Sim vs live differ
- **Sim**: splits position — TP1 34%, TP2 50% of remainder, TP3/trail rest.
- **Live (Binance)**: closes the **full position at TP1** (`live_tp1_qty = live_qty`, `live_tp2_qty = live_tp3_qty = 0`). Position size is too small to split at Binance's 0.001 BTC minimum.
- Live qty formula: `round(LIVE_SIZE_USD * LEVERAGE / price, 3)` — **must include LEVERAGE**.
- Live P&L stored separately in `portfolio["live_trades"]`.

## LONG and SHORT branches
The exit ladder in `price_monitor` has separate mirrored blocks for LONG and SHORT. **Always change both together.**

## Endpoints
| Endpoint | Notes |
|----------|-------|
| `GET /status` | HTML dashboard; Live tab default, Simulation tab |
| `POST /webhook` | TradingView alerts |
| `GET /strategies` | Strategy engine paper positions, per-strategy summary, last 50 closed trades |
| `GET /agents` | Fresh agent analysis, read-only; `?force=LONG` / `?force=SHORT` runs all agents without a crossover |
| `POST /reset-trades` | Reset sim portfolio — no auth |
| `POST /admin/seed` | Replace portfolio — requires `secret` |
| `GET /my-ip` | Server outbound IP for Binance whitelist |
| `GET /debug/balance` | Raw Binance balance response |

## Known gaps
- `sell` / `short_close` signals close only the sim position — no Binance order sent; live position left open until TP1 or SL.
- Entry and signal-exit Telegram messages always say "SIMULATION" even in live mode.
- `/reset-trades` has no auth.
- `datetime.utcnow()` is deprecated but used throughout.
