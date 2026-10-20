"""Walk-forward test of the Market page's crude-oil outlook (engine/providers/commodity.py), exactly as it runs live.

At every 5th trading day from 2014, the outlook is computed from Brent prices up to that day only (its odds come
from earlier days with the same set-up), then compared with what Brent actually did 1 and 3 months later.
Checks: how often its "leaning up/down" calls were right vs always guessing "up"; whether its odds are honest
(when it says 60% up, does it go up about 60% of the time?); whether the "likely range next month" held ~2 times in 3.
The news-tone part cannot be tested: there is no free archive of past oil headlines.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.environ.get("SP_DIR", "stockpilot"))
from engine.providers import commodity as C  # noqa: E402


def main():
    h = yf.download("BZ=F", period="max", auto_adjust=True, progress=False)["Close"]
    c = (h.iloc[:, 0] if isinstance(h, pd.DataFrame) else h).dropna()
    c = c[c > 0]
    rows = []
    idx = [i for i in range(len(c)) if c.index[i] >= pd.Timestamp("2014-01-01")][::5]
    for i in idx:
        if i + 63 >= len(c):
            break
        o = C.outlook(c.iloc[max(0, i - 2520):i + 1])  # the live code uses ~10 years
        if not o or "1m" not in o["prob"]:
            continue
        p1, p3 = o["prob"]["1m"], o["prob"].get("3m")
        r1, r3 = float(c.iloc[i + 21] / c.iloc[i] - 1), float(c.iloc[i + 63] / c.iloc[i] - 1)
        rows.append({"date": str(c.index[i].date()), "p1": p1["up"] / 100, "base1": p1["base_up"] / 100, "up1": r1 > 0,
                     "p3": p3["up"] / 100 if p3 else np.nan, "base3": p3["base_up"] / 100 if p3 else np.nan, "up3": r3 > 0,
                     "in_range": o["range_1m"][0] <= float(c.iloc[i + 21]) <= o["range_1m"][1], "verdict": o["verdict"]})
    p = pd.DataFrame(rows)
    res = {"about": __doc__.strip().splitlines()[0], "period": [p["date"].min(), p["date"].max()], "forecast_dates": len(p)}
    for hz in ("1", "3"):
        q = p.dropna(subset=[f"p{hz}"])
        call = q[(q[f"p{hz}"] >= 0.55) | (q[f"p{hz}"] <= 0.45)]
        right = ((call[f"p{hz}"] >= 0.55) == call[f"up{hz}"])
        brier = float(((q[f"p{hz}"] - q[f"up{hz}"]) ** 2).mean())
        brier_base = float(((q[f"base{hz}"] - q[f"up{hz}"]) ** 2).mean())
        q = q.assign(bucket=pd.cut(q[f"p{hz}"], [0, 0.4, 0.5, 0.55, 0.6, 0.7, 1.0]))
        calib = {str(k): {"said_up": round(float(g[f"p{hz}"].mean()), 2), "actually_up": round(float(g[f"up{hz}"].mean()), 2), "n": len(g)}
                 for k, g in q.groupby("bucket", observed=True)}
        res[f"{hz}_month"] = {"share_of_dates_with_a_lean": round(len(call) / len(q), 3), "lean_accuracy": round(float(right.mean()), 3),
                              "always_up_accuracy_same_dates": round(float(call[f"up{hz}"].mean()), 3),
                              "always_up_accuracy_all_dates": round(float(q[f"up{hz}"].mean()), 3),
                              "brier_score_model": round(brier, 4), "brier_score_plain_base_rate": round(brier_base, 4),
                              "model_better_than_base_rate": brier < brier_base, "calibration": calib}
    res["range_1m_held"] = round(float(p["in_range"].mean()), 3)
    res["range_1m_target"] = 0.68
    json.dump(res, open("crude_results.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
