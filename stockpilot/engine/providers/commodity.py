"""Crude oil outlook: trend, momentum, a historical up/down probability, an expected range, and news tone.

The probability is a base rate, not a forecast: over the last ~10 years of Brent prices, on days that looked
like today (same trend set-up and momentum bucket), how often was the price higher 1 month / 3 months later.
"""
import datetime as dt
import re
from urllib.parse import quote_plus

import numpy as np

# oil-specific news reading: what each headline implies for the PRICE.
# Headlines often say where the price came from ("dips after hitting a 5-month high"); only the part before
# "after / from / despite / as / amid" says which way it is moving now, so the rest is ignored for direction.
UP_MOVE = r"\b(rise|rises|rising|rose|gain|gains|gained|jump|jumps|jumped|surge|surges|surged|rally|rallies|rallied|climb|climbs|climbed|soar|soars|spike|spikes|firm|firms|up \d|higher|hits? (a )?\d+-(month|year|week) high)\b"
DOWN_MOVE = r"\b(fall|falls|falling|fell|dip|dips|dipped|ease|eases|eased|easing|slip|slips|slipped|retreat|retreats|retreated|decline|declines|declined|drop|drops|dropped|slide|slides|slid|slump|slumps|tumble|tumbles|plunge|plunges|sink|sinks|sank|lower|down \d|weaken|weakens)\b"
BULL_DRIVER = r"\b(opec\+? (cut|cuts)|output cut|production cut|supply cut|disruption|outage|attack|attacks|strike on|escalat\w*|blockade|new sanctions|sanctions on|tighten\w* sanctions|inventor(y|ies) (fall|falls|draw)|stockpiles? (fall|falls)|draw(down)?|supply shortfall|tight supply|hurricane)\b"
BEAR_DRIVER = r"\b(output (hike|increase)|production (hike|increase)|raise[sd]? output|boost\w* output|glut|surplus|oversupply|inventor(y|ies) (rise|rises|build)|stockpiles? (rise|rises)|build|demand (concern|worries|slow\w*)|weak demand|slowdown|recession|ceasefire|truce|peace (deal|talks)|de-?escalat\w*|waive[sd]? \w*\s?sanctions|lift\w* sanctions|ease[sd]? sanctions|sanctions (waiver|relief)|supplies recover|could ease|to ease)\b"


def _tone(title: str) -> int:
    t = title.lower()
    head = re.split(r"\b(after|from|despite|amid|as|on|following|since)\b", t, maxsplit=1)[0]
    head = re.sub(r"\b(inventor(y|ies)|stockpiles?|crude stocks)\b\s+\w+", " ", head)  # "inventories fall" is not the price falling
    score = 0
    if re.search(DOWN_MOVE, head): score -= 1
    elif re.search(UP_MOVE, head): score += 1
    if re.search(BEAR_DRIVER, t): score -= 1
    if re.search(BULL_DRIVER, t) and not re.search(r"de-?escalat", t): score += 1
    return (score > 0) - (score < 0)


def oil_news(days: int = 7, limit: int = 15) -> list:
    import feedparser
    seen, out = set(), []
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    for q in ("brent crude oil price", "OPEC oil output", "crude oil India"):
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en")
        for e in feed.entries[:30]:
            when = dt.datetime(*e.published_parsed[:6]) if getattr(e, "published_parsed", None) else None
            if when and when < cutoff:
                continue
            title = re.sub(r"\s+-\s+[^-]+$", "", e.title)
            k = re.sub(r"\W+", "", title.lower())[:60]
            if k in seen or not re.search(r"\b(oil|crude|brent|opec|wti)\b", title.lower()):
                continue
            seen.add(k)
            out.append({"title": title, "date": when.strftime("%Y-%m-%d") if when else "", "link": e.link, "tone": _tone(title)})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:limit]


def _rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _state(price, d50, d200, rsi):
    trend = "up" if price > d200 and d50 > d200 else "down" if price < d200 and d50 < d200 else "mixed"
    mom = "weak" if rsi < 40 else "strong" if rsi > 60 else "neutral"
    return trend, mom


