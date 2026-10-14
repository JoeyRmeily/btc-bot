from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
from datetime import datetime
import httpx, json, os, asyncio, websockets, hmac, hashlib, time

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6623057612")
SECRET           = "btcbot2024"
PORTFOLIO_FILE   = "portfolio.json"
STARTING_BALANCE = 1000.0

SL_PCT     = 1.5
TP1_PCT    = 2.0
TP2_PCT    = 3.5
TP3_PCT    = 5.0
TRAIL_PCT  = 1.5
TRADE_SIZE = 0.20
MAX_POSITIONS = 12

# ─── Binance Futures (live trading) ───────────
LIVE_TRADING       = os.environ.get("LIVE_TRADING", "false").lower() == "true"
BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY", "")
FUTURES_BASE       = "https://fapi.binance.com"
LEVERAGE           = 2
LIVE_SIZE_USD      = float(os.environ.get("LIVE_SIZE_USD", "100"))  # $ per live trade

# ─── Binance Futures helpers ──────────────────
def _sign(params: dict) -> dict:
    params["timestamp"] = int(time.time() * 1000)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    params["signature"] = hmac.new(
        BINANCE_SECRET_KEY.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    return params

async def futures_order(symbol: str, side: str, qty: float, reduce_only: bool = False):
    p = {
        "symbol":   symbol,
        "side":     side,
        "type":     "MARKET",
        "quantity": round(qty, 3),
    }
    if reduce_only:
        p["reduceOnly"] = "true"
    params = _sign(p)
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{FUTURES_BASE}/fapi/v1/order", params=params,
                             headers={"X-MBX-APIKEY": BINANCE_API_KEY})
            result = r.json()
            if result.get("code"):
                await telegram(f"⚠️ Binance order error: {result.get('msg')} (code {result.get('code')})")
            return result
    except Exception as e:
        await telegram(f"⚠️ Binance request failed: {e}")
        return {}

async def get_live_balance():
    try:
        params = _sign({})
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{FUTURES_BASE}/fapi/v2/balance", params=params,
                            headers={"X-MBX-APIKEY": BINANCE_API_KEY})
            for a in r.json():
                if a["asset"] == "USDT":
                    return float(a["balance"]), float(a["availableBalance"])
    except Exception:
        pass
    return None, None

async def init_futures():
    for symbol in ["BTCUSDT"]:
        try:
            p = _sign({"symbol": symbol, "marginType": "ISOLATED"})
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"{FUTURES_BASE}/fapi/v1/marginType", params=p,
                             headers={"X-MBX-APIKEY": BINANCE_API_KEY})
            p = _sign({"symbol": symbol, "leverage": LEVERAGE})
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(f"{FUTURES_BASE}/fapi/v1/leverage", params=p,
                                 headers={"X-MBX-APIKEY": BINANCE_API_KEY})
                await telegram(f"✅ Binance LIVE MODE ON — {symbol} {LEVERAGE}x isolated\nSize per trade: ${LIVE_SIZE_USD}")
        except Exception as e:
            await telegram(f"⚠️ init_futures error ({symbol}): {e}")

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
        if "live_trades" not in p:
            p["live_trades"] = []
        return p
    return {
        "balance":    STARTING_BALANCE,
        "positions":  [],
        "trades":     [],
        "total_pnl":  0.0,
        "wins":       0,
        "losses":     0,
        "live_trades": []
    }

def save(p):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(p, f, indent=2)

# ─── Telegram ─────────────────────────────────
async def telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json={"chat_id": TELEGRAM_CHAT_ID,
                                        "text": msg, "parse_mode": "HTML"})
        if not r.json().get("ok"):
            print(f"TELEGRAM FAILED: {r.json().get('description')} | {msg[:80]!r}", flush=True)
    except Exception as e:
        print(f"TELEGRAM ERROR: {e} | {msg[:80]!r}", flush=True)

# ─── Live price ───────────────────────────────
price_cache: dict[str, float] = {}

async def get_price(symbol="BTCUSDT"):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
            return float(r.json()["price"])
    except Exception:
        return None

async def ws_price_feed():
    """Stream real-time prices from Binance WebSocket into price_cache."""
    url = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20) as ws:
                async for raw in ws:
                    data = json.loads(raw)
                    symbol = data.get("s")
                    price  = data.get("p")
                    if symbol and price:
                        price_cache[symbol] = float(price)
        except Exception:
            await asyncio.sleep(5)  # reconnect after 5s on any error

# ─── Partial close helper ─────────────────────
def partial_close(port, pos, qty_to_close, price, reason):
    entry = pos["entry_price"]
    if pos.get("side") == "SHORT":
        pnl = (entry - price) * qty_to_close
        port["balance"] += qty_to_close * entry + pnl
    else:
        pnl = (price - entry) * qty_to_close
        port["balance"] += qty_to_close * price
    pnl_pct = (pnl / (qty_to_close * entry)) * 100

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

def live_record(port, pos, live_qty_closed, price, reason):
    if not LIVE_TRADING or live_qty_closed < 0.001:
        return
    entry = pos["entry_price"]
    if pos.get("side") == "SHORT":
        pnl = (entry - price) * live_qty_closed
    else:
        pnl = (price - entry) * live_qty_closed
    pnl_pct = (pnl / (live_qty_closed * entry)) * 100
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    port["live_trades"].append({
        "symbol":     pos["symbol"],
        "side":       pos["side"],
        "entry":      entry,
        "exit":       round(price, 2),
        "qty":        round(live_qty_closed, 3),
        "pnl":        round(pnl, 2),
        "pnl_pct":    round(pnl_pct, 2),
        "entry_time": pos["time"],
        "exit_time":  now,
        "reason":     reason
    })

