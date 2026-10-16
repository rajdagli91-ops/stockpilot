"""Price + fundamentals provider using Yahoo Finance (free, NSE symbols as SYMBOL.NS).

Every provider returns plain dicts so a paid source (Screener Premium, Trendlyne, etc.)
can be dropped in later with the same shape.
"""
import math
import time

import pandas as pd
import yfinance as yf


def _yahoo(symbol: str) -> str:
    return symbol if symbol.startswith("^") or "." in symbol else f"{symbol}.NS"


def _num(x):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def _row(df: pd.DataFrame, *names):
    """First matching row of a yfinance statement, oldest -> newest values."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                return [float(v) for v in s.sort_index().values]
    return None


def _cagr(series):
    if not series or len(series) < 2:
        return None
    first, last, yrs = series[0], series[-1], len(series) - 1
    if first <= 0 or last <= 0:
        return None
    return ((last / first) ** (1 / yrs) - 1) * 100


def history(symbol: str, period: str = "2y") -> pd.DataFrame:
    df = yf.Ticker(_yahoo(symbol)).history(period=period, auto_adjust=True)
    return df[["Close", "Volume"]].dropna() if not df.empty else df


def bulk_history(symbols, period: str = "2y", batch: int = 150) -> dict:
    """Prices for many stocks at once (one request per batch). Returns {symbol: DataFrame}."""
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


def fundamentals(symbol: str) -> dict:
    t = yf.Ticker(_yahoo(symbol))
    info = {}
    try:
        info = t.info or {}
    except Exception:
        pass
    inc = cf = None
    try:
        inc = t.income_stmt
    except Exception:
        pass
    try:
        cf = t.cashflow
    except Exception:
        pass
    rev = _row(inc, "Total Revenue", "Operating Revenue")
    ni = _row(inc, "Net Income", "Net Income Common Stockholders")
    ocf = _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities")
    de = _num(info.get("debtToEquity"))
    dy = _num(info.get("dividendYield"))
    if dy is not None and dy < 1:  # yfinance sometimes gives 0.012 instead of 1.2
        dy *= 100
    roe = _num(info.get("returnOnEquity"))
    time.sleep(0.4)  # be polite
    return {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": _num(info.get("marketCap")),
        "pe": _num(info.get("trailingPE")),
        "pb": _num(info.get("priceToBook")),
        "roe": roe * 100 if roe is not None else None,
        "debt_equity": de / 100 if de is not None else None,
        "dividend_yield": dy,
        "op_margin": (_num(info.get("operatingMargins")) or 0) * 100 if info.get("operatingMargins") is not None else None,
        "rev_cagr": _cagr(rev),
        "profit_cagr": _cagr(ni),
        "cash_conversion": (ocf[-1] / ni[-1]) if ocf and ni and ni[-1] > 0 else (-1 if ni and ni[-1] <= 0 else None),
        "loss_making": bool(ni and ni[-1] <= 0),
        "years": len(rev) if rev else 0,
    }