def outlook(close, usdinr=None, news=None) -> dict | None:
    """close: daily Brent closes (pandas Series), ideally 10 years. usdinr: latest ₹ per $."""
    c = close.dropna()
    if len(c) < 260:
        return None
    d50, d200, rsi = c.rolling(50).mean(), c.rolling(200).mean(), _rsi(c)
    last = float(c.iloc[-1])
    trend, mom = _state(last, float(d50.iloc[-1]), float(d200.iloc[-1]), float(rsi.iloc[-1]))
    ret = lambda n: round(float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100), 1) if len(c) > n else None
    vol = float(np.log(c).diff().iloc[-63:].std() * np.sqrt(252))  # annualised, last 3 months
    hi52, lo52 = float(c.iloc[-252:].max()), float(c.iloc[-252:].min())

    # base rates: past days in the same set-up -> share higher after 21 / 63 trading days
    probs = {}
    df = c.to_frame("c")
    df["d50"], df["d200"], df["rsi"] = d50, d200, rsi
    df = df.dropna()
    states = [_state(r.c, r.d50, r.d200, r.rsi) for r in df.itertuples()]
    for h, name in ((21, "1m"), (63, "3m")):
        fwd = (df["c"].shift(-h) / df["c"] - 1).values
        same = [i for i, s in enumerate(states[:-h]) if s == (trend, mom)]
        same_t = [i for i, s in enumerate(states[:-h]) if s[0] == trend]
        allv = fwd[:-h]
        pick = same if len(same) >= 60 else same_t  # fall back to trend-only when the exact set-up is rare
        if len(pick) >= 30:
            f = fwd[pick]
            probs[name] = {"up": round(float((f > 0).mean()) * 100), "n": len(pick), "median": round(float(np.median(f)) * 100, 1),
                           "matched": "trend + momentum" if pick is same else "trend only",
                           "base_up": round(float((allv > 0).mean()) * 100)}
    sig1m = vol * np.sqrt(21 / 252)
    rng = [round(last * float(np.exp(-sig1m)), 2), round(last * float(np.exp(sig1m)), 2)]
    # how often this range held in the past (same rule, using only data before each date): shown next to the range
    lr = np.log(c)
    s63 = lr.diff().rolling(63).std() * np.sqrt(21)
    move = (lr.shift(-21) - lr).abs()
    ok = (move <= s63).iloc[63:-21].iloc[::5]
    range_held = round(float(ok.mean()), 3) if len(ok) > 50 else None

    news = news or []
    pos, neg = sum(1 for x in news if x["tone"] > 0), sum(1 for x in news if x["tone"] < 0)
    ntone = "bullish (supports higher prices)" if pos >= neg + 2 else "bearish (points to lower prices)" if neg >= pos + 2 else "mixed"

    why = []
    why.append({"up": "Uptrend: price above its 200-day average and the 50-day above the 200-day",
                "down": "Downtrend: price below its 200-day average and the 50-day below the 200-day",
                "mixed": "No clear trend: the price and its averages are crossing"}[trend])
    why.append(f"Momentum {mom} (RSI {float(rsi.iloc[-1]):.0f})" + (": oversold, bounces are common" if rsi.iloc[-1] < 30 else
                                                                  ": overbought, pull-backs are common" if rsi.iloc[-1] > 70 else ""))
    pos52 = (last - lo52) / (hi52 - lo52) * 100 if hi52 > lo52 else 50
    why.append(f"At {pos52:.0f}% of its 52-week range (${lo52:,.0f}–${hi52:,.0f})")
    why.append(f"Swinging about {vol * 100:.0f}% a year; a typical 1-month move is ±{sig1m * 100:.0f}%")

    p1 = probs.get("1m", {}).get("up")
    lean = None if p1 is None else "up" if p1 >= 55 else "down" if p1 <= 45 else "even"
    verdict = {"up": "Leaning up", "down": "Leaning down", "even": "No edge either way", None: "Not enough history"}[lean]
    if lean in ("up", "down") and ((lean == "up" and ntone.startswith("bearish")) or (lean == "down" and ntone.startswith("bullish"))):
        verdict += ", but this week's news points the other way"
    elif lean in ("up", "down") and ntone.split()[0] in ("bullish", "bearish"):
        verdict += ", and this week's news agrees"

    inr = lambda usd: round(usd * usdinr, 0) if usdinr else None
    impact = [
        "Falling crude helps: oil marketers (HPCL, BPCL, IOC), paints, tyres, airlines, chemicals, and India's import bill and the rupee.",
        "Rising crude helps: upstream producers (ONGC, Oil India); it hurts the users above and can push inflation and the rupee weaker.",
    ]
    return {"name": "Brent crude", "unit": "US$ per barrel", "last": round(last, 2), "last_inr": inr(last),
            "usdinr": usdinr, "date": c.index[-1].strftime("%Y-%m-%d"),
            "chg": {"1w": ret(5), "1m": ret(21), "3m": ret(63), "1y": ret(252)},
            "dma50": round(float(d50.iloc[-1]), 2), "dma200": round(float(d200.iloc[-1]), 2), "rsi": round(float(rsi.iloc[-1]), 1),
            "hi52": round(hi52, 2), "lo52": round(lo52, 2), "vol": round(vol * 100, 1), "range_1m": rng,
            "range_1m_inr": [inr(rng[0]), inr(rng[1])], "range_held": range_held, "trend": trend, "momentum": mom, "prob": probs,
            "years": round(len(c) / 252, 1), "verdict": verdict, "why": why,
            "news": news[:10], "news_pos": pos, "news_neg": neg, "news_tone": ntone, "impact": impact}