# ─── Background price monitor ─────────────────
async def price_monitor():
    while True:
        await asyncio.sleep(1)
        port = load()
        if not port["positions"]:
            continue

        changed   = False
        to_remove = []

        for pos in port["positions"]:
            symbol = pos.get("symbol", "BTCUSDT")
            price  = price_cache.get(symbol) or await get_price(symbol)
            if price is None:
                continue

            now   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            entry = pos["entry_price"]

            side = pos.get("side", "LONG")

            mode_tag = "🔴 LIVE" if LIVE_TRADING else "⚠️ SIMULATION"

            if side == "SHORT":
                # ── SHORT Stop Loss (price goes UP) ──────────
                if price >= pos["sl_price"] and pos["qty_remaining"] > 0:
                    lq = pos.get("live_qty", 0) - pos.get("live_tp1_qty", 0) * pos.get("tp1_hit", False) - pos.get("live_tp2_qty", 0) * pos.get("tp2_hit", False)
                    lq = max(round(lq, 3), 0)
                    if LIVE_TRADING and lq >= 0.001:
                        await futures_order(symbol, "BUY", lq, reduce_only=True)
                    pnl, pnl_pct = partial_close(port, pos, pos["qty_remaining"], price, "SL")
                    live_record(port, pos, lq, price, "SL")
                    to_remove.append(pos)
                    changed = True
                    total = port["wins"] + port["losses"]
                    wr = (port["wins"] / total * 100) if total else 0
                    await telegram(
                        f"🛑 <b>STOP LOSS — SHORT {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Exit: <b>${price:,.2f}</b>\n"
                        f"📥 Entry: ${entry:,.2f}\n"
                        f"❌ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🏦 Balance: ${port['balance']:.2f}\n"
                        f"📊 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
                        f"{mode_tag}"
                    )
                    continue

                # ── SHORT TP1 (price goes DOWN) ───────────────
                if not pos.get("tp1_hit") and price <= pos["tp1_price"]:
                    if LIVE_TRADING and pos.get("live_tp1_qty", 0) >= 0.001:
                        await futures_order(symbol, "BUY", pos["live_tp1_qty"], reduce_only=True)
                    pnl, pnl_pct = partial_close(port, pos, pos["tp1_qty"], price, "TP1")
                    live_record(port, pos, pos.get("live_tp1_qty", 0), price, "TP1")
                    pos["tp1_hit"]  = True
                    pos["sl_price"] = entry
                    changed = True
                    await telegram(
                        f"🎯 <b>TP1 HIT — SHORT {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Price: <b>${price:,.2f}</b> (-{TP1_PCT}%)\n"
                        f"📦 Closed 34% of position\n"
                        f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🛡 SL moved to break-even: ${entry:,.2f}\n"
                        f"⏳ Watching TP2 at ${pos['tp2_price']:,.2f}\n"
                        f"🏦 Balance: ${port['balance']:.2f}\n"
                        f"{mode_tag}"
                    )

                # ── SHORT TP2 ─────────────────────────────────
                if pos.get("tp1_hit") and not pos.get("tp2_hit") and price <= pos["tp2_price"]:
                    if LIVE_TRADING and pos.get("live_tp2_qty", 0) >= 0.001:
                        await futures_order(symbol, "BUY", pos["live_tp2_qty"], reduce_only=True)
                    pnl, pnl_pct = partial_close(port, pos, pos["tp2_qty"], price, "TP2")
                    live_record(port, pos, pos.get("live_tp2_qty", 0), price, "TP2")
                    pos["tp2_hit"]  = True
                    pos["sl_price"] = pos["tp1_price"]
                    changed = True
                    await telegram(
                        f"🎯🎯 <b>TP2 HIT — SHORT {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Price: <b>${price:,.2f}</b> (-{TP2_PCT}%)\n"
                        f"📦 Closed 50% of remaining\n"
                        f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🛡 SL moved to TP1: ${pos['sl_price']:,.2f} (locked profit)\n"
                        f"⏳ Trailing stop activates at TP3 ${pos['tp3_price']:,.2f}\n"
                        f"🏦 Balance: ${port['balance']:.2f}\n"
                        f"{mode_tag}"
                    )

                # ── SHORT TP3 — activate trailing ─────────────
                if pos.get("tp2_hit") and not pos.get("tp3_hit") and price <= pos["tp3_price"]:
                    pos["tp3_hit"]      = True
                    pos["trail_active"] = True
                    pos["trail_low"]    = price
                    pos["trail_stop"]   = round(price * (1 + TRAIL_PCT / 100), 2)
                    changed = True
                    await telegram(
                        f"🚀 <b>TP3 REACHED — SHORT {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Price: <b>${price:,.2f}</b> (-{TP3_PCT}%)\n"
                        f"🔄 Trailing stop: ${pos['trail_stop']:,.2f} (+{TRAIL_PCT}%)\n"
                        f"📦 Holding remainder — letting it run!\n"
                        f"{mode_tag}"
                    )

                # ── SHORT Trailing stop ───────────────────────
                if pos.get("trail_active") and pos["qty_remaining"] > 0:
                    if price < pos.get("trail_low", float("inf")):
                        pos["trail_low"]  = price
                        pos["trail_stop"] = round(price * (1 + TRAIL_PCT / 100), 2)
                        changed = True
                    if price >= pos["trail_stop"]:
                        if LIVE_TRADING and pos.get("live_tp3_qty", 0) >= 0.001:
                            await futures_order(symbol, "BUY", pos["live_tp3_qty"], reduce_only=True)
                        pnl, pnl_pct = partial_close(port, pos, pos["qty_remaining"], price, "TP3+Trail")
                        live_record(port, pos, pos.get("live_tp3_qty", 0), price, "TP3+Trail")
                        to_remove.append(pos)
                        changed = True
                        await telegram(
                            f"🏁 <b>TRAILING EXIT — SHORT {symbol}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━\n"
                            f"🕐 {now}\n"
                            f"💰 Exit: <b>${price:,.2f}</b>\n"
                            f"📉 Low: ${pos['trail_low']:,.2f}\n"
                            f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                            f"🏦 Balance: ${port['balance']:.2f}\n"
                            f"{mode_tag}"
                        )

            else:
                # ── LONG Stop Loss ────────────────────────────
                if price <= pos["sl_price"] and pos["qty_remaining"] > 0:
                    lq = pos.get("live_qty", 0) - pos.get("live_tp1_qty", 0) * pos.get("tp1_hit", False) - pos.get("live_tp2_qty", 0) * pos.get("tp2_hit", False)
                    lq = max(round(lq, 3), 0)
                    if LIVE_TRADING and lq >= 0.001:
                        await futures_order(symbol, "SELL", lq, reduce_only=True)
                    pnl, pnl_pct = partial_close(port, pos, pos["qty_remaining"], price, "SL")
                    live_record(port, pos, lq, price, "SL")
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
                        f"{mode_tag}"
                    )
                    continue

                # ── LONG TP1 ──────────────────────────────────
                if not pos.get("tp1_hit") and price >= pos["tp1_price"]:
                    if LIVE_TRADING and pos.get("live_tp1_qty", 0) >= 0.001:
                        await futures_order(symbol, "SELL", pos["live_tp1_qty"], reduce_only=True)
                    pnl, pnl_pct = partial_close(port, pos, pos["tp1_qty"], price, "TP1")
                    live_record(port, pos, pos.get("live_tp1_qty", 0), price, "TP1")
                    pos["tp1_hit"]  = True
                    pos["sl_price"] = entry
                    changed = True
                    await telegram(
                        f"🎯 <b>TP1 HIT — {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Price: <b>${price:,.2f}</b> (+{TP1_PCT}%)\n"
                        f"📦 Closed 34% of position\n"
                        f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🛡 SL moved to break-even: ${entry:,.2f}\n"
                        f"⏳ Watching TP2 at ${pos['tp2_price']:,.2f}\n"
                        f"🏦 Balance: ${port['balance']:.2f}\n"
                        f"{mode_tag}"
                    )

                # ── LONG TP2 ──────────────────────────────────
                if pos.get("tp1_hit") and not pos.get("tp2_hit") and price >= pos["tp2_price"]:
                    if LIVE_TRADING and pos.get("live_tp2_qty", 0) >= 0.001:
                        await futures_order(symbol, "SELL", pos["live_tp2_qty"], reduce_only=True)
                    pnl, pnl_pct = partial_close(port, pos, pos["tp2_qty"], price, "TP2")
                    live_record(port, pos, pos.get("live_tp2_qty", 0), price, "TP2")
                    pos["tp2_hit"]  = True
                    pos["sl_price"] = pos["tp1_price"]
                    changed = True
                    await telegram(
                        f"🎯🎯 <b>TP2 HIT — {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Price: <b>${price:,.2f}</b> (+{TP2_PCT}%)\n"
                        f"📦 Closed 50% of remaining\n"
                        f"✅ P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🛡 SL moved to TP1: ${pos['sl_price']:,.2f} (locked profit)\n"
                        f"⏳ Trailing stop activates at TP3 ${pos['tp3_price']:,.2f}\n"
                        f"🏦 Balance: ${port['balance']:.2f}\n"
                        f"{mode_tag}"
                    )

                # ── LONG TP3 — activate trailing ──────────────
                if pos.get("tp2_hit") and not pos.get("tp3_hit") and price >= pos["tp3_price"]:
                    pos["tp3_hit"]      = True
                    pos["trail_active"] = True
                    pos["trail_peak"]   = price
                    pos["trail_stop"]   = round(price * (1 - TRAIL_PCT / 100), 2)
                    changed = True
                    await telegram(
                        f"🚀 <b>TP3 REACHED — {symbol}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🕐 {now}\n"
                        f"💰 Price: <b>${price:,.2f}</b> (+{TP3_PCT}%)\n"
                        f"🔄 Trailing stop: ${pos['trail_stop']:,.2f} (-{TRAIL_PCT}%)\n"
                        f"📦 Holding remainder — letting it run!\n"
                        f"{mode_tag}"
                    )

                # ── LONG Trailing stop ─────────────────────────
                if pos.get("trail_active") and pos["qty_remaining"] > 0:
                    if price > pos.get("trail_peak", 0):
                        pos["trail_peak"] = price
                        pos["trail_stop"] = round(price * (1 - TRAIL_PCT / 100), 2)
                        changed = True
                    if price <= pos["trail_stop"]:
                        if LIVE_TRADING and pos.get("live_tp3_qty", 0) >= 0.001:
                            await futures_order(symbol, "SELL", pos["live_tp3_qty"], reduce_only=True)
                        pnl, pnl_pct = partial_close(port, pos, pos["qty_remaining"], price, "TP3+Trail")
                        live_record(port, pos, pos.get("live_tp3_qty", 0), price, "TP3+Trail")
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
                            f"{mode_tag}"
                        )

        for pos in to_remove:
            if pos in port["positions"]:
                port["positions"].remove(pos)

        if changed:
            save(port)

