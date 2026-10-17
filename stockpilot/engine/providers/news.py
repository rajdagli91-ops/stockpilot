"""News (Google News RSS) and exchange filings (BSE announcements, best effort)."""
import datetime as dt
import re
from urllib.parse import quote_plus

import feedparser
import requests

POS = ["order win", "bags", "wins", "record", "beats", "surge", "surges", "upgrade", "upgraded", "buy rating", "rated buy",
       "profit rises", "profit jumps", "profit up", "profit grows", "profit surges", "growth", "expansion", "acquires", "dividend",
       "bonus", "approval", "strong", "rally", "rallies", "outperform", "target raised", "raises target", "order", "orders",
       "contract", "all-time high", "52-week high", "record high", "soars", "jumps", "rises", "gains", "climbs"]
NEG = ["fraud", "probe", "raid", "downgrade", "downgraded", "sell rating", "rated sell", "loss widens", "profit falls",
       "profit drops", "profit declines", "profit down", "misses", "plunge", "plunges", "slump", "slumps", "resign", "resigns",
       "default", "penalty", "sebi order", "pledge", "insolvency", "weak", "cut target", "target cut", "lawsuit", "ban",
       "banned", "delay", "delayed", "underperform", "crash", "crashes", "tumble", "tumbles", "slips", "slides", "sinks", "falls", "drops", "declines", "shares fall", "52-week low"]
RED_FLAGS = ["auditor resign", "resignation of auditor", "default", "insolvency", "fraud", "pledge invoked",
             "forensic", "sebi order", "show cause", "nclt", "downgrade"]


_POS = [re.compile(r"\b" + re.escape(w) + r"\b") for w in POS]
_NEG = [re.compile(r"\b" + re.escape(w) + r"\b") for w in NEG]  # whole words: "ban" must not match "Bank"


def _sentiment(text: str) -> int:
    t = text.lower()
    p, n = sum(1 for r in _POS if r.search(t)), sum(1 for r in _NEG if r.search(t))
    return (p > n) - (n > p)


GENERIC = {"limited", "ltd", "ltd.", "the", "co", "co.", "company", "corporation", "corp", "(india)", "india", "inc"}


def _stem(name: str) -> str:
    words = [w for w in re.split(r"\s+", name.strip()) if w.lower() not in GENERIC]
    return " ".join(words[:2] if len(words) >= 3 else words)


def _about(title: str, stem: str, symbol: str | None) -> bool:
    """Headline is about this company: its name appears (not as part of another name, e.g. Indian Bank vs South
    Indian Bank) or its NSE symbol appears as a word."""
    if symbol and re.search(r"\b" + re.escape(symbol) + r"\b", title):
        return True
    for m in re.finditer(re.escape(stem), title, flags=re.I):
        before = title[:m.start()].rstrip()
        prev = before.split()[-1] if before.split() else ""
        if not prev or not prev[0].isupper() or prev[-1] in ":,;|-–(" or prev.lower() in ("in", "on", "of", "for", "and", "&"):
            return True
    return False


def headlines(name: str, days: int = 30, limit: int = 12, symbol: str | None = None, strict: bool = True) -> list:
    stem = _stem(name) or name
    q = f'"{stem}" share' if strict else name
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(url)
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)
    out = []
    for e in feed.entries[:40]:
        when = dt.datetime(*e.published_parsed[:6]) if getattr(e, "published_parsed", None) else None
        if when and when < cutoff:
            continue
        title = re.sub(r"\s+-\s+[^-]+$", "", e.title)  # strip " - Publisher"
        if strict and not _about(title, stem, symbol):
            continue
        out.append({"title": title, "date": when.strftime("%Y-%m-%d") if when else "", "link": e.link,
                    "tone": _sentiment(title)})
        if len(out) >= limit:
            break
    return out


def bse_filings(bse_code: str, days: int = 90, limit: int = 10) -> list:
    from engine.providers import bse
    return bse.company_filings(bse_code, days, limit)
