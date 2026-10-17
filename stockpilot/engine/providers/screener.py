"""Screener.in company pages (public, no login): upcoming results date and shareholding pattern.

Only fetched for the stocks that matter (pick candidates and your holdings), slowly, and cached
for a week, so the site is not hammered. Returns {} for anything it cannot read.
"""
import datetime as dt
import html as htmlmod
import re
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "en-IN,en;q=0.9"}
_s = requests.Session()
_s.headers.update(UA)


def _text(h):
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def _pct(x):
    try:
        return float(x.replace("%", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def parse(page: str) -> dict:
    out = {}
    txt = _text(re.sub(r"<script[\s\S]*?</script>", " ", page))
    m = re.search(r"Upcoming result date:\s*(\d{1,2} [A-Za-z]+ \d{4})", txt)
    if m:
        try:
            out["result_date"] = dt.datetime.strptime(m.group(1), "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    i = page.find('id="shareholding"')
    if i >= 0:
        sec = page[i:i + 60000]
        t = re.search(r"<table[\s\S]*?</table>", sec)  # first table = quarterly
        if t:
            rows = re.findall(r"<tr[\s\S]*?</tr>", t.group(0))
            hdr = [_text(c) for c in re.findall(r"<th[^>]*>([\s\S]*?)</th>", rows[0])] if rows else []
            out["sh_quarters"] = [h for h in hdr if h][-2:]
            for r in rows[1:]:
                cells = [_text(c) for c in re.findall(r"<td[^>]*>([\s\S]*?)</td>", r)]
                if not cells:
                    continue
                lab = cells[0].replace("+", "").strip().lower()
                vals = [v for v in (_pct(c) for c in cells[1:]) if v is not None]
                key = {"promoters": "promoter", "fiis": "fii", "diis": "dii"}.get(lab)
                if key and vals:
                    out[key] = vals[-5:]  # last 5 quarters, oldest first
    return out


def fetch(symbol: str) -> dict:
    for url in (f"https://www.screener.in/company/{symbol}/consolidated/", f"https://www.screener.in/company/{symbol}/"):
        try:
            r = _s.get(url, timeout=20)
            if r.status_code == 429:
                time.sleep(20)
                r = _s.get(url, timeout=20)
            if r.status_code != 200:
                continue
            d = parse(r.text)
            if d.get("promoter") or d.get("fii") or d.get("result_date"):
                d["fetched"] = dt.date.today().isoformat()
                return d
        except Exception as e:
            print(f"screener {symbol}: {e}")
    return {}


def refresh(symbols, cache: dict, max_fetch: int = 150, max_age_days: int = 7, delay: float = 1.5) -> dict:
    """Fetch stale/missing symbols (in priority order) into cache. Returns the cache."""
    today = dt.date.today()
    n = 0
    for s in symbols:
        c = cache.get(s) or {}
        try:
            fresh = c.get("fetched") and (today - dt.date.fromisoformat(c["fetched"])).days < max_age_days
        except ValueError:
            fresh = False
        # results dates move: re-check a cached date that is near or already past
        if fresh and c.get("result_date"):
            try:
                if (dt.date.fromisoformat(c["result_date"]) - today).days < 3:
                    fresh = False
            except ValueError:
                pass
        if fresh:
            continue
        if n >= max_fetch:
            break
        d = fetch(s)
        n += 1
        if d:
            cache[s] = d
        time.sleep(delay)
    print(f"screener: fetched {n} pages, {len(cache)} cached")
    return cache
