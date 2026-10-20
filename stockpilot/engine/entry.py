"""Entry checklist: is today a sensible time to start buying this stock?

Every check gives a score 0-100 (None = no data), a status (good / ok / bad / na), the value seen and a
plain-English reason. The entry score is the weighted average of the checks the user has switched on;
'gate' checks can also cap it (a stock at its 52-week high or with results next week never scores as a
top pick, however good everything else looks). The website recomputes the score from the same numbers
when the user switches checks on or off, so the definitions below are shipped to it in universe.json.
"""
import datetime as dt
import statistics

import numpy as np

# key: (label, group, default weight, gate cap or None)
CHECKS = {
    "trend":     ("Trend: price above 200-day avg and 50-day above 200-day", "Price", 1, 45),
    # vs50 / vs200 / rsi / hi52 are shown for information but count 0 by default: tested on 3 years of NSE data
    # (research/redesign_test.py) "stretched, so wait" made picks WORSE; stocks near highs kept rising.
    "mom":       ("Momentum: 12-month return (excluding the last month)", "Price", 3, None),
    "vs50":      ("Price vs 50-day average (short-term stretch)", "Price", 0, None),
    "vs200":     ("Price vs 200-day average (long-term stretch)", "Price", 0, None),
    "rsi":       ("RSI: momentum not overheated", "Price", 0, None),
    "hi52":      ("Distance from 52-week high", "Price", 0, None),
    "risk":      ("Risk to stop-loss (50-day avg / 20-day low)", "Price", 2, None),
    "squeeze":   ("Volatility: calm now vs its usual swings", "Price", 1, None),
    "volume":    ("Volume: buying vs selling pressure (50 days)", "Money flow", 2, None),
    "delivery":  ("Delivery %: shares actually taken home (10 days)", "Money flow", 1, None),
    "big_money": ("Big investors: FII/DII holding change + bulk/block deals", "Money flow", 1, None),
    "holding":   ("Promoter holding change (last quarter)", "Money flow", 1, None),
    "peg":       ("P/E vs profit growth (PEG)", "Valuation", 3, None),
    "pe_hist":   ("P/E vs its own last 3-4 years", "Valuation", 2, None),
    "pe_sector": ("P/E vs listed sector peers", "Valuation", 2, None),
    "quarter":   ("Latest quarter vs long-term growth", "Business", 2, None),
    "results":   ("Results date (avoid buying just before)", "Timing", 1, 54),
    "market":    ("Market: Nifty trend + India VIX", "Timing", 2, None),
}
ORDER = list(CHECKS)
MIN_ENTRY = 55


def interp(x, pts):
    if x is None:
        return None
    xs, ys = zip(*pts)
    return float(np.interp(x, xs, ys))


def _status(s):
    return "na" if s is None else "good" if s >= 65 else "ok" if s >= 45 else "bad"


def _chk(key, score, value, why, gate=False, num=None):
    c = {"k": key, "s": None if score is None else round(score), "st": _status(score), "v": value, "why": why, "g": bool(gate)}
    if num is not None:
        c["n"] = round(float(num), 3)
    return c


def market_regime(bench_close, vix):
    """Shared by every stock: Nifty vs its averages, India VIX."""
    if bench_close is None or len(bench_close) < 200:
        return None
    last = float(bench_close.iloc[-1])
    d50, d200 = float(bench_close.rolling(50).mean().iloc[-1]), float(bench_close.rolling(200).mean().iloc[-1])
    s = 80 if last > d200 and d50 > d200 else 60 if last > d200 else 45 if d50 > d200 else 30
    if vix is not None:
        s += 5 if vix < 13 else -8 if vix > 18 else 0
        s -= 10 if vix > 22 else 0
    return {"score": max(0, min(100, s)), "nifty_vs_200": (last / d200 - 1) * 100, "vix": vix,
            "trend": "up" if last > d200 and d50 > d200 else "mixed" if last > d200 or d50 > d200 else "down"}


