from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from datetime import datetime
import httpx, json, os, asyncio

TELEGRAM_TOKEN   = "8635147020:AAGxhQLIJQfN7FUWGr-3gcQam7uORywuesQ"
TELEGRAM_CHAT_ID = "6623057612"
SECRET           = "btcbot2024"
PORTFOLIO_FILE   = "portfolio.json"
STARTING_BALANCE = 1000.0

SL_PCT     = 1.0
TP1_PCT    = 2.0
TP2_PCT    = 3.5
TP3_PCT    = 5.0
TRAIL_PCT  = 1.5
TRADE_SIZE = 0.33
MAX_POSITIONS = 3

# ─── Portfolio ────────────────────────────────
def load():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            p = json.load(f)
        # migrate old single-position format
        if "position" in p and "positions" not in p:
            old = p.pop("position")
            p["positions"] = []
            if old:
                entry   = old.get("entry_price", 0)
                qty     = old.get("quantity", 0)
                amt     = old.get("amount", 0)
                tp1_qty = round(qty * 0.34, 6)
                tp2_qty = round((qty - tp1_qty) * 0.50, 6)
                tp3_qty = round(qty - tp1_qty - tp2_qty, 6)
                p["positions"].append({
                    "symbol":        old.get("symbol", "BTCUSDT"),
                    "side":          "LONG",
                    "entry_price":   entry,
                    "qty_total":     qty,
                    "qty_remaining": qty,
                    "tp1_qty":       tp1_qty,
                    "tp2_qty":       tp2_qty,
                    "tp3_qty":       tp3_qty,
                    "amount":        amt,
                    "time":          old.get("time", ""),
                    "sl_price":      old.get("sl_price",  round(entry * 0.99,  2)),
                    "tp1_price":     old.get("tp1_price", round(entry * 1.02,  2)),
                    "tp2_price":     old.get("tp2_price", round(entry * 1.035, 2)),
                    "tp3_price":     old.get("tp3_price", round(entry * 1.05,  2)),
                    "tp1_hit":      False,
                    "tp2_hit":      False,
                    "tp3_hit":      False,
                    "trail_active": False,
                    "trail_peak":   None,
                    "trail_stop":   None,
                    "score":        old.get("score", "6")
                })
        return p
    return {
        "balance":   STARTING_BALANCE,
        "positions": [],
        "trades":    [],
        "total_pnl": 0.0,
        "wins":      0,
        "losses":    0
    }

def save(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

# ─── Telegram ─────────────────────────────────
async def telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as c:
        await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID,
                                "text": msg, "parse_mode": "HTML"})

# ─── Live price ───────────────────────────────
async def get_price(symbol="BTCUSDT"):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
            return float(r.json()["price"])
    except Exception:
        return None

# ─── Partial close helper ─────────────────────
def partial_close(port, pos, qty_to_close, price, reason):
    value   = qty_to_close * price
    cost    = qty_to_close * pos["entry_price"]
    pnl     = value - cost
    pnl_pct = (pnl / cost) * 100

    port["balance"]   += value
    port["total_pnl"] += pnl
    if pnl > 0:
        port["wins"] += 1
    else:
        port["losses"] += 1

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    port["trades"].append({
        "symbol":     pos["symbol"],
        "side":       pos["side"],
        "entry":      pos["entry_price"],
        "exit":       round(price, 2),
        "qty":        round(qty_to_close, 6),
        "pnl":        round(pnl, 2),
        "pnl_pct":    round(pnl_pct, 2),
        "entry_time": pos["time"],
        "exit_time":  now,
        "reason":     reason
    })
    pos["qty_remaining"] -= qty_to_close
    return pnl, pnl_pct