def gold_news(days: int = 7, limit: int = 10) -> list:
    """Latest gold headlines (shown as context only: their tone has not been tested, so no up/down reading)."""
    import feedparser
    seen, out = set(), []
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
    for q in ("gold price", "gold rate India", "MCX gold"):
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en")
        for e in feed.entries[:30]:
            when = dt.datetime(*e.published_parsed[:6]) if getattr(e, "published_parsed", None) else None
            if when and when < cutoff:
                continue
            title = re.sub(r"\s+-\s+[^-]+$", "", e.title)
            k = re.sub(r"\W+", "", title.lower())[:60]
            if k in seen or not re.search(r"\bgold\b", title.lower()):
                continue
            seen.add(k)
            out.append({"title": title, "date": when.strftime("%Y-%m-%d") if when else "", "link": e.link, "tone": 0})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:limit]


def fetch_gold(prov, usdinr=None) -> dict | None:
    h = prov.history("GC=F", period="10y")
    if h is None or not len(h):
        return None
    try:
        news = gold_news()
    except Exception as e:
        print(f"gold news skipped: {e}")
        news = []
    o = outlook(h["Close"], None, news)
    if not o:
        return o
    per10g = lambda usd: round(usd * usdinr / 31.1035 * 10) if usdinr and usd else None
    o.update({"name": "Gold", "unit": "US$ per troy ounce", "usdinr": usdinr, "inr_unit": "per 10 g",
              "last_inr": per10g(o["last"]), "range_1m_inr": [per10g(o["range_1m"][0]), per10g(o["range_1m"][1])],
              "news_tone": "context only", "news_pos": 0, "news_neg": 0,
              "impact": ["Rising gold helps: jewellers' inventory (Titan, Kalyan, Senco), gold-loan companies (Muthoot, Manappuram) and holders of gold ETFs/SGBs.",
                         "A gold rally often means investors are nervous (war, inflation, falling rates); it tends to come with a weaker rupee and higher Indian gold import costs."]})
    return o


def fetch_crude(prov, usdinr=None) -> dict | None:
    h = prov.history("BZ=F", period="10y")
    if h is None or not len(h):
        return None
    try:
        news = oil_news()
    except Exception as e:
        print(f"oil news skipped: {e}")
        news = []
    o = outlook(h["Close"], usdinr, news)
    if o:
        o["inr_unit"] = "per barrel"
    return o
