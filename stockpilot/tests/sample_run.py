"""Offline test: runs the full engine on synthetic data (no internet).

    python -m tests.sample_run full        # or quick / portfolio / symbols INFY
Set SAMPLE_PORTFOLIO=1 to also test the encrypted portfolio report (writes config/portfolios.enc.json; delete it after).
"""
import datetime as dt
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

from engine import ipo as ipo_mod
from engine import run, secure, universe
from engine.providers import bse as real_bse


def _rng(sym):
    return np.random.default_rng(int(hashlib.md5(sym.encode()).hexdigest()[:8], 16))


class FakeProv:
    @staticmethod
    def history(sym, period="2y"):
        r = _rng(sym)
        n = 500 if period == "2y" else 65
        drift, vol = r.normal(0.0004, 0.0006), r.uniform(0.008, 0.03)
        px = 100 * np.exp(np.cumsum(r.normal(drift, vol, n)))
        return pd.DataFrame({"Close": px, "Volume": r.integers(1e4, 2e6, n)}, index=pd.bdate_range(end="2026-10-16", periods=n))

    @staticmethod
    def fundamentals(sym):
        r = _rng(sym + "f")
        return {"name": None, "sector": str(r.choice(["Financial Services", "Technology", "Energy", "Industrials", "Healthcare", "Basic Materials"])),
                "industry": "Sample", "pe": float(r.uniform(5, 60)), "pb": float(r.uniform(0.8, 8)), "roe": float(r.uniform(4, 30)),
                "debt_equity": float(r.uniform(0, 1.8)), "dividend_yield": float(r.uniform(0, 5)), "rev_cagr": float(r.uniform(-5, 25)),
                "profit_cagr": float(r.uniform(-10, 30)), "cash_conversion": float(r.uniform(0.2, 1.3)), "loss_making": False, "years": 4}


class FakeNews:
    @staticmethod
    def headlines(name, days=30, limit=12):
        r = _rng(name)
        pool = [("{} bags large order", 1), ("{} profit rises 18% in Q2", 1), ("Brokerage downgrade on {}", -1),
                ("{} shares slump after weak guidance", -1), ("{} announces dividend", 1), ("{} board meeting date", 0)]
        return [{"title": pool[i][0].format(name), "date": f"2026-10-1{j}", "link": "https://example.com", "tone": pool[i][1]}
                for j, i in enumerate(r.choice(len(pool), 4, replace=False))]

    @staticmethod
    def bse_filings(code, days=90, limit=10):
        return [{"title": "Outcome of board meeting", "date": "2026-10-10", "link": "", "red_flag": False, "source": "BSE"}] if code else []


class FakeBSE:
    volume_spikes = staticmethod(real_bse.volume_spikes)

    @staticmethod
    def latest_bhavcopy():
        day = dt.date(2026, 10, 15)
        rows = []
        for i in range(300):
            sym = f"SAMPLE{i:03d}" if i < 250 else f"BSEONLY{i:03d}"
            vol = 5e5 * (8 if i in (7, 260, 270) else 1)
            rows.append({"code": str(500000 + i), "symbol": sym, "isin": f"INE{i:06d}", "name": f"{sym} Ltd", "series": "A",
                         "close": 120.0, "volume": vol, "turnover": vol * 120})
        return day, rows

    @staticmethod
    def bhavcopy(day):
        return [{"code": str(500000 + i), "symbol": f"SAMPLE{i:03d}" if i < 250 else f"BSEONLY{i:03d}", "isin": f"INE{i:06d}",
                 "name": "x", "series": "A", "close": 120.0, "volume": 5e5, "turnover": 6e7} for i in range(300)]

    @staticmethod
    def announcements(days=1):
        return [{"code": "500012", "name": "SAMPLE012 Ltd", "title": "Bagged order worth ₹500 Cr", "category": "Company Update",
                 "date": "2026-10-16", "link": "", "major": True, "red_flag": False},
                {"code": "500280", "name": "BSEONLY280 Ltd", "title": "Resignation of statutory auditor", "category": "Company Update",
                 "date": "2026-10-16", "link": "", "major": False, "red_flag": True}]

    @staticmethod
    def nse_announcements():
        return []


if __name__ == "__main__":
    fake = [(f"SAMPLE{i:03d}", f"Sample Company {i}", f"INE{i:06d}") for i in range(250)]
    universe.nse_list = lambda: fake
    universe.nse_equities = lambda: [(a, b) for a, b, _ in fake]
    ipo_mod.nse_ipos = lambda: [{"name": "Sample Coatings Ltd", "status": "Open", "open": "2026-10-20", "close": "2026-10-22",
                                 "price_high": 400, "price_band": "380-400", "issue_size_cr": 850, "board": "Mainboard", "source": "NSE"}]
    ipo_mod.csv_ipos = lambda: [{"name": "Sample Coatings Ltd", "sector": "Basic Materials", "revenue": [620, 780, 960], "pat": [48, 66, 92],
                                 "net_worth": 540, "debt": 120, "eps": 18.5, "ofs_pct": 40},
                                {"name": "Tiny Tech SME Ltd", "board": "SME", "status": "Upcoming", "open": "2026-10-23", "price_high": 90,
                                 "sector": "Technology", "revenue": [20, 22, 25], "pat": [1, 1.1, 0.9], "net_worth": 15, "debt": 12, "eps": 2, "ofs_pct": 80}]
    if os.environ.get("SAMPLE_PORTFOLIO") == "1":
        os.environ.setdefault("PORTFOLIO_KEY", "test passphrase")
        pf = {"portfolios": [{"id": "a", "name": "Raj – Portfolio 1", "holdings": [{"symbol": "SAMPLE012", "qty": 100, "avg": 90}, {"symbol": "INFY", "qty": 50, "avg": 1400}]},
                             {"id": "b", "name": "Father – A49E068", "holdings": [{"symbol": "YESBANK", "qty": 31000, "avg": 17}, {"symbol": "LT", "qty": 225, "avg": 1777}]}]}
        (run.ROOT / "config" / "portfolios.enc.json").write_text(json.dumps(secure.encrypt(pf, os.environ["PORTFOLIO_KEY"])))
    a = sys.argv[1:] or ["full"]
    slot = os.environ.get("SLOT")
    run.main(a[0], a[1:], slot, FakeProv, FakeNews, FakeBSE, sample=True)
