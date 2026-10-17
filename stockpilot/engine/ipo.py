"""IPO analysis: open and upcoming IPOs, their sector, valuation vs listed peers, financials, and an Apply / Avoid call.

Sources (later ones fill or override earlier ones)
  1. NSE IPO lists (current + upcoming). Often refused from cloud servers; skipped quietly if so.
  2. Chittorgarh IPO pages: the list of open and upcoming IPOs, then each IPO's own page for its
     issue size and offer-for-sale split, restated financials (revenue, profit, net worth, debt),
     post-issue EPS / P/E, promoter holding and broker reviews (all figures from the RHP).
  3. config/ipos.csv: add any IPO yourself with numbers from its prospectus (RHP). Rows here win.

Financial figures in ipos.csv are in ₹ crore; eps in ₹; ofs_pct = % of the issue that is promoters/investors selling.
"""
import csv
import datetime as dt
import re
import statistics
from pathlib import Path

import html as htmlmod
import time

import requests

from engine.providers import global_mkts
from engine.scoring import interp

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "config" / "ipos.csv"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}

SECTOR_WORDS = [
    (r"pharma|health|hospital|diagnost|life ?science|medic|bio", "Healthcare"),
    (r"bank|financ|capital|credit|insur|fintech|nbfc|housing fin|securities|broking|amc|wealth", "Financial Services"),
    (r"tech|software|info|digital|data|cloud|it services|systems|solutions", "Technology"),
    (r"power|energy|solar|renewable|green|electric", "Utilities"),
    (r"oil|gas|petro|refin", "Energy"),
    (r"chem|steel|metal|alloy|cement|mining|paper|plastic|polymer|paint|coating", "Basic Materials"),
    (r"auto|motor|vehicle|tyre|ev\b|hotel|travel|retail|fashion|apparel|jewel", "Consumer Cyclical"),
    (r"food|agro|dairy|beverage|fmcg|consumer|personal care", "Consumer Defensive"),
    (r"infra|engineer|construct|logistic|defen[cs]e|aero|industr|machin|equipment|rail|port|ship|manufactur|fabricat", "Industrials"),
    (r"realty|real estate|estate|propert|developer", "Real Estate"),
    (r"telecom|media|entertain|broadcast|communication", "Communication Services"),
]


def guess_sector(text):
    t = (text or "").lower()
    for pat, sec in SECTOR_WORDS:
        if re.search(pat, t):
            return sec
    return None


def _num(x):
    try:
        x = str(x).replace(",", "").strip()
        return float(x) if x not in ("", "-", "na", "NA") else None
    except ValueError:
        return None


def nse_ipos() -> list[dict]:
    out = []
    try:
        s = requests.Session()
        s.get("https://www.nseindia.com/", headers=UA, timeout=10)
        for url, status in (("https://www.nseindia.com/api/ipo-current-issue", "Open"),
                            ("https://www.nseindia.com/api/all-upcoming-issues?category=ipo", "Upcoming")):
            r = s.get(url, headers=UA, timeout=15)
            if r.status_code != 200:
                continue
            j = r.json()
            items = j if isinstance(j, list) else j.get("data", [])
            for x in items:
                name = x.get("companyName") or x.get("company") or x.get("symbol")
                if not name:
                    continue
                band = str(x.get("issuePrice") or x.get("priceBand") or "")
                nums = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", band)]
                size = _num(x.get("issueSize"))
                if size and size > 1e5:  # NSE gives the number of shares offered, not rupees
                    size = round(size * max(nums) / 1e7, 1) if nums else None
                out.append({"name": name.strip(), "symbol": x.get("symbol"), "status": status,
                            "open": (x.get("issueStartDate") or "")[:11], "close": (x.get("issueEndDate") or "")[:11],
                            "price_high": max(nums) if nums else None, "price_band": band,
                            "issue_size_cr": size,
                            "board": "SME" if "sme" in str(x.get("series") or x.get("isBse") or "").lower() or str(x.get("series")) == "SME" else "Mainboard",
                            "subscription": _num(x.get("noOfTime") or x.get("subscription")), "source": "NSE"})
    except Exception as e:
        print(f"NSE IPO list skipped: {e}")
    return out


