"""Bot de trading SIMULÉ sur les nouveaux tokens pump.fun (Solana).

ARGENT VIRTUEL UNIQUEMENT : aucun vrai ordre n'est passé, aucune clé de wallet
n'est utilisée.

Principe (une exécution toutes les 5 minutes) :
  1. Repère les pools Solana créés il y a moins de 30 min (GeckoTerminal, API
     publique gratuite) dont le token vient de pump.fun (adresse finissant par
     "pump").
  2. Filtre : liquidité minimale, activité, plus d'acheteurs que de vendeurs.
  3. "Achète" virtuellement avec frais et slippage réalistes.
  4. Vend à +10 % (take-profit), -10 % (stop-loss), après 60 min, ou si la
     liquidité s'effondre (rug pull probable).
État du portefeuille : portfolio.json
"""
import json
import math
import os
from datetime import datetime, timezone

import requests

# --- Paramètres modifiables ---
START_CASH = 1000.0        # capital virtuel de départ ($)
POSITION_SIZE = 50.0       # montant par position ($)
MAX_POSITIONS = 10         # positions simultanées max
FEE = 0.01                 # frais par ordre (1 %, comme pump.fun)
BASE_SLIPPAGE = 0.03       # slippage minimum (3 %)

TAKE_PROFIT = 0.10         # vente à +10 %
STOP_LOSS = -0.10          # vente à -10 %
MAX_HOLD_MIN = 60          # vente après 60 minutes
RUG_LIQUIDITY = 1_000      # vente d'urgence sous ce seuil de liquidité ($)

# Filtres d'entrée
MIN_AGE_MIN = 1            # pool créé il y a au moins 1 min
MAX_AGE_MIN = 30           # ... et au plus 30 min
MIN_LIQUIDITY = 5_000      # liquidité minimale ($)
MIN_TXNS_5M = 15           # transactions minimales sur 5 min
MIN_BUY_RATIO = 1.2        # achats / ventes sur 5 min
PAGES_TO_SCAN = 3          # pages de "nouveaux pools" (20 par page)

STATE_FILE = "portfolio.json"
GT = "https://api.geckoterminal.com/api/v2"
HEADERS = {"Accept": "application/json;version=20230302"}


