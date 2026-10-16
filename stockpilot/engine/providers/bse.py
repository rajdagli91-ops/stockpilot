"""BSE: daily price file (bhavcopy), volume spikes, and corporate announcements. NSE announcements best effort.

BSE serves these files publicly. NSE blocks most automated requests from cloud servers, so its feed is
tried once and skipped quietly if refused.
"""
import csv
import datetime as dt
import io
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Referer": "https://www.bseindia.com/", "Accept": "application/json, text/plain, */*"}

MAJOR = ["result", "order", "contract", "award", "acquisition", "acquire", "merger", "amalgamation", "demerger",
         "bonus", "split", "buyback", "buy back", "credit rating", "fund rais", "qip", "preferential", "rights issue",
         "joint venture", "expansion", "capacity", "commission", "launch", "approval", "usfda", "dividend",
         "strategic", "stake", "open offer", "delisting"]
RED = ["auditor resign", "resignation of auditor", "resignation of statutory auditor", "default", "insolvency",
       "fraud", "pledge", "forensic", "sebi order", "show cause", "nclt", "downgrade", "search and seizure", "raid"]


def _num(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def bhavcopy(day: dt.date) -> list[dict]:
    """All BSE equity trades for one day: code, symbol, isin, name, close, volume, turnover (₹)."""
    urls = [f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{day:%Y%m%d}_F_0000.CSV"]
    for u in urls:
        r = requests.get(u, headers=UA, timeout=30)
        if r.status_code != 200 or len(r.content) < 1000:
            continue
        rows = list(csv.DictReader(io.StringIO(r.text)))
        out = []
        for x in rows:
            out.append({"code": (x.get("FinInstrmId") or "").strip(), "symbol": (x.get("TckrSymb") or "").strip(),
                        "isin": (x.get("ISIN") or "").strip(), "name": (x.get("FinInstrmNm") or "").strip(),
                        "series": (x.get("SctySrs") or "").strip(), "close": _num(x.get("ClsPric")),
                        "volume": _num(x.get("TtlTradgVol")), "turnover": _num(x.get("TtlTrfVal"))})
        return out
    return []


def latest_bhavcopy(max_back: int = 6):
    d = dt.date.today()
    for i in range(max_back):
        day = d - dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        try:
            rows = bhavcopy(day)
            if rows:
                return day, rows
        except Exception as e:
            print(f"bhavcopy {day}: {e}")
    return None, []


def volume_spikes(day, rows, store: dict, min_turnover_cr=1.0, ratio=3.0, min_price=10):
    """Update the rolling 20-day volume store and return codes trading at >= ratio x their average."""
    spikes = []
    key = day.isoformat() if day else None
    for r in rows:
        c, v = r["code"], r["volume"]
        if not c or v is None:
            continue
        hist = store.get(c, [])
        prev = [x[1] for x in hist if x[0] != key][-20:]
        if len(prev) >= 5 and r["close"] and r["close"] >= min_price and (r["turnover"] or 0) / 1e7 >= min_turnover_cr:
            avg = sum(prev) / len(prev)
            if avg > 0 and v / avg >= ratio:
                spikes.append({**r, "vol_ratio": round(v / avg, 1)})
        if key and (not hist or hist[-1][0] != key):
            hist.append([key, v])
        store[c] = hist[-21:]
    spikes.sort(key=lambda x: -(x["turnover"] or 0))
    return spikes


def announcements(days: int = 1, pages: int = 15) -> list[dict]:
    """Corporate announcements from all BSE companies. Each tagged major / red flag."""
    to = dt.date.today()
    frm = to - dt.timedelta(days=days)
    out = []
    for p in range(1, pages + 1):
        url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno="
               f"{p}&strCat=-1&strPrevDate={frm:%Y%m%d}&strScrip=&strSearch=P&strToDate={to:%Y%m%d}&strType=C&subcategory=-1")
        r = requests.get(url, headers=UA, timeout=20)
        rows = (r.json() or {}).get("Table", []) if r.status_code == 200 else []
        if not rows:
            break
        for x in rows:
            title = " ".join(filter(None, [x.get("NEWSSUB"), x.get("HEADLINE")])).strip()
            low = (title + " " + (x.get("CATEGORYNAME") or "")).lower()
            pdf = x.get("ATTACHMENTNAME")
            out.append({"code": str(x.get("SCRIP_CD") or "").strip(), "name": (x.get("SLONGNAME") or "").strip(),
                        "title": title[:240], "category": x.get("CATEGORYNAME"), "date": (x.get("NEWS_DT") or "")[:10],
                        "link": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pdf}" if pdf else "",
                        "major": any(k in low for k in MAJOR), "red_flag": any(k in low for k in RED)})
        time.sleep(0.5)
    return out


def company_filings(bse_code: str, days: int = 90, limit: int = 10) -> list[dict]:
    if not bse_code:
        return []
    to = dt.date.today()
    frm = to - dt.timedelta(days=days)
    url = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate="
           f"{frm:%Y%m%d}&strScrip={bse_code}&strSearch=P&strToDate={to:%Y%m%d}&strType=C&subcategory=-1")
    r = requests.get(url, headers=UA, timeout=15)
    rows = (r.json() or {}).get("Table", []) if r.status_code == 200 else []
    out = []
    for x in rows[:limit]:
        subj = (x.get("NEWSSUB") or x.get("HEADLINE") or "").strip()
        pdf = x.get("ATTACHMENTNAME")
        out.append({"title": subj, "date": (x.get("NEWS_DT") or "")[:10], "source": "BSE",
                    "link": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{pdf}" if pdf else "",
                    "red_flag": any(f in subj.lower() for f in RED)})
    return out


def nse_announcements() -> list[dict]:
    """NSE corporate announcements. Usually refused from cloud servers; returns [] if so."""
    try:
        s = requests.Session()
        h = {"User-Agent": UA["User-Agent"], "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}
        s.get("https://www.nseindia.com/", headers=h, timeout=10)
        r = s.get("https://www.nseindia.com/api/corporate-announcements?index=equities", headers=h, timeout=15)
        if r.status_code != 200:
            return []
        out = []
        for x in r.json()[:400]:
            title = (x.get("desc") or "") + " " + (x.get("attchmntText") or "")
            low = title.lower()
            out.append({"symbol": x.get("symbol"), "name": x.get("sm_name"), "title": title.strip()[:240],
                        "date": (x.get("an_dt") or "")[:11], "link": x.get("attchmntFile") or "", "source": "NSE",
                        "major": any(k in low for k in MAJOR), "red_flag": any(k in low for k in RED)})
        return out
    except Exception as e:
        print(f"NSE announcements skipped: {e}")
        return []
