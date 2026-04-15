from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from datetime import datetime
import httpx, json, os

app = FastAPI()

TELEGRAM_TOKEN   = "8635147020:AAGxhQLIJQfN7FUWGr-3gcQam7uORywuesQ"
TELEGRAM_CHAT_ID = "6623057612"
SECRET           = "btcbot2024"

PORTFOLIO_FILE   = "portfolio.json"
STARTING_BALANCE = 1000.0
TRADE_SIZE_PCT   = 0.33  # 33% per trade = up to 3 positions simultaneously

def load():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {
        "balance": STARTING_BALANCE,
        "positions": {},        # multiple positions support
        "trades": [],
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0
    }

def save(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

async def telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as c:
        await c.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "HTML"
        })

@app.post("/webhook")
async def webhook(request: Request):
    data   = await request.json()
    if data.get("secret") != SECRET:
        return {"status": "unauthorized"}

    signal = data.get("signal", "").upper()
    price  = float(data.get("price", 0))
    symbol = data.get("symbol", "BTCUSDT")
    now    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    port   = load()

    # migrate old portfolio format
    if "position" in port and "positions" not in port:
        port["positions"] = {}
        if port["position"] is not None:
            port["positions"][symbol] = port["position"]
        del port["position"]
        save(port)

    sl_pct = 1.5
    tp_pct = 3.0

    # ── LONG (BUY) ──
    if signal == "BUY":
        if symbol in port["positions"]:
            await telegram(
                f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
                f"Signal: LONG | Price: ${price:,.2f}\n"
                f"Reason: Already in a {port['positions'][symbol]['type']} position"
            )
        elif port["balance"] < STARTING_BALANCE * TRADE_SIZE_PCT:
            await telegram(
                f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
                f"Signal: LONG | Price: ${price:,.2f}\n"
                f"Reason: Insufficient balance (${port['balance']:.2f})"
            )
        else:
            amount   = STARTING_BALANCE * TRADE_SIZE_PCT
            qty      = amount / price
            sl_price = round(price * (1 - sl_pct / 100), 2)
            tp_price = round(price * (1 + tp_pct / 100), 2)
            port["positions"][symbol] = {
                "type": "LONG", "entry_price": price,
                "quantity": qty, "amount": amount,
                "time": now, "sl_price": sl_price, "tp_price": tp_price
            }
            port["balance"] -= amount
            save(port)
            await telegram(
                f"🟢 <b>LONG SIGNAL — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now}\n"
                f"💰 Entry: <b>${price:,.2f}</b>\n"
                f"📦 Qty: {qty:.6f} BTC\n"
                f"💵 Invested: ${amount:.2f}\n"
                f"🛑 Stop Loss: ${sl_price:,.2f} (-{sl_pct}%)\n"
                f"🎯 Take Profit: ${tp_price:,.2f} (+{tp_pct}%)\n"
                f"🏦 Free Balance: ${port['balance']:.2f}\n"
                f"📊 Open Positions: {len(port['positions'])}/3\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ SIMULATION — No real money used"
            )

    # ── SHORT ──
    elif signal == "SHORT":
        if symbol in port["positions"]:
            await telegram(
                f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
                f"Signal: SHORT | Price: ${price:,.2f}\n"
                f"Reason: Already in a {port['positions'][symbol]['type']} position"
            )
        elif port["balance"] < STARTING_BALANCE * TRADE_SIZE_PCT:
            await telegram(
                f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
                f"Signal: SHORT | Price: ${price:,.2f}\n"
                f"Reason: Insufficient balance (${port['balance']:.2f})"
            )
        else:
            amount   = STARTING_BALANCE * TRADE_SIZE_PCT
            qty      = amount / price
            sl_price = round(price * (1 + sl_pct / 100), 2)
            tp_price = round(price * (1 - tp_pct / 100), 2)
            port["positions"][symbol] = {
                "type": "SHORT", "entry_price": price,
                "quantity": qty, "amount": amount,
                "time": now, "sl_price": sl_price, "tp_price": tp_price
            }
            port["balance"] -= amount
            save(port)
            await telegram(
                f"🔴 <b>SHORT SIGNAL — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now}\n"
                f"💰 Entry: <b>${price:,.2f}</b>\n"
                f"📦 Qty: {qty:.6f} BTC\n"
                f"💵 Invested: ${amount:.2f}\n"
                f"🛑 Stop Loss: ${sl_price:,.2f} (+{sl_pct}%)\n"
                f"🎯 Take Profit: ${tp_price:,.2f} (-{tp_pct}%)\n"
                f"🏦 Free Balance: ${port['balance']:.2f}\n"
                f"📊 Open Positions: {len(port['positions'])}/3\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ SIMULATION — No real money used"
            )

    # ── CLOSE LONG (SELL) ──
    elif signal == "SELL":
        if symbol not in port["positions"]:
            await telegram(
                f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
                f"Signal: SELL | Price: ${price:,.2f}\n"
                f"Reason: No open LONG position to close"
            )
        else:
            pos     = port["positions"][symbol]
            value   = pos["quantity"] * price
            pnl     = value - pos["amount"]
            pnl_pct = (pnl / pos["amount"]) * 100
            port["balance"]   += value
            port["total_pnl"] += pnl
            port["wins"]      += 1 if pnl > 0 else 0
            port["losses"]    += 1 if pnl < 0 else 0
            port["trades"].append({
                "symbol": symbol, "type": "LONG",
                "entry": pos["entry_price"], "exit": price,
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "entry_time": pos["time"], "exit_time": now
            })
            del port["positions"][symbol]
            save(port)

            total = port["wins"] + port["losses"]
            wr    = (port["wins"] / total * 100) if total > 0 else 0
            emoji = "✅" if pnl > 0 else "❌"
            await telegram(
                f"🏁 <b>LONG CLOSED — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now}\n"
                f"💰 Exit: <b>${price:,.2f}</b>\n"
                f"📥 Entry was: ${pos['entry_price']:,.2f}\n"
                f"{emoji} P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏦 Balance: ${port['balance']:.2f}\n"
                f"📊 Total P&L: ${port['total_pnl']:.2f}\n"
                f"🎯 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ SIMULATION — No real money used"
            )

    # ── CLOSE SHORT ──
    elif signal == "SHORT_CLOSE":
        if symbol not in port["positions"]:
            await telegram(
                f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
                f"Signal: SHORT CLOSE | Price: ${price:,.2f}\n"
                f"Reason: No open SHORT position to close"
            )
        else:
            pos     = port["positions"][symbol]
            pnl     = pos["amount"] - (pos["quantity"] * price)
            pnl_pct = (pnl / pos["amount"]) * 100
            port["balance"]   += pos["amount"] + pnl
            port["total_pnl"] += pnl
            port["wins"]      += 1 if pnl > 0 else 0
            port["losses"]    += 1 if pnl < 0 else 0
            port["trades"].append({
                "symbol": symbol, "type": "SHORT",
                "entry": pos["entry_price"], "exit": price,
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "entry_time": pos["time"], "exit_time": now
            })
            del port["positions"][symbol]
            save(port)

            total = port["wins"] + port["losses"]
            wr    = (port["wins"] / total * 100) if total > 0 else 0
            emoji = "✅" if pnl > 0 else "❌"
            await telegram(
                f"🏁 <b>SHORT CLOSED — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now}\n"
                f"💰 Exit: <b>${price:,.2f}</b>\n"
                f"📥 Entry was: ${pos['entry_price']:,.2f}\n"
                f"{emoji} P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🏦 Balance: ${port['balance']:.2f}\n"
                f"📊 Total P&L: ${port['total_pnl']:.2f}\n"
                f"🎯 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ SIMULATION — No real money used"
            )

    return {"status": "ok"}

