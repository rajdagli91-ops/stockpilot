"""Does 12-1 month momentum work on NSE over 10+ years? (the only price signal that passed the 3-year test)

Monthly: rank the stocks that existed that month by their return from 12 months ago to 1 month ago, hold the
top 10% for one month (equal weight), compare with holding every eligible stock equally.
Costs charged on actual turnover (0.4% per full round trip). Results by calendar year and overall.
Universe: today's 500 largest NSE stocks; each joins once it has 13 months of prices. Using today's list means
long-dead or shrunk companies are missing (survivorship bias): that flatters momentum, so read the result as a
best case, and look at how consistent it is year by year rather than at the headline number.
"""
import json
import time

import numpy as np
import pandas as pd
import requests
import yfinance as yf

COST = 0.004


def universe(n=500):
    u = requests.get("https://raw.githubusercontent.com/rajdagli91-ops/stockpilot/data/universe.json", timeout=30).json()
    rows = [dict(zip(u["fields"], r)) for r in u["rows"]]
    rows = [r for r in rows if r.get("mcap_cr") and not str(r["symbol"]).endswith(".BO")]
    return [r["symbol"] for r in sorted(rows, key=lambda r: -r["mcap_cr"])[:n]]


def monthly(symbols):
    out = {}
    for i in range(0, len(symbols), 100):
        ch = [s + ".NS" for s in symbols[i:i + 100]]
        df = yf.download(ch, period="max", interval="1mo", auto_adjust=True, group_by="ticker", progress=False, threads=True)
        for t in ch:
            try:
                c = df[t]["Close"].dropna()
            except KeyError:
                continue
            c = c[c > 0]
            if len(c) > 14:
                c.index = c.index.to_period("M").to_timestamp("M")
                out[t[:-3]] = c[~c.index.duplicated(keep="last")]
    return pd.DataFrame(out).sort_index()


def stats(r):
    r = pd.Series(r).dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 12
    return {"cagr": round(float(eq.iloc[-1] ** (1 / yrs) - 1), 4) if yrs else None,
            "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 4), "months": int(len(r))}


def main():
    t0 = time.time()
    px = monthly(universe())
    nifty = yf.download("^NSEI", period="max", interval="1mo", auto_adjust=True, progress=False)["Close"]
    nifty = nifty.iloc[:, 0] if isinstance(nifty, pd.DataFrame) else nifty
    nifty.index = nifty.index.to_period("M").to_timestamp("M")
    nifty = nifty[~nifty.index.duplicated(keep="last")]
    ret = px.pct_change(fill_method=None)
    mom = px.shift(1) / px.shift(12) - 1
    rows, prev = [], set()
    dates = [d for d in px.index if d >= pd.Timestamp("2013-01-31")][:-1]  # last month has no next-month return
    for d in dates:
        i = px.index.get_loc(d)
        nxt = px.index[i + 1]
        m = mom.loc[d].dropna()
        fwd = ret.loc[nxt].reindex(m.index).dropna()
        m = m.reindex(fwd.index)
        if len(m) < 50:
            continue
        k = max(10, len(m) // 10)
        top = set(m.sort_values().tail(k).index)
        turnover = len(top - prev) / k if prev else 1.0
        prev = top
        rows.append({"date": nxt.strftime("%Y-%m"), "n": len(m), "top": float(fwd[list(top)].mean()),
                     "bottom": float(fwd[list(m.sort_values().head(k).index)].mean()), "all": float(fwd.mean()),
                     "cost": COST * turnover, "nifty": float(nifty.pct_change().get(nxt, np.nan)),
                     "ic": float(pd.Series(m).rank().corr(fwd.rank()))})
    p = pd.DataFrame(rows)
    p["top_net"] = p["top"] - p["cost"]
    p["year"] = p["date"].str[:4]
    by_year = {}
    for y, g in p.groupby("year"):
        by_year[y] = {"momentum_top10_net": round(float(np.prod(1 + g["top_net"]) - 1), 3), "equal_weight_all": round(float(np.prod(1 + g["all"]) - 1), 3),
                      "nifty": round(float(np.prod(1 + g["nifty"].fillna(0)) - 1), 3), "stocks": int(g["n"].mean()),
                      "months_momentum_beat_all": f"{int((g['top_net'] > g['all']).sum())}/{len(g)}"}
    res = {"about": __doc__.strip().splitlines()[:3], "period": [p["date"].min(), p["date"].max()],
           "momentum_top10_net": stats(p["top_net"]), "equal_weight_all": stats(p["all"]), "nifty": stats(p["nifty"].fillna(0)),
           "momentum_bottom10": stats(p["bottom"]),
           "share_of_months_momentum_beat_all": round(float((p["top_net"] > p["all"]).mean()), 3),
           "share_of_years_momentum_beat_all": round(float(np.mean([v["momentum_top10_net"] > v["equal_weight_all"] for v in by_year.values()])), 3),
           "mean_monthly_rank_corr": round(float(p["ic"].mean()), 4),
           "avg_monthly_turnover": round(float((p["cost"] / COST).mean()), 3), "by_year": by_year,
           "runtime_min": round((time.time() - t0) / 60, 1)}
    json.dump(res, open("mom10_results.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
