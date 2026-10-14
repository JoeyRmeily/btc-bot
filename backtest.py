"""Replay history through the live agents in main.py and score every threshold.

    python backtest.py --months 12

Exits mirror live Binance behavior: full close at TP1, stop at SL. If one candle
touches both, SL is assumed first (conservative). Fees: taker on entry and exit.
"""
import argparse, bisect, csv, json, os, time
from datetime import datetime, timezone
import httpx
import main

FEE_PCT   = 0.05 * 2
CACHE_DIR = ".backtest_cache"
MS        = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}

def download(symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{symbol}_{interval}_{start_ms}_{end_ms}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    rows, cursor = [], start_ms
    with httpx.Client(timeout=30) as c:
        while cursor < end_ms:
            batch = c.get(f"{main.SPOT_BASE}/api/v3/klines", params={
                "symbol": symbol, "interval": interval, "startTime": cursor,
                "endTime": end_ms, "limit": 1000}).json()
            if not batch:
                break
            rows += batch
            cursor = batch[-1][0] + MS[interval]
            time.sleep(0.1)
    candles = [{"t": k[0], "o": float(k[1]), "h": float(k[2]), "l": float(k[3]),
                "c": float(k[4]), "v": float(k[5])}
               for k in rows if k[0] + MS[interval] <= end_ms]
    with open(path, "w") as f:
        json.dump(candles, f)
    return candles

def download_fear_greed() -> list:
    rows = httpx.get("https://api.alternative.me/fng/?limit=0", timeout=30).json()["data"]
    return sorted((int(r["timestamp"]) * 1000, {"score": int(r["value"]), "label": r["value_classification"]})
                  for r in rows)

def closed_before(candles: list, close_times: list, t: int, n: int) -> list:
    """Last n candles fully closed at time t."""
    i = bisect.bisect_right(close_times, t)
    return candles[max(0, i - n):i]

def simulate(c15: list, i: int, d: str) -> tuple:
    entry = c15[i]["c"]
    tp_pct, sl_pct = main.TP1_PCT, main.SL_PCT
    tp = entry * (1 + tp_pct / 100) if d == "LONG" else entry * (1 - tp_pct / 100)
    sl = entry * (1 - sl_pct / 100) if d == "LONG" else entry * (1 + sl_pct / 100)
    for n, k in enumerate(c15[i + 1:], 1):
        hit_sl = k["l"] <= sl if d == "LONG" else k["h"] >= sl
        hit_tp = k["h"] >= tp if d == "LONG" else k["l"] <= tp
        if hit_sl:
            return "SL", -sl_pct - FEE_PCT, n
        if hit_tp:
            return "TP", tp_pct - FEE_PCT, n
    return "OPEN", 0.0, len(c15) - i - 1

def stats(trades: list) -> dict:
    closed = [t for t in trades if t["result"] != "OPEN"]
    wins   = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] <= 0]
    n = len(closed)
    return {
        "n": n,
        "wr": 100 * len(wins) / n if n else 0,
        "pf": sum(wins) / -sum(losses) if losses else float("inf") if wins else 0,
        "net": sum(wins) + sum(losses),
        "hours": sum(t["bars"] for t in closed) / n / 4 if n else 0,
    }

def row(label: str, s: dict) -> str:
    pf = "inf" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return f"{label:<26}{s['n']:>6}{s['wr']:>8.1f}%{pf:>7}{s['net']:>+9.1f}%{s['hours']:>8.1f}h"

