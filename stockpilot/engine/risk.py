"""Aladdin-style risk numbers for each stock, so the website can stress-test a portfolio.

For every analysed stock, from 2 years of weekly returns:
  beta     how much it moves when the Nifty moves 1% (all else equal)
  b_crude  extra move for a 1% move in Brent crude, holding the Nifty, rupee and rates fixed
  b_inr    extra move for a 1% weaker rupee (USD/INR up 1%)
  b_rates  extra move for a 1-point rise in the US 10-year yield
  r2       how much of its weekly moves these four explain (low = mostly company-specific)
  dd2y     worst peak-to-trough fall in 2 years (%), worst4w = worst 4-week fall (%)
These come from one regression on all four drivers together, so each number is that driver's own effect.
"""
import numpy as np
import pandas as pd

FACTORS = {"nifty": "^NSEI", "crude": "BZ=F", "inr": "INR=X", "rates": "^TNX"}


def factor_weekly(prov) -> pd.DataFrame | None:
    """Weekly changes of the four drivers over ~2 years (rates: change in yield points, others: % change)."""
    cols = {}
    for k, sym in FACTORS.items():
        try:
            h = prov.history(sym, period="3y")
            w = h["Close"].resample("W-FRI").last().dropna()
            w.index = w.index.tz_localize(None) if getattr(w.index, "tz", None) else w.index
            cols[k] = w.diff() if k == "rates" else w.pct_change() * 100
        except Exception as e:
            print(f"risk factor {k}: {e}")
    if "nifty" not in cols:
        return None
    df = pd.DataFrame(cols).dropna(how="all").iloc[-105:]
    return df


def stock_risk(hist: pd.DataFrame, F: pd.DataFrame | None) -> dict:
    if hist is None or len(hist) < 120 or F is None:
        return {}
    c = hist["Close"].copy()
    c.index = c.index.tz_localize(None) if getattr(c.index, "tz", None) else c.index
    w = c.resample("W-FRI").last().dropna()
    r = (w.pct_change() * 100).rename("r")
    df = pd.concat([r, F], axis=1, join="inner").dropna()
    out = {}
    if len(df) >= 40:
        xs = [k for k in FACTORS if k in df]
        X = np.column_stack([np.ones(len(df))] + [df[k].values for k in xs])
        y = df["r"].values
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        out["r2"] = round(float(1 - resid.var() / y.var()), 2) if y.var() > 0 else None
        names = {"nifty": "beta", "crude": "b_crude", "inr": "b_inr", "rates": "b_rates"}
        for k, b in zip(xs, coef[1:]):
            out[names[k]] = round(float(b), 2)
    last2y = c.iloc[-504:]
    peak = last2y.cummax()
    out["dd2y"] = round(float((last2y / peak - 1).min() * 100), 1)
    if len(w) >= 6:
        out["worst4w"] = round(float((w / w.shift(4) - 1).iloc[-104:].min() * 100), 1)
    return out


def compact(rk: dict) -> list | None:
    """For universe.json: [beta, b_crude, b_inr, b_rates, r2, dd2y]."""
    if not rk:
        return None
    return [rk.get(k) for k in ("beta", "b_crude", "b_inr", "b_rates", "r2", "dd2y")]


def worst_window(prov) -> dict | None:
    """The Nifty's worst 4-week fall in the last 2 years: a real past shock to replay on today's portfolio."""
    try:
        h = prov.history(FACTORS["nifty"], period="3y")["Close"]
        w = h.resample("W-FRI").last().dropna().iloc[-105:]
        chg = (w / w.shift(4) - 1) * 100
        end = chg.idxmin()
        start = w.index[w.index.get_loc(end) - 4]
        return {"from": start.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d"), "nifty_chg": round(float(chg.min()), 1)}
    except Exception as e:
        print(f"worst window: {e}")
        return None
