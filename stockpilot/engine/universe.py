"""The stock universe: every NSE equity (series EQ) from NSE's public equity list, plus your tracked stocks.

The list (with ISINs, used to match NSE symbols to BSE codes) is cached in config/universe_cache.csv
so a run still works if NSE refuses the download.
"""
import csv
import io
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "config" / "universe_cache.csv"
NSE_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"


def _read_cache():
    if not CACHE.exists():
        return []
    with open(CACHE, newline="", encoding="utf-8") as f:
        return [(r["symbol"], r["name"], r.get("isin", "")) for r in csv.DictReader(f)]


def nse_list() -> list[tuple[str, str, str]]:
    """[(symbol, name, isin)]"""
    try:
        r = requests.get(NSE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv"})
        r.raise_for_status()
        rows = [{k.strip(): (v or "").strip() for k, v in x.items()} for x in csv.DictReader(io.StringIO(r.text))]
        out = [(x["SYMBOL"], x["NAME OF COMPANY"], x.get("ISIN NUMBER", "")) for x in rows if x.get("SERIES") == "EQ"]
        if len(out) > 500:
            with open(CACHE, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["symbol", "name", "isin"])
                w.writerows(out)
            return out
    except Exception as e:
        print(f"NSE list download failed ({e}); using cache")
    return _read_cache()


def nse_equities() -> list[tuple[str, str]]:
    return [(s, n) for s, n, _ in nse_list()]


def tracked() -> list[dict]:
    """Stocks in config/watchlist.csv. Portfolio stocks are added from the encrypted portfolio file at run time."""
    p = ROOT / "config" / "watchlist.csv"
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("symbol", "").strip()]