@app.post("/reset-trades")
async def reset_trades(request: Request):
    data = await request.json()
    if data.get("secret") != SECRET:
        return {"status": "unauthorized"}
    p = load()
    if p["trades"]:
        last_trade = p["trades"].pop()
        p["total_pnl"] -= last_trade["pnl"]
        if last_trade["pnl"] > 0:
            p["wins"] -= 1
        else:
            p["losses"] -= 1
        save(p)
        return {"status": "removed", "trade": last_trade}
    return {"status": "no trades to remove"}

@app.get("/status")
async def status():
    p = load()
    positions = p.get("positions", {})
    total = p["wins"] + p["losses"]
    wr = round((p["wins"] / total * 100), 1) if total > 0 else 0

    # Build positions HTML
    if positions:
        pos_html = ""
        for sym, pos in positions.items():
            color = "green" if pos["type"] == "LONG" else "red"
            pos_html += f"""
            <div class="card open">
                <h2>{'📈' if pos['type'] == 'LONG' else '📉'} {pos['type']} — {sym}</h2>
                <div class="row"><span>Entry Price</span><span>${pos['entry_price']:,.2f}</span></div>
                <div class="row"><span>Quantity</span><span>{pos['quantity']:.6f} BTC</span></div>
                <div class="row"><span>Invested</span><span>${pos['amount']:,.2f}</span></div>
                <div class="row"><span>Stop Loss</span><span class="red">${pos.get('sl_price',0):,.2f}</span></div>
                <div class="row"><span>Take Profit</span><span class="green">${pos.get('tp_price',0):,.2f}</span></div>
                <div class="row"><span>Opened At</span><span>{pos['time']}</span></div>
            </div>
            """
    else:
        pos_html = """
        <div class="card">
            <h2>📭 No Open Positions</h2>
            <p style="color:#888;text-align:center;">Waiting for next signal...</p>
        </div>
        """

    trades_html = ""
    for t in reversed(p["trades"][-10:]):
        color = "green" if t["pnl"] > 0 else "red"
        emoji = "✅" if t["pnl"] > 0 else "❌"
        ttype = t.get("type", "LONG")
        trades_html += f"""
        <div class="row">
            <span>{t['entry_time']}</span>
            <span>{'📈' if ttype == 'LONG' else '📉'} {ttype} {t.get('symbol','BTCUSDT')}</span>
            <span>${t['entry']:,.0f} → ${t['exit']:,.0f}</span>
            <span class="{color}">{emoji} ${t['pnl']:.2f} ({t['pnl_pct']:.2f}%)</span>
        </div>
        """

    slots_used = len(positions)
    slots_free = 3 - slots_used

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>BTC Signal Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <meta http-equiv="refresh" content="30">
        <style>
            * {{ margin:0; padding:0; box-sizing:border-box; }}
            body {{ background:#0d1117; color:#e6edf3; font-family:Arial,sans-serif; padding:20px; }}
            h1 {{ text-align:center; color:#58a6ff; margin-bottom:20px; font-size:24px; }}
            .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:15px; margin-bottom:20px; }}
            .stat {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:20px; text-align:center; }}
            .stat .value {{ font-size:28px; font-weight:bold; margin:10px 0; }}
            .stat .label {{ color:#8b949e; font-size:13px; }}
            .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:20px; margin-bottom:20px; }}
            .card.open {{ border-color:#238636; }}
            .card h2 {{ margin-bottom:15px; font-size:16px; }}
            .row {{ display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid #21262d; font-size:14px; flex-wrap:wrap; gap:5px; }}
            .row:last-child {{ border-bottom:none; }}
            .green {{ color:#3fb950; font-weight:bold; }}
            .red {{ color:#f85149; font-weight:bold; }}
            .yellow {{ color:#d29922; font-weight:bold; }}
            .badge {{ display:inline-block; padding:4px 10px; border-radius:20px; font-size:12px; background:#238636; }}
            .slots {{ display:flex; gap:8px; justify-content:center; margin-top:5px; }}
            .slot {{ width:20px; height:20px; border-radius:50%; background:#238636; }}
            .slot.empty {{ background:#30363d; }}
            footer {{ text-align:center; color:#8b949e; font-size:12px; margin-top:20px; }}
        </style>
    </head>
    <body>
        <h1>🤖 BTC Signal Bot <span class="badge">LIVE</span></h1>

        <div class="grid">
            <div class="stat">
                <div class="label">💰 Free Balance</div>
                <div class="value green">${p['balance']:,.2f}</div>
                <div class="label">USDT</div>
            </div>
            <div class="stat">
                <div class="label">📊 Total P&L</div>
                <div class="value {'green' if p['total_pnl'] >= 0 else 'red'}">${p['total_pnl']:,.2f}</div>
                <div class="label">USD</div>
            </div>
            <div class="stat">
                <div class="label">🎯 Win Rate</div>
                <div class="value {'green' if wr >= 50 else 'red'}">{wr}%</div>
                <div class="label">{p['wins']}W / {p['losses']}L</div>
            </div>
            <div class="stat">
                <div class="label">📂 Position Slots</div>
                <div class="value yellow">{slots_used}/3</div>
                <div class="slots">
                    {''.join(['<div class="slot"></div>' if i < slots_used else '<div class="slot empty"></div>' for i in range(3)])}
                </div>
            </div>
        </div>

        {pos_html}

        <div class="card">
            <h2>📜 Last 10 Trades</h2>
            {'<p style="color:#888;text-align:center;padding:10px;">No trades yet</p>' if not p['trades'] else trades_html}
        </div>

        <footer>Auto-refreshes every 30 seconds • SIMULATION MODE • No real money used</footer>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/")
async def root():
    return {"status": "BTC Signal Bot V2 running ✅"}
