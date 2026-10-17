"""Prices and fundamentals from Yahoo Finance (free; NSE symbols as SYMBOL.NS, BSE as SYMBOL.BO).

Yahoo blocks its "quoteSummary" endpoint (P/E, ROE, sector...) for cloud servers ("Invalid Crumb"),
so fundamentals come from its annual financial-statements feed instead and ratios are computed here:
  P/E = market cap / net income, ROE = net income / equity, debt/equity, cash conversion, growth.
Statements reported in another currency (e.g. Infosys reports in USD) are converted to INR.
Sectors come from NSE's index constituent lists (Nifty Total Market), cached in config/sectors.csv.

Every provider returns plain dicts so a paid source can be dropped in later with the same shape.
"""
import csv
import io
import math
import threading
import time
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parents[2]
SECTOR_CACHE = ROOT / "config" / "sectors.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
TS_URL = "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
TYPES = ["annualTotalRevenue", "annualNetIncome", "annualStockholdersEquity", "annualTotalDebt",
         "annualOperatingCashFlow", "annualOrdinarySharesNumber", "annualCashDividendsPaid", "annualOperatingIncome",
         "quarterlyNetIncome", "quarterlyTotalRevenue"]

# NSE industry names -> the sector names used by the global-markets module
NSE_TO_SECTOR = {
    "information technology": "Technology", "healthcare": "Healthcare", "financial services": "Financial Services",
    "oil gas & consumable fuels": "Energy", "power": "Utilities", "metals & mining": "Basic Materials",
    "chemicals": "Basic Materials", "construction materials": "Basic Materials", "forest materials": "Basic Materials",
    "automobile and auto components": "Consumer Cyclical", "consumer durables": "Consumer Cyclical",
    "consumer services": "Consumer Cyclical", "textiles": "Consumer Cyclical", "realty": "Real Estate",
    "fast moving consumer goods": "Consumer Defensive", "capital goods": "Industrials", "construction": "Industrials",
    "services": "Industrials", "diversified": "Industrials", "telecommunication": "Communication Services",
    "media entertainment & publication": "Communication Services", "media, entertainment & publication": "Communication Services",
}
SECTOR_LISTS = ["ind_niftytotalmarket_list.csv", "ind_nifty500list.csv", "ind_niftymicrocap250_list.csv"]

_session = requests.Session()
_session.headers.update(UA)
_lock = threading.Lock()
_fx_cache: dict[str, float] = {}
_sectors: dict[str, tuple[str, str]] | None = None


def _yahoo(symbol: str) -> str:
    if symbol.startswith("^") or "=" in symbol or "." in symbol:
        return symbol
    return f"{symbol}.NS"


def _num(x):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def _cagr(series):
    s = [x for x in (series or []) if x is not None]
    if len(s) < 2 or s[0] <= 0 or s[-1] <= 0:
        return None
    return ((s[-1] / s[0]) ** (1 / (len(s) - 1)) - 1) * 100


# ---------------------------------------------------------------- prices
def history(symbol: str, period: str = "5y") -> pd.DataFrame:
    df = yf.Ticker(_yahoo(symbol)).history(period=period, auto_adjust=True)
    return df[["Close", "Volume"]].dropna() if not df.empty else df


def bulk_history(symbols, period: str = "5y", batch: int = 150) -> dict:
    """Prices for many symbols at once (one request per batch). Returns {symbol: DataFrame}."""
    out = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        try:
            df = yf.download([_yahoo(s) for s in chunk], period=period, group_by="ticker", auto_adjust=True,
                             threads=True, progress=False)
        except Exception as e:
            print(f"batch {i}: {e}")
            continue
        for s in chunk:
            try:
                try:
                    sub = df[_yahoo(s)]
                except KeyError:
                    sub = df
                sub = sub[["Close", "Volume"]].dropna()
                if len(sub):
                    out[s] = sub
            except Exception:
                pass
        time.sleep(1)
    return out


# ---------------------------------------------------------------- sectors (NSE lists)
def _load_sectors():
    global _sectors
    with _lock:
        if _sectors is not None:
            return _sectors
        found = {}
        for name in SECTOR_LISTS:
            try:
                r = requests.get(f"https://niftyindices.com/IndexConstituent/{name}", headers=UA, timeout=20)
                if r.status_code == 200 and "Symbol" in r.text[:300]:
                    for x in csv.DictReader(io.StringIO(r.text)):
                        sym, ind = (x.get("Symbol") or "").strip(), (x.get("Industry") or "").strip()
                        if sym and ind:
                            found.setdefault(sym, ind)
            except Exception as e:
                print(f"sector list {name} skipped: {e}")
        if len(found) > 300:
            SECTOR_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with open(SECTOR_CACHE, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "industry"])
                w.writerows(sorted(found.items()))
        elif SECTOR_CACHE.exists():
            with open(SECTOR_CACHE, newline="", encoding="utf-8") as f:
                found = {r["symbol"]: r["industry"] for r in csv.DictReader(f)}
        print(f"sectors known for {len(found)} stocks")
        _sectors = {s: (NSE_TO_SECTOR.get(i.lower(), "Industrials"), i) for s, i in found.items()}
        return _sectors