# ─── Background price monitor (every 30 s) ────
async def price_monitor():
    while True:
        await asyncio.sleep(30)
        port = load()
        if not port["positions"]:
            continue

        changed   = False
        to_remove = []

        for pos in port["positions"]:
            symbol = pos.get("symbol", "BTCUSDT")
            price  = await get_price(symbol)
            if price is None:
                continue

            now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            entry = pos["entry_price"]

            # ── Stop Loss ────────────────────────────────
            if price <= pos["sl_price"] and pos["qty_remaining"] > 0:
                pnl, pnl_pct = partial_close(port, pos, pos["qty_remaining"], price, "SL")
                to_remove.append(pos)
                changed = True
                total = port["wins"] + port["losses"]
                wr = (port["wins"] / total * 100) if total else 0
                await telegram(
                    f"🛑 <b>STOP LOSS — {symbol}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🕐 {now}\n"
                    f"💰 Exit: <b>${price:,.2f}</b>\n"
                    f"📥 Entry: ${entry:,.2f}\n"
                    f"❌ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🏦 Balance: ${port['balance']:.2f}\n"
                    f"📊 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
                    f"⚠️ SIMULATION"
                )
                continue

            # ── TP1 ──────────────────────────────────────
            if not pos.get("tp1_hit") and price >= pos["tp1_price"]:
                pnl, pnl_pct = partial_close(port, pos, pos["tp1_qty"], price, "TP1")
                pos["tp1_hit"] = True
                changed = True
                await telegram(
                    f"🎯 <b>TP1 HIT — {symbol}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🕐 {now}\n"
                    f"💰 Price: <b>${price:,.2f}</b> (+{TP1_PCT}%)\n"
                    f"📦 Closed 34% of position\n"
                    f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"⏳ Watching TP2 at ${pos['tp2_price']:,.2f}\n"
                    f"🏦 Balance: ${port['balance']:.2f}\n"
                    f"⚠️ SIMULATION"
                )

            # ── TP2 ──────────────────────────────────────
            if pos.get("tp1_hit") and not pos.get("tp2_hit") and price >= pos["tp2_price"]:
                pnl, pnl_pct = partial_close(port, pos, pos["tp2_qty"], price, "TP2")
                pos["tp2_hit"] = True
                changed = True
                await telegram(
                    f"🎯🎯 <b>TP2 HIT — {symbol}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🕐 {now}\n"
                    f"💰 Price: <b>${price:,.2f}</b> (+{TP2_PCT}%)\n"
                    f"📦 Closed 50% of remaining\n"
                    f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"⏳ Trailing stop activates at TP3 ${pos['tp3_price']:,.2f}\n"
                    f"🏦 Balance: ${port['balance']:.2f}\n"
                    f"⚠️ SIMULATION"
                )

            # ── TP3 — activate trailing stop ─────────────
            if pos.get("tp2_hit") and not pos.get("tp3_hit") and price >= pos["tp3_price"]:
                pos["tp3_hit"]    = True
                pos["trail_active"] = True
                pos["trail_peak"] = price
                pos["trail_stop"] = round(price * (1 - TRAIL_PCT / 100), 2)
                changed = True
                await telegram(
                    f"🚀 <b>TP3 REACHED — {symbol}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🕐 {now}\n"
                    f"💰 Price: <b>${price:,.2f}</b> (+{TP3_PCT}%)\n"
                    f"🔄 Trailing stop: ${pos['trail_stop']:,.2f} (-{TRAIL_PCT}%)\n"
                    f"📦 Holding remainder — letting it run!\n"
                    f"⚠️ SIMULATION"
                )

            # ── Trailing stop ─────────────────────────────
            if pos.get("trail_active") and pos["qty_remaining"] > 0:
                if price > pos.get("trail_peak", 0):
                    pos["trail_peak"] = price
                    pos["trail_stop"] = round(price * (1 - TRAIL_PCT / 100), 2)
                    changed = True
                if price <= pos["trail_stop"]:
                    pnl, pnl_pct = partial_close(port, pos, pos["qty_remaining"], price, "TP3+Trail")
                    to_remove.append(pos)
                    changed = True
                    await telegram(
                        f"🏁 <b>TRAILING EXIT — {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Exit: <b>${price:,.2f}</b>\n"
                        f"📈 Peak: ${pos['trail_peak']:,.2f}\n"
                        f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                        f"🏦 Balance: ${port['balance']:.2f}\n"
                        f"⚠️ SIMULATION"
                    )

        for pos in to_remove:
            if pos in port["positions"]:
                port["positions"].remove(pos)

        if changed:
            save(port)

