"""Do stop-losses help on NSE? 10+ years of daily prices, today's 500 largest stocks.

Every month from 2013, a position is opened in each stock (and separately in only the top-10% momentum stocks,
which is how StockPilot now picks). Each position is followed for 12 months under different exit rules:
  HOLD              keep for 12 months
  TRAIL_15/20/25    sell when the price falls 15/20/25% from its highest close since buying
  FIXED_15/20/25    sell when the price falls 15/20/25% below the buy price
  BELOW_200DMA      sell at the first close below the 200-day average
After a sale the money earns 6% a year (liquid fund) until the 12 months are up. Selling costs 0.5% (slippage + tax
on top of normal brokerage, conservative). Today's list leaves out companies that collapsed or delisted: those are
exactly where stops help most, so this test UNDER-states the benefit of stops. Keep that in mind when reading it.
"""
import json
import time

import numpy as np
import pandas as pd
import requests
import yfinance as yf

HOLD_DAYS, CASH, EXIT_COST = 252, 0.06, 0.005
RULES = ["HOLD", "TRAIL_15", "TRAIL_20", "TRAIL_25", "FIXED_15", "FIXED_20", "FIXED_25", "BELOW_200DMA"]


def universe(n=500):
    u = requests.get("https://raw.githubusercontent.com/rajdagli91-ops/stockpilot/data/universe.json", timeout=30).json()
    rows = [dict(zip(u["fields"], r)) for r in u["rows"]]
    rows = [r for r in rows if r.get("mcap_cr") and not str(r["symbol"]).endswith(".BO")]
    return [r["symbol"] for r in sorted(rows, key=lambda r: -r["mcap_cr"])[:n]]


def daily(symbols):
    out = {}
    for i in range(0, len(symbols), 100):
        ch = [s + ".NS" for s in symbols[i:i + 100]]
        df = yf.download(ch, start="2011-01-01", auto_adjust=True, group_by="ticker", progress=False, threads=True)
        for t in ch:
            try:
                c = df[t]["Close"].dropna()
            except KeyError:
                continue
            c = c[c > 0]
            if len(c) > 600:
                out[t[:-3]] = c
    return out


def simulate(c, ma200, i):
    """Returns {rule: (12-month return, sold_early, return_if_held)} for a buy at index i."""
    p0 = c[i]
    path = c[i:i + HOLD_DAYS + 1]
    held = path[-1] / p0 - 1
    peak = np.maximum.accumulate(path)
    res = {"HOLD": (held, False)}
    for rule in RULES[1:]:
        if rule.startswith("TRAIL"):
            hit = np.nonzero(path / peak - 1 <= -int(rule[-2:]) / 100)[0]
        elif rule.startswith("FIXED"):
            hit = np.nonzero(path / p0 - 1 <= -int(rule[-2:]) / 100)[0]
        else:
            m = ma200[i:i + HOLD_DAYS + 1]
            hit = np.nonzero((path < m) & ~np.isnan(m))[0]
            hit = hit[hit > 0]
        if len(hit):
            k = int(hit[0])
            r = (path[k] / p0) * (1 - EXIT_COST) * (1 + CASH) ** ((HOLD_DAYS - k) / 252) - 1
            res[rule] = (r, True)
        else:
            res[rule] = (held, False)
    return res, held


def summarise(recs):
    out = {}
    for rule in RULES:
        r = np.array([x[rule][0] for x in recs])
        sold = np.array([x[rule][1] for x in recs])
        held = np.array([x["_held"] for x in recs])
        early = sold & (held > r)  # sold, and holding would have done better
        out[rule] = {"avg_12m_return": round(float(r.mean()), 4), "median": round(float(np.median(r)), 4),
                     "worst_5pct": round(float(np.percentile(r, 5)), 4), "share_losing_over_30pct": round(float((r < -0.30).mean()), 4),
                     "share_positive": round(float((r > 0).mean()), 3), "share_stopped": round(float(sold.mean()), 3),
                     "stopped_but_holding_was_better": round(float(early.sum() / max(1, sold.sum())), 3)}
    return out


def main():
    t0 = time.time()
    px = daily(universe())
    cal = pd.DatetimeIndex(sorted(set().union(*[set(c.index) for c in px.values()])))
    month_starts = pd.Series(cal, index=cal).groupby([cal.year, cal.month]).first()
    entries = [d for d in month_starts if pd.Timestamp("2013-01-01") <= d <= cal[-HOLD_DAYS - 1]]
    allrecs, momrecs = [], []
    mom_at = {}
    for d in entries:  # momentum ranking at each entry date (12-1 month return)
        m = {}
        for s, c in px.items():
            i = c.index.searchsorted(d)
            if i >= 252 and i < len(c):
                m[s] = c.iloc[i - 21] / c.iloc[i - 252] - 1
        if len(m) > 50:
            cut = np.percentile(list(m.values()), 90)
            mom_at[d] = {s for s, v in m.items() if v >= cut}
    for s, c in px.items():
        arr, ma = c.values, c.rolling(200).mean().values
        for d in entries:
            i = c.index.searchsorted(d)
            if i < 200 or i + HOLD_DAYS >= len(arr):
                continue
            res, held = simulate(arr, ma, i)
            rec = {**res, "_held": held}
            allrecs.append(rec)
            if s in mom_at.get(d, ()):
                momrecs.append(rec)
    out = {"about": __doc__.strip().splitlines(), "positions_all": len(allrecs), "positions_momentum_top10": len(momrecs),
           "entries_from": str(entries[0].date()), "entries_to": str(entries[-1].date()),
           "all_stocks": summarise(allrecs), "momentum_top10": summarise(momrecs),
           "runtime_min": round((time.time() - t0) / 60, 1)}
    json.dump(out, open("stop_results.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "about"}, indent=1))


if __name__ == "__main__":
    main()
