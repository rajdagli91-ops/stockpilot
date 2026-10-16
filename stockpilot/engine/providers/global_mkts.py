"""Global markets that move Indian stocks, and how they tilt each sector.

Output (data/market.json):
  mood       0-100 overall global risk mood (50 = neutral)
  markets    each index/commodity/currency with 1-day and 5-day change
  tilts      per-sector score 0-100 (50 = no global effect) with the reasons
"""
import numpy as np

# Yahoo symbols. GIFT Nifty is not on Yahoo, so Nifty's own pre-open gap is inferred from Asia + US.
MARKETS = [
    # key, yahoo, label, group
    ("sp500", "^GSPC", "S&P 500", "US"),
    ("nasdaq", "^IXIC", "Nasdaq", "US"),
    ("dow", "^DJI", "Dow Jones", "US"),
    ("us_vix", "^VIX", "US VIX (fear index)", "US"),
    ("us10y", "^TNX", "US 10-year yield", "Rates & FX"),
    ("dxy", "DX-Y.NYB", "Dollar index", "Rates & FX"),
    ("usdinr", "INR=X", "USD / INR", "Rates & FX"),
    ("nikkei", "^N225", "Nikkei 225", "Asia"),
    ("hangseng", "^HSI", "Hang Seng", "Asia"),
    ("kospi", "^KS11", "Kospi", "Asia"),
    ("shanghai", "000001.SS", "Shanghai", "Asia"),
    ("ftse", "^FTSE", "FTSE 100", "Europe"),
    ("dax", "^GDAXI", "DAX", "Europe"),
    ("brent", "BZ=F", "Brent crude", "Commodities"),
    ("gold", "GC=F", "Gold", "Commodities"),
    ("copper", "HG=F", "Copper", "Commodities"),
    ("nifty", "^NSEI", "Nifty 50", "India"),
    ("india_vix", "^INDIAVIX", "India VIX", "India"),
]

# How a 5-day move in each driver tilts each sector (Yahoo sector names). +1 = good for the sector when the driver rises.
SENS = {
    "usdinr": {"Technology": 1.0, "Healthcare": 0.6, "Consumer Cyclical": -0.3, "Energy": -0.3, "Basic Materials": -0.2},
    "brent": {"Energy": 0.2, "Consumer Cyclical": -0.6, "Basic Materials": -0.5, "Industrials": -0.3, "Utilities": -0.2, "Consumer Defensive": -0.3},
    "us10y": {"Financial Services": -0.5, "Real Estate": -0.6, "Technology": -0.3, "Utilities": -0.3},
    "dxy": {"Financial Services": -0.3, "Basic Materials": -0.4, "Real Estate": -0.3},
    "nasdaq": {"Technology": 0.9, "Communication Services": 0.5},
    "sp500": {"Technology": 0.3, "Healthcare": 0.3, "Industrials": 0.2},
    "copper": {"Basic Materials": 0.8, "Industrials": 0.2},
    "gold": {"Consumer Cyclical": -0.1},
    "hangseng": {"Basic Materials": 0.3},
}
SECTORS = ["Technology", "Healthcare", "Financial Services", "Energy", "Basic Materials", "Consumer Cyclical",
           "Consumer Defensive", "Industrials", "Utilities", "Real Estate", "Communication Services"]


def _chg(series, n):
    if series is None or len(series) <= n:
        return None
    return float((series.iloc[-1] / series.iloc[-1 - n] - 1) * 100)


def fetch(prov) -> dict:
    hist = prov.bulk_history([m[1] for m in MARKETS], period="3mo") if hasattr(prov, "bulk_history") else \
        {m[1]: prov.history(m[1], period="3mo") for m in MARKETS}
    rows, ch1, ch5 = [], {}, {}
    for key, ysym, label, group in MARKETS:
        h = hist.get(ysym)
        c = h["Close"].dropna() if h is not None and len(h) else None
        d1, d5 = _chg(c, 1), _chg(c, 5)
        ch1[key], ch5[key] = d1, d5
        rows.append({"key": key, "label": label, "group": group, "last": round(float(c.iloc[-1]), 2) if c is not None and len(c) else None,
                     "chg_1d": None if d1 is None else round(d1, 2), "chg_5d": None if d5 is None else round(d5, 2),
                     "date": c.index[-1].strftime("%Y-%m-%d") if c is not None and len(c) else None})

    # overall mood: overnight equities up = good, fear/yields/dollar/crude spiking = bad
    parts = []
    for k, w in (("sp500", 1.0), ("nasdaq", 0.6), ("nikkei", 0.6), ("hangseng", 0.5), ("kospi", 0.4), ("dax", 0.4)):
        if ch1.get(k) is not None:
            parts.append(w * np.clip(ch1[k] / 1.5, -1, 1))
    for k, w, scale in (("us_vix", -0.8, 10), ("india_vix", -0.6, 8), ("brent", -0.4, 3), ("us10y", -0.3, 3), ("dxy", -0.3, 1), ("usdinr", -0.4, 0.6)):
        if ch1.get(k) is not None:
            parts.append(w * np.clip(ch1[k] / scale, -1, 1))
    mood = 50 + 50 * float(np.clip(sum(parts) / 3, -1, 1)) if parts else None

    tilts = {}
    for sec in SECTORS:
        s, why = 0.0, []
        for drv, m in SENS.items():
            if sec in m and ch5.get(drv) is not None:
                scale = {"usdinr": 1.0, "us10y": 4.0, "dxy": 1.5}.get(drv, 3.0)
                eff = m[sec] * float(np.clip(ch5[drv] / scale, -1, 1))
                s += eff
                if abs(eff) >= 0.25:
                    label = next(r["label"] for r in rows if r["key"] == drv)
                    why.append(f"{label} {'up' if ch5[drv] > 0 else 'down'} {abs(ch5[drv]):.1f}% in 5 days")
        tilt = 50 + 35 * float(np.clip(s, -1.2, 1.2)) / 1.2
        if mood is not None:
            tilt = 0.75 * tilt + 0.25 * mood
        tilts[sec] = {"score": round(tilt), "why": why[:3]}

    bias = None
    if mood is not None:
        bias = "Positive" if mood >= 60 else "Negative" if mood <= 40 else "Neutral"
    return {"mood": None if mood is None else round(mood), "bias": bias, "markets": rows, "tilts": tilts}


def sector_tilt(market: dict, sector: str | None):
    if not market or not sector:
        return None
    t = (market.get("tilts") or {}).get(sector)
    return t["score"] if t else market.get("mood")
