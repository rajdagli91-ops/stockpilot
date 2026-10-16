"""News (Google News RSS) and exchange filings (BSE announcements, best effort)."""
import datetime as dt
import re
from urllib.parse import quote_plus

import feedparser
import requests

POS = ["order win", "bags", "wins", "record", "beats", "surge", "upgrade", "buy rating", "profit rises",
       "profit jumps", "growth", "expansion", "acquires", "dividend", "bonus", "approval", "strong", "rally", "outperform"]
NEG = ["fraud", "probe", "raid", "downgrade", "sell rating", "loss widens", "profit falls", "profit drops", "misses",
       "plunge", "slump", "resign", "default", "penalty", "sebi order", "pledge", "insolvency", "weak", "cut target",
       "lawsuit", "ban", "delay", "underperform", "crash"]
RED_FLAGS = ["auditor resign", "resignation of auditor", "default", "insolvency", "fraud", "pledge invoked",
             "forensic", "sebi order", "show cause", "nclt", "downgrade"]


def _sentiment(text: str) -> int:
    t = text.lower()
    return sum(w in t for w in POS) - sum(w in t for w in NEG)


def headlines(name: str, days: int = 30, limit: int = 12) -> list:
    url = f"https://news.google.com/rss/search?q={quote_plus(name + ' share')}&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(url)
    cutoff = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=days)
    out = []
    for e in feed.entries[:40]:
        when = dt.datetime(*e.published_parsed[:6]) if getattr(e, "published_parsed", None) else None
        if when and when < cutoff:
            continue
        title = re.sub(r"\s+-\s+[^-]+$", "", e.title)  # strip " - Publisher"
        out.append({"title": title, "date": when.strftime("%Y-%m-%d") if when else "", "link": e.link,
                    "tone": _sentiment(title)})
        if len(out) >= limit:
            break
    return out


def bse_filings(bse_code: str, days: int = 90, limit: int = 10) -> list:
    from engine.providers import bse
    return bse.company_filings(bse_code, days, limit)