# ---------------------------------------------------------------- Chittorgarh (prospectus figures)
CG = "https://www.chittorgarh.com"
CG_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
         "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-IN,en;q=0.9"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _text(h):
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", " ", h or ""))).strip()


def _tables(page):
    """Every <table> as a list of rows, each row a list of cell texts."""
    out = []
    for t in re.findall(r"<table[\s\S]*?</table>", page):
        rows = [[_text(c) for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", r)] for r in re.findall(r"<tr[\s\S]*?</tr>", t)]
        rows = [r for r in rows if any(r)]
        if rows:
            out.append(rows)
    return out


def _rowmap(tables):
    """label (lower case) -> rest of row, first occurrence wins."""
    m = {}
    for t in tables:
        for r in t:
            if len(r) >= 2 and r[0]:
                m.setdefault(r[0].lower(), r[1:])
    return m


def _cr(s):
    """'1,38,22,116 shares (agg. up to ₹475 Cr)' -> 475.0"""
    m = re.search(r"₹\s*([\d,]+(?:\.\d+)?)\s*Cr", s or "")
    return _num(m.group(1)) if m else None


def _dates(s):
    """'15 to 19 Oct, 2026' or '29 Sep to 3 Oct, 2026' -> ('2026-10-15', '2026-10-19')"""
    m = re.search(r"(\d{1,2})(?:\s+([A-Za-z]{3}))?\s+to\s+(\d{1,2})\s+([A-Za-z]{3})[a-z]*,?\s+(\d{4})", s or "")
    if not m:
        return "", ""
    d1, m1, d2, m2, y = m.groups()
    try:
        mo2 = MONTHS[m2.lower()]
        mo1 = MONTHS[(m1 or m2).lower()]
        y1 = int(y) - (1 if mo1 > mo2 else 0)
        return dt.date(y1, mo1, int(d1)).isoformat(), dt.date(int(y), mo2, int(d2)).isoformat()
    except (KeyError, ValueError):
        return "", ""


def _get(s, url):
    for attempt in range(2):
        try:
            r = s.get(url, headers=CG_UA, timeout=20)
            if r.status_code == 200:
                return r.text
            print(f"chittorgarh {url}: HTTP {r.status_code}")
        except Exception as e:
            print(f"chittorgarh {url}: {e}")
        time.sleep(2)
    return None


def cg_page(s, url, status=None) -> dict | None:
    page = _get(s, url)
    if not page:
        return None
    tabs = _tables(page)
    rm = _rowmap(tabs)
    first = lambda k: (rm.get(k) or [""])[0]
    h1 = re.search(r"<h1[^>]*>([\s\S]*?)</h1>", page)
    name = re.sub(r"\s*IPO\b.*$", "", _text(h1.group(1)) if h1 else "", flags=re.I).strip()
    if not name:
        return None
    op, cl = _dates(first("ipo date"))
    band = first("price band") or first("issue price")
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", band.replace(",", ""))]
    listing = first("listing at")
    sale = first("sale type").lower()
    total = _cr(first("total issue size"))
    ofs = _cr(first("offer for sale"))
    ofs_pct = 100.0 if "ofs only" in sale else 0.0 if "fresh capital only" in sale else (
        round(ofs / total * 100, 1) if ofs is not None and total else None)
    # restated financials: header row "Period Ended | 30 Jun 2026 | 31 Mar 2026 | ..."
    rev = pat = None
    net_worth = debt = None
    years = []
    for t in tabs:
        if t[0] and t[0][0].lower().startswith("period ended"):
            hdr = t[0][1:]
            rows = {r[0].lower(): r[1:] for r in t[1:] if r}
            annual = [i for i, h in enumerate(hdr) if re.match(r"31\s*mar", h, re.I)] or list(range(len(hdr)))
            annual = sorted(annual, key=lambda i: hdr[i][-4:])[-3:]  # oldest -> newest, last 3 years
            pick = lambda key, idx: [_num((rows.get(key) or [None] * len(hdr))[i]) if i < len(rows.get(key) or []) else None for i in idx]
            rk = next((k for k in rows if k.startswith("revenue")), None) or ("total income" if "total income" in rows else None)
            rev = pick(rk, annual) if rk else None
            pat = pick("profit after tax", annual) if "profit after tax" in rows else None
            nw = next((k for k in rows if k.startswith("net worth")), None)
            net_worth = pick(nw, [0])[0] if nw else None
            debt = pick("total borrowing", [0])[0] if "total borrowing" in rows else None
            years = [hdr[i] for i in annual]
            break
    eps = pe = mcap = None
    for t in tabs:
        if t[0] and t[0][0].lower().startswith("valuation metric"):
            for r in t[1:]:
                lab, vals = r[0].lower(), r[1:]
                post = next((v for v in reversed(vals) if v), "")
                if lab.startswith("eps"):
                    eps = -1.0 if "-ve" in post.lower() else _num(re.sub(r"[^\d.\-]", "", post))
                elif lab.startswith("p/e"):
                    pe = None if "-ve" in post.lower() else _num(re.sub(r"[^\d.\-]", "", post))
                elif lab.startswith("market cap"):
                    mcap = _cr(post) or _num(re.sub(r"[^\d.]", "", post.split("Cr")[0]))
    promo = rm.get("promoter and promoter group") or rm.get("promoter & promoter group")
    reviews = {}
    for t in tabs:
        if t[0] and t[0][0].lower().startswith("review by"):
            heads = [h.lower() for h in t[0][1:]]
            for r in t[1:]:
                reviews[r[0].lower()] = {h: int(_num(v) or 0) for h, v in zip(heads, r[1:])}
    ind = re.search(r"Recently Listed IPOs in ([^<]{2,60})<", page)
    industry = _text(ind.group(1)) if ind else ""
    about = ""
    txt = _text(page)
    m = re.search(r"About " + re.escape(name[:25]) + r"[^.]{0,60}?\.\s*(?:Updated on [A-Za-z]{3} \d{1,2}, \d{4}\s*)?(.{40,700}?)(?:Company Financials|Competitive Strength|$)", txt)
    if m:
        about = m.group(1).strip()[:420]
    return {"name": name, "status": status or "Open/Upcoming", "open": op, "close": cl,
            "price_high": max(nums) if nums else None, "price_band": band.replace("₹", "").strip(),
            "issue_size_cr": total, "ofs_pct": ofs_pct, "board": "SME" if "sme" in listing.lower() else "Mainboard",
            "industry": industry, "about": about,
            "sector": guess_sector(industry) or guess_sector(name) or guess_sector(about),
            "revenue": rev, "pat": pat, "fin_years": years, "net_worth": net_worth, "debt": debt,
            "eps": eps, "pe_post": pe, "market_cap_cr": mcap,
            "promoter_pre": _num((promo or [None])[0].replace("%", "")) if promo and promo[0] else None,
            "promoter_post": _num(promo[1].replace("%", "")) if promo and len(promo) > 1 and promo[1] else None,
            "reviews": reviews, "rhp_link": url, "source": "Chittorgarh (RHP figures)"}


def web_ipos(max_pages: int = 30) -> list[dict]:
    """Open + upcoming IPOs from Chittorgarh, each with figures from its own page."""
    s = requests.Session()
    page = _get(s, f"{CG}/report/ipo-in-india-list-main-board-sme/82/")
    if not page:
        print("Chittorgarh IPO list unavailable")
        return []
    links = []  # (url, status)
    for label, status in (("upcoming IPOs", "Upcoming"), ("current active IPOs", "Open")):
        m = re.search(r"The " + label + r"[^<]*?are([\s\S]{0,6000}?)</p>", page, re.I)
        for u in re.findall(r'href="((?:https://www\.chittorgarh\.com)?/ipo/[a-z0-9-]+/\d+/)"', m.group(1) if m else ""):
            u = u if u.startswith("http") else CG + u
            if u not in [x[0] for x in links]:
                links.append((u, status))
    if not links:  # page layout changed: fall back to the first IPO links on the page
        for u in dict.fromkeys(re.findall(r'href="((?:https://www\.chittorgarh\.com)?/ipo/[a-z0-9-]+/\d+/)"', page)):
            links.append((u if u.startswith("http") else CG + u, None))
    out = []
    for u, status in links[:max_pages]:
        x = cg_page(s, u, status)
        if x:
            if x["close"]:
                try:
                    if dt.date.fromisoformat(x["close"]) < dt.date.today() - dt.timedelta(days=1):
                        continue  # already closed
                except ValueError:
                    pass
            out.append(x)
        time.sleep(1)
    print(f"Chittorgarh: {len(out)} IPOs read from {len(links)} links "
          f"({sum(1 for x in out if x['revenue'] and any(v is not None for v in x['revenue']))} with financials)")
    return out


def csv_ipos() -> list[dict]:
    if not CSV.exists():
        return []
    out = []
    with open(CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not (r.get("name") or "").strip() or r["name"].startswith("#"):
                continue
            close = (r.get("close") or "").strip()
            try:
                if close and dt.date.fromisoformat(close) < dt.date.today() - dt.timedelta(days=3):
                    continue  # finished
            except ValueError:
                pass
            out.append({"name": r["name"].strip(), "symbol": (r.get("symbol") or "").strip() or None, "status": "Listed by you",
                        "open": (r.get("open") or "").strip(), "close": close,
                        "price_high": _num(r.get("price_high")), "price_band": (r.get("price_band") or "").strip(),
                        "issue_size_cr": _num(r.get("issue_size_cr")), "board": (r.get("board") or "Mainboard").strip(),
                        "sector": (r.get("sector") or "").strip() or None, "industry": (r.get("industry") or "").strip(),
                        "revenue": [_num(r.get(k)) for k in ("revenue_fy1", "revenue_fy2", "revenue_fy3")],
                        "pat": [_num(r.get(k)) for k in ("pat_fy1", "pat_fy2", "pat_fy3")],
                        "net_worth": _num(r.get("net_worth_cr")), "debt": _num(r.get("debt_cr")),
                        "eps": _num(r.get("eps_post_issue")), "ofs_pct": _num(r.get("ofs_pct")),
                        "rhp_link": (r.get("rhp_link") or "").strip(), "source": "ipos.csv"})
    return out


def _cagr(xs):
    xs = [x for x in (xs or []) if x is not None]
    if len(xs) < 2 or xs[0] <= 0 or xs[-1] <= 0:
        return None
    return ((xs[-1] / xs[0]) ** (1 / (len(xs) - 1)) - 1) * 100


def peers(details: dict, sector: str | None):
    ds = [d for d in details.values() if d.get("sector") == sector and d.get("overall") is not None]
    if not sector or len(ds) < 3:
        return None
    pes = [d["fundamentals"].get("pe") for d in ds if (d.get("fundamentals") or {}).get("pe") and 0 < d["fundamentals"]["pe"] < 200]
    ers = [d["expected_return"] for d in ds if d.get("expected_return") is not None]
    return {"count": len(ds), "avg_score": round(statistics.mean(d["overall"] for d in ds)),
            "median_pe": round(statistics.median(pes), 1) if pes else None,
            "avg_expected_return": round(statistics.mean(ers), 1) if ers else None,
            "best": [d["symbol"] for d in sorted(ds, key=lambda d: -d["overall"])[:3]]}


def analyse(ipo, details, market, newsp=None):
    sector = ipo.get("sector") or guess_sector(f"{ipo['name']} {ipo.get('industry', '')}")
    peer = peers(details, sector)
    tilt = global_mkts.sector_tilt(market, sector)
    rev_g, pat_g = _cagr(ipo.get("revenue")), _cagr(ipo.get("pat"))
    pat_last = next((x for x in reversed(ipo.get("pat") or []) if x is not None), None)
    roe = pat_last / ipo["net_worth"] * 100 if pat_last is not None and ipo.get("net_worth") else None
    de = ipo["debt"] / ipo["net_worth"] if ipo.get("debt") is not None and ipo.get("net_worth") else None
    pe = ipo["price_high"] / ipo["eps"] if ipo.get("price_high") and ipo.get("eps") and ipo["eps"] > 0 else ipo.get("pe_post")
    rel_pe = pe / peer["median_pe"] if pe and peer and peer.get("median_pe") else None
    sector_score = None
    if peer:
        sector_score = 0.7 * peer["avg_score"] + 0.3 * (tilt if tilt is not None else 50)
    params = {
        "sector": ("Sector strength (listed peers + global)", 25, sector_score, f"{sector or 'unknown'}"),
        "valuation": ("Valuation vs listed peers", 25, interp(rel_pe, [(0.6, 100), (0.9, 75), (1.1, 55), (1.4, 30), (2, 5)]) if rel_pe else
                      interp(pe, [(10, 90), (20, 70), (30, 50), (45, 30), (70, 10)]) if pe else
                      (10 if (pat_last is not None and pat_last <= 0) or (ipo.get("eps") is not None and ipo["eps"] < 0) else None),
                      f"P/E {pe:.0f} vs peers {peer['median_pe']:.0f}" if rel_pe else f"P/E {pe:.0f} (no peer data)" if pe else
                      "loss-making" if (ipo.get("eps") is not None and ipo["eps"] < 0) else None),
        "growth": ("Growth (revenue & profit CAGR)", 20, None if rev_g is None and pat_g is None else
                   interp(((rev_g or pat_g) + (pat_g or rev_g)) / 2, [(0, 10), (10, 45), (20, 75), (30, 95)]),
                   ", ".join(t for t in (f"revenue {rev_g:.0f}%/yr" if rev_g is not None else "",
                                           f"profit {pat_g:.0f}%/yr" if pat_g is not None else
                                           "profit turned positive" if (ipo.get("pat") or [None])[0] is not None and ipo["pat"][0] <= 0 < (pat_last or 0) else "") if t) or None),
        "profitability": ("Return on net worth", 10, interp(roe, [(0, 0), (10, 35), (15, 60), (22, 90)]) if roe is not None else
                          (0 if pat_last is not None and pat_last <= 0 else None),
                          None if roe is None else f"{roe:.0f}%"),
        "debt": ("Debt / net worth", 5, interp(de, [(0, 100), (0.5, 70), (1, 40), (2, 10)]) if de is not None else None,
                 None if de is None else f"{de:.2f}x"),
        "fresh_money": ("Fresh capital vs promoters selling (OFS)", 10,
                        interp(ipo.get("ofs_pct"), [(0, 100), (30, 70), (60, 35), (100, 5)]) if ipo.get("ofs_pct") is not None else None,
                        None if ipo.get("ofs_pct") is None else f"{ipo['ofs_pct']:.0f}% offer-for-sale"),
        "board": ("Listing board risk", 5, 30 if (ipo.get("board") or "").upper() == "SME" else 70, ipo.get("board")),
    }
    have = {k: v for k, v in params.items() if v[2] is not None}
    tw = sum(v[1] for v in params.values())
    cw = sum(v[1] for v in have.values())
    score = sum(v[2] * v[1] for v in have.values()) / cw if cw else None
    coverage = cw / tw
    pro, con = [], []
    if sector_score is not None:
        (pro if sector_score >= 60 else con if sector_score < 45 else []).append(
            f"{sector} stocks score {peer['avg_score']}/100 on average in today's scan; expected return {peer['avg_expected_return']}%/yr")
    if rel_pe:
        (pro if rel_pe < 0.95 else con if rel_pe > 1.2 else []).append(
            f"Priced at {pe:.0f}× earnings vs {peer['median_pe']:.0f}× for listed peers ({'cheaper' if rel_pe < 1 else 'dearer'})")
    if rev_g is not None and pat_g is not None:
        (pro if min(rev_g, pat_g) >= 18 else con if min(rev_g, pat_g) < 8 else []).append(
            f"Revenue {rev_g:.0f}%/yr, profit {pat_g:.0f}%/yr over the last 3 years")
    if roe is not None:
        (pro if roe >= 18 else con if roe < 10 else []).append(f"Return on net worth {roe:.0f}%")
    if de is not None and de > 1:
        con.append(f"High debt: {de:.1f}× net worth")
    if ipo.get("ofs_pct") is not None:
        if ipo["ofs_pct"] >= 60:
            con.append(f"{ipo['ofs_pct']:.0f}% of the issue is existing holders selling, not money for the business")
        elif ipo["ofs_pct"] <= 20:
            pro.append("Mostly fresh capital going into the business")
    if ipo.get("eps") is not None and ipo["eps"] < 0:
        con.append("Negative earnings per share after the issue: no P/E to justify the price")
    pp, pq = ipo.get("promoter_pre"), ipo.get("promoter_post")
    if pq is not None:
        if pq < 35:
            con.append(f"Promoters will own only {pq:.0f}% after the IPO" + (f" (from {pp:.0f}%)" if pp else ""))
        elif pq >= 60:
            pro.append(f"Promoters keep {pq:.0f}% after the IPO")
    rv = (ipo.get("reviews") or {}).get("brokers") or {}
    if sum(rv.values()):
        good, bad = rv.get("subscribe", 0) + rv.get("may apply", 0), rv.get("avoid", 0)
        txt = f"Broker reviews: {rv.get('subscribe', 0)} subscribe, {rv.get('may apply', 0)} may apply, {rv.get('neutral', 0)} neutral, {bad} avoid"
        (pro if good > bad else con).append(txt)
    if (ipo.get("board") or "").upper() == "SME":
        con.append("SME board: thin trading, wide price swings, larger minimum lot")
    pats = [x for x in (ipo.get("pat") or []) if x is not None]
    if pat_last is not None and pat_last <= 0:
        if len(pats) >= 2 and pats[-1] < pats[-2]:
            con.append(f"Losses are widening: ₹{abs(pats[-2]):,.0f} Cr → ₹{abs(pats[-1]):,.0f} Cr")
        else:
            con.append("Loss-making in the latest year")
    if rev_g is not None and rev_g >= 20 and (pat_last is None or pat_last <= 0):
        pro.append(f"Revenue growing fast ({rev_g:.0f}%/yr), but not yet profitable")
    if coverage < 0.5:
        verdict, call = "Not enough data yet: the IPO's financials weren't published or couldn't be read (you can add them in config/ipos.csv)", "NEED DATA"
    elif score >= 65:
        call, verdict = "APPLY", "Apply: sector, price and financials line up for long-term holding"
    elif score < 45:
        call, verdict = "AVOID", "Avoid: weak sector, rich price or weak financials"
    else:
        call, verdict = "NEUTRAL", "Neutral: only apply if you'd be happy to hold it for years at this price"
    headlines = []
    if newsp:
        try:
            headlines = newsp.headlines(ipo["name"] + " IPO", days=20, limit=5)
        except Exception:
            pass
    return {**{k: v for k, v in ipo.items() if k not in ("revenue", "pat")}, "sector": sector,
            "revenue": ipo.get("revenue"), "pat": ipo.get("pat"),
            "score": None if score is None or coverage < 0.5 else round(score), "coverage": round(coverage, 2), "call": call,
            "verdict": verdict, "for": pro, "against": con, "peers": peer,
            "params": [{"key": k, "label": v[0], "weight": v[1], "score": None if v[2] is None else round(v[2]), "value": v[3]} for k, v in params.items()],
            "headlines": headlines}


def run(details, market, newsp=None):
    byname = {}
    for x in nse_ipos() + web_ipos() + csv_ipos():  # later sources overwrite earlier ones with the same name
        k = re.sub(r"[^a-z0-9]", "", re.sub(r"\b(limited|ltd)\b", "", x["name"].lower()))
        byname[k] = {**byname.get(k, {}), **{a: b for a, b in x.items() if b not in (None, "", [])}}
    out = [analyse(x, details, market, newsp) for x in byname.values()]
    order = {"APPLY": 0, "NEUTRAL": 1, "NEED DATA": 2, "AVOID": 3}
    out.sort(key=lambda x: (order.get(x["call"], 9), -(x["score"] or 0)))
    return out
