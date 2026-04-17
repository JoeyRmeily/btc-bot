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
TRADE_SIZE_PCT   = 0.33

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {"balance": STARTING_BALANCE, "positions": [], "closed_trades": []}

def save_portfolio(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

async def send_telegram(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"})

def score_label(score: int) -> str:
    if score >= 6: return "🔥 Strong"
    if score == 5: return "⚡ Medium"
    return "✅ Valid"

def get_tp_levels(price: float, score: int, is_long: bool):
    d = 1 if is_long else -1
    tp1 = price * (1 + d * 0.02)
    tp2 = price * (1 + d * 0.035) if score >= 5 else None
    tp3 = price * (1 + d * 0.05)  if score >= 6 else None
    sl  = price * (1 - d * 0.01)
    return tp1, tp2, tp3, sl

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    if data.get("secret") != SECRET:
        return {"error": "unauthorized"}

    signal  = data.get("signal", "").lower()
    price   = float(data.get("price", 0))
    symbol  = data.get("symbol", "BTCUSDT")
    score   = int(data.get("score", 4))
    now     = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    portfolio = load_portfolio()
    balance   = portfolio["balance"]
    positions = portfolio["positions"]
    trade_amt = balance * TRADE_SIZE_PCT

    # ── OPEN LONG ─────────────────────────────────────────────────────────────
    if signal == "buy":
        if len(positions) >= 3:
            await send_telegram("⚠️ Max 3 positions open. Signal skipped.")
            return {"status": "skipped"}

        tp1, tp2, tp3, sl = get_tp_levels(price, score, True)

        position = {
            "type": "LONG", "symbol": symbol,
            "entry": price, "size": trade_amt,
            "score": score, "time": now,
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3
        }
        positions.append(position)
        save_portfolio(portfolio)

        msg = (
            f"🟢 <b>LONG OPENED</b>\n"
            f"Symbol: {symbol}\n"
            f"Score: {score}/7 {score_label(score)}\n"
            f"Entry: ${price:,.2f}\n"
            f"Size: ${trade_amt:,.2f}\n"
            f"SL: ${sl:,.2f} <i>(-1%)</i>\n"
            f"TP1: ${tp1:,.2f} <i>(+2%)</i>"
        )
        if tp2: msg += f"\nTP2: ${tp2:,.2f} <i>(+3.5%)</i>"
        if tp3: msg += f"\nTP3: ${tp3:,.2f} <i>(+5% + trail)</i>"
        msg += f"\n\nBalance: ${balance:,.2f}\n🕐 {now}"
        await send_telegram(msg)

    # ── CLOSE LONG ────────────────────────────────────────────────────────────
    elif signal in ["sell", "close_long"]:
        longs = [p for p in positions if p["type"] == "LONG"]
        if not longs:
            return {"status": "no long to close"}

        pos = longs[0]
        pnl = (price - pos["entry"]) / pos["entry"] * pos["size"]
        portfolio["balance"] += pnl
        positions.remove(pos)
        portfolio["closed_trades"].append({**pos, "exit": price, "pnl": pnl, "close_time": now})
        save_portfolio(portfolio)

        emoji = "✅" if pnl > 0 else "❌"
        msg = (
            f"{emoji} <b>LONG CLOSED</b>\n"
            f"Symbol: {symbol}\n"
            f"Entry: ${pos['entry']:,.2f} → Exit: ${price:,.2f}\n"
            f"PnL: ${pnl:+,.2f}\n"
            f"New Balance: ${portfolio['balance']:,.2f}\n🕐 {now}"
        )
        await send_telegram(msg)

    # ── OPEN SHORT ────────────────────────────────────────────────────────────
    elif signal == "short":
        if len(positions) >= 3:
            await send_telegram("⚠️ Max 3 positions open. Signal skipped.")
            return {"status": "skipped"}

        tp1, tp2, tp3, sl = get_tp_levels(price, score, False)

        position = {
            "type": "SHORT", "symbol": symbol,
            "entry": price, "size": trade_amt,
            "score": score, "time": now,
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3
        }
        positions.append(position)
        save_portfolio(portfolio)

        msg = (
            f"🔴 <b>SHORT OPENED</b>\n"
            f"Symbol: {symbol}\n"
            f"Score: {score}/7 {score_label(score)}\n"
            f"Entry: ${price:,.2f}\n"
            f"Size: ${trade_amt:,.2f}\n"
            f"SL: ${sl:,.2f} <i>(+1%)</i>\n"
            f"TP1: ${tp1:,.2f} <i>(-2%)</i>"
        )
        if tp2: msg += f"\nTP2: ${tp2:,.2f} <i>(-3.5%)</i>"
        if tp3: msg += f"\nTP3: ${tp3:,.2f} <i>(-5% + trail)</i>"
        msg += f"\n\nBalance: ${balance:,.2f}\n🕐 {now}"
        await send_telegram(msg)

    # ── CLOSE SHORT ───────────────────────────────────────────────────────────
    elif signal in ["short_close", "close_short"]:
        shorts = [p for p in positions if p["type"] == "SHORT"]
        if not shorts:
            return {"status": "no short to close"}

        pos = shorts[0]
        pnl = (pos["entry"] - price) / pos["entry"] * pos["size"]
        portfolio["balance"] += pnl
        positions.remove(pos)
        portfolio["closed_trades"].append({**pos, "exit": price, "pnl": pnl, "close_time": now})
        save_portfolio(portfolio)

        emoji = "✅" if pnl > 0 else "❌"
        msg = (
            f"{emoji} <b>SHORT CLOSED</b>\n"
            f"Symbol: {symbol}\n"
            f"Entry: ${pos['entry']:,.2f} → Exit: ${price:,.2f}\n"
            f"PnL: ${pnl:+,.2f}\n"
            f"New Balance: ${portfolio['balance']:,.2f}\n🕐 {now}"
        )
        await send_telegram(msg)

    return {"status": "ok"}

@app.get("/status", response_class=HTMLResponse)
async def status():
    p = load_portfolio()
    positions = p["positions"]
    closed = p["closed_trades"]
    balance = p["balance"]
    total_pnl = balance - STARTING_BALANCE
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    win_rate = (wins / len(closed) * 100) if closed else 0

    slots = ""
    for i in range(3):
        if i < len(positions):
            pos = positions[i]
            tp_info = f"TP1: ${pos.get('tp1', 0):,.0f}"
            if pos.get('tp2'): tp_info += f" | TP2: ${pos['tp2']:,.0f}"
            if pos.get('tp3'): tp_info += f" | TP3: ${pos['tp3']:,.0f}"
            pnl_now = (pos["entry"] - pos["entry"]) * pos["size"]
            slots += f"""
            <div class='slot active'>
                <b>{pos['type']} — {pos['symbol']}</b><br>
                Score: {pos.get('score', '?')}/7<br>
                Entry: ${pos['entry']:,.2f}<br>
                Size: ${pos['size']:,.2f}<br>
                SL: ${pos.get('sl', 0):,.2f}<br>
                {tp_info}<br>
                <small>{pos['time']}</small>
            </div>"""
        else:
            slots += "<div class='slot empty'>Empty Slot</div>"

    trades_html = ""
    for t in reversed(closed[-10:]):
        pnl = t.get('pnl', 0)
        color = "#00ff88" if pnl > 0 else "#ff4444"
        trades_html += f"<tr><td>{t['type']}</td><td>${t['entry']:,.2f}</td><td>${t.get('exit',0):,.2f}</td><td style='color:{color}'>${pnl:+,.2f}</td><td>{t.get('score','?')}/7</td><td>{t['time']}</td></tr>"

    return f"""<!DOCTYPE html><html><head><title>BTC Bot V8</title>
    <meta http-equiv='refresh' content='30'>
    <style>
        body{{background:#0d1117;color:#e6edf3;font-family:monospace;padding:20px}}
        h1{{color:#58a6ff}} h2{{color:#8b949e}}
        .stats{{display:flex;gap:20px;margin:20px 0}}
        .stat{{background:#161b22;padding:15px;border-radius:8px;min-width:140px}}
        .stat .val{{font-size:24px;font-weight:bold;color:#58a6ff}}
        .positive{{color:#00ff88!important}} .negative{{color:#ff4444!important}}
        .slots{{display:flex;gap:15px;margin:20px 0}}
        .slot{{background:#161b22;padding:15px;border-radius:8px;width:220px;border:1px solid #30363d}}
        .slot.active{{border-color:#58a6ff}}
        .slot.empty{{color:#8b949e;text-align:center;padding-top:40px}}
        table{{width:100%;border-collapse:collapse;margin-top:10px}}
        th,td{{padding:8px;text-align:left;border-bottom:1px solid #21262d}}
        th{{color:#8b949e}}
    </style></head><body>
    <h1>BTC Bot V8 Dashboard</h1>
    <div class='stats'>
        <div class='stat'><div>Balance</div><div class='val'>${balance:,.2f}</div></div>
        <div class='stat'><div>Total PnL</div><div class='val {"positive" if total_pnl>=0 else "negative"}'>${total_pnl:+,.2f}</div></div>
        <div class='stat'><div>Trades</div><div class='val'>{len(closed)}</div></div>
        <div class='stat'><div>Win Rate</div><div class='val'>{win_rate:.0f}%</div></div>
        <div class='stat'><div>Open</div><div class='val'>{len(positions)}/3</div></div>
    </div>
    <h2>Open Positions</h2>
    <div class='slots'>{slots}</div>
    <h2>Last 10 Trades</h2>
    <table><tr><th>Type</th><th>Entry</th><th>Exit</th><th>PnL</th><th>Score</th><th>Time</th></tr>
    {trades_html}</table>
    </body></html>"""

@app.get("/")
async def root():
    return {"status": "BTC Bot V8 running", "dashboard": "/status"}

@app.post("/reset-trades")
async def reset_trades():
    p = load_portfolio()
    if p["closed_trades"]:
        p["closed_trades"].pop()
        save_portfolio(p)
        return {"status": "last trade removed"}
    return {"status": "no trades to remove"}
