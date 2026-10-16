"""IPO analysis: open and upcoming IPOs, their sector, valuation vs listed peers, financials, and an Apply / Avoid call.

Sources
  1. NSE IPO lists (current + upcoming). Often refused from cloud servers; skipped quietly if so.
  2. config/ipos.csv: add any IPO yourself with numbers from its prospectus (RHP). Rows here win over NSE data.

Financial figures in ipos.csv are in ₹ crore; eps in ₹; ofs_pct = % of the issue that is promoters/investors selling.
"""
import csv
import datetime as dt
import re
import statistics
from pathlib import Path

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
    (r"infra|engineer|construct|logistic|defen[cs]e|aero|industr|machin|equipment|rail|port|ship", "Industrials"),
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
                out.append({"name": name.strip(), "symbol": x.get("symbol"), "status": status,
                            "open": (x.get("issueStartDate") or "")[:11], "close": (x.get("issueEndDate") or "")[:11],
                            "price_high": max(nums) if nums else None, "price_band": band,
                            "issue_size_cr": _num(x.get("issueSize")),
                            "board": "SME" if "sme" in str(x.get("series") or x.get("isBse") or "").lower() or str(x.get("series")) == "SME" else "Mainboard",
                            "subscription": _num(x.get("noOfTime") or x.get("subscription")), "source": "NSE"})
    except Exception as e:
        print(f"NSE IPO list skipped: {e}")
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
    pe = ipo["price_high"] / ipo["eps"] if ipo.get("price_high") and ipo.get("eps") and ipo["eps"] > 0 else None
    rel_pe = pe / peer["median_pe"] if pe and peer and peer.get("median_pe") else None
    sector_score = None
    if peer:
        sector_score = 0.7 * peer["avg_score"] + 0.3 * (tilt if tilt is not None else 50)
    params = {
        "sector": ("Sector strength (listed peers + global)", 25, sector_score, f"{sector or 'unknown'}"),
        "valuation": ("Valuation vs listed peers", 25, interp(rel_pe, [(0.6, 100), (0.9, 75), (1.1, 55), (1.4, 30), (2, 5)]) if rel_pe else
                      (10 if pat_last is not None and pat_last <= 0 else None), None if rel_pe is None else f"P/E {pe:.0f} vs peers {peer['median_pe']:.0f}"),
        "growth": ("Growth (revenue & profit CAGR)", 20, None if rev_g is None and pat_g is None else
                   interp(((rev_g or pat_g) + (pat_g or rev_g)) / 2, [(0, 10), (10, 45), (20, 75), (30, 95)]),
                   f"revenue {rev_g:.0f}%/yr, profit {pat_g:.0f}%/yr" if rev_g is not None and pat_g is not None else None),
        "profitability": ("Return on net worth", 10, interp(roe, [(0, 0), (10, 35), (15, 60), (22, 90)]) if roe is not None else None,
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
    if (ipo.get("board") or "").upper() == "SME":
        con.append("SME board: thin trading, wide price swings, larger minimum lot")
    if pat_last is not None and pat_last <= 0:
        con.append("Loss-making in the latest year")
    if coverage < 0.5:
        verdict, call = "Not enough data: add prospectus figures in config/ipos.csv", "NEED DATA"
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
            "score": None if score is None else round(score), "coverage": round(coverage, 2), "call": call,
            "verdict": verdict, "for": pro, "against": con, "peers": peer,
            "params": [{"key": k, "label": v[0], "weight": v[1], "score": None if v[2] is None else round(v[2]), "value": v[3]} for k, v in params.items()],
            "headlines": headlines}


def run(details, market, newsp=None):
    byname = {}
    for x in nse_ipos() + csv_ipos():  # csv rows overwrite NSE rows with the same name
        k = re.sub(r"[^a-z0-9]", "", x["name"].lower().replace("limited", "").replace("ltd", ""))
        byname[k] = {**byname.get(k, {}), **{a: b for a, b in x.items() if b not in (None, "", [])}}
    out = [analyse(x, details, market, newsp) for x in byname.values()]
    order = {"APPLY": 0, "NEUTRAL": 1, "NEED DATA": 2, "AVOID": 3}
    out.sort(key=lambda x: (order.get(x["call"], 9), -(x["score"] or 0)))
    return out
