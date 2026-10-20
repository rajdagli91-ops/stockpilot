"""Redesign test for StockPilot's price-based entry and hold scores, with a design / validation split.

Design set   : 200 largest NSE stocks, Nov 2023 - Aug 2025   (variants are compared here)
Validation 1 : same 200 stocks, Sep 2025 - Sep 2026          (never used for choosing)
Validation 2 : stocks ranked 201-500 by market cap, Nov 2023 - Sep 2026 (never used for choosing)

Variants are fixed in advance (a short list, chosen from reasoning about the first test, not tuned):
  OLD  current entry score (17 checks, current weights and blocking gates)
  V1   drop the 4 "stretched = wait" factors (52-week high, vs 50-day, vs 200-day, RSI) and their gates
  V2   V1 + a 12-1 month momentum check (weight 3)
  V3   V2 without the falling-trend gate (trend still counts as a factor)
Pass rule, decided before looking at validation: the chosen variant must have a positive average rank correlation
with next-month returns AND its buy rule must beat holding the whole list equally, after costs, on BOTH validation sets.
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

PRED, STEP, COST = 21, 10, 0.004
DESIGN = ("2023-11-01", "2025-08-31")
VALID1 = ("2025-09-01", "2026-12-31")
MOM_PTS = [(-40, 10), (-15, 30), (0, 45), (15, 60), (30, 72), (60, 85), (100, 92)]


def universe():
    u = requests.get("https://raw.githubusercontent.com/rajdagli91-ops/stockpilot/data/universe.json", timeout=30).json()
    rows = [dict(zip(u["fields"], r)) for r in u["rows"]]
    rows = [r for r in rows if r.get("mcap_cr") and not str(r["symbol"]).endswith(".BO")]
    s = [r["symbol"] for r in sorted(rows, key=lambda r: -r["mcap_cr"])]
    return s[:200], s[200:500]


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


def build_rows(data, bench, vix, start, end):
    cal = bench.index
    idx = [i for i in range(cal.searchsorted(pd.Timestamp(start)), len(cal) - PRED, STEP) if cal[i] <= pd.Timestamp(end)]
    rows = []
    for ai in idx:
        a = cal[ai]
        bh, v = bench.loc[:a], vix.loc[:a]
        S.CTX.clear()
        S.CTX["market"] = E.market_regime(bh["Close"], float(v["Close"].iloc[-1]) if len(v) else None)
        S.CTX["today"] = a.date()
        for s, d in data.items():
            if a not in d.index:
                continue
            i = d.index.get_loc(a)
            if i < 260 or i + PRED >= len(d):
                continue
            t = S.technicals(d.iloc[:i + 1], bh)
            if not t:
                continue
            sc = S.score_stock({}, t, [], [], None, s)
            e = sc.get("entry") or {}
            c = d["Close"]
            r = {"date": str(a.date()), "sym": s, "tech": sc.get("technical"),
                 "mom": float(c.iloc[i - 21] / c.iloc[i - 252] - 1) * 100,
                 "act21": float(c.iloc[i + 21] / c.iloc[i] - 1), "act10": float(c.iloc[i + 10] / c.iloc[i] - 1)}
            for ch in e.get("checks") or []:
                r["f_" + ch["k"]], r["g_" + ch["k"]], r["x_" + ch["k"]], r["xg_" + ch["k"]] = ch["s"], bool(ch.get("g")), ch.get("x"), bool(ch.get("xg"))
            rows.append(r)
    df = pd.DataFrame(rows)
    df["f_mom"] = df["mom"].apply(lambda x: E.interp(x, MOM_PTS))
    df["x_mom"] = df["f_mom"]
    return df


DROP = {"hi52", "vs50", "vs200", "rsi"}


def score(df, variant, side="f"):
    """Recompute entry (side f) or hold (side x) score for a variant from the stored per-check scores."""
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    keys = [k for k in E.ORDER] + ["mom"]
    for k in keys:
        w = E.CHECKS[k][2] if k in E.CHECKS else 3
        if variant != "OLD" and k in DROP:
            w = 0
        if k == "mom" and variant in ("OLD", "V1"):
            w = 0
        col = f"{side}_{k}"
        if col not in df or not w:
            continue
        has = df[col].notna()
        num += df[col].fillna(0) * w * has
        den += w * has
    s = num / den.replace(0, np.nan)
    if side == "f":
        for k in E.ORDER:
            cap = E.CHECKS[k][3]
            if cap is None or f"g_{k}" not in df:
                continue
            if variant != "OLD" and k in DROP:
                continue
            if variant == "V3" and k == "trend":
                continue
            s = np.where(df[f"g_{k}"].fillna(False), np.minimum(s, cap), s)
    else:
        broken = df.get("xg_trend", pd.Series(False, index=df.index)).fillna(False)
        s = np.where(broken, np.minimum(s, 17.5 + s / 2), s)
    return pd.Series(s, index=df.index)


def spearman(a, b):
    a, b = pd.Series(list(a)).rank(), pd.Series(list(b)).rank()
    return float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else np.nan


def evaluate(df, hz=21):
    out = {}
    comp = lambda x: float(np.prod(1 + np.asarray(x)) - 1)
    for v in ("OLD", "V1", "V2", "V3"):
        df["e"] = score(df, v, "f")
        df["h"] = score(df, v, "x")
        df["pick"] = 0.55 * df["tech"].fillna(50) + 0.45 * df["e"].fillna(50)
        per = []
        for d, g in df.groupby("date"):
            k = max(10, len(g) // 10)
            ok = g[g["e"] >= 55].sort_values("pick").tail(k)
            per.append({"ic_e": spearman(g["e"], g[f"act{hz}"]), "ic_h": spearman(g["h"], g[f"act{hz}"]),
                        "ic_pick": spearman(g["pick"], g[f"act{hz}"]),
                        "rule": ok[f"act{hz}"].mean() if len(ok) else g[f"act{hz}"].mean(), "all": g[f"act{hz}"].mean(),
                        "n_rule": len(ok),
                        "weak": g[g["h"] < 35][f"act{hz}"].mean() if (g["h"] < 35).any() else np.nan,
                        "strong": g[g["h"] >= 50][f"act{hz}"].mean() if (g["h"] >= 50).any() else np.nan})
        p = pd.DataFrame(per)
        q = p.iloc[::2] if hz == 21 else p
        t = lambda x: round(float(x.mean() / (x.std() / np.sqrt(x.count()))), 2)
        out[v] = {"entry_ic": round(float(p["ic_e"].mean()), 4), "entry_ic_t": t(p["ic_e"]),
                  "pick_ic": round(float(p["ic_pick"].mean()), 4), "pick_ic_t": t(p["ic_pick"]),
                  "hold_ic": round(float(p["ic_h"].mean()), 4), "hold_ic_t": t(p["ic_h"]),
                  "rule_after_costs": round(comp(q["rule"] - COST), 4), "hold_all_equal": round(comp(q["all"]), 4),
                  "rule_beats_equal_per_period": round(float((q["rule"] - COST - q["all"]).mean()), 4),
                  "avg_stocks_bought": round(float(p["n_rule"].mean()), 1),
                  "consider_exit_next_month": round(float(p["weak"].mean()), 4), "healthy_next_month": round(float(p["strong"].mean()), 4)}
    return out


def main():
    t0 = time.time()
    bench = yf.download("^NSEI", period="4y", auto_adjust=True, progress=False)
    bench.columns = [c[0] if isinstance(c, tuple) else c for c in bench.columns]
    vix = yf.download("^INDIAVIX", period="4y", auto_adjust=True, progress=False)
    vix.columns = [c[0] if isinstance(c, tuple) else c for c in vix.columns]
    big, mid = universe()
    dbig, dmid = load(big), load(mid)
    res = {"pass_rule": __doc__.strip().splitlines()[-2:], "cost_round_trip": COST}
    d0 = build_rows(dbig, bench, vix, *DESIGN)
    res["design_top200_2023_25"] = evaluate(d0)
    res["design_top200_2023_25_10d"] = evaluate(d0, 10)
    d1 = build_rows(dbig, bench, vix, *VALID1)
    res["valid1_top200_last12m"] = evaluate(d1)
    d2 = build_rows(dmid, bench, vix, DESIGN[0], VALID1[1])
    res["valid2_rank201_500_3y"] = evaluate(d2)
    res["stocks"] = {"top200": len(dbig), "rank201_500": len(dmid)}
    res["runtime_min"] = round((time.time() - t0) / 60, 1)
    json.dump(res, open("redesign_results.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
