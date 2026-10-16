"""Scoring model built around one goal: 20% CAGR with a 25-30% drawdown tolerance.

Each parameter scores 0-100 (None = no data). The overall score is a weighted average of
the parameters that have data; `coverage` says how much of the model had data.
Edit config/settings.json to change the hurdle, weights or thresholds.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

SETTINGS = json.loads((Path(__file__).resolve().parent.parent / "config" / "settings.json").read_text())


def interp(x, pts):
    """Piecewise-linear score from [(value, score), ...] sorted by value."""
    if x is None:
        return None
    xs, ys = zip(*pts)
    return float(np.interp(x, xs, ys))


# ---------- technical inputs
def technicals(df: pd.DataFrame, bench: pd.DataFrame | None) -> dict:
    if df is None or len(df) < 60:
        return {}
    c = df["Close"]
    last = float(c.iloc[-1])
    dma50 = float(c.rolling(50).mean().iloc[-1])
    dma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float(100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])) if loss.iloc[-1] > 0 else 100.0
    look = min(len(c) - 1, 252)
    ret12 = (last / float(c.iloc[-look - 1]) - 1) * 100
    rel = None
    if bench is not None and len(bench) > look:
        b = bench["Close"]
        rel = ret12 - (float(b.iloc[-1]) / float(b.iloc[-look - 1]) - 1) * 100
    hi52 = float(c.iloc[-look:].max())
    vol = float(np.log(c).diff().iloc[-look:].std() * np.sqrt(252) * 100)
    return {"price": last, "dma50": dma50, "dma200": dma200, "rsi": rsi, "ret_12m": ret12, "rel_12m": rel,
            "from_52w_high": (last / hi52 - 1) * 100, "volatility": vol,
            "vs_200dma": (last / dma200 - 1) * 100 if dma200 else None}


# ---------- 20% CAGR hurdle: expected annual return over the next ~5 years
def expected_return(f: dict) -> float | None:
    growth = f.get("profit_cagr")
    if growth is None:
        growth = f.get("rev_cagr")
    pe = f.get("pe")
    if growth is None:
        return None
    g = max(min(growth, 30.0), -10.0)  # cap: past growth rarely repeats above 30%
    dy = f.get("dividend_yield") or 0.0
    rerate = 0.0
    if pe and pe > 0:
        fair = max(12.0, min(50.0, max(g, 0) * 1.5))
        # P/E drifts to fair value over 5 years: annual drag/boost
        rerate = max(-10.0, min(10.0, ((fair / pe) ** (1 / 5) - 1) * 100))
    elif f.get("loss_making"):
        return -5.0
    return min(g + dy + rerate, 35.0)  # anything above 35%/yr is noise, not a forecast


def score_stock(f: dict, t: dict, news: list, filings: list, macro: dict | None = None) -> dict:
    s = SETTINGS
    fin = (f.get("sector") or "").lower().startswith("financial")
    er = expected_return(f)
    peg = None
    if f.get("pe") and f["pe"] > 0:
        g = f.get("profit_cagr") or f.get("rev_cagr")
        peg = f["pe"] / max(g, 1) if g is not None else None
    tone = [n["tone"] for n in news]
    news_s = float(np.clip(np.mean(tone), -2, 2)) if tone else None
    flags = sum(1 for x in filings if x.get("red_flag")) + sum(
        1 for n in news if any(k in n["title"].lower() for k in ("fraud", "default", "insolvency", "auditor resign")))
    trend = None
    if t.get("vs_200dma") is not None:
        trend = interp(t["vs_200dma"], [(-25, 5), (-10, 25), (0, 50), (10, 80), (20, 90), (40, 75)])
        if t["dma200"] and t["dma50"] > t["dma200"]:
            trend = min(100, trend + 10)

    params = {
        # ---- fundamentals
        "expected_return": ("Expected return (target 15–18%/yr)", "fundamental",
                            interp(er, [(0, 0), (8, 15), (12, 35), (15, 60), (18, 80), (24, 100)]), er, "%/yr"),
        "profit_growth": ("Profit growth (3y CAGR)", "fundamental",
                          interp(f.get("profit_cagr"), [(-10, 0), (0, 15), (10, 50), (20, 85), (28, 100)]), f.get("profit_cagr"), "%"),
        "revenue_growth": ("Revenue growth (3y CAGR)", "fundamental",
                           interp(f.get("rev_cagr"), [(-5, 0), (0, 15), (10, 55), (18, 85), (25, 100)]), f.get("rev_cagr"), "%"),
        "roe": ("Return on equity", "fundamental",
                interp(f.get("roe"), [(0, 0), (8, 25), (12, 50), (18, 80), (25, 100)]), f.get("roe"), "%"),
        "debt": ("Debt / equity", "fundamental",
                 None if fin else interp(f.get("debt_equity"), [(0, 100), (0.5, 75), (1, 45), (2, 10), (3, 0)]), f.get("debt_equity"), "x"),
        "valuation": ("Valuation (P/E ÷ growth)", "fundamental",
                      10.0 if f.get("loss_making") else interp(peg, [(0.5, 100), (1, 75), (1.5, 50), (2.5, 20), (4, 5)]), peg, "x"),
        "cash_quality": ("Cash conversion (OCF ÷ profit)", "fundamental",
                         None if fin else interp(f.get("cash_conversion"), [(0, 0), (0.3, 20), (0.7, 60), (1.0, 100)]), f.get("cash_conversion"), "x"),
        # ---- technicals
        "trend": ("Trend (vs 200-day avg)", "technical", trend, t.get("vs_200dma"), "%"),
        "momentum": ("12m return vs Nifty", "technical",
                     interp(t.get("rel_12m"), [(-40, 0), (-15, 25), (0, 50), (15, 75), (35, 100)]), t.get("rel_12m"), "%"),
        "rsi": ("RSI (14)", "technical",
                interp(t.get("rsi"), [(20, 60), (30, 75), (45, 65), (60, 55), (70, 35), (80, 15)]), t.get("rsi"), ""),
        "volatility": ("Volatility (fits 25–30% drawdown?)", "technical",
                       interp(t.get("volatility"), [(18, 95), (25, 80), (32, 60), (40, 40), (55, 15), (70, 5)]), t.get("volatility"), "%"),
        # ---- news & filings
        "news": ("News tone (30 days)", "news", None if news_s is None else 50 + news_s * 25, news_s, ""),
        "red_flags": ("Red flags in filings/news", "news", max(0, 100 - 35 * flags) if (news or filings) else None, flags, ""),
        # ---- global markets (how overnight / 5-day world moves hit this stock's sector)
        "global": ("Global markets effect on sector", "market", (macro or {}).get("score"), (macro or {}).get("score"), ""),
    }
    w = s["weights"]
    total = sum(w.values())
    have = {k: v for k, v in params.items() if v[2] is not None}
    covered = sum(w[k] for k in have)
    overall = sum(v[2] * w[k] for k, v in have.items()) / covered if covered else None
    coverage = covered / total

    def group_avg(gname):
        xs = [(v[2], w[k]) for k, v in have.items() if v[1] == gname]
        return sum(a * b for a, b in xs) / sum(b for _, b in xs) if xs else None

    fund, tech, nws, mkt = group_avg("fundamental"), group_avg("technical"), group_avg("news"), group_avg("market")
    th = s["thresholds"]
    hz = s["horizons"]
    ers = params["expected_return"][2]
    red = flags > 0
    # ---- short-term setup: room to run to a technical target, with a stop-loss
    st = None
    price = t.get("price")
    if price and t.get("dma50") and t.get("from_52w_high") is not None:
        upside = -t["from_52w_high"]  # % to the 52-week high
        if upside < 3 and t.get("volatility"):  # at/near the high: a breakout, use a 3-month 1-sigma move
            upside = t["volatility"] / 2
        stop = max(t["dma50"] * 0.98, price * (1 - hz["short_term_max_stop_pct"] / 100))
        if stop >= price:
            stop = price * (1 - hz["short_term_max_stop_pct"] / 100)
        risk = (price - stop) / price * 100
        st = {"upside": round(upside, 1), "target": round(price * (1 + upside / 100), 2), "stop": round(stop, 2),
              "risk": round(risk, 1), "reward_risk": round(upside / risk, 1) if risk > 0 else None}
    uptrend = bool(t.get("dma200") and price and t.get("dma50") and price > t["dma50"] > t["dma200"])
    rsi_ok = t.get("rsi") is not None and 45 <= t["rsi"] <= 70
    long_ok = (overall is not None and overall >= th["buy"] and er is not None and er >= hz["long_term_min_cagr"]
               and (fund or 0) >= 55 and not red)
    short_ok = (st is not None and uptrend and rsi_ok and not red and (tech or 0) >= 65
                and st["upside"] >= hz["short_term_min_return"] and (st["reward_risk"] or 0) >= 1.5)
    if overall is None:
        signal, horizon = "NO DATA", None
    elif long_ok:
        signal = "BUY"
        strong = er >= hz["long_term_good_cagr"]
        horizon = {"term": "Long term", "period": "3+ years", "expected": round(er, 1),
                   "note": f"Expected ~{er:.0f}%/yr" + (" (strong)" if strong else "") + ". Review after each quarterly result."}
        if t.get("rsi") and t["rsi"] > 70:
            horizon["note"] += " Overbought now: buy in 2–3 tranches or on a dip."
    elif short_ok:
        signal = "BUY"
        horizon = {"term": "Short term", "period": "1–3 months", "expected": st["upside"], "target": st["target"], "stop": st["stop"],
                   "note": f"Trade, not an investment: target ₹{st['target']:,.0f} (+{st['upside']:.0f}%), stop-loss ₹{st['stop']:,.0f} (−{st['risk']:.0f}%)."}
    elif overall < th["sell"] or red or (ers is not None and ers < th["sell_hurdle_score"] and (trend or 50) < 40):
        signal = "SELL"
        oversold = t.get("rsi") is not None and t["rsi"] < 30
        horizon = {"term": "Exit", "period": "on a bounce" if oversold else "now",
                   "note": "Oversold right now (RSI < 30): sell into the next bounce rather than at the low." if oversold
                   else "Unlikely to reach your return targets; free the money for better ideas."}
    else:
        signal = "HOLD"
        if er is not None and er >= hz["long_term_min_cagr"]:
            horizon = {"term": "Long term", "period": "hold 3+ years", "expected": round(er, 1),
                       "note": "Business is on track for your target; the price/trend isn't ready for fresh buying. Don't add yet."}
        elif uptrend and (tech or 0) >= 60:
            horizon = {"term": "Short term", "period": "next 1–3 months", "stop": st["stop"] if st else None,
                       "note": "Momentum is carrying it, but fundamentals miss your target. Hold with a stop-loss" +
                               (f" at ₹{st['stop']:,.0f}." if st else ".")}
        else:
            horizon = {"term": "Review", "period": "in 3–6 months", "expected": None if er is None else round(er, 1),
                       "note": f"Expected return {'~' + format(er, '.0f') + '%/yr' if er is not None else 'unclear'} is below your {hz['long_term_min_cagr']}% target. Re-check after next results."}
    if coverage < 0.55:
        conf = "Low"
    elif coverage >= 0.8 and fund is not None and tech is not None and abs(fund - tech) < 20:
        conf = "High"
    else:
        conf = "Medium"
    # 'no red flags' is the normal state, so it never counts as a strength
    ranked = sorted(((k, v) for k, v in have.items() if not (k == 'red_flags' and v[2] >= 100)), key=lambda kv: kv[1][2])
    return {
        "overall": round(overall, 1) if overall is not None else None,
        "fundamental": round(fund, 1) if fund is not None else None,
        "technical": round(tech, 1) if tech is not None else None,
        "news": round(nws, 1) if nws is not None else None,
        "signal": signal, "confidence": conf, "coverage": round(coverage, 2),
        "expected_return": round(er, 1) if er is not None else None,
        "horizon": horizon, "short_term": st,
        "strengths": [v[0] for _, v in ranked[::-1][:2]],
        "weaknesses": [v[0] for _, v in ranked[:2]],
        "points": action_points(params, signal, er, t, flags, (macro or {}).get("why") or [], horizon),
        "params": [{"key": k, "label": v[0], "group": v[1], "score": None if v[2] is None else round(v[2]),
                    "value": None if v[3] is None else round(float(v[3]), 2), "unit": v[4], "weight": w[k]}
                   for k, v in params.items()],
    }


# ---------- plain-language reasons behind BUY / HOLD / EXIT
def _f(x, d=0):
    return f"{x:.{d}f}"


GOOD = {
    "expected_return": lambda v: f"Expected return about {_f(v)}%/yr meets your 15–18% long-term target",
    "profit_growth": lambda v: f"Profits have compounded {_f(v)}% a year over 3 years",
    "revenue_growth": lambda v: f"Revenue growing {_f(v)}% a year",
    "roe": lambda v: f"High return on equity ({_f(v)}%): the business earns well on its capital",
    "debt": lambda v: f"Low debt (debt/equity {_f(v, 2)})",
    "valuation": lambda v: f"Reasonably priced for its growth (P/E ÷ growth {_f(v, 1)})",
    "cash_quality": lambda v: f"Profits are backed by cash (operating cash {_f(v, 1)}× profit)",
    "trend": lambda v: f"Uptrend: {_f(v)}% above its 200-day average",
    "momentum": lambda v: f"Beat the Nifty by {_f(v)} points over 12 months",
    "rsi": lambda v: f"Not overheated (RSI {_f(v)})",
    "volatility": lambda v: f"Steady stock ({_f(v)}% annual volatility) fits your drawdown limit",
    "news": lambda v: "Recent news flow is positive",
    "global": lambda v: "Global markets are a tailwind for this sector",
}
BAD = {
    "expected_return": lambda v: f"Expected return only about {_f(v)}%/yr, below your 15% long-term target",
    "profit_growth": lambda v: f"Profit growth weak ({_f(v)}% a year over 3 years)",
    "revenue_growth": lambda v: f"Revenue growth weak ({_f(v)}% a year)",
    "roe": lambda v: f"Low return on equity ({_f(v)}%)",
    "debt": lambda v: f"High debt (debt/equity {_f(v, 2)})",
    "valuation": lambda v: f"Expensive for its growth (P/E ÷ growth {_f(v, 1)})",
    "cash_quality": lambda v: f"Profits not turning into cash (operating cash {_f(v, 1)}× profit)",
    "trend": lambda v: f"Downtrend: {_f(abs(v))}% below its 200-day average",
    "momentum": lambda v: f"Lagged the Nifty by {_f(abs(v))} points over 12 months",
    "rsi": lambda v: f"Overbought short term (RSI {_f(v)}); wait for a dip to add",
    "volatility": lambda v: f"Very volatile ({_f(v)}%/yr); falls could exceed your 25–30% limit",
    "news": lambda v: "Recent news flow is negative",
    "red_flags": lambda v: f"{int(v)} red flag(s) in recent filings or news; read them before acting",
    "global": lambda v: "Global markets are a headwind for this sector",
}


def action_points(params, signal, er, t, flags, macro_why, horizon=None):
    have = [(k, v) for k, v in params.items() if v[2] is not None and v[3] is not None]
    good = sorted([(k, v) for k, v in have if v[2] >= 70 and k in GOOD], key=lambda kv: -kv[1][2] * SETTINGS["weights"].get(kv[0], 1))
    bad = sorted([(k, v) for k, v in have if v[2] <= 35 and k in BAD], key=lambda kv: kv[1][2] / max(SETTINGS["weights"].get(kv[0], 1), 1))
    pro = [GOOD[k](v[3]) for k, v in good[:4]]
    con = [BAD[k](v[3]) for k, v in bad[:4]]
    if macro_why:
        (pro if (params["global"][2] or 50) >= 50 else con).append("Global: " + "; ".join(macro_why[:2]))
    h = horizon or {}
    label = {"BUY": "Buy", "SELL": "Exit", "HOLD": "Hold"}.get(signal, "No call")
    verdict = f"{label} · {h.get('term', '')} ({h.get('period', '')})" if h else label
    if h.get("note"):
        verdict += f": {h['note']}"
    # for stocks you don't own, Hold/Exit make no sense: say whether it is a buy or not
    if signal == "BUY":
        verdict_new = verdict
    elif signal == "SELL":
        verdict_new = "Not a buy: fails your return targets" + (f" ({con[0]})" if con else "")
    elif signal == "HOLD":
        verdict_new = "Not a buy yet" + (f": {h['note']}" if h.get("note") else "")
    else:
        verdict_new = "Not enough data to call"
    return {"verdict": verdict, "verdict_new": verdict_new, "for": pro, "against": con}
