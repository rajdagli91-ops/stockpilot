"""Forward track record: once a week, save what StockPilot recommended and every price, so the calls can be
graded honestly later (1, 3 and 6 months on) against the Nifty and against simply holding every stock.

data/track.json = {"start": date, "snaps": [{"date", "nifty", "picks": {cap: [symbols]}, "buy": [...], "sell": [...],
                                              "px": {symbol: price}}]}
Snapshots are never edited after they are written. The website does the grading from these snapshots plus today's
prices, so nothing is graded with hindsight.
"""
import datetime as dt

CAPS = ["All", "Large", "Mid", "Small", "Micro"]


def _blocked_by_news(r):
    nb = r.get("nw")
    if nb and nb.get("flags", 0) > 0:
        return True
    if (r.get("stage") or 0) < 3:
        return True
    return bool(nb and nb.get("tone7") is not None and nb.get("n7", 0) >= 2 and nb["tone7"] <= -0.5)


def default_picks(rows, cap, target, min_traded, min_entry=55, per_sector=2, n=5):
    """The same top picks the website shows with default settings (target, all factors on, a cap filter)."""
    pool = [r for r in rows.values() if (r.get("stage") or 0) >= 2 and r.get("overall") is not None
            and r.get("expected_return") is not None and r["expected_return"] >= target
            and (r.get("signal") == "BUY" or (r.get("signal") == "HOLD" and r["overall"] >= 60))
            and r.get("confidence") in ("High", "Medium") and (r.get("traded_cr") or 0) >= min_traded
            and (cap == "All" or r.get("cap") == cap)]
    ready = [r for r in pool if (r.get("entry") or 0) >= min_entry and not _blocked_by_news(r)]
    ready.sort(key=lambda r: -(0.55 * r["overall"] + 0.45 * r["entry"]))
    out, secs = [], {}
    for r in ready:
        s = r.get("sector") or "Other"
        if secs.get(s, 0) >= per_sector:
            continue
        out.append(r["symbol"])
        secs[s] = secs.get(s, 0) + 1
        if len(out) == n:
            break
    return out


def snapshot(store, rows, nifty_close, today, target, min_traded):
    store = store or {}
    snaps = store.get("snaps", [])
    week = today.isocalendar()[:2]
    if snaps and dt.date.fromisoformat(snaps[-1]["date"]).isocalendar()[:2] == week:
        return store  # one snapshot per week, never overwritten
    analysed = {k: r for k, r in rows.items() if (r.get("stage") or 0) >= 2 and r.get("price")}
    snaps.append({"date": today.isoformat(), "nifty": round(float(nifty_close), 2) if nifty_close else None,
                  "picks": {c: default_picks(rows, c, target, min_traded) for c in CAPS},
                  "buy": sorted(k for k, r in analysed.items() if r.get("signal") == "BUY"),
                  "sell": sorted(k for k, r in analysed.items() if r.get("signal") == "SELL"),
                  "px": {k: round(float(r["price"]), 2) for k, r in analysed.items()}})
    store["snaps"] = snaps
    store.setdefault("start", today.isoformat())
    return store
