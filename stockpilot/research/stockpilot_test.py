"""Out-of-sample test of StockPilot's own price-based signals, on the same dates and metrics as the Kronos test.

What can be tested honestly: everything built from price and volume (technical score, entry checklist price
factors, hold score, market regime), recomputed at each past date from data up to that date only.
What cannot: fundamentals, news, screener holdings and bulk deals; free sources only give today's values, so
testing them on past dates would leak the future (we'd already know which companies grew). They are left out.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, os.environ.get("SP_DIR", "stockpilot"))
from engine import entry as E  # noqa: E402
from engine import scoring as S  # noqa: E402

NIFTY50 = ["ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
           "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
           "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
           "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
           "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"]

TEST_START = os.environ.get("TEST_START", "2025-09-01")
PRED, STEP, COST = 21, 10, 0.004


def top_by_mcap(n=200):
    try:
        u = requests.get("https://raw.githubusercontent.com/rajdagli91-ops/stockpilot/data/universe.json", timeout=30).json()
        rows = [dict(zip(u["fields"], r)) for r in u["rows"]]
        rows = [r for r in rows if r.get("mcap_cr") and not str(r["symbol"]).endswith(".BO")]
        return [r["symbol"] for r in sorted(rows, key=lambda r: -r["mcap_cr"])[:n]]
    except Exception as e:
        print("universe fetch failed", e)
        return []


def load(symbols):
    out = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        df = yf.download([s + ".NS" for s in chunk], period="4y", auto_adjust=True, group_by="ticker", progress=False, threads=True)
        for s in chunk:
            try:
                d = df[s + ".NS"][["Close", "Volume"]].dropna()
            except KeyError:
                continue
            if len(d) > 300:
                out[s] = d
    return out


def spearman(a, b):
    a, b = pd.Series(list(a)).rank(), pd.Series(list(b)).rank()
    return float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else np.nan


def run(name, data, bench, vix, start=TEST_START):
    cal = bench.index
    start_i = cal.searchsorted(pd.Timestamp(start))
    anchors = [cal[i] for i in range(start_i, len(cal) - PRED, STEP)]
    rows = []
    for a in anchors:
        bh = bench.loc[:a]
        v = vix.loc[:a]
        S.CTX.clear()
        S.CTX["market"] = E.market_regime(bh["Close"], float(v["Close"].iloc[-1]) if len(v) else None)
        S.CTX["today"] = a.date()
        for s, d in data.items():
            if a not in d.index:
                continue
            i = d.index.get_loc(a)
            if i < 260 or i + PRED >= len(d):
                continue
            h = d.iloc[:i + 1]
            t = S.technicals(h, bh)
            if not t:
                continue
            sc = S.score_stock({}, t, [], [], None, s)
            e = sc.get("entry") or {}
            c = d["Close"]
            chk = {"f_" + c["k"]: c["s"] for c in (e.get("checks") or [])}
            chk.update({"x_" + c["k"]: c.get("x") for c in (e.get("checks") or [])})
            rows.append({**chk, "date": str(a.date()), "sym": s, "tech": sc.get("technical"), "entry": e.get("score"),
                         "hold": (e.get("hold") or {}).get("score"),
                         "mom": float(c.iloc[i - 21] / c.iloc[i - 252] - 1), "rev": float(-(c.iloc[i] / c.iloc[i - 5] - 1)),
                         "act10": float(c.iloc[i + 10] / c.iloc[i] - 1), "act21": float(c.iloc[i + 21] / c.iloc[i] - 1),
                         "regime": (S.CTX["market"] or {}).get("trend")})
    df = pd.DataFrame(rows)
    df["pick"] = 0.55 * df["tech"].fillna(50) + 0.45 * df["entry"].fillna(50)  # StockPilot ranking, price parts only
    df.to_csv(f"sp_{name}.csv", index=False)
    out = {"stocks": int(df["sym"].nunique()), "anchors": int(df["date"].nunique()), "period": [df["date"].min(), df["date"].max()], "horizons": {}}
    comp = lambda x: float(np.prod(1 + np.asarray(x)) - 1)
    for hz in (10, 21):
        per = []
        for d, g in df.groupby("date"):
            k = max(5, len(g) // 5) if len(g) > 60 else 10
            r = {"date": d, "all": g[f"act{hz}"].mean(), "regime": g["regime"].iloc[0]}
            for sig in ("tech", "entry", "hold", "pick", "mom", "rev"):
                gg = g.dropna(subset=[sig])
                r[f"ic_{sig}"] = spearman(gg[sig], gg[f"act{hz}"])
                r[f"top_{sig}"] = gg.sort_values(sig).tail(k)[f"act{hz}"].mean()
                r[f"bot_{sig}"] = gg.sort_values(sig).head(k)[f"act{hz}"].mean()
            # the actual rule: only buy when entry >= 55 (else stay in the index)
            ok = g[g["entry"] >= 55].sort_values("pick").tail(k)
            r["rule_ret"] = ok[f"act{hz}"].mean() if len(ok) else r["all"]
            # holdings flagged weak (hold < 35) vs the rest
            r["weak"] = g[g["hold"] < 35][f"act{hz}"].mean() if (g["hold"] < 35).any() else np.nan
            r["strong"] = g[g["hold"] >= 50][f"act{hz}"].mean() if (g["hold"] >= 50).any() else np.nan
            per.append(r)
        p = pd.DataFrame(per)
        q = p if hz == 10 else p.iloc[::2]
        hres = {}
        for sig in ("tech", "entry", "hold", "pick", "mom", "rev"):
            ic = p[f"ic_{sig}"]
            hres[sig] = {"mean_ic": round(float(ic.mean()), 4), "ic_tstat": round(float(ic.mean() / (ic.std() / np.sqrt(len(ic)))), 2),
                         "pct_dates_ic_pos": round(float((ic > 0).mean()), 3),
                         "top_minus_all": round(float((p[f"top_{sig}"] - p["all"]).mean()), 4),
                         "top_minus_bottom": round(float((p[f"top_{sig}"] - p[f"bot_{sig}"]).mean()), 4),
                         "top_compounded_after_costs": round(comp(q[f"top_{sig}"] - COST), 4)}
        hres["equal_weight_all"] = round(comp(q["all"]), 4)
        hres["entry_rule_compounded_after_costs"] = round(comp(q["rule_ret"] - COST), 4)
        hres["hold_weak_lt35_avg_return"] = round(float(p["weak"].mean()), 4)
        hres["hold_strong_ge50_avg_return"] = round(float(p["strong"].mean()), 4)
        hres["market_avg_return_by_regime"] = {k: round(float(v), 4) for k, v in p.groupby("regime")["all"].mean().items()}
        # each entry factor (f_) and each hold factor (x_) on its own: does a higher score lead to better returns?
        fac = {}
        for col in [c for c in df.columns if c.startswith(("f_", "x_"))]:
            ics = [spearman(g[col], g[f"act{hz}"]) for _, g in df.dropna(subset=[col]).groupby("date") if g[col].nunique() > 2 and len(g) > 10]
            ics = [x for x in ics if x == x]
            if len(ics) >= 10:
                fac[col] = {"mean_ic": round(float(np.mean(ics)), 4), "t": round(float(np.mean(ics) / (np.std(ics) / np.sqrt(len(ics)))), 2), "n": len(ics)}
        hres["factors"] = fac
        out["horizons"][str(hz)] = hres
    return out


def main():
    t0 = time.time()
    bench = yf.download("^NSEI", period="4y", auto_adjust=True, progress=False)
    bench.columns = [c[0] if isinstance(c, tuple) else c for c in bench.columns]
    vix = yf.download("^INDIAVIX", period="4y", auto_adjust=True, progress=False)
    vix.columns = [c[0] if isinstance(c, tuple) else c for c in vix.columns]
    res = {"what": "StockPilot price-based signals only (fundamentals/news not testable without look-ahead)", "cost_round_trip": COST}
    n50 = load(NIFTY50)
    res["nifty50"] = run("nifty50", n50, bench, vix)                      # same window as the Kronos test
    res["nifty50_3y"] = run("nifty50_3y", n50, bench, vix, "2023-11-01")  # longer window: more dates, less luck
    big = top_by_mcap(200)
    if big:
        b200 = load(big)
        res["top200"] = run("top200", b200, bench, vix)
        res["top200_3y"] = run("top200_3y", b200, bench, vix, "2023-11-01")
    res["runtime_min"] = round((time.time() - t0) / 60, 1)
    json.dump(res, open("sp_results.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