# ---------------------------------------------------------------- currency
def _fx_to_inr(ccy: str | None) -> float:
    if not ccy or ccy == "INR":
        return 1.0
    with _lock:
        if ccy in _fx_cache:
            return _fx_cache[ccy]
    rate = None
    try:
        h = yf.Ticker(f"{ccy}INR=X").history(period="5d")
        rate = float(h["Close"].dropna().iloc[-1]) if len(h) else None
    except Exception:
        pass
    rate = rate or {"USD": 85.0, "EUR": 92.0, "GBP": 108.0}.get(ccy, 1.0)
    with _lock:
        _fx_cache[ccy] = rate
    return rate


# ---------------------------------------------------------------- fundamentals
def _timeseries(ysym: str) -> tuple[dict, str | None]:
    params = {"type": ",".join(TYPES), "period1": "1420070400", "period2": str(int(time.time()) + 86400)}
    for attempt in range(3):
        r = _session.get(TS_URL.format(sym=ysym), params=params, timeout=20)
        if r.status_code == 429:
            time.sleep(3 + attempt * 5)
            continue
        r.raise_for_status()
        break
    out, ccy = {}, None
    for res in (r.json().get("timeseries") or {}).get("result") or []:
        key = (res.get("meta") or {}).get("type", [None])[0]
        vals = [v for v in (res.get(key) or []) if v and v.get("reportedValue")]
        vals.sort(key=lambda v: v["asOfDate"])
        if vals:
            n = 6 if key.startswith("quarterly") else 4
            out[key] = [float(v["reportedValue"]["raw"]) for v in vals][-n:]
            out[key + "_dates"] = [v["asOfDate"] for v in vals][-n:]
            if key.startswith("annual"):
                ccy = ccy or vals[-1].get("currencyCode")
    return out, ccy


def fundamentals(symbol: str, price: float | None = None) -> dict:
    ysym = _yahoo(symbol)
    ts, ccy = _timeseries(ysym)
    time.sleep(0.3)
    sectors = _load_sectors()
    sector, industry = sectors.get(symbol.replace(".BO", "").replace(".NS", ""), (None, None))
    last = lambda k: (ts.get(k) or [None])[-1]
    rev, ni = ts.get("annualTotalRevenue"), ts.get("annualNetIncome")
    eq, debt, ocf = last("annualStockholdersEquity"), last("annualTotalDebt"), last("annualOperatingCashFlow")
    shares, divs, opinc = last("annualOrdinarySharesNumber"), last("annualCashDividendsPaid"), last("annualOperatingIncome")
    ni_last = ni[-1] if ni else None
    fx = _fx_to_inr(ccy)
    if price is None:
        try:
            h = history(symbol, "5d")
            price = float(h["Close"].iloc[-1]) if len(h) else None
        except Exception:
            price = None
    mcap = price * shares if price and shares else None  # INR
    pe = mcap / (ni_last * fx) if mcap and ni_last and ni_last > 0 else None
    pb = mcap / (eq * fx) if mcap and eq and eq > 0 else None
    fin = sector == "Financial Services"
    return {
        "name": None, "sector": sector, "industry": industry, "currency": ccy,
        "market_cap": mcap, "pe": pe, "pb": pb,
        "roe": ni_last / eq * 100 if ni_last is not None and eq and eq > 0 else None,
        "debt_equity": None if fin else (debt / eq if debt is not None and eq and eq > 0 else None),
        "dividend_yield": abs(divs) * fx / mcap * 100 if divs and mcap else None,
        "op_margin": opinc / rev[-1] * 100 if opinc is not None and rev and rev[-1] else None,
        "rev_cagr": _cagr(rev), "profit_cagr": _cagr(ni),
        "cash_conversion": (ocf / ni_last) if ocf is not None and ni_last and ni_last > 0 else (-1 if ni_last is not None and ni_last <= 0 else None),
        "loss_making": bool(ni_last is not None and ni_last <= 0),
        "years": len(rev) if rev else 0,
        "ni_hist": [[d, v] for d, v in zip(ts.get("annualNetIncome_dates") or [], ni or [])],
        "q_ni": ts.get("quarterlyNetIncome") or [], "q_rev": ts.get("quarterlyTotalRevenue") or [],
        "q_dates": ts.get("quarterlyNetIncome_dates") or [],
    }