def build(sym, f, t, ctx=None):
    """All checks for one stock. ctx: market, sector_pe (median by sector), delivery, deals, screener, today."""
    ctx = ctx or {}
    f, t = f or {}, t or {}
    price, d50, d200 = t.get("price"), t.get("dma50"), t.get("dma200")
    if not price or not d50:
        return []
    out = []
    ext50 = (price / d50 - 1) * 100
    ext200, rsi, hi = t.get("vs_200dma"), t.get("rsi"), t.get("from_52w_high")
    trend_ok = bool(d200 and price > d200 and d50 > d200)
    out.append(_chk("trend", 85 if trend_ok else 25 if d200 else None,
                    "up" if trend_ok else "down/flat" if d200 else "—",
                    "Uptrend: buying with the trend" if trend_ok else "Price below its 200-day average or 50-day below 200-day: falling knife risk",
                    gate=not trend_ok and d200 is not None))
    out.append(_chk("vs50", interp(ext50, [(-10, 35), (-4, 70), (0, 95), (5, 90), (10, 60), (15, 30), (25, 5)]), f"{ext50:+.1f}%",
                    "Close to its 50-day average: not stretched short term" if abs(ext50) <= 6 else
                    f"{ext50:+.0f}% from its 50-day average: {'stretched' if ext50 > 0 else 'weak'} short term", num=ext50))
    if ext200 is not None:
        s = interp(ext200, [(-15, 15), (-5, 40), (0, 70), (8, 95), (20, 80), (30, 45), (40, 20), (60, 5)])
        out.append(_chk("vs200", s, f"{ext200:+.1f}%",
                        f"{ext200:+.0f}% vs 200-day average: " + ("healthy uptrend" if 0 <= ext200 <= 25 else
                                                                   "over-stretched, big pull-backs are common" if ext200 > 25 else "below long-term trend"),
                        gate=ext200 > 40, num=ext200))
    if rsi is not None:
        out.append(_chk("rsi", interp(rsi, [(25, 40), (35, 75), (45, 95), (58, 85), (65, 55), (72, 25), (80, 5)]), f"{rsi:.0f}",
                        "Momentum healthy, not overheated" if 40 <= rsi <= 62 else "Overbought: often cools off first" if rsi > 62 else "Oversold: still falling", num=rsi))
    if hi is not None:
        near = hi > -5
        out.append(_chk("hi52", interp(hi, [(-45, 15), (-30, 45), (-18, 85), (-8, 95), (-5, 60), (0, 45)]),
                        f"{hi:.1f}%", "At its 52-week high" if hi > -0.5 else f"{abs(hi):.0f}% below its 52-week high" +
                        (": too close to the peak" if near else ": has pulled back from the peak" if hi > -25 else ": far below the peak, check why"), gate=near, num=hi))
    m = t.get("ret_12_1")
    if m is not None:
        out.append(_chk("mom", interp(m, [(-40, 10), (-15, 30), (0, 45), (15, 60), (30, 72), (60, 85), (100, 92)]), f"{m:+.0f}%",
                        "Strong 12-month run: winners tend to keep winning for a while" if m >= 30 else
                        "Positive 12-month run" if m > 5 else "Weak 12-month run: losers tend to keep lagging" if m < -10 else "Flat over 12 months", num=m))
    low20 = t.get("low20")
    stop = None
    if low20 and low20 < price:
        stop = max(low20, d50 * 0.97) if d50 < price else low20
    if stop:
        risk = (price / stop - 1) * 100
        out.append(_chk("risk", interp(risk, [(4, 95), (7, 80), (10, 60), (15, 35), (20, 10)]), f"{risk:.1f}% to ₹{stop:,.0f}",
                        f"A stop-loss near ₹{stop:,.0f} means risking about {risk:.0f}%" + (" (tight, good)" if risk <= 8 else " (wide)" if risk > 12 else "")))
    sq = t.get("vol_ratio")
    if sq:
        out.append(_chk("squeeze", interp(sq, [(0.6, 90), (0.8, 75), (1.0, 55), (1.3, 35), (1.7, 15)]), f"{sq:.2f}× usual",
                        "Calmer than usual: moves after quiet spells tend to be cleaner" if sq < 0.9 else
                        "Swinging more than usual: wilder entry" if sq > 1.15 else "Normal swings", num=sq))
    acc = t.get("acc_ratio")
    if acc:
        out.append(_chk("volume", interp(acc, [(0.6, 15), (0.85, 40), (1.0, 55), (1.25, 75), (1.6, 92)]), f"{acc:.2f} up/down volume",
                        "More volume on up days: buyers in control" if acc >= 1.15 else
                        "More volume on down days: sellers in control" if acc < 0.9 else "Balanced buying and selling"))
    dl = (ctx.get("delivery") or {}).get(sym)
    if dl and len(dl) >= 3:
        avg = sum(dl[-5:]) / len(dl[-5:])
        trend = avg - sum(dl[:-5]) / len(dl[:-5]) if len(dl) > 5 else 0
        s = max(0, min(100, interp(avg, [(15, 20), (30, 45), (45, 65), (60, 85), (75, 92)]) + max(-10, min(10, trend))))
        out.append(_chk("delivery", s, f"{avg:.0f}% (5-day)", f"{avg:.0f}% of traded shares were taken into demat" +
                        (" and rising: investors, not just traders" if trend > 3 else " and falling" if trend < -3 else "")))
    scr = (ctx.get("screener") or {}).get(sym) or {}
    deals = [d for d in (ctx.get("deals") or []) if d["symbol"] == sym]
    fii, dii = scr.get("fii"), scr.get("dii")
    inst = None
    if fii and dii and len(fii) >= 2 and len(dii) >= 2:
        inst = (fii[-1] - fii[-2]) + (dii[-1] - dii[-2])
    net = sum(d["value_cr"] * (1 if d["side"].startswith("B") else -1) for d in deals)
    if inst is not None or deals:
        s = interp(inst, [(-2, 20), (-0.5, 40), (0, 55), (0.5, 70), (2, 90)]) if inst is not None else 55
        if deals:
            s = max(0, min(100, s + (10 if net > 0 else -10 if net < 0 else 0)))
        bits = ([f"FII+DII {inst:+.2f} pts last quarter"] if inst is not None else []) + \
               ([f"bulk/block deals net {'buy' if net > 0 else 'sell'} ₹{abs(net):,.1f} Cr (30 days)"] if deals else [])
        out.append(_chk("big_money", s, "; ".join(bits), "Institutions adding" if s >= 65 else "Institutions reducing" if s < 45 else "No clear institutional move"))
    pr = scr.get("promoter")
    if pr and len(pr) >= 2:
        ch = pr[-1] - pr[-2]
        out.append(_chk("holding", interp(ch, [(-3, 15), (-0.5, 40), (0, 65), (0.5, 80), (2, 92)]), f"{pr[-1]:.1f}% ({ch:+.2f} pts)",
                        "Promoters bought more" if ch > 0.05 else "Promoters sold or were diluted" if ch < -0.05 else "Promoter stake unchanged"))
    pe = f.get("pe")
    g = f.get("profit_cagr") if f.get("profit_cagr") is not None else f.get("rev_cagr")
    peg = pe / g if pe and pe > 0 and g and g > 0 else None
    if peg is not None:
        out.append(_chk("peg", interp(peg, [(0.5, 100), (1, 85), (1.5, 65), (2, 40), (3, 15), (4, 5)]), f"{peg:.1f} (P/E {pe:.0f})",
                        f"P/E is {peg:.1f}× its profit growth: " + ("cheap for its growth" if peg < 1 else "fair" if peg < 1.6 else "expensive for its growth"), num=peg))
    elif f.get("loss_making"):
        out.append(_chk("peg", 10, "loss-making", "No profits, so no P/E to justify the price"))
    hist = [x for x in (f.get("pe_history") or []) if x and 0 < x < 300]
    if pe and pe > 0 and len(hist) >= 2:
        rel = pe / statistics.median(hist)
        out.append(_chk("pe_hist", interp(rel, [(0.6, 92), (0.85, 78), (1.0, 62), (1.3, 40), (1.8, 15)]),
                        f"{pe:.0f} vs usual {statistics.median(hist):.0f}",
                        "Cheaper than its own usual P/E" if rel < 0.9 else "Dearer than its own usual P/E" if rel > 1.15 else "Near its usual P/E", num=rel))
    med = (ctx.get("sector_pe") or {}).get(f.get("sector"))
    if pe and pe > 0 and med:
        rel = pe / med
        out.append(_chk("pe_sector", interp(rel, [(0.6, 92), (0.85, 78), (1.0, 62), (1.3, 40), (1.8, 15)]), f"{pe:.0f} vs sector {med:.0f}",
                        "Cheaper than sector peers" if rel < 0.9 else "Dearer than sector peers" if rel > 1.15 else "In line with sector peers", num=rel))
    q = f.get("q_ni") or []
    if len(q) >= 5 and q[-5] and q[-5] > 0:
        yoy = (q[-1] / q[-5] - 1) * 100
        base = f.get("profit_cagr")
        s = interp(yoy, [(-30, 10), (0, 35), (10, 55), (20, 75), (35, 90)])
        if base is not None and s is not None:
            s = max(0, min(100, s + (8 if yoy >= base else -8)))
        out.append(_chk("quarter", s, f"{yoy:+.0f}% YoY" + (f" vs {base:.0f}%/yr" if base is not None else ""),
                        "Latest quarter growing faster than its long-term pace" if base is not None and yoy >= base else
                        "Latest quarter weaker than its long-term pace" if base is not None else "Latest quarter vs a year ago"))
    elif len(q) >= 2 and q[-1] is not None and q[-1] <= 0:
        out.append(_chk("quarter", 15, "loss in latest quarter", "Latest quarter was a loss"))
    rd = scr.get("result_date")
    today = ctx.get("today") or dt.date.today()
    if rd:
        try:
            days = (dt.date.fromisoformat(rd) - today).days
        except ValueError:
            days = None
        if days is not None and days >= 0:
            s = 25 if days <= 7 else 55 if days <= 14 else 85
            out.append(_chk("results", s, f"in {days} day{'s' if days != 1 else ''} ({rd})",
                            "Results within a week: price can gap 10-15% either way; wait for them" if days <= 7 else
                            "Results in under 2 weeks: consider waiting" if days <= 14 else "No results due soon", gate=days <= 7))
        elif days is not None and days >= -10:
            out.append(_chk("results", 80, f"just out ({rd})", "Results just announced: no near-term surprise pending"))
    m = ctx.get("market")
    if m:
        out.append(_chk("market", m["score"], f"Nifty {m['nifty_vs_200']:+.1f}% vs 200-day" + (f", VIX {m['vix']:.1f}" if m.get("vix") else ""),
                        {"up": "Market in an uptrend", "mixed": "Market trend mixed", "down": "Market in a downtrend: even good stocks tend to fall"}[m["trend"]]))
    add_exit(out)
    return out