def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--csv", default="backtest_results.csv")
    args = ap.parse_args()

    now_ms   = int(time.time() // 900 * 900 * 1000)
    start_ms = now_ms - args.months * 30 * MS["1d"]
    print(f"Downloading {args.symbol} history ({args.months} months + warmup)...")
    c15   = download(args.symbol, "15m", start_ms - 4 * MS["1d"], now_ms)
    c1h   = download(args.symbol, "1h",  start_ms - 15 * MS["1d"], now_ms)
    c4h   = download(args.symbol, "4h",  start_ms - 55 * MS["1d"], now_ms)
    daily = download(args.symbol, "1d",  start_ms - 410 * MS["1d"], now_ms)
    fng   = download_fear_greed()
    fng_t = [t for t, _ in fng]
    close = {iv: [k["t"] + MS[iv] for k in cs] for iv, cs in
             (("1h", c1h), ("4h", c4h), ("1d", daily))}

    trades = []
    first = next(i for i, k in enumerate(c15) if k["t"] >= start_ms)
    print(f"Replaying {len(c15) - first:,} candles through the agents...")
    for i in range(max(first, 300), len(c15)):
        w15 = c15[i - 299:i + 1]
        if not main.agent_ema_cross(w15)[0]:
            continue
        t = c15[i]["t"] + MS["15m"]
        fi = bisect.bisect_right(fng_t, t) - 1
        r = main.evaluate(
            args.symbol, w15,
            closed_before(c1h, close["1h"], t, 300),
            closed_before(c4h, close["4h"], t, 300),
            closed_before(daily, close["1d"], t, 400),
            fng[fi][1] if fi >= 0 else None,
            now=datetime.fromtimestamp(t / 1000, timezone.utc).replace(tzinfo=None),
        )
        result, pnl, bars = simulate(c15, i, r["direction"])
        trades.append({
            "time": datetime.fromtimestamp(t / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "direction": r["direction"], "entry": r["close"], "gate": r["gate"], "score": r["score"],
            "result": result, "pnl": round(pnl, 3), "bars": bars,
            "votes": {name: a["vote"] for name, a in r["agents"].items()},
        })

    gated = [t for t in trades if t["gate"]]
    be = main.SL_PCT + FEE_PCT
    print(f"\nTP1 {main.TP1_PCT}% / SL {main.SL_PCT}% / fees {FEE_PCT}%  ->  "
          f"break-even win rate {100 * be / (main.TP1_PCT + main.SL_PCT):.1f}%")
    print(f"\n{'':<26}{'trades':>6}{'win':>9}{'PF':>7}{'net':>10}{'avg hold':>9}")
    print(row("All EMA crossovers", stats(trades)))
    print(row("V9 rules only (gate)", stats(gated)))
    for th in range(min((t["score"] for t in gated), default=0), max((t["score"] for t in gated), default=0) + 1):
        sel = [t for t in gated if t["score"] >= th]
        mark = "  <- current" if th == main.MIN_AGENT_SCORE else ""
        print(row(f"V9 + score >= {th:+d}", stats(sel)) + mark)

    for d in ("LONG", "SHORT"):
        print(row(f"V9 gate, {d} only", stats([t for t in gated if t["direction"] == d])))

    required = ("EMA Cross", "MTF 1H/4H", "RSI", "VWAP", "Volume", "EMA50 trend")
    scoring = [n for n in (trades[0]["votes"] if trades else {}) if n not in required]
    print(f"\nScoring agents on V9-gated trades (win rate when the agent voted...)")
    print(f"{'agent':<14}{'+1':>16}{'0':>16}{'-1':>16}")
    for name in scoring:
        cells = []
        for v in (1, 0, -1):
            s = stats([t for t in gated if t["votes"].get(name) == v])
            cells.append(f"{s['wr']:5.1f}% ({s['n']:>3})" if s["n"] else "      -")
        print(f"{name:<14}" + "".join(f"{c:>16}" for c in cells))

    print(f"\nV9 rules on ALL crossovers (win rate when the rule passed vs failed)")
    for name in ("MTF 1H/4H", "RSI", "VWAP", "Volume", "EMA50 trend"):
        ok = stats([t for t in trades if t["votes"].get(name) == 1])
        bad = stats([t for t in trades if t["votes"].get(name) == -1])
        print(f"{name:<14}  pass {ok['wr']:5.1f}% ({ok['n']:>3})   fail {bad['wr']:5.1f}% ({bad['n']:>3})")

    with open(args.csv, "w", newline="") as f:
        names = list(trades[0]["votes"]) if trades else []
        w = csv.writer(f)
        w.writerow(["time", "direction", "entry", "gate", "score", "result", "pnl_pct", "bars"] + names)
        for t in trades:
            w.writerow([t["time"], t["direction"], t["entry"], t["gate"], t["score"],
                        t["result"], t["pnl"], t["bars"]] + [t["votes"].get(n) for n in names])
    print(f"\nEvery crossover saved to {args.csv}")

if __name__ == "__main__":
    main_cli()
