# Binance API Rules

## Signature (_sign)
All params — including `reduceOnly` — must be in the dict **before** calling `_sign()`.
Adding any param after `_sign()` causes error -1022 (invalid signature) and the order is rejected.

```python
# CORRECT
p = {"symbol": ..., "reduceOnly": "true"}
params = _sign(p)

# WRONG — causes -1022
params = _sign({"symbol": ...})
params["reduceOnly"] = "true"   # ← not included in signature
```

## Minimum order size
Binance BTCUSDT Futures minimum: **0.001 BTC** per order.

With `LIVE_SIZE_USD=100` and `LEVERAGE=2` at ~$60k:
- `live_qty = round(100 * 2 / 60000, 3) = 0.003` — just enough for 3 × 0.001 splits.
- But to keep it simple, the full position closes at TP1: `live_tp1_qty = live_qty`.

## Live qty formula
```python
live_qty = round(LIVE_SIZE_USD * LEVERAGE / price, 3)
```
Must include `LEVERAGE`. Without it the qty is 2× too small and TP quantities round to 0.

## Leverage / margin setup
`init_futures()` runs at startup and sets 2× isolated margin on BTCUSDT only.
When adding new coins, update `init_futures()` and the WebSocket subscription.

## Error codes
| Code | Meaning | Usual cause |
|------|---------|-------------|
| -1022 | Invalid signature | Param added after `_sign()` |
| -1021 | Timestamp outside recvWindow | Server clock drift |
