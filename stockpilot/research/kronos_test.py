"""Out-of-sample test of Kronos (open-source candlestick foundation model) on NSE stocks.

Question: does Kronos's price forecast help pick Indian stocks, after costs, better than simple rules?
- Universe: Nifty 50 stocks (daily candles from Yahoo Finance).
- Period: anchors every 10 trading days from TEST_START (after Kronos was published, so not in its training data).
- At each anchor: Kronos sees the last 400 daily candles and forecasts the next 21; we compare the forecast
  10-day and 21-day returns with what actually happened.
- Baselines on the same dates: 12-1 month momentum, 1-week reversal.
- Also checks whether Kronos's forecast candle ranges predict actual volatility (useful as a risk input).
Writes results.json and results.md.
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.environ.get("KRONOS_DIR", "Kronos"))
from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

NIFTY50 = ["ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
           "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
           "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
           "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
           "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"]
TEST_START = os.environ.get("TEST_START", "2025-09-01")
CTX, PRED, STEP = 400, 21, 10
MODEL = os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-small")
SAMPLES = int(os.environ.get("SAMPLES", "5"))
COST = 0.004  # round trip: brokerage + STT + slippage, conservative for large caps


def load():
    out = {}
    df = yf.download([s + ".NS" for s in NIFTY50], period="4y", interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=True)
    for s in NIFTY50:
        try:
            d = df[s + ".NS"][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except KeyError:
            continue
        d.columns = ["open", "high", "low", "close", "volume"]
        d = d[d["close"] > 0]
        if len(d) > CTX + 60:
            out[s] = d
    return out


def spearman(a, b):
    a, b = pd.Series(a).rank(), pd.Series(b).rank()
    return float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else np.nan


def main():
    t0 = time.time()
    data = load()
    print(f"loaded {len(data)} stocks")
    cal = sorted(set().union(*[set(d.index) for d in data.values()]))
    cal = pd.DatetimeIndex(cal)
    start_i = cal.searchsorted(pd.Timestamp(TEST_START))
    anchors = [cal[i] for i in range(start_i, len(cal) - PRED, STEP)]
    print(f"{len(anchors)} anchors from {anchors[0].date()} to {anchors[-1].date()}")

    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    mdl = Kronos.from_pretrained(MODEL)
    pred = KronosPredictor(mdl, tok, device="cpu", max_context=512)

    rows = []
    for k, a in enumerate(anchors):
        dfs, xts, yts, syms, last, fut, feats = [], [], [], [], [], [], []
        for s, d in data.items():
            if a not in d.index:
                continue
            i = d.index.get_loc(a)
            if i < CTX or i + PRED >= len(d):
                continue
            ctx = d.iloc[i - CTX + 1:i + 1]
            nxt = d.iloc[i + 1:i + 1 + PRED]
            dfs.append(ctx.reset_index(drop=True))
            xts.append(pd.Series(ctx.index))
            yts.append(pd.Series(nxt.index))
            syms.append(s)
            last.append(float(ctx["close"].iloc[-1]))
            fut.append(nxt)
            c = d["close"]
            feats.append({"mom": float(c.iloc[i - 21] / c.iloc[i - 252] - 1) if i >= 252 else np.nan,
                          "rev": float(-(c.iloc[i] / c.iloc[i - 5] - 1))})
        if len(dfs) < 20:
            continue
        ps = pred.predict_batch(dfs, xts, yts, pred_len=PRED, T=1.0, top_p=0.9, sample_count=SAMPLES, verbose=False)
        for s, p, lc, nxt, f in zip(syms, ps, last, fut, feats):
            r = {"date": str(a.date()), "sym": s, **f}
            for h in (10, 21):
                r[f"pred{h}"] = float(p["close"].iloc[h - 1] / lc - 1)
                r[f"act{h}"] = float(nxt["close"].iloc[h - 1] / lc - 1)
            r["pred_range"] = float(((p["high"] - p["low"]) / p["close"]).clip(lower=0).mean())
            r["act_range"] = float(((nxt["high"] - nxt["low"]) / nxt["close"]).mean())
            r["past_range"] = float(((dfs[syms.index(s)]["high"] - dfs[syms.index(s)]["low"]) / dfs[syms.index(s)]["close"]).iloc[-21:].mean())
            rows.append(r)
        print(f"anchor {k + 1}/{len(anchors)} {a.date()} done, {time.time() - t0:.0f}s")

    df = pd.DataFrame(rows)
    df.to_csv("predictions.csv", index=False)
    res = {"model": MODEL, "samples": SAMPLES, "stocks": int(df["sym"].nunique()), "anchors": int(df["date"].nunique()),
           "period": [df["date"].min(), df["date"].max()], "predictions": len(df), "cost_round_trip": COST, "horizons": {}}
    for h in (10, 21):
        per = []
        for d, g in df.groupby("date"):
            g = g.dropna(subset=[f"pred{h}", f"act{h}"])
            ic = spearman(g[f"pred{h}"], g[f"act{h}"])
            icm = spearman(g["mom"], g[f"act{h}"]) if g["mom"].notna().sum() > 10 else np.nan
            icr = spearman(g["rev"], g[f"act{h}"])
            g = g.sort_values(f"pred{h}")
            top, bot = g.tail(10), g.head(10)
            per.append({"date": d, "ic": ic, "ic_mom": icm, "ic_rev": icr,
                        "top": top[f"act{h}"].mean(), "bot": bot[f"act{h}"].mean(), "all": g[f"act{h}"].mean(),
                        "top_mom": g.sort_values("mom").tail(10)[f"act{h}"].mean(),
                        "hit": float((np.sign(g[f"pred{h}"]) == np.sign(g[f"act{h}"])).mean()),
                        "hit_up": float((g[f"act{h}"] > 0).mean())})
        p = pd.DataFrame(per)
        # non-overlapping periods only for compounding (10-day: every anchor; 21-day: every 2nd anchor)
        q = p if h == 10 else p.iloc[::2]
        comp = lambda x: float(np.prod(1 + x) - 1)
        res["horizons"][str(h)] = {
            "mean_ic": round(float(p["ic"].mean()), 4), "ic_tstat": round(float(p["ic"].mean() / (p["ic"].std() / np.sqrt(len(p)))), 2),
            "pct_dates_ic_pos": round(float((p["ic"] > 0).mean()), 3),
            "mean_ic_momentum": round(float(p["ic_mom"].mean()), 4), "mean_ic_reversal": round(float(p["ic_rev"].mean()), 4),
            "direction_hit_rate": round(float(p["hit"].mean()), 3), "share_of_moves_up": round(float(p["hit_up"].mean()), 3),
            "top10_minus_all_per_period": round(float((p["top"] - p["all"]).mean()), 4),
            "top10_minus_bottom10_per_period": round(float((p["top"] - p["bot"]).mean()), 4),
            "compounded": {"kronos_top10_after_costs": round(comp(q["top"] - COST), 4), "equal_weight_all_50": round(comp(q["all"]), 4),
                           "momentum_top10_after_costs": round(comp(q["top_mom"] - COST), 4)},
        }
    res["volatility"] = {
        "rank_corr_pred_range_vs_actual": round(float(df.groupby("date").apply(lambda g: spearman(g["pred_range"], g["act_range"])).mean()), 3),
        "rank_corr_past_range_vs_actual": round(float(df.groupby("date").apply(lambda g: spearman(g["past_range"], g["act_range"])).mean()), 3)}
    res["runtime_min"] = round((time.time() - t0) / 60, 1)
    json.dump(res, open("results.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