# ─── Agent signal generator ───────────────────
# AGENT_MODE: "off" = disabled, "shadow" = Telegram analysis only, "live" = trades
AGENT_MODE      = os.environ.get("AGENT_MODE", "shadow").lower()
AGENT_SYMBOLS   = ["BTCUSDT"]
MIN_AGENT_SCORE = int(os.environ.get("MIN_AGENT_SCORE", "2"))
LAST_HALVING    = datetime(2024, 4, 20)
SPOT_BASE       = "https://api.binance.com"

agent_state: dict = {"last_bar": {}, "last_analysis": {}}
_macro_cache: dict = {}

async def fetch_klines(symbol: str, interval: str, limit: int) -> list:
    """Closed candles only (Binance's last kline is still forming)."""
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{SPOT_BASE}/api/v3/klines",
                        params={"symbol": symbol, "interval": interval, "limit": limit + 1})
        rows = r.json()[:-1]
    return [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
             "c": float(k[4]), "v": float(k[5])} for k in rows]

async def cached(key: str, ttl: int, fetch):
    hit = _macro_cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = await fetch()
    _macro_cache[key] = (time.time(), value)
    return value

# ── Indicator math (matches Pine: SMA-seeded EMA, Wilder RSI/ATR) ──
def ema(values: list, period: int) -> list:
    if len(values) < period:
        return [values[-1]] * len(values)
    out = [None] * (period - 1) + [sum(values[:period]) / period]
    k = 2 / (period + 1)
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def sma(values: list, period: int) -> float:
    return sum(values[-period:]) / period

