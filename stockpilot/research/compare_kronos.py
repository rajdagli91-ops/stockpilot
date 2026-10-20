"""StockPilot (old and new scoring) on exactly the Kronos test's stocks and dates: Nifty 50, every 10 trading days
from 2025-09-01, 1-month and 2-week outcomes. Uses research/redesign_test.py's code so the scoring is identical."""
import json
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(__file__))
import redesign_test as R  # noqa: E402

NIFTY50 = ["ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE", "BAJAJFINSV",
           "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL", "GRASIM", "HCLTECH", "HDFCBANK",
           "HDFCLIFE", "HINDALCO", "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK",
           "LT", "M&M", "MARUTI", "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
           "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TMPV", "TATASTEEL", "TCS", "TECHM", "TITAN", "TRENT", "ULTRACEMCO", "WIPRO"]


def main():
    bench = yf.download("^NSEI", period="4y", auto_adjust=True, progress=False)
    bench.columns = [c[0] if isinstance(c, tuple) else c for c in bench.columns]
    vix = yf.download("^INDIAVIX", period="4y", auto_adjust=True, progress=False)
    vix.columns = [c[0] if isinstance(c, tuple) else c for c in vix.columns]
    df = R.build_rows(R.load(NIFTY50), bench, vix, "2025-09-01", "2026-12-31")
    out = {"window": [df["date"].min(), df["date"].max()], "dates": int(df["date"].nunique()), "stocks": int(df["sym"].nunique())}
    for hz in (21, 10):
        ev = R.evaluate(df, hz)
        # direction: share of stocks whose up/down matched the side of the median score (comparable to Kronos hit rate)
        out[f"{hz}d"] = {"OLD_stockpilot": ev["OLD"], "NEW_stockpilot": ev["V2"]}
    json.dump(out, open("cmp_results.json", "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