@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(price_monitor())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# ─── Webhook ──────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    data   = await request.json()
    if data.get("secret") != SECRET:
        return {"status": "unauthorized"}

    signal = data.get("signal", "").lower()
    price  = float(data.get("price", 0))
    symbol = data.get("symbol", "BTCUSDT")
    score  = data.get("score", "?")
    now    = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    port   = load()

    score_label = "Valid"
    if str(score) == "5":
        score_label = "Medium ⚡"
    elif str(score) in ("6", "7"):
        score_label = "Strong 🔥"

    # ── LONG entry ───────────────────────────────
    if signal == "buy":
        if len(port["positions"]) >= MAX_POSITIONS:
            await telegram(f"⚠️ Max {MAX_POSITIONS} positions open — BUY skipped ({symbol})")
            return {"status": "skipped"}

        amount = port["balance"] * TRADE_SIZE
        if amount < 10:
            await telegram(f"⚠️ Insufficient balance — BUY skipped ({symbol})")
            return {"status": "skipped"}

        qty_total = amount / price
        tp1_qty   = round(qty_total * 0.34, 6)
        tp2_qty   = round((qty_total - tp1_qty) * 0.50, 6)
        tp3_qty   = round(qty_total - tp1_qty - tp2_qty, 6)

        sl  = round(price * (1 - SL_PCT  / 100), 2)
        tp1 = round(price * (1 + TP1_PCT / 100), 2)
        tp2 = round(price * (1 + TP2_PCT / 100), 2)
        tp3 = round(price * (1 + TP3_PCT / 100), 2)

        pos = {
            "symbol":        symbol,
            "side":          "LONG",
            "entry_price":   price,
            "qty_total":     round(qty_total, 6),
            "qty_remaining": round(qty_total, 6),
            "tp1_qty":       tp1_qty,
            "tp2_qty":       tp2_qty,
            "tp3_qty":       tp3_qty,
            "amount":        amount,
            "time":          now,
            "sl_price":      sl,
            "tp1_price":     tp1,
            "tp2_price":     tp2,
            "tp3_price":     tp3,
            "tp1_hit":       False,
            "tp2_hit":       False,
            "tp3_hit":       False,
            "trail_active":  False,
            "trail_peak":    None,
            "trail_stop":    None,
            "score":         score
        }
        port["balance"] -= amount
        port["positions"].append(pos)
        save(port)

        await telegram(
            f"🟢 <b>LONG ENTRY — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n"
            f"💰 Entry: <b>${price:,.2f}</b>\n"
            f"📊 Score: {score}/7 — {score_label}\n"
            f"📦 Size: ${amount:.2f} ({qty_total:.6f} {symbol[:3]})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛑 SL:  ${sl:,.2f}  (-{SL_PCT}%)\n"
            f"🎯 TP1: ${tp1:,.2f} (+{TP1_PCT}%)  → 34%\n"
            f"🎯 TP2: ${tp2:,.2f} (+{TP2_PCT}%)  → 50% rem\n"
            f"🎯 TP3: ${tp3:,.2f} (+{TP3_PCT}%) + trail\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Free Balance: ${port['balance']:.2f}\n"
            f"📂 Open: {len(port['positions'])}/{MAX_POSITIONS}\n"
            f"⚠️ SIMULATION — No real money used"
        )

    # ── LONG exit (sell signal) ───────────────────
    elif signal == "sell":
        longs = [p for p in port["positions"] if p["symbol"] == symbol and p["side"] == "LONG"]
        if not longs:
            await telegram(f"⚠️ SELL signal — no LONG open for {symbol}")
            return {"status": "no_position"}

        for pos in longs:
            qty = pos["qty_remaining"]
            if qty <= 0:
                port["positions"].remove(pos)
                continue
            pnl, pnl_pct = partial_close(port, pos, qty, price, "SIGNAL EXIT")
            port["positions"].remove(pos)
            total = port["wins"] + port["losses"]
            wr    = (port["wins"] / total * 100) if total else 0
            emoji = "✅" if pnl > 0 else "❌"
            await telegram(
                f"📤 <b>SELL SIGNAL EXIT — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now}\n"
                f"💰 Exit: <b>${price:,.2f}</b>\n"
                f"{emoji} P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                f"🏦 Balance: ${port['balance']:.2f}\n"
                f"📊 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
                f"⚠️ SIMULATION"
            )
        save(port)

    return {"status": "ok"}

# ─── Reset ────────────────────────────────────
@app.post("/reset-trades")
async def reset_trades():
    save({
        "balance":   STARTING_BALANCE,
        "positions": [],
        "trades":    [],
        "total_pnl": 0.0,
        "wins":      0,
        "losses":    0
    })
    return {"status": "reset ok"}

# ─── Seed portfolio (temporary admin) ────────
@app.post("/admin/seed")
async def seed(request: Request):
    data = await request.json()
    if data.get("secret") != SECRET:
        return {"status": "unauthorized"}
    save(data["portfolio"])
    return {"status": "seeded"}