def rma(values: list, period: int) -> list:
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append((out[-1] * (period - 1) + v) / period)
    return out

def rsi(closes: list, period: int = 14) -> float:
    diffs  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gain   = rma([max(d, 0) for d in diffs], period)[-1]
    loss   = rma([max(-d, 0) for d in diffs], period)[-1]
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

def session_vwap(candles: list) -> float:
    """VWAP anchored at 00:00 UTC of the last candle's day, like Pine's ta.vwap."""
    day_start = candles[-1]["t"] - candles[-1]["t"] % 86_400_000
    day = [k for k in candles if k["t"] >= day_start]
    vol = sum(k["v"] for k in day)
    return sum((k["h"] + k["l"] + k["c"]) / 3 * k["v"] for k in day) / vol if vol else candles[-1]["c"]

def atr(candles: list, period: int = 14) -> float:
    trs = [max(k["h"] - k["l"], abs(k["h"] - p["c"]), abs(k["l"] - p["c"]))
           for p, k in zip(candles, candles[1:])]
    return rma(trs, period)[-1]

def vote(v: int, reason: str, weight: int = 1, required: bool = False) -> dict:
    return {"vote": v, "weight": weight, "required": required, "reason": reason}

# ── Required agents: the V9 rules. All must vote +1 or no trade. ──
def agent_ema_cross(c15: list) -> tuple:
    closes = [k["c"] for k in c15]
    e9, e21 = ema(closes, 9), ema(closes, 21)
    if e9[-1] > e21[-1] and e9[-2] <= e21[-2]:
        return "LONG", vote(1, "EMA9 crossed above EMA21", required=True)
    if e9[-1] < e21[-1] and e9[-2] >= e21[-2]:
        return "SHORT", vote(1, "EMA9 crossed below EMA21", required=True)
    return None, vote(0, "No crossover", required=True)

def agent_mtf(c1h: list, c4h: list, d: str) -> dict:
    h1c, h4c = [k["c"] for k in c1h], [k["c"] for k in c4h]
    h1_e9, h1_e21 = ema(h1c, 9)[-1], ema(h1c, 21)[-1]
    h4_e9, h4_e21, h4_e50 = ema(h4c, 9)[-1], ema(h4c, 21)[-1], ema(h4c, 50)[-1]
    h1_rsi, h4_rsi, h1_vwap = rsi(h1c), rsi(h4c), session_vwap(c1h)
    if d == "LONG":
        h1 = h1_e9 > h1_e21 and h1_rsi > 50 and h1c[-1] > h1_vwap
        h4 = h4_e9 > h4_e21 > h4_e50 and h4_rsi > 50
    else:
        h1 = h1_e9 < h1_e21 and h1_rsi < 50 and h1c[-1] < h1_vwap
        h4 = h4_e9 < h4_e21 < h4_e50 and h4_rsi < 50
    return vote(1 if h1 and h4 else -1,
                f"1H {'ok' if h1 else 'fail'} (RSI {h1_rsi:.0f}), 4H stack {'ok' if h4 else 'fail'} (RSI {h4_rsi:.0f})",
                required=True)