# ---------------------------------------------------------------- exit side
# The same 17 factors, read from a holder's point of view: "should I keep holding?" Each check gets a hold
# score x (high = keep holding, low = exit pressure), its own reason xw, and a trim flag t (stretched or
# expensive: book part profit rather than exit). Being near the 52-week high is good for a holder; the
# results date and the entry stop-loss do not decide a hold, so they score nothing on this side.
HOLD_CAP = {"trend": 40}   # checks that pull the hold score down when they fail (see hold_score)


def add_exit(checks):
    for c in checks:
        k, x, why, trim = c["k"], c.get("n"), None, False
        xs = c["s"]
        if k == "trend":
            up = c["st"] == "good"
            xs, why = (85, "Uptrend intact: no price reason to sell") if up else \
                      (20, "Trend broken: price below its 200-day average (or 50-day below 200-day), the classic exit warning")
            c["xg"] = not up
        elif k == "vs50" and x is not None:
            xs = interp(x, [(-15, 10), (-8, 30), (-3, 55), (0, 75), (10, 85), (25, 70), (40, 55)])
            why = f"{x:+.0f}% vs 50-day average: " + ("holding up" if x >= -3 else "short-term weakness" if x > -8 else "sharp short-term fall")
        elif k == "vs200" and x is not None:
            xs, trim = interp(x, [(-20, 5), (-10, 20), (-3, 40), (0, 60), (10, 85), (30, 80), (45, 55), (70, 35)]), x > 40
            why = f"{x:+.0f}% vs 200-day average: " + ("below its long-term trend" if x < 0 else
                                                       "far above its long-term average, consider booking part profit" if x > 40 else "healthy")
        elif k == "rsi" and x is not None:
            xs, trim = interp(x, [(20, 25), (30, 40), (40, 60), (55, 85), (68, 75), (78, 50), (88, 30)]), x > 75
            why = "Overbought: a time to book part profit, not to add" if x > 75 else "Weak momentum" if x < 35 else "Momentum fine"
        elif k == "hi52" and x is not None:
            xs = interp(x, [(-50, 10), (-35, 25), (-20, 55), (-10, 75), (0, 90)])
            why = "Near its 52-week high: strength, a reason to hold" if x > -10 else \
                  f"{abs(x):.0f}% below its 52-week high" + (": a normal pull-back" if x > -25 else ": a deep fall, check the reason")
        elif k == "risk":
            xs, why = None, f"Used for buying only (stop-loss level {c['v']})"
        elif k == "squeeze" and x is not None:
            xs = interp(x, [(0.6, 75), (1.0, 65), (1.3, 45), (1.7, 25)])
            why = "Swinging much more than usual: often comes before a trend change" if x > 1.3 else "Normal or calm swings"
        elif k == "peg":
            if x is None:
                xs, why = 20, "Loss-making: the price rests on hope of future profits"
            else:
                xs, trim = interp(x, [(0.5, 95), (1, 85), (2, 65), (3, 40), (4, 20), (6, 5)]), x > 3
                why = f"PEG {x:.1f}: " + ("still reasonable for its growth" if x <= 2 else "rich, growth must stay high" if x <= 3 else "very expensive for its growth, consider booking part profit")
        elif k in ("pe_hist", "pe_sector") and x is not None:
            xs = interp(x, [(0.6, 90), (1, 75), (1.5, 50), (2, 25), (3, 10)])
            trim = k == "pe_hist" and x > 1.8
            what = "its own usual P/E" if k == "pe_hist" else "sector peers"
            why = f"P/E {x:.1f}× {what}: " + ("not stretched" if x <= 1.3 else "getting stretched" if x <= 1.8 else "far above normal, re-rating risk")
        elif k == "results":
            xs, why = None, "Results date does not decide a hold or sell (expect a big move either way)"
        c["x"] = None if xs is None else round(xs)
        c["xst"] = _status(xs)
        c["xw"] = why or c["why"]
        if trim:
            c["t"] = True


