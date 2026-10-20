"""Walk-forward test: can price, global-market, oil, gold, rupee and rates data predict next-month moves?

Fixed before seeing results (no tuning on the test period):
  * Universe A = 200 largest NSE stocks, B = ranks 201-500 (today's lists: some survivorship bias).
  * Every 10 trading days ("anchor") from TEST_START: features use data up to that day only.
  * Model learns only from earlier anchors whose 21-day outcome was already known on that day (expanding window).
  * Primary model: ridge regression on per-date rank-scaled features (simple, hard to overfit).
    Secondary: gradient boosting (to see whether a flexible model finds more; more prone to overfit).
  * Q1 stock picking: rank correlation, "beats the median stock" accuracy, top-10% basket vs equal weight after costs.
  * Q2 market timing: logistic model on global/macro features -> Nifty up/down next month vs always guessing "up".
Pass bar: Q1 basket beats equal weight after costs on BOTH universes; Q2 accuracy beats the always-up base rate.
Fundamentals and news are not included: free sources have no point-in-time history for them.
"""
import json
import os
import time
import warnings

import numpy as np
import pandas as pd
import requests
import yfinance as yf
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge

warnings.filterwarnings("ignore")
TEST_START = os.environ.get("TEST_START", "2023-11-01")
H, STEP, COST = 21, 10, 0.004
MACRO = {"nifty": "^NSEI", "ivix": "^INDIAVIX", "spx": "^GSPC", "usvix": "^VIX", "crude": "BZ=F", "gold": "GC=F",
         "dxy": "DX-Y.NYB", "inr": "INR=X", "us10y": "^TNX"}


def universe():
    u = requests.get("https://raw.githubusercontent.com/rajdagli91-ops/stockpilot/data/universe.json", timeout=30).json()
    rows = [dict(zip(u["fields"], r)) for r in u["rows"]]
    rows = [r for r in rows if r.get("mcap_cr") and not str(r["symbol"]).endswith(".BO")]
    s = [r["symbol"] for r in sorted(rows, key=lambda r: -r["mcap_cr"])]
    return s[:200], s[200:500]