def agent_rsi(c15: list, d: str) -> dict:
    r = rsi([k["c"] for k in c15])
    ok = 45 <= r <= 70 if d == "LONG" else 30 <= r <= 55
    return vote(1 if ok else -1, f"RSI {r:.1f}", required=True)

def agent_vwap(c15: list, d: str) -> dict:
    v, c = session_vwap(c15), c15[-1]["c"]
    ok = c > v if d == "LONG" else c < v
    return vote(1 if ok else -1, f"Close {c:,.0f} vs VWAP {v:,.0f}", required=True)

def agent_volume(c15: list) -> dict:
    vols  = [k["v"] for k in c15]
    ratio = vols[-1] / sma(vols[:-1], 20)
    return vote(1 if ratio >= 1.5 else -1, f"Volume {ratio:.2f}x avg", required=True)

def agent_trend15(c15: list, d: str) -> dict:
    closes = [k["c"] for k in c15]
    e50 = ema(closes, 50)[-1]
    ok = closes[-1] > e50 if d == "LONG" else closes[-1] < e50
    return vote(1 if ok else -1, f"Close vs EMA50 {e50:,.0f}", required=True)

# ── Scoring agents: weighted votes, summed against MIN_AGENT_SCORE ──
def agent_macd(c15: list, d: str) -> dict:
    closes = [k["c"] for k in c15]
    e12, e26 = ema(closes, 12), ema(closes, 26)
    line = [a - b for a, b in zip(e12[25:], e26[25:])]
    hist = [m - s for m, s in zip(line[8:], ema(line, 9)[8:])]
    up = hist[-1] > 0 and hist[-1] > hist[-2]
    dn = hist[-1] < 0 and hist[-1] < hist[-2]
    v = (1 if up else -1 if hist[-1] < 0 else 0) if d == "LONG" else (1 if dn else -1 if hist[-1] > 0 else 0)
    return vote(v, f"Histogram {hist[-1]:+.1f}")

def agent_bollinger(c15: list, d: str) -> dict:
    closes = [k["c"] for k in c15][-20:]
    mid = sum(closes) / 20
    std = (sum((x - mid) ** 2 for x in closes) / 20) ** 0.5
    up, lo, c = mid + 2 * std, mid - 2 * std, closes[-1]
    if d == "LONG":
        v = -1 if c > up else 1 if c > mid else 0
    else:
        v = -1 if c < lo else 1 if c < mid else 0
    return vote(v, f"Band {lo:,.0f} / {mid:,.0f} / {up:,.0f}")

def agent_obv(c15: list, d: str) -> dict:
    obv = [0.0]
    for p, k in zip(c15, c15[1:]):
        obv.append(obv[-1] + (k["v"] if k["c"] > p["c"] else -k["v"] if k["c"] < p["c"] else 0))
    rising = obv[-1] > ema(obv, 20)[-1]
    v = 1 if rising == (d == "LONG") else -1
    return vote(v, f"OBV {'above' if rising else 'below'} its EMA20")

def agent_atr(c15: list) -> dict:
    pct = atr(c15) / c15[-1]["c"] * 100
    return vote(-1 if pct < 0.15 else 1 if pct >= 0.30 else 0, f"ATR {pct:.2f}% of price")

def agent_200dma(daily: list, d: str) -> dict:
    closes = [k["c"] for k in daily]
    dma = sma(closes, 200)
    mayer = closes[-1] / dma
    above = closes[-1] > dma
    return vote(1 if above == (d == "LONG") else -1,
                f"{'Above' if above else 'Below'} 200DMA {dma:,.0f} (Mayer {mayer:.2f})", weight=2)

def agent_pi_cycle(daily: list, d: str) -> dict:
    closes = [k["c"] for k in daily]
    if len(closes) < 350:
        return vote(0, "Not enough daily history")
    ratio = sma(closes, 111) / (2 * sma(closes, 350))
    if ratio >= 1:
        return vote(-1 if d == "LONG" else 1, f"Top signal active (ratio {ratio:.2f})")
    return vote(0, f"No top signal (ratio {ratio:.2f})")

def agent_cycle(d: str, now: datetime | None = None) -> dict:
    months = ((now or datetime.utcnow()) - LAST_HALVING).days / 30.44
    if months < 6:    phase, bias = "post-halving accumulation", 0
    elif months < 18: phase, bias = "bull run", 1
    elif months < 30: phase, bias = "late bull / top zone", 0
    elif months < 42: phase, bias = "bear market", -1
    else:             phase, bias = "bottoming", 0
    return vote(bias if d == "LONG" else -bias, f"{phase}, {months:.0f} months after halving")

def agent_fear_greed(fg: dict | None, d: str) -> dict:
    if not fg:
        return vote(0, "Unavailable")
    score = fg["score"]
    if d == "LONG" and score >= 80:
        return vote(-1, f"{score} Extreme Greed, overheated")
    if d == "SHORT" and score <= 20:
        return vote(-1, f"{score} Extreme Fear, bounce risk")
    return vote(0, f"{score} {fg['label']}")

async def fetch_fear_greed():
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            row = (await c.get("https://api.alternative.me/fng/?limit=1")).json()["data"][0]
        return {"score": int(row["value"]), "label": row["value_classification"]}
    except Exception:
        return None

