# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Automated crypto trading bot. TradingView fires webhook alerts → FastAPI server on DigitalOcean → virtual portfolio simulation + optional live Binance Futures orders + Telegram notifications.

## Files
| File | Purpose |
|------|---------|
| `main.py` | Entire app — FastAPI webhook, price monitor, Binance client, Telegram, dashboard |
| `requirements.txt` | `fastapi uvicorn httpx websockets` |
| `seed_trades.json` | Restore trade history after redeploy via `POST /admin/seed` |

The Pine Script strategy lives in TradingView, not this repo.

## Conventions
- Single file, section banners (`# ─── Name ───`), aligned assignments, HTML-formatted Telegram messages.
- Commit messages: short imperative, e.g. `Fix -1022 signature error`.
- Every push to `main` deploys live. When `LIVE_TRADING=true` it affects real money.

## Detailed rules
@rules/architecture.md
@rules/deployment.md
@rules/binance.md
@rules/strategy.md
@rules/agents.md
