"""NSE public archive files (no cookies needed): daily delivery % and bulk/block deals."""
import csv
import datetime as dt
import io
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Referer": "https://www.nseindia.com/"}


def _get(url):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        return r.text if r.status_code == 200 and len(r.text) > 200 else None
    except Exception as e:
        print(f"nse {url}: {e}")
        return None


def delivery(days: int = 10, max_back: int = 20) -> dict:
    """{symbol: [delivery %, oldest first]} for the last `days` trading days (EQ series)."""
    out, got, d = {}, 0, dt.date.today()
    for _ in range(max_back):
        if got >= days:
            break
        if d.weekday() < 5:
            txt = _get(f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv")
            if txt:
                got += 1
                for r in csv.DictReader(io.StringIO(txt)):
                    r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
                    if r.get("SERIES") != "EQ":
                        continue
                    try:
                        out.setdefault(r["SYMBOL"], []).insert(0, float(r["DELIV_PER"]))
                    except (ValueError, KeyError):
                        pass
                time.sleep(0.5)
        d -= dt.timedelta(days=1)
    print(f"NSE delivery: {got} days, {len(out)} symbols")
    return out


def deals() -> list[dict]:
    """Latest day's bulk and block deals."""
    out = []
    for kind in ("bulk", "block"):
        txt = _get(f"https://nsearchives.nseindia.com/content/equities/{kind}.csv")
        if not txt:
            continue
        for r in csv.DictReader(io.StringIO(txt)):
            r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
            sym = r.get("Symbol")
            if not sym or sym == "NO RECORDS":
                continue
            try:
                qty = float(r.get("Quantity Traded", "0").replace(",", ""))
                px = float((r.get("Trade Price / Wght. Avg. Price") or "0").replace(",", ""))
            except ValueError:
                continue
            try:
                day = dt.datetime.strptime(r.get("Date", ""), "%d-%b-%Y").date().isoformat()
            except ValueError:
                day = dt.date.today().isoformat()
            out.append({"date": day, "symbol": sym, "client": r.get("Client Name", "")[:60], "side": r.get("Buy/Sell", "").upper(),
                        "qty": qty, "value_cr": round(qty * px / 1e7, 2), "kind": kind})
    print(f"NSE deals: {len(out)}")
    return out


def merge_deals(store: list, new: list, keep_days: int = 30) -> list:
    """Rolling 30-day store of deals (the archive only shows the latest day)."""
    seen = {(x["date"], x["symbol"], x["client"], x["side"], x["qty"]) for x in store}
    store = store + [x for x in new if (x["date"], x["symbol"], x["client"], x["side"], x["qty"]) not in seen]
    cut = (dt.date.today() - dt.timedelta(days=keep_days)).isoformat()
    return [x for x in store if x["date"] >= cut]