# ── Orchestrator ──
def evaluate(symbol: str, c15: list, c1h: list, c4h: list, daily: list | None, fg: dict | None,
             force: str | None = None, now: datetime | None = None) -> dict:
    """Pure agent vote on closed candles. Shared by the live loop and backtest.py."""
    direction, trigger = agent_ema_cross(c15)
    if not direction and force:
        direction = force
    result = {"symbol": symbol, "bar": c15[-1]["t"], "close": c15[-1]["c"], "direction": direction,
              "agents": {"EMA Cross": trigger}, "fire": False, "gate": False, "score": 0}
    if not direction:
        return result
    result["agents"].update({
        "MTF 1H/4H":   agent_mtf(c1h, c4h, direction),
        "RSI":         agent_rsi(c15, direction),
        "VWAP":        agent_vwap(c15, direction),
        "Volume":      agent_volume(c15),
        "EMA50 trend": agent_trend15(c15, direction),
        "MACD":        agent_macd(c15, direction),
        "Bollinger":   agent_bollinger(c15, direction),
        "OBV":         agent_obv(c15, direction),
        "ATR":         agent_atr(c15),
        "200DMA":      agent_200dma(daily, direction),
        "Pi Cycle":    agent_pi_cycle(daily, direction),
        "Halving":     agent_cycle(direction, now),
        "Fear/Greed":  agent_fear_greed(fg, direction),
    })
    gate  = all(a["vote"] == 1 for a in result["agents"].values() if a["required"])
    score = sum(a["vote"] * a["weight"] for a in result["agents"].values() if not a["required"])
    result.update(gate=gate, score=score, fire=gate and score >= MIN_AGENT_SCORE)
    return result

async def analyze(symbol: str, force: str | None = None) -> dict:
    """force="LONG"/"SHORT" runs every agent without a crossover (inspection only)."""
    c15, c1h, c4h = await asyncio.gather(
        fetch_klines(symbol, "15m", 300),
        fetch_klines(symbol, "1h", 300),
        fetch_klines(symbol, "4h", 300),
    )
    daily = fg = None
    if force or agent_ema_cross(c15)[0]:
        daily = await cached(f"daily:{symbol}", 3600, lambda: fetch_klines(symbol, "1d", 400))
        fg    = await cached("fng", 3600, fetch_fear_greed)
    return evaluate(symbol, c15, c1h, c4h, daily, fg, force)

def format_analysis(r: dict) -> str:
    lines = []
    for name, a in r["agents"].items():
        icon = "✅" if a["vote"] > 0 else "❌" if a["vote"] < 0 else "➖"
        tag  = " 🔒" if a["required"] else f" ({a['vote'] * a['weight']:+d})"
        lines.append(f"{icon} {name}{tag}: {a['reason']}")
    verdict = ("🟢 <b>SIGNAL FIRED</b>" if r["fire"]
               else "🔴 <b>BLOCKED</b> — V9 rule failed" if not r["gate"]
               else f"🔴 <b>BLOCKED</b> — score below {MIN_AGENT_SCORE}")
    return (f"🤖 <b>Agents — {r['direction']} {r['symbol']}</b> [{AGENT_MODE.upper()}]\n"
            f"━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) +
            f"\n━━━━━━━━━━━━━━━━━━\n📊 Score: <b>{r['score']:+d}</b> (need {MIN_AGENT_SCORE:+d}, 🔒 = must pass)\n{verdict}")

async def run_agents(symbol: str):
    r = await analyze(symbol)
    if agent_state["last_bar"].get(symbol) == r["bar"]:
        return
    agent_state["last_bar"][symbol] = r["bar"]
    agent_state["last_analysis"][symbol] = r
    if not r["direction"]:
        return
    await telegram(format_analysis(r))
    if r["fire"] and AGENT_MODE == "live":
        price = price_cache.get(symbol) or await get_price(symbol) or r["close"]
        await process_signal({
            "secret": SECRET,
            "signal": "buy" if r["direction"] == "LONG" else "short",
            "price":  str(price),
            "symbol": symbol,
            "score":  str(r["score"]),
        })

async def signal_generator():
    if AGENT_MODE == "off":
        return
    await telegram(f"🤖 Agent signal generator started — mode: <b>{AGENT_MODE.upper()}</b>, min score {MIN_AGENT_SCORE:+d}")
    while True:
        # wake 10s after each 15m candle closes
        await asyncio.sleep(900 - time.time() % 900 + 10)
        for symbol in AGENT_SYMBOLS:
            try:
                await run_agents(symbol)
            except Exception as e:
                await telegram(f"⚠️ Agent error ({symbol}): {e}")

@asynccontextmanager
async def lifespan(_app):
    if LIVE_TRADING:
        await init_futures()
    ws_task      = asyncio.create_task(ws_price_feed())
    monitor_task = asyncio.create_task(price_monitor())
    agent_task   = asyncio.create_task(signal_generator())
    yield
    ws_task.cancel()
    monitor_task.cancel()
    agent_task.cancel()

app = FastAPI(lifespan=lifespan)

# ─── Webhook ──────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request):
    return await process_signal(await request.json())

