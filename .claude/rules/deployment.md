# Deployment

Hosted on DigitalOcean at `188.166.77.144` via systemd service `btc-bot`.

## Deploy a change
```bash
# local
git add main.py && git commit -m "..." && git push

# on the server (SSH in first)
ssh root@188.166.77.144
cd /root/btc-bot && git pull && systemctl restart btc-bot
systemctl status btc-bot   # confirm active (running)
```

## Useful server commands
```bash
journalctl -u btc-bot -f --no-pager                        # live logs
journalctl -u btc-bot --since "1 hour ago" --no-pager      # recent logs
cat /root/btc-bot/portfolio.json | python3 -m json.tool    # current state
grep -A10 "async def futures_order" /root/btc-bot/main.py  # verify a fix
```

## Run locally
```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Test webhook:
```bash
curl -X POST localhost:8000/webhook -H "Content-Type: application/json" \
  -d '{"secret":"btcbot2024","signal":"buy","price":"65000","symbol":"BTCUSDT","score":"6"}'
```

## Environment variables (set in DigitalOcean)
| Var | Default | Notes |
|-----|---------|-------|
| `LIVE_TRADING` | `false` | `true` = real Binance orders and real money |
| `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` | `""` | Server IP must be whitelisted on Binance |
| `LIVE_SIZE_USD` | `100` | Margin per live trade |
| `AGENT_MODE` | `shadow` | `off` / `shadow` (Telegram only) / `live` (agents place trades) |
| `MIN_AGENT_SCORE` | `2` | Scoring-agent threshold, range -9..+9 |

Find where env vars are set with `systemctl cat btc-bot`: look for `Environment=` or `EnvironmentFile=` lines. After changing them, run `systemctl daemon-reload && systemctl restart btc-bot`.

Check the agents on the server: `curl -s "localhost/agents?force=LONG" | python3 -m json.tool`

**Every push to `main` is live.** When `LIVE_TRADING=true` it affects real money.