def hold_score(checks, enabled=None):
    en = set(enabled) if enabled is not None else set(ORDER)
    num = den = 0.0
    capped = []
    for c in checks:
        if c["k"] not in en:
            continue
        w = CHECKS[c["k"]][2]
        if c.get("x") is not None:
            num += c["x"] * w
            den += w
        if c.get("xg") and c["k"] in HOLD_CAP:
            capped.append(c["k"])
    if not den:
        return None, []
    s = num / den
    if capped:
        # a broken trend pulls the score down but keeps the order between stocks (a hard cap made hundreds
        # of stocks read exactly 40): 30 -> 30, 35 -> 35, 50 -> 42, 74 -> 54
        s = min(s, 17.5 + s / 2)
    return round(s), capped


def summarize_hold(checks):
    s, capped = hold_score(checks)
    if s is None:
        return None
    trims = [c["k"] for c in checks if c.get("t") and CHECKS[c["k"]][2]]
    bad = sorted((c for c in checks if c.get("xst") == "bad"), key=lambda c: c["x"])
    good = sorted((c for c in checks if c.get("xst") == "good"), key=lambda c: -c["x"])
    verdict = (("Exit warning: trend broken and several factors weak" if capped else "Exit warning: several factors weak") if s < 35 else
               "Stretched or expensive: consider booking part profit" if len(trims) >= 2 and not capped else
               "Trend broken: the rest holds up, watch closely" if capped else
               "Weakening: watch closely" if s < 50 else "Healthy: keep holding")
    return {"score": s, "verdict": verdict, "capped_by": capped, "trim": trims,
            "why": [c["xw"] for c in bad[:3]], "good": [c["xw"] for c in good[:3]],
            "n_good": len(good), "n_bad": len(bad), "n_ok": sum(1 for c in checks if c.get("xst") == "ok")}