async def process_signal(data: dict):
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

        # live qty: close full position at TP1 on Binance
        live_qty       = round(LIVE_SIZE_USD * LEVERAGE / price, 3) if LIVE_TRADING else 0
        live_tp1_qty   = live_qty
        live_tp2_qty   = 0.0
        live_tp3_qty   = 0.0

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
            "score":         score,
            "live_qty":      live_qty,
            "live_tp1_qty":  live_tp1_qty,
            "live_tp2_qty":  live_tp2_qty,
            "live_tp3_qty":  live_tp3_qty,
        }
        port["balance"] -= amount
        port["positions"].append(pos)
        save(port)

        if LIVE_TRADING:
            await futures_order(symbol, "BUY", live_qty)

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

    # ── SHORT entry ──────────────────────────────
    elif signal in ("short", "sell short"):
        if len(port["positions"]) >= MAX_POSITIONS:
            await telegram(f"⚠️ Max {MAX_POSITIONS} positions open — SHORT skipped ({symbol})")
            return {"status": "skipped"}

        amount = port["balance"] * TRADE_SIZE
        if amount < 10:
            await telegram(f"⚠️ Insufficient balance — SHORT skipped ({symbol})")
            return {"status": "skipped"}

        qty_total = amount / price
        tp1_qty   = round(qty_total * 0.34, 6)
        tp2_qty   = round((qty_total - tp1_qty) * 0.50, 6)
        tp3_qty   = round(qty_total - tp1_qty - tp2_qty, 6)

        # live qty: close full position at TP1 on Binance
        live_qty       = round(LIVE_SIZE_USD * LEVERAGE / price, 3) if LIVE_TRADING else 0
        live_tp1_qty   = live_qty
        live_tp2_qty   = 0.0
        live_tp3_qty   = 0.0

        sl  = round(price * (1 + SL_PCT  / 100), 2)
        tp1 = round(price * (1 - TP1_PCT / 100), 2)
        tp2 = round(price * (1 - TP2_PCT / 100), 2)
        tp3 = round(price * (1 - TP3_PCT / 100), 2)

        pos = {
            "symbol":        symbol,
            "side":          "SHORT",
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
            "trail_low":     None,
            "trail_stop":    None,
            "score":         score,
            "live_qty":      live_qty,
            "live_tp1_qty":  live_tp1_qty,
            "live_tp2_qty":  live_tp2_qty,
            "live_tp3_qty":  live_tp3_qty,
        }
        port["balance"] -= amount
        port["positions"].append(pos)
        save(port)

        if LIVE_TRADING:
            await futures_order(symbol, "SELL", live_qty)

        await telegram(
            f"🔴 <b>SHORT ENTRY — {symbol}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕐 {now}\n"
            f"💰 Entry: <b>${price:,.2f}</b>\n"
            f"📊 Score: {score}/7 — {score_label}\n"
            f"📦 Size: ${amount:.2f} ({qty_total:.6f} {symbol[:3]})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛑 SL:  ${sl:,.2f}  (+{SL_PCT}%)\n"
            f"🎯 TP1: ${tp1:,.2f} (-{TP1_PCT}%)  → 34%\n"
            f"🎯 TP2: ${tp2:,.2f} (-{TP2_PCT}%)  → 50% rem\n"
            f"🎯 TP3: ${tp3:,.2f} (-{TP3_PCT}%) + trail\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏦 Free Balance: ${port['balance']:.2f}\n"
            f"📂 Open: {len(port['positions'])}/{MAX_POSITIONS}\n"
            f"⚠️ SIMULATION — No real money used"
        )

    # ── SHORT exit ────────────────────────────────
    elif signal in ("short_close", "buy to cover"):
        shorts = [p for p in port["positions"] if p["symbol"] == symbol and p["side"] == "SHORT"]
        if not shorts:
            await telegram(f"⚠️ SHORT CLOSE signal — no SHORT open for {symbol}")
            return {"status": "no_position"}

        for pos in shorts:
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
                f"📤 <b>SHORT CLOSE — {symbol}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🕐 {now}\n"
                f"💰 Exit: <b>${price:,.2f}</b>\n"
                f"{emoji} P&L: <b>${pnl:.2f} ({pnl_pct:.2f}%)</b>\n"
                f"🏦 Balance: ${port['balance']:.2f}\n"
                f"📊 Win Rate: {wr:.1f}% ({port['wins']}W/{port['losses']}L)\n"
                f"⚠️ SIMULATION"
            )
        save(port)

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

# ─── Agents (read-only, never trades) ─────────
@app.get("/agents")
async def agents_now(symbol: str = "BTCUSDT", force: str = ""):
    """Current agent view. ?force=LONG or SHORT runs every agent even without a crossover."""
    f = force.upper() if force.upper() in ("LONG", "SHORT") else None
    r = await analyze(symbol, f)
    return {"mode": AGENT_MODE, "min_score": MIN_AGENT_SCORE, "analysis": r,
            "last_fired_check": agent_state["last_analysis"].get(symbol)}