# ─── Dashboard ────────────────────────────────
@app.get("/status", response_class=HTMLResponse)
async def status():
    port      = load()
    positions = port.get("positions", [])
    trades    = port.get("trades", [])

    live_prices = {}
    for pos in positions:
        sym = pos["symbol"]
        if sym not in live_prices:
            p = await get_price(sym)
            if p:
                live_prices[sym] = p

    total  = port["wins"] + port["losses"]
    wr     = (port["wins"] / total * 100) if total else 0
    unreal = sum(
        (live_prices.get(p["symbol"], p["entry_price"]) - p["entry_price"]) * p["qty_remaining"]
        for p in positions
    )

    cards_html = ""
    for pos in positions:
        price = live_prices.get(pos["symbol"], pos["entry_price"])
        upnl  = (price - pos["entry_price"]) * pos["qty_remaining"]
        upct  = (upnl / pos["amount"]) * 100 if pos["amount"] else 0
        color = "#00c853" if upnl >= 0 else "#f44336"
        tp1c  = "✅" if pos.get("tp1_hit") else "⬜"
        tp2c  = "✅" if pos.get("tp2_hit") else "⬜"
        tp3c  = "✅" if pos.get("tp3_hit") else "⬜"
        trail = f"<br>🔄 Trail stop: ${pos['trail_stop']:,.0f}" if pos.get("trail_active") else ""
        s = str(pos.get("score", ""))
        badge = " 🔥" if s in ("6","7") else (" ⚡" if s == "5" else "")
        cards_html += f"""
        <div class="card pos-card">
          <div class="pos-header">{pos['side']} — {pos['symbol']}{badge}</div>
          <div>Score: {pos.get('score','?')}/7</div>
          <div>Entry: <b>${pos['entry_price']:,.2f}</b> → Live: <b>${price:,.2f}</b></div>
          <div>Size: ${pos['amount']:.2f}</div>
          <div>🛑 SL: <span style="color:#f44336">${pos['sl_price']:,.2f}</span></div>
          <div>{tp1c} TP1: ${pos['tp1_price']:,.2f}</div>
          <div>{tp2c} TP2: ${pos['tp2_price']:,.2f}</div>
          <div>{tp3c} TP3: ${pos['tp3_price']:,.2f}{trail}</div>
          <div style="color:{color};font-weight:bold">Unrealized: ${upnl:+.2f} ({upct:+.2f}%)</div>
          <div style="opacity:0.5;font-size:11px">{pos['time']}</div>
        </div>"""

    for _ in range(MAX_POSITIONS - len(positions)):
        cards_html += '<div class="card empty-card">Empty Slot</div>'

    rows = ""
    for t in reversed(trades[-10:]):
        color = "#00c853" if t["pnl"] > 0 else "#f44336"
        rows += f"""<tr>
          <td>{t.get('side','LONG')} {t.get('symbol','BTCUSDT')}</td>
          <td>${t['entry']:,.2f}</td>
          <td>${t['exit']:,.2f}</td>
          <td style="color:{color}">${t['pnl']:+.2f} ({t['pnl_pct']:+.2f}%)</td>
          <td>{t.get('reason','—')}</td>
        </tr>"""

    unreal_color = "#00c853" if unreal >= 0 else "#f44336"
    pnl_color    = "green" if port["total_pnl"] >= 0 else ""

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>BTC Bot V9 Dashboard</title>
  <style>
    * {{ box-sizing:border-box }}
    body {{ background:#0d1117; color:#e6edf3; font-family:monospace; padding:24px; margin:0 }}
    h1   {{ color:#58a6ff; margin-bottom:20px }}
    h2   {{ color:#8b949e; margin:20px 0 10px }}
    .stats {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:24px }}
    .stat  {{ background:#161b22; border:1px solid #30363d; border-radius:8px; padding:16px 24px; min-width:120px }}
    .stat-label {{ color:#8b949e; font-size:12px; margin-bottom:4px }}
    .stat-value {{ color:#58a6ff; font-size:22px; font-weight:bold }}
    .stat-value.green {{ color:#00c853 }}
    .cards {{ display:flex; gap:16px; flex-wrap:wrap }}
    .card  {{ background:#161b22; border:1px solid #30363d; border-radius:8px;
              padding:16px; width:260px; font-size:13px; line-height:1.8 }}
    .pos-card   {{ border-color:#58a6ff }}
    .pos-header {{ color:#58a6ff; font-weight:bold; font-size:14px; margin-bottom:6px }}
    .empty-card {{ color:#333; display:flex; align-items:center; justify-content:center;
                   min-height:120px }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; margin-top:8px }}
    th    {{ color:#8b949e; text-align:left; padding:8px; border-bottom:1px solid #30363d }}
    td    {{ padding:8px; border-bottom:1px solid #21262d }}
  </style>
</head>
<body>
  <h1>BTC Bot V9 Dashboard</h1>
  <div class="stats">
    <div class="stat">
      <div class="stat-label">Balance</div>
      <div class="stat-value">${port['balance']:.2f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Unrealized P&amp;L</div>
      <div class="stat-value" style="color:{unreal_color}">${unreal:+.2f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Total P&amp;L</div>
      <div class="stat-value {pnl_color}">${port['total_pnl']:+.2f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Trades</div>
      <div class="stat-value">{total}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Win Rate</div>
      <div class="stat-value">{wr:.1f}%</div>
    </div>
    <div class="stat">
      <div class="stat-label">Open</div>
      <div class="stat-value">{len(positions)}/{MAX_POSITIONS}</div>
    </div>
  </div>

  <h2>Open Positions</h2>
  <div class="cards">{cards_html}</div>

  <h2>Last 10 Trades</h2>
  <table>
    <tr><th>Type</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th></tr>
    {rows if rows else '<tr><td colspan="5" style="color:#333;text-align:center">No trades yet</td></tr>'}
  </table>
</body>
</html>"""
    return html

@app.get("/")
async def root():
    return {"status": "BTC Bot V9 running ✅"}