def dl(tickers, period="6y"):
    out = {}
    for i in range(0, len(tickers), 100):
        ch = tickers[i:i + 100]
        df = yf.download(ch, period=period, auto_adjust=True, group_by="ticker", progress=False, threads=True)
        for t in ch:
            try:
                d = df[t][["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(d) > 400:
                d.index = d.index.tz_localize(None) if getattr(d.index, "tz", None) else d.index
                out[t] = d
    return out


def macro_frame():
    m = dl(list(MACRO.values()))
    c = pd.DataFrame({k: m[v]["Close"] for k, v in MACRO.items() if v in m}).ffill()
    f = pd.DataFrame(index=c.index)
    for k in c:
        if k in ("us10y",):
            f[f"{k}_chg21"] = c[k] - c[k].shift(21)
        elif k in ("ivix", "usvix"):
            f[f"{k}_lvl"] = c[k]
            f[f"{k}_chg21"] = c[k] / c[k].shift(21) - 1
        else:
            f[f"{k}_r21"] = c[k] / c[k].shift(21) - 1
            f[f"{k}_r63"] = c[k] / c[k].shift(63) - 1
    n = c["nifty"]
    f["nifty_vs200"] = n / n.rolling(200).mean() - 1
    f["nifty_vs50"] = n / n.rolling(50).mean() - 1
    rets = c.pct_change()
    return f, rets


def stock_features(d, mrets):
    c, v = d["Close"], d["Volume"]
    r = c.pct_change()
    f = pd.DataFrame(index=d.index)
    f["mom12_1"] = c.shift(21) / c.shift(252) - 1
    f["mom6_1"] = c.shift(21) / c.shift(126) - 1
    f["ret1m"] = c / c.shift(21) - 1
    f["ret1w"] = c / c.shift(5) - 1
    f["vs50"] = c / c.rolling(50).mean() - 1
    f["vs200"] = c / c.rolling(200).mean() - 1
    f["hi52"] = c / c.rolling(252).max() - 1
    up = r.clip(lower=0).ewm(alpha=1 / 14).mean()
    dn = (-r.clip(upper=0)).ewm(alpha=1 / 14).mean()
    f["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    f["vol60"] = r.rolling(60).std() * np.sqrt(252)
    f["volratio"] = r.rolling(20).std() / r.rolling(120).std()
    sgn = np.sign(r)
    f["updown_vol"] = (v * (sgn > 0)).rolling(50).sum() / (v * (sgn < 0)).rolling(50).sum().replace(0, np.nan)
    f["turnover_trend"] = (c * v).rolling(20).mean() / (c * v).rolling(120).mean()
    mr = mrets.reindex(d.index)
    for k in ("nifty", "crude", "gold", "inr", "spx"):
        if k in mr:
            cov = r.rolling(250).cov(mr[k])
            var = mr[k].rolling(250).var()
            f[f"beta_{k}"] = cov / var
    f["fwd"] = c.shift(-H) / c - 1
    return f


def ranks(g, cols):
    return g[cols].rank(pct=True) - 0.5


def comp(x):
    return float(np.prod(1 + np.asarray(x, dtype=float)) - 1)


def run_stocks(name, data, mf, mrets, cal):
    frames = []
    for t, d in data.items():
        f = stock_features(d, mrets)
        f["sym"] = t
        frames.append(f)
    allf = pd.concat(frames)
    anchors = cal[252 + 30::STEP]  # NSE trading days
    allf = allf[allf.index.isin(anchors)].copy()
    mfa = mf.reindex(anchors, method="ffill")
    # stock x macro interactions: e.g. oil-sensitive stock when oil has just jumped
    for k, mk in (("crude", "crude_r21"), ("gold", "gold_r21"), ("inr", "inr_r21"), ("spx", "spx_r21"), ("nifty", "nifty_r21")):
        if f"beta_{k}" in allf and mk in mfa:
            allf[f"x_{k}"] = allf[f"beta_{k}"] * allf.index.map(mfa[mk])
    feats = [c for c in allf.columns if c not in ("sym", "fwd")]
    allf = allf.reset_index().rename(columns={"index": "date", "Date": "date"})
    allf = allf.dropna(subset=["fwd", "mom12_1"])
    for c in feats:
        allf[c] = allf.groupby("date")[c].transform(lambda s: s.rank(pct=True) - 0.5).fillna(0)
    allf["y"] = allf.groupby("date")["fwd"].transform(lambda s: s.rank(pct=True) - 0.5)
    dates = sorted(allf["date"].unique())
    test_dates = [d for d in dates if d >= pd.Timestamp(TEST_START)]
    per, coefs = [], []
    for d in test_dates:
        known = [x for x in dates if x + pd.Timedelta(days=int(H * 1.5)) <= d]  # outcome already known on day d
        tr = allf[allf["date"].isin(known)]
        te = allf[allf["date"] == d]
        if len(tr) < 2000 or len(te) < 30:
            continue
        ridge = Ridge(alpha=10.0).fit(tr[feats], tr["y"])
        gbm = HistGradientBoostingRegressor(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=200).fit(tr[feats], tr["y"])
        coefs.append(dict(zip(feats, ridge.coef_)))
        te = te.assign(p_ridge=ridge.predict(te[feats]), p_gbm=gbm.predict(te[feats]))
        k = max(10, len(te) // 10)
        row = {"date": str(d.date()), "all": te["fwd"].mean(), "n": len(te)}
        for m in ("p_ridge", "p_gbm", "mom12_1"):
            row[f"ic_{m}"] = te[[m, "fwd"]].corr(method="spearman").iloc[0, 1]
            med = te["fwd"].median()
            row[f"acc_{m}"] = float(((te[m] > te[m].median()) == (te["fwd"] > med)).mean())
            top = te.sort_values(m).tail(k)
            row[f"top_{m}"] = top["fwd"].mean()
            row[f"topacc_{m}"] = float((top["fwd"] > med).mean())  # how often a top pick beat the median stock
        per.append(row)
    p = pd.DataFrame(per)
    q = p.iloc[::2]  # non-overlapping months for compounding
    res = {"stocks": int(allf["sym"].nunique()), "test_dates": len(p), "period": [p["date"].min(), p["date"].max()],
           "equal_weight_all": round(comp(q["all"]), 4)}
    for m, lab in (("p_ridge", "ridge_model"), ("p_gbm", "boosting_model"), ("mom12_1", "plain_momentum")):
        ic = p[f"ic_{m}"]
        res[lab] = {"mean_rank_corr": round(float(ic.mean()), 4), "t_stat": round(float(ic.mean() / (ic.std() / np.sqrt(len(ic)))), 2),
                    "beats_median_accuracy_all_stocks": round(float(p[f"acc_{m}"].mean()), 3),
                    "top10pct_beat_median_rate": round(float(p[f"topacc_{m}"].mean()), 3),
                    "top10pct_basket_after_costs": round(comp(q[f"top_{m}"] - COST), 4),
                    "beats_equal_weight": bool(comp(q[f"top_{m}"] - COST) > comp(q["all"]))}
    cf = pd.DataFrame(coefs).mean().sort_values()
    res["ridge_most_negative"] = {k: round(float(v), 4) for k, v in cf.head(5).items()}
    res["ridge_most_positive"] = {k: round(float(v), 4) for k, v in cf.tail(5).items()}
    p.to_csv(f"wf_{name}.csv", index=False)
    return res


def run_timing(mf, nifty_close):
    df = mf.copy()
    df["fwd"] = nifty_close.reindex(df.index).shift(-H) / nifty_close.reindex(df.index) - 1
    df = df.dropna()
    anchors = df.index[::STEP]
    df = df.loc[anchors]
    feats = [c for c in df.columns if c != "fwd"]
    per = []
    for d in [x for x in df.index if x >= pd.Timestamp(TEST_START)]:
        tr = df[df.index + pd.Timedelta(days=int(H * 1.5)) <= d]
        if len(tr) < 40:
            continue
        mu, sd = tr[feats].mean(), tr[feats].std().replace(0, 1)
        lr = LogisticRegression(C=0.3, max_iter=2000).fit((tr[feats] - mu) / sd, (tr["fwd"] > 0).astype(int))
        prob = float(lr.predict_proba(((df.loc[[d], feats] - mu) / sd))[0, 1])
        per.append({"date": str(d.date()), "prob_up": prob, "up": bool(df.loc[d, "fwd"] > 0), "fwd": float(df.loc[d, "fwd"]),
                    "base_up_so_far": float((tr["fwd"] > 0).mean())})
    p = pd.DataFrame(per)
    p["pred_up"] = p["prob_up"] > 0.5
    conf = p[(p["prob_up"] > 0.65) | (p["prob_up"] < 0.35)]
    q = p.iloc[::2]
    return {"test_dates": len(p), "model_accuracy": round(float((p["pred_up"] == p["up"]).mean()), 3),
            "always_up_accuracy": round(float(p["up"].mean()), 3),
            "confident_calls": len(conf), "confident_accuracy": round(float((conf["pred_up"] == conf["up"]).mean()), 3) if len(conf) else None,
            "nifty_buy_hold": round(comp(q["fwd"]), 4),
            "in_when_model_says_up_else_cash": round(comp(np.where(q["pred_up"], q["fwd"], 0.0)), 4)}


def main():
    t0 = time.time()
    mf, mrets = macro_frame()
    nifty = dl(["^NSEI"])["^NSEI"]["Close"]
    big, mid = universe()
    res = {"plan": __doc__.strip().splitlines(), "cost_round_trip": COST}
    mf_n = mf.reindex(nifty.index, method="ffill")
    res["Q1_top200"] = run_stocks("top200", dl([s + ".NS" for s in big]), mf_n, mrets, nifty.index)
    res["Q1_rank201_500"] = run_stocks("rank201_500", dl([s + ".NS" for s in mid]), mf_n, mrets, nifty.index)
    res["Q2_market_timing"] = run_timing(mf_n, nifty)
    res["runtime_min"] = round((time.time() - t0) / 60, 1)
    json.dump(res, open("wf_results.json", "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
