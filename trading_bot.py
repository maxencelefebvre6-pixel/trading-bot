import json
import os
from datetime import datetime, timezone

import yfinance as yf

SYMBOLS = ["BTC-USD", "ETH-USD"]
START_CASH = 1000.0
FEE = 0.001
SMA_FAST, SMA_SLOW = 20, 50
STATE_FILE = "portfolio.json"


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"cash": START_CASH, "positions": {}, "trades": [], "equity_log": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_signal(symbol):
    df = yf.download(symbol, period="1y", interval="1d",
                     progress=False, auto_adjust=True)
    close = df["Close"].squeeze().dropna()
    if len(close) < SMA_SLOW + 1:
        raise ValueError(f"Pas assez de données pour {symbol}")
    fast = close.rolling(SMA_FAST).mean().iloc[-1]
    slow = close.rolling(SMA_SLOW).mean().iloc[-1]
    return float(close.iloc[-1]), "buy" if fast > slow else "sell"


def main():
    state = load_state()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prices = {}

    for symbol in SYMBOLS:
        try:
            price, signal = get_signal(symbol)
        except Exception as e:
            print(f"[{symbol}] erreur : {e}")
            continue
        prices[symbol] = price
        held = state["positions"].get(symbol, 0.0)

        if signal == "buy" and held == 0:
            budget = min(state["cash"], START_CASH / len(SYMBOLS))
            if budget > 10:
                qty = budget * (1 - FEE) / price
                state["cash"] -= budget
                state["positions"][symbol] = qty
                state["trades"].append({"date": now, "symbol": symbol,
                                        "side": "buy", "price": price, "qty": qty})
                print(f"[{symbol}] ACHAT {qty:.6f} à {price:.2f}")
        elif signal == "sell" and held > 0:
            state["cash"] += held * price * (1 - FEE)
            state["positions"].pop(symbol)
            state["trades"].append({"date": now, "symbol": symbol,
                                    "side": "sell", "price": price, "qty": held})
            print(f"[{symbol}] VENTE {held:.6f} à {price:.2f}")
        else:
            print(f"[{symbol}] signal {signal}, rien à faire")

    equity = state["cash"] + sum(
        qty * prices.get(sym, 0.0) for sym, qty in state["positions"].items()
    )
    state["equity_log"].append({"date": now, "equity": round(equity, 2)})
    state["equity_log"] = state["equity_log"][-365:]
    save_state(state)

    perf = (equity / START_CASH - 1) * 100
    print(f"Valeur du portefeuille : {equity:.2f} (départ {START_CASH:.0f}) = {perf:+.2f} %")


if __name__ == "__main__":
    main()