# ─── Dashboard ────────────────────────────────
@app.get("/status", response_class=HTMLResponse)
async def status():
    port      = load()
    positions = port.get("positions", [])
    trades    = port.get("trades", [])
    live_trs  = port.get("live_trades", [])

    live_prices = {}
    for pos in positions:
        sym = pos["symbol"]
        if sym not in live_prices:
            p = await get_price(sym)
            if p:
                live_prices[sym] = p

    # live Binance balance
    binance_wallet, binance_avail = (None, None)
    if LIVE_TRADING:
        binance_wallet, binance_avail = await get_live_balance()

    total  = port["wins"] + port["losses"]
    wr     = (port["wins"] / total * 100) if total else 0

    # live tab stats
    live_pnl   = sum(t["pnl"] for t in live_trs)
    live_wins  = sum(1 for t in live_trs if t["pnl"] > 0)
    live_loss  = sum(1 for t in live_trs if t["pnl"] <= 0)
    live_total = live_wins + live_loss
    live_wr    = (live_wins / live_total * 100) if live_total else 0
    unreal = sum(
        ((p["entry_price"] - live_prices.get(p["symbol"], p["entry_price"])) if p.get("side") == "SHORT"
         else (live_prices.get(p["symbol"], p["entry_price"]) - p["entry_price"])) * p["qty_remaining"]
        for p in positions
    )

    cards_html = ""
    for pos in positions:
        price = live_prices.get(pos["symbol"], pos["entry_price"])
        upnl  = ((pos["entry_price"] - price) if pos.get("side") == "SHORT" else (price - pos["entry_price"])) * pos["qty_remaining"]
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
    for t in reversed(trades):
        color = "#00c853" if t["pnl"] > 0 else "#f44336"
        rows += f"""<tr>
          <td>{t.get('side','LONG')} {t.get('symbol','BTCUSDT')}</td>
          <td>${t['entry']:,.2f}</td>
          <td>${t['exit']:,.2f}</td>
          <td style="color:{color}">${t['pnl']:+.2f} ({t['pnl_pct']:+.2f}%)</td>
          <td>{t.get('reason','—')}</td>
        </tr>"""

    live_rows = ""
    for t in reversed(live_trs):
        color = "#00c853" if t["pnl"] > 0 else "#f44336"
        live_rows += f"""<tr>
          <td>{t.get('side','LONG')} {t.get('symbol','BTCUSDT')}</td>
          <td>${t['entry']:,.2f}</td>
          <td>${t['exit']:,.2f}</td>
          <td style="color:{color}">${t['pnl']:+.2f} ({t['pnl_pct']:+.2f}%)</td>
          <td>{t.get('reason','—')}</td>
          <td style="opacity:0.6">{t.get('exit_time','')}</td>
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
    .tabs {{ display:flex; gap:8px; margin-bottom:24px }}
    .tab  {{ padding:8px 24px; border-radius:6px; cursor:pointer; font-family:monospace;
             font-size:14px; border:1px solid #30363d; background:#161b22; color:#8b949e }}
    .tab.active {{ background:#58a6ff; color:#0d1117; border-color:#58a6ff; font-weight:bold }}
    .tab-content {{ display:none }}
    .tab-content.active {{ display:block }}
    .live-badge {{ background:#f44336; color:white; padding:2px 8px; border-radius:4px;
                   font-size:11px; margin-left:8px; vertical-align:middle }}
  </style>
  <script>
    function switchTab(name) {{
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.getElementById('tab-' + name).classList.add('active');
      document.getElementById('content-' + name).classList.add('active');
    }}
  </script>
</head>
<body>
  <h1>BTC Bot V9 Dashboard</h1>
  <div class="tabs">
    <button class="tab active" id="tab-live" onclick="switchTab('live')">🔴 Live Trading <span class="live-badge">LIVE</span></button>
    <button class="tab" id="tab-sim" onclick="switchTab('sim')">Simulation</button>
  </div>

  <!-- ── SIMULATION TAB ── -->
  <div class="tab-content" id="content-sim">
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
  <h2>All Trades</h2>
  <table>
    <tr><th>Type</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th></tr>
    {rows if rows else '<tr><td colspan="5" style="color:#333;text-align:center">No trades yet</td></tr>'}
  </table>
  </div>

  <!-- ── LIVE TAB ── -->
  <div class="tab-content active" id="content-live">
  <div class="stats">
    <div class="stat">
      <div class="stat-label">Binance Balance</div>
      <div class="stat-value">{"$"+f"{binance_wallet:.2f}" if binance_wallet is not None else "—"}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Available</div>
      <div class="stat-value">{"$"+f"{binance_avail:.2f}" if binance_avail is not None else "—"}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Live P&amp;L</div>
      <div class="stat-value" style="color:{'#00c853' if live_pnl >= 0 else '#f44336'}">${live_pnl:+.2f}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Live Trades</div>
      <div class="stat-value">{live_total}</div>
    </div>
    <div class="stat">
      <div class="stat-label">Win Rate</div>
      <div class="stat-value">{live_wr:.1f}%</div>
    </div>
    <div class="stat">
      <div class="stat-label">Size/Trade</div>
      <div class="stat-value">${LIVE_SIZE_USD:.0f}</div>
    </div>
  </div>
  <h2>Live Trades</h2>
  <table>
    <tr><th>Type</th><th>Entry</th><th>Exit</th><th>P&amp;L</th><th>Reason</th><th>Time</th></tr>
    {live_rows if live_rows else '<tr><td colspan="6" style="color:#333;text-align:center">No live trades yet</td></tr>'}
  </table>
  </div>

</body>
</html>"""
    return html

@app.get("/my-ip")
async def my_ip():
    async with httpx.AsyncClient() as c:
        r = await c.get("https://api.ipify.org?format=json")
        return r.json()

@app.get("/debug/balance")
async def debug_balance():
    params = _sign({})
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{FUTURES_BASE}/fapi/v2/balance", params=params,
                        headers={"X-MBX-APIKEY": BINANCE_API_KEY})
        return {"status": r.status_code, "body": r.json()}

@app.get("/")
async def root():
    return {"status": "BTC Bot V9 running ✅"}
