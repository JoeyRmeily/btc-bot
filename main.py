from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
from datetime import datetime
import httpx, json, os, asyncio

TELEGRAM_TOKEN   = "8635147020:AAGxhQLIJQfN7FUWGr-3gcQam7uORywuesQ"
TELEGRAM_CHAT_ID = "6623057612"
SECRET           = "btcbot2024"

PORTFOLIO_FILE   = "portfolio.json"
STARTING_BALANCE = 1000.0
SL_PCT           = 1.5   # matches Pine Script
TP_PCT           = 3.0   # matches Pine Script

# ─────────────────────────────────────────────
# Portfolio helpers
# ─────────────────────────────────────────────
def load():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE) as f:
            return json.load(f)
    return {"balance": STARTING_BALANCE, "position": None,
            "trades": [], "total_pnl": 0.0, "wins": 0, "losses": 0}

def save(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────
async def telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as c:
        await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID,
                                "text": msg, "parse_mode": "HTML"})

# ─────────────────────────────────────────────
# Live price (Binance public — no API key needed)
# ─────────────────────────────────────────────
async def get_btc_price():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT")
            return float(r.json()["price"])
    except Exception:
        return None

# ─────────────────────────────────────────────
# Close trade (signal exit / SL / TP)
# ─────────────────────────────────────────────
async def close_trade(port, price, reason, symbol="BTCUSDT"):
    pos     = port["position"]
    value   = pos["quantity"] * price
    pnl     = value - pos["amount"]
    pnl_pct = (pnl / pos["amount"]) * 100
    now     = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    port["balance"]   += value
    port["total_pnl"] += pnl
    port["wins"]      += 1 if pnl > 0 else 0
    port["losses"]    += 1 if pnl <= 0 else 0
    port["trades"].append({
        "entry":      pos["entry_price"],
        "exit":       price,
        "pnl":        round(pnl, 2),
        "pnl_pct":    round(pnl_pct, 2),
        "entry_time": pos["time"],
        "exit_time":  now,
        "reason":     reason
    })
    port["position"] = None
    save(port)

    total = port["wins"] + port["losses"]
    wr    = (port["wins"] / total * 100) if total > 0 else 0
    emoji = "✅" if pnl > 0 else "❌"

    if reason == "TP":
        head = f"🎯 <b>TAKE PROFIT HIT — {symbol}</b>"
    elif reason == "SL":
        head = f"🛑 <b>STOP LOSS HIT — {symbol}</b>"
    else:
        head = f"📤 <b>SELL SIGNAL EXIT — {symbol}</b>"

    await telegram(
        f"{head}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}\n"
        f"💰 Exit: <b>${price:,.2f}</b>\n"
        f"📥 Entry: ${pos['entry_price']:,.2f}\n"
        f"{emoji} P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Balance: ${port['balance']:.2f}\n"
        f"📊 Total P&L: ${port['total_pnl']:.2f}\n"
        f"🎯 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ SIMULATION — No real money used"
    )

# ─────────────────────────────────────────────
# Background SL/TP monitor (checks every 30 s)
# ─────────────────────────────────────────────
async def price_monitor():
    while True:
        await asyncio.sleep(30)
        port = load()
        if port["position"] is None:
            continue

        price = await get_btc_price()
        if price is None:
            continue

        pos      = port["position"]
        sl_price = pos.get("sl_price")
        tp_price = pos.get("tp_price")

        if tp_price and price >= tp_price:
            await close_trade(port, price, "TP")
        elif sl_price and price <= sl_price:
            await close_trade(port, price, "SL")

@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(price_monitor())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

# ─────────────────────────────────────────────
# Webhook — receives TradingView alerts
# ─────────────────────────────────────────────
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

    if signal == "BUY" and port["position"] is None:
        amount   = port["balance"] * 0.95
        qty      = amount / price
        sl_price = round(price * (1 - SL_PCT / 100), 2)
        tp_price = round(price * (1 + TP_PCT / 100), 2)

        port["position"] = {
            "entry_price": price,
            "quantity":    qty,
            "amount":      amount,
            "time":        now,
            "sl_price":    sl_price,
            "tp_price":    tp_price
        }
        port["balance"] -= amount
        save(port)

        await telegram(
            f"🟢 <b>BUY SIGNAL — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n"
            f"💰 Entry: <b>${price:,.2f}</b>\n"
            f"📦 Qty: {qty:.6f} BTC\n"
            f"💵 Invested: ${amount:.2f}\n"
            f"🛑 Stop Loss:   ${sl_price:,.2f}  (-{SL_PCT}%)\n"
            f"🎯 Take Profit: ${tp_price:,.2f}  (+{TP_PCT}%)\n"
            f"🏦 Free Balance: ${port['balance']:.2f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ SIMULATION — No real money used"
        )

    elif signal == "SELL" and port["position"] is not None:
        await close_trade(port, price, "SIGNAL", symbol)

    else:
        reason = "Already in a trade" if signal == "BUY" else "No open trade to close"
        await telegram(
            f"⚠️ <b>SIGNAL SKIPPED — {symbol}</b>\n"
            f"Signal: {signal} | Price: ${price:,.2f}\n"
            f"Reason: {reason}"
        )

    return {"status": "ok"}

# ─────────────────────────────────────────────
# Status — includes live unrealized P&L
# ─────────────────────────────────────────────
@app.get("/status")
async def status():
    port = load()
    if port["position"]:
        price = await get_btc_price()
        if price:
            pos = port["position"]
            unreal_pnl = (pos["quantity"] * price) - pos["amount"]
            port["current_price"]   = price
            port["unrealized_pnl"]  = round(unreal_pnl, 2)
            port["unrealized_pct"]  = round((unreal_pnl / pos["amount"]) * 100, 2)
    return port

@app.get("/")
async def root():
    return {"status": "BTC Signal Bot running ✅"}