def compact_hold(checks):
    """For universe.json: hold scores in ORDER + trim bitmask."""
    by = {c["k"]: c for c in checks}
    return [by[k].get("x") if k in by else None for k in ORDER], sum(1 << i for i, k in enumerate(ORDER) if k in by and by[k].get("t"))


def score(checks, enabled=None, weights=None):
    """Weighted entry score from the enabled checks, then gate caps. Returns (score, capped_by)."""
    en = set(enabled) if enabled is not None else set(ORDER)
    num = den = 0.0
    caps = []
    for c in checks:
        if c["k"] not in en:
            continue
        w = (weights or {}).get(c["k"], CHECKS[c["k"]][2])
        if c["s"] is not None and w:
            num += c["s"] * w
            den += w
        cap = CHECKS[c["k"]][3]
        if cap is not None and c.get("g"):
            caps.append((cap, c["k"]))
    if not den:
        return None, []
    s = num / den
    for cap, k in caps:
        s = min(s, cap)
    return round(s), [k for _, k in caps]


def summarize(checks, t):
    """The 'entry' object the site shows: score, verdict, buy zone, good / bad points."""
    if not checks:
        return None
    s, capped = score(checks)
    d50 = t.get("dma50") or t.get("price")
    lo, hi_z = round(d50 * 0.98, 2), round(d50 * 1.04, 2)
    by = {c["k"]: c for c in checks}
    if "trend" in capped:
        verdict = "Wait: trend is down; buy only after it turns up"
    elif "results" in capped:
        verdict = "Wait until results are out (" + by["results"]["v"] + ")"
    elif "hi52" in capped:
        verdict = f"Near its 52-week high: wait for a dip to ₹{lo:,.0f}–{hi_z:,.0f}, or buy only in small parts"
    elif "vs200" in capped:
        verdict = f"Over-stretched above its 200-day average: wait for a dip to ₹{lo:,.0f}–{hi_z:,.0f}"
    elif s is not None and s >= 65:
        verdict = "Good entry now"
    elif s is not None and s >= 50:
        verdict = "Fair entry: buy in 2–3 parts"
    else:
        verdict = f"Wait for a dip to ₹{lo:,.0f}–{hi_z:,.0f}"
    bad = sorted((c for c in checks if c["st"] == "bad"), key=lambda c: c["s"])
    good = sorted((c for c in checks if c["st"] == "good"), key=lambda c: -c["s"])
    return {"score": s, "verdict": verdict, "zone": [lo, hi_z], "trend_ok": by.get("trend", {}).get("st") == "good",
            "capped_by": capped, "why": [c["why"] for c in bad[:3]], "good": [c["why"] for c in good[:3]],
            "n_good": len(good), "n_bad": len(bad), "n_ok": sum(1 for c in checks if c["st"] == "ok"),
            "vs_50dma": round((t["price"] / t["dma50"] - 1) * 100, 1) if t.get("dma50") else None,
            "peg": next((float(c["v"].split()[0]) for c in checks if c["k"] == "peg" and c["s"] is not None and c["v"][0].isdigit()), None),
            "pe": None, "hold": summarize_hold(checks), "checks": checks}


def compact(checks):
    """For universe.json: scores in ORDER (None for missing) + gate bitmask, so the site can rescore fast."""
    by = {c["k"]: c for c in checks}
    return [by[k]["s"] if k in by else None for k in ORDER], sum(1 << i for i, k in enumerate(ORDER) if k in by and by[k].get("g"))