# --- Utilitaires ---
def http_get(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def now():
    return datetime.now(timezone.utc)


def now_iso():
    return now().isoformat(timespec="seconds")


def minutes_since(iso):
    return (now() - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 60


def slippage(trade_value, liquidity):
    """Plus la liquidité est faible par rapport à l'ordre, pire est le prix."""
    if liquidity <= 0:
        return 0.5
    return min(0.5, BASE_SLIPPAGE + trade_value / liquidity)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            if state.get("mode") == "pumpfun":
                return state
        except Exception:
            pass
    return {"mode": "pumpfun", "cash": START_CASH, "positions": {},
            "trades": [], "equity_log": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --- Données de marché ---
def fetch_tokens(addresses):
    """Prix et liquidité totale des tokens. Retourne ({adresse: infos}, ok)."""
    out, ok = {}, True
    addresses = list(addresses)
    for i in range(0, len(addresses), 30):
        chunk = addresses[i:i + 30]
        try:
            data = http_get(f"{GT}/networks/solana/tokens/multi/{','.join(chunk)}")
        except Exception as e:
            print(f"Erreur API (prix) : {e}")
            ok = False
            continue
        for item in data.get("data", []):
            a = item.get("attributes", {})
            if a.get("address"):
                out[a["address"]] = {
                    "price": num(a.get("price_usd")),
                    "liquidity": num(a.get("total_reserve_in_usd")),
                }
    return out, ok


def discover_candidates():
    """Nouveaux pools récents dont le token vient de pump.fun."""
    found = {}
    for page in range(1, PAGES_TO_SCAN + 1):
        try:
            data = http_get(f"{GT}/networks/solana/new_pools?page={page}")
        except Exception as e:
            print(f"Erreur API (nouveaux pools, page {page}) : {e}")
            break
        for pool in data.get("data", []):
            try:
                a = pool["attributes"]
                token_id = pool["relationships"]["base_token"]["data"]["id"]
                addr = token_id.split("_", 1)[1]
                if not addr.endswith("pump") or addr in found:
                    continue
                age = minutes_since(a["pool_created_at"])
                m5 = (a.get("transactions") or {}).get("m5") or {}
                buys, sells = m5.get("buys", 0), m5.get("sells", 0)
                price = num(a.get("base_token_price_usd"))
                liquidity = num(a.get("reserve_in_usd"))
                if price <= 0:
                    continue
                found[addr] = {
                    "symbol": (a.get("name") or "?").split(" / ")[0],
                    "price": price, "liquidity": liquidity, "age": age,
                    "buys": buys, "sells": sells,
                }
            except (KeyError, IndexError, ValueError, TypeError):
                continue
    return found


def entry_score(c):
    ratio = c["buys"] / max(c["sells"], 1)
    if (
        not (MIN_AGE_MIN <= c["age"] <= MAX_AGE_MIN)
        or c["liquidity"] < MIN_LIQUIDITY
        or c["buys"] + c["sells"] < MIN_TXNS_5M
        or ratio < MIN_BUY_RATIO
    ):
        return None
    return ratio * math.sqrt(c["buys"] + c["sells"])


# --- Logique de trading ---
def sell(state, addr, pos, price, liquidity, reason):
    value = pos["qty"] * price
    slip = slippage(value, liquidity)
    proceeds = value * (1 - slip) * (1 - FEE)
    state["cash"] += proceeds
    state["positions"].pop(addr)
    state["trades"].append({
        "date": now_iso(), "side": "sell", "symbol": pos["symbol"],
        "address": addr, "price": price, "proceeds": round(proceeds, 2),
        "pnl_usd": round(proceeds - pos["cost"], 2), "reason": reason,
    })
    print(f"VENTE {pos['symbol']} ({reason}) : {proceeds:.2f}$ "
          f"(coût {pos['cost']:.2f}$)")


def manage_positions(state):
    """Retourne True si l'état doit être sauvegardé."""
    if not state["positions"]:
        return False
    dirty = False
    tokens, ok = fetch_tokens(state["positions"].keys())
    for addr, pos in list(state["positions"].items()):
        info = tokens.get(addr)
        if info is None or info["price"] <= 0:
            if ok:
                pos["missing"] = pos.get("missing", 0) + 1
                dirty = True
                if pos["missing"] >= 3:  # introuvable 3 fois de suite = perte totale
                    state["positions"].pop(addr)
                    state["trades"].append({
                        "date": now_iso(), "side": "sell", "symbol": pos["symbol"],
                        "address": addr, "price": 0, "proceeds": 0,
                        "pnl_usd": round(-pos["cost"], 2), "reason": "token disparu",
                    })
                    print(f"PERTE TOTALE {pos['symbol']} : token disparu")
            continue

        if pos.get("missing"):
            pos["missing"] = 0
            dirty = True
        price, liquidity = info["price"], info["liquidity"]
        pos["last_price"] = price
        pnl = price / pos["entry_price"] - 1
        held = minutes_since(pos["entry_time"])

        reason = None
        if liquidity < RUG_LIQUIDITY:
            reason = "liquidité effondrée"
        elif pnl <= STOP_LOSS:
            reason = "stop-loss"
        elif pnl >= TAKE_PROFIT:
            reason = "take-profit"
        elif held >= MAX_HOLD_MIN:
            reason = "durée max"

        if reason:
            sell(state, addr, pos, price, liquidity, reason)
        else:
            print(f"GARDE {pos['symbol']} : {pnl * 100:+.1f} % ({held:.0f} min, "
                  f"liquidité {liquidity:,.0f}$)")
    return dirty


def open_positions(state):
    slots = MAX_POSITIONS - len(state["positions"])
    if slots <= 0 or state["cash"] < POSITION_SIZE:
        return
    candidates = discover_candidates()
    scored = []
    for addr, c in candidates.items():
        if addr in state["positions"]:
            continue
        score = entry_score(c)
        if score is not None:
            scored.append((score, addr, c))
    scored.sort(reverse=True, key=lambda x: x[0])
    print(f"{len(candidates)} tokens pump.fun récents, {len(scored)} passent les filtres.")

    for _, addr, c in scored[:slots]:
        if state["cash"] < POSITION_SIZE:
            break
        slip = slippage(POSITION_SIZE, c["liquidity"])
        entry_price = c["price"] * (1 + slip)
        qty = POSITION_SIZE * (1 - FEE) / entry_price
        state["cash"] -= POSITION_SIZE
        state["positions"][addr] = {
            "symbol": c["symbol"], "qty": qty, "entry_price": entry_price,
            "last_price": c["price"], "cost": POSITION_SIZE,
            "entry_time": now_iso(), "missing": 0,
        }
        state["trades"].append({
            "date": now_iso(), "side": "buy", "symbol": c["symbol"],
            "address": addr, "price": entry_price, "cost": POSITION_SIZE,
        })
        print(f"ACHAT {c['symbol']} à {entry_price:.10f}$ "
              f"(âge {c['age']:.0f} min, liquidité {c['liquidity']:,.0f}$, "
              f"slippage {slip * 100:.1f} %)")


def main():
    state = load_state()
    trades_before = len(state["trades"])

    dirty = manage_positions(state)
    open_positions(state)

    equity = state["cash"] + sum(
        p["qty"] * p.get("last_price", p["entry_price"])
        for p in state["positions"].values()
    )
    perf = (equity / START_CASH - 1) * 100
    print(f"Portefeuille : {equity:.2f}$ (départ {START_CASH:.0f}$) = {perf:+.2f} % "
          f"| positions ouvertes : {len(state['positions'])}")

    # On ne sauvegarde que si quelque chose a changé, ou 1 fois par heure,
    # pour éviter 288 commits par jour dans le dépôt.
    last = state["equity_log"][-1]["date"] if state["equity_log"] else None
    stale = last is None or minutes_since(last) >= 55
    if len(state["trades"]) != trades_before or dirty or stale:
        if stale or len(state["trades"]) != trades_before:
            state["equity_log"].append({"date": now_iso(), "equity": round(equity, 2)})
        state["equity_log"] = state["equity_log"][-1000:]
        state["trades"] = state["trades"][-500:]
        save_state(state)


if __name__ == "__main__":
    main()
