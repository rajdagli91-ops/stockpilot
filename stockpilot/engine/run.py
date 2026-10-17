"""StockPilot engine.

Modes
  python -m engine.run full  [--slot "Pre-market"]   whole market: NSE scan + BSE movers/news + global markets + picks
  python -m engine.run quick [--slot "1 PM"]         your stocks + top candidates + today's BSE news (a few minutes)
  python -m engine.run portfolio                     only your portfolio stocks (fastest)
  python -m engine.run symbols INFY TCS              just these stocks

Full-scan funnel:
  1. Prices for every NSE equity                   -> technicals, liquidity filter
  2. Fundamentals for liquid stocks                -> scored without news
  3. News + filings for the best ~30, your stocks, and BSE stocks with major news or a volume spike
  4. Top 5 picks; your portfolio report (encrypted) sent by Telegram/email if configured
"""
import datetime as dt
import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import ipo as ipo_mod
from engine import report as rpt
from engine import entry as entry_mod
from engine import scoring, secure, universe
from engine.providers import bse as bse_p
from engine.providers import nse_files, screener
from engine.providers import global_mkts
from engine.providers import news as news_p
from engine.providers import yahoo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "data"
S = scoring.SETTINGS
SCAN = S["scan"]
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FIELDS = ["symbol", "name", "sector", "price", "overall", "signal", "confidence", "expected_return",
          "ret_12m", "traded_cr", "stage", "fundamental", "technical", "news", "exchange", "event", "horizon",
          "mcap_cr", "cap", "entry", "pe", "ec", "eg", "xc", "xt", "hold", "nw"]


def fname(sym):
    return sym.replace("&", "_") + ".json"


def jload(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def jsave(p, obj, compact=True):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":") if compact else None))


# ---------------------------------------------------------------- state
def load_state():
    j = jload(OUT / "universe.json", None)
    if j:
        return {r[0]: dict(zip(j["fields"], r)) for r in j["rows"]}, j.get("meta", {})
    return {}, {}


def save_state(rows, meta, picks, wait=()):
    ordered = sorted(rows.values(), key=lambda r: (-(r.get("stage") or 0), -(r.get("overall") or -1)))
    jsave(OUT / "universe.json", {"meta": meta, "fields": FIELDS, "rows": [[r.get(k) for k in FIELDS] for r in ordered]})
    jsave(OUT / "picks.json", {"meta": meta, "picks": picks, "wait": list(wait)}, compact=False)


def load_details():
    out = {}
    for p in (OUT / "stocks").glob("*.json") if (OUT / "stocks").exists() else []:
        d = jload(p, None)
        if d:
            out[d["symbol"]] = d
    return out


def load_portfolios():
    """Decrypt config/portfolios.enc.json with the PORTFOLIO_KEY secret. Returns [] if either is missing."""
    key, p = os.environ.get("PORTFOLIO_KEY"), ROOT / "config" / "portfolios.enc.json"
    if not key or not p.exists():
        return []
    try:
        data = secure.decrypt(json.loads(p.read_text()), key)
        return data.get("portfolios", [])
    except Exception as e:
        print(f"Could not decrypt portfolios (check PORTFOLIO_KEY matches the passphrase in Settings): {e}")
        return []


# ---------------------------------------------------------------- analysis
def liquidity(h):
    return float((h["Close"] * h["Volume"]).iloc[-20:].mean() / 1e7)  # ₹ crore/day


def stage1(names, bench, prov):
    hists = prov.bulk_history(list(names)) if hasattr(prov, "bulk_history") else {s: prov.history(s) for s in names}
    out = {}
    for sym, h in hists.items():
        if h is None or len(h) < 60:
            continue
        t = scoring.technicals(h, bench)
        out[sym] = ({"symbol": sym, "name": names.get(sym, sym), "price": round(t["price"], 2), "ret_12m": round(t["ret_12m"], 1),
                     "traded_cr": round(liquidity(h), 2), "stage": 1, "signal": "SCAN", "exchange": "NSE"}, h, t)
    return out


def full_analysis(sym, name, hist, t, prov, newsp, market, bse_code="", with_news=True, extra_filings=()):
    errors, f = [], {}
    try:
        f = prov.fundamentals(sym, t.get("price")) or {}
        f["pe_history"] = pe_history(f, hist)
    except Exception as e:
        errors.append(f"fundamentals: {e}")
    headlines, filings = [], list(extra_filings)
    if with_news:
        try:
            headlines = newsp.headlines(name)
        except Exception as e:
            errors.append(f"news: {e}")
        if bse_code:
            try:
                seen = {x["title"] for x in filings}
                filings += [x for x in newsp.bse_filings(bse_code) if x["title"] not in seen]
            except Exception as e:
                errors.append(f"filings: {e}")
    tilt = global_mkts.sector_tilt(market, f.get("sector"))
    why = ((market or {}).get("tilts") or {}).get(f.get("sector") or "", {}).get("why", [])
    sc = scoring.score_stock(f, t, headlines, filings, {"score": tilt, "why": why} if tilt is not None else None, sym)
    weekly = []
    if hist is not None and len(hist):
        w = hist["Close"].resample("W-FRI").last().dropna().iloc[-104:]
        weekly = [[d.strftime("%Y-%m-%d"), round(float(v), 2)] for d, v in w.items()]
    return {"symbol": sym, "name": f.get("name") or name, "sector": f.get("sector"), "industry": f.get("industry"),
            "price": round(t["price"], 2) if t.get("price") else None, "bse_code": bse_code,
            "fundamentals": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in f.items()},
            "technicals": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()},
            "headlines": headlines, "filings": filings[:12], "weekly": weekly, "errors": errors,
            "stage": 3 if with_news else 2, "analysed": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M"), **sc}


def row_from(d, base):
    r = dict(base)
    r.update({k: d.get(k) for k in ("name", "sector", "price", "overall", "signal", "confidence", "expected_return",
                                    "fundamental", "technical", "news", "stage", "horizon")})
    mc = (d.get("fundamentals") or {}).get("market_cap")
    if mc:
        r["mcap_cr"] = round(mc / 1e7)
    e = d.get("entry") or {}
    r["entry"] = e.get("score")
    r["pe"] = (d.get("fundamentals") or {}).get("pe")
    r["ec"], r["eg"] = entry_mod.compact(e.get("checks") or [])
    r["xc"], r["xt"] = entry_mod.compact_hold(e.get("checks") or [])
    r["hold"] = (e.get("hold") or {}).get("score")
    r["nw"] = d.get("news_brief")
    return r


def pe_history(f, hist):
    """P/E at each past financial year-end: today's P/E scaled by price then/now and profit now/then."""
    pe, ni = f.get("pe"), f.get("ni_hist") or []
    if not pe or pe <= 0 or len(ni) < 2 or hist is None or not len(hist):
        return []
    ni_now, px_now = ni[-1][1], float(hist["Close"].iloc[-1])
    out = []
    for d, v in ni[:-1]:
        if not v or v <= 0 or not ni_now or ni_now <= 0:
            continue
        try:
            px = hist["Close"][:d]
            if not len(px):
                continue
            out.append(round(pe * (float(px.iloc[-1]) / px_now) * (ni_now / v), 1))
        except Exception:
            continue
    return out


def rebuild_entries(details, rows, ctx):
    """Recompute every stock's entry checklist with the full context (sector P/E, screener, deals...)."""
    by_sec = {}
    for d in details.values():
        f = d.get("fundamentals") or {}
        if f.get("pe") and 0 < f["pe"] < 200 and f.get("sector"):
            by_sec.setdefault(f["sector"], []).append(f["pe"])
    import statistics
    ctx["sector_pe"] = {k: statistics.median(v) for k, v in by_sec.items() if len(v) >= 5}
    for sym, d in details.items():
        t, f = d.get("technicals") or {}, d.get("fundamentals") or {}
        e = entry_mod.summarize(entry_mod.build(sym, f, t, ctx), t)
        if e:
            e["pe"] = round(f["pe"], 1) if f.get("pe") else None
            d["entry"] = e
            if sym in rows:
                rows[sym]["entry"] = e["score"]
                rows[sym]["ec"], rows[sym]["eg"] = entry_mod.compact(e["checks"])
                rows[sym]["xc"], rows[sym]["xt"] = entry_mod.compact_hold(e["checks"])
                rows[sym]["hold"] = (e.get("hold") or {}).get("score")
                rows[sym]["pe"] = f.get("pe")


def news_pool(rows, per_cap):
    """Every stock that could become a top pick under any cap filter or a lower return target: the best
    `per_cap` of each cap bucket by the pick ranking (business score + entry timing), with a loose filter."""
    pool = [r for r in rows.values() if (r.get("stage") or 0) >= 2 and r.get("overall") is not None
            and (r.get("signal") == "BUY" or (r.get("signal") == "HOLD" and r["overall"] >= 60))
            and r.get("confidence") in ("High", "Medium") and (r.get("expected_return") or 0) >= 12
            and (r.get("traded_cr") or 0) >= SCAN["min_traded_cr"]]
    pool.sort(key=lambda r: -(0.55 * r["overall"] + 0.45 * (r.get("entry") or 0)))
    out, n = [], {}
    for r in pool:
        c = r.get("cap") or "Micro"
        if n.get(c, 0) < per_cap:
            out.append(r["symbol"])
            n[c] = n.get(c, 0) + 1
    return out


def add_news(sym, d, newsp, market, bse_code="", extra=()):
    """Fetch latest headlines + filings for an already analysed stock and re-score it (no fundamentals refetch)."""
    f, t = dict(d.get("fundamentals") or {}), dict(d.get("technicals") or {})
    headlines, filings, errors = [], list(extra), []
    try:
        headlines = newsp.headlines(d.get("name") or sym)
    except Exception as e:
        errors.append(f"news: {e}")
    if bse_code:
        try:
            seen = {x["title"] for x in filings}
            filings += [x for x in newsp.bse_filings(bse_code) if x["title"] not in seen]
        except Exception as e:
            errors.append(f"filings: {e}")
    tilt = global_mkts.sector_tilt(market, f.get("sector"))
    why = ((market or {}).get("tilts") or {}).get(f.get("sector") or "", {}).get("why", [])
    sc = scoring.score_stock(f, t, headlines, filings, {"score": tilt, "why": why} if tilt is not None else None, sym)
    d.update(sc)
    d.update({"headlines": headlines, "filings": filings[:12], "stage": 3, "errors": (d.get("errors") or []) + errors,
              "news_checked": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M")})
    return d


def assign_caps(rows):
    """SEBI-style buckets by market-cap rank: 1-100 Large, 101-250 Mid, 251-500 Small, rest Micro."""
    ranked = sorted((r for r in rows.values() if r.get("mcap_cr")), key=lambda r: -r["mcap_cr"])
    for i, r in enumerate(ranked, 1):
        r["cap"] = "Large" if i <= 100 else "Mid" if i <= 250 else "Small" if i <= 500 else "Micro"


def choose_picks(rows, details):
    """Top picks = good business (BUY) AND a sensible entry price today. BUYs that are too stretched go to a wait list."""
    min_entry = SCAN.get("min_entry_score", 55)
    pool = [r for r in rows.values() if r.get("stage") == 3 and r.get("signal") == "BUY"
            and r.get("confidence") in ("High", "Medium") and (r.get("traded_cr") or 0) >= SCAN["min_traded_cr"]]
    entry = lambda r: ((details.get(r["symbol"]) or {}).get("entry") or {})
    ready = [r for r in pool if (entry(r).get("score") or 0) >= min_entry]
    ready.sort(key=lambda r: -(0.55 * (r["overall"] or 0) + 0.45 * entry(r)["score"]))
    picks, sectors = [], {}
    for r in ready:
        sec = r.get("sector") or "Other"
        if sectors.get(sec, 0) >= SCAN["max_picks_per_sector"]:
            continue
        d = details.get(r["symbol"]) or {}
        t = d.get("technicals") or {}
        picks.append({**r, "strengths": d.get("strengths"), "weaknesses": d.get("weaknesses"), "points": d.get("points"),
                      "headlines": (d.get("headlines") or [])[:3], "vs_200dma": t.get("vs_200dma"), "rsi": t.get("rsi"),
                      "entry": d.get("entry")})
        sectors[sec] = sectors.get(sec, 0) + 1
        if len(picks) == SCAN["top_picks"]:
            break
    chosen = {p["symbol"] for p in picks}
    wait = sorted((r for r in pool if r["symbol"] not in chosen and entry(r) and entry(r).get("score", 100) < min_entry),
                  key=lambda r: -(r["overall"] or 0))[:8]
    wait = [{k: r.get(k) for k in ("symbol", "name", "sector", "price", "overall", "expected_return", "horizon")} |
            {"entry": entry(r)} for r in wait]
    return picks, wait


# ---------------------------------------------------------------- BSE / NSE events
def exchange_events(mode, nse_isin, bsep, rows):
    """BSE stocks with major announcements or (full scan) a volume spike. Returns (events, code_of_nse_symbol)."""
    events, code_of, bse_only = [], {}, {}
    isin_to_nse = {isin: sym for sym, isin in nse_isin.items() if isin}
    code_info = {}
    if mode == "full":
        try:
            day, bhav = bsep.latest_bhavcopy()
            store = jload(OUT / "bse_volumes.json", {})
            if day and len({d for v in store.values() for d, _ in v}) < 5 and hasattr(bsep, "bhavcopy"):
                # first run: backfill ~20 trading days so volume spikes can be measured straight away
                back = day
                for _ in range(28):
                    back -= dt.timedelta(days=1)
                    if back.weekday() >= 5:
                        continue
                    try:
                        old = bsep.bhavcopy(back)
                    except Exception:
                        old = []
                    for r in old:
                        if r["code"] and r["volume"] is not None:
                            store.setdefault(r["code"], []).insert(0, [back.isoformat(), r["volume"]])
                for c in store:
                    store[c] = sorted({d: v for d, v in store[c]}.items())[-21:]
                    store[c] = [list(x) for x in store[c]]
            for r in bhav:
                code_info[r["code"]] = r
                if r["isin"] in isin_to_nse:
                    code_of[isin_to_nse[r["isin"]]] = r["code"]
            spikes = bsep.volume_spikes(day, bhav, store, SCAN.get("bse_min_turnover_cr", 1), SCAN.get("bse_volume_ratio", 3))
            jsave(OUT / "bse_volumes.json", store)
            jsave(OUT / "bse_codes.json", code_of)
            jsave(OUT / "bse_symbols.json", {c: r["symbol"] for c, r in code_info.items() if r.get("symbol")})
            for s in spikes[:SCAN.get("bse_max_events", 40)]:
                sym = isin_to_nse.get(s["isin"]) or f"{s['symbol']}.BO"
                events.append({"symbol": sym, "name": s["name"], "type": "volume", "exchange": "NSE" if sym in nse_isin else "BSE",
                               "detail": f"Volume {s['vol_ratio']}× its 20-day average · ₹{(s['turnover'] or 0) / 1e7:.1f} Cr traded",
                               "date": day.isoformat() if day else "", "link": ""})
            print(f"BSE: {len(bhav)} stocks in bhavcopy {day}, {len(spikes)} volume spikes")
        except Exception as e:
            print(f"BSE bhavcopy skipped: {e}")
    else:
        code_of = jload(OUT / "bse_codes.json", {})
        code_info = {c: {"symbol": t} for c, t in jload(OUT / "bse_symbols.json", {}).items()}
    try:
        days = 3 if dt.date.today().weekday() == 0 else 1
        anns = bsep.announcements(days)
        major = [a for a in anns if a["major"] or a["red_flag"]]
        nse_by_code = {c: s for s, c in code_of.items()}
        for a in major:
            sym = nse_by_code.get(a["code"])
            info = code_info.get(a["code"])
            if not sym:
                sym = f"{info['symbol']}.BO" if info and info.get("symbol") else None
            if not sym:
                continue
            events.append({"symbol": sym, "name": a["name"], "type": "red_flag" if a["red_flag"] else "news",
                           "exchange": "NSE" if sym in nse_isin else "BSE", "detail": a["title"], "date": a["date"],
                           "link": a["link"], "category": a.get("category")})
        print(f"BSE announcements: {len(anns)} total, {len(major)} major or red-flag")
    except Exception as e:
        print(f"BSE announcements skipped: {e}")
    for a in bsep.nse_announcements():
        if (a["major"] or a["red_flag"]) and a.get("symbol"):
            events.append({"symbol": a["symbol"], "name": a["name"], "type": "red_flag" if a["red_flag"] else "news",
                           "exchange": "NSE", "detail": a["title"], "date": a["date"], "link": a["link"]})
    return events, code_of


# ---------------------------------------------------------------- main
def main(mode="quick", symbols=None, slot=None, prov=yahoo, newsp=news_p, bsep=bse_p, sample=False):
    started = dt.datetime.now(IST)
    rows, meta = load_state()
    details = {} if mode == "full" else load_details()
    failed = []
    bench = None
    try:
        bench = prov.history(S["benchmark"])
    except Exception:
        print("benchmark failed", file=sys.stderr)

    # ---- global markets
    market = jload(OUT / "market.json", {})
    try:
        market = global_mkts.fetch(prov)
        market["updated"] = dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
        jsave(OUT / "market.json", market, compact=False)
        print(f"global mood {market.get('mood')} ({market.get('bias')})")
    except Exception as e:
        print(f"global markets skipped: {e}")

    # ---- shared entry-check context: market regime, delivery %, bulk/block deals, screener cache
    ctx = scoring.CTX
    ctx.clear()
    try:
        vix = next((m.get("last") for m in market.get("markets", []) if m.get("key") == "india_vix"), None)
        vix = vix if vix and 5 < vix < 80 else None
        ctx["market"] = entry_mod.market_regime(bench["Close"] if bench is not None and len(bench) else None, vix)
    except Exception as e:
        print(f"market regime skipped: {e}")
    if mode in ("full", "quick") and not sample:
        try:
            ctx["delivery"] = nse_files.delivery()
        except Exception as e:
            print(f"delivery skipped: {e}")
        try:
            ctx["deals"] = nse_files.merge_deals(jload(OUT / "deals.json", []), nse_files.deals())
            jsave(OUT / "deals.json", ctx["deals"])
        except Exception as e:
            print(f"deals skipped: {e}")
    else:
        ctx["deals"] = jload(OUT / "deals.json", [])
    ctx["screener"] = jload(OUT / "screener.json", {})
    ctx["today"] = dt.datetime.now(IST).date()

    # ---- who to track: watchlist + your (encrypted) portfolios
    portfolios = load_portfolios()
    track = {r["symbol"].strip().upper(): r for r in universe.tracked()}
    for p in portfolios:
        for h in p.get("holdings", []):
            track.setdefault(h["symbol"].upper(), {"symbol": h["symbol"], "name": h.get("raw") or h["symbol"]})
    print(f"tracking {len(track)} stocks from watchlist + {len(portfolios)} portfolios")

    nse = universe.nse_list() if mode == "full" else []
    nse_isin = {s: i for s, _, i in nse} if nse else {r["symbol"]: "" for r in rows.values() if r.get("exchange") != "BSE"}
    events, code_of = exchange_events(mode, nse_isin, bsep, rows) if mode in ("full", "quick") else ([], jload(OUT / "bse_codes.json", {}))
    for sym, r in track.items():
        if r.get("bse_code"):
            code_of[sym] = r["bse_code"].strip()
    event_syms = list(dict.fromkeys(e["symbol"] for e in events if e["type"] in ("news", "volume", "red_flag")))
    ann_by_sym = {}
    for e in events:
        if e["type"] != "volume":
            ann_by_sym.setdefault(e["symbol"], []).append({"title": e["detail"], "date": e["date"], "link": e["link"],
                                                            "red_flag": e["type"] == "red_flag", "source": e.get("exchange")})

    hist_of = {}
    if mode == "full":
        names = {s: n for s, n, _ in nse} or {s: r.get("name") or s for s, r in track.items()}
        for s, r in track.items():
            names.setdefault(s, r.get("name") or s)
        print(f"stage 1: prices for {len(names)} NSE stocks")
        s1 = stage1(names, bench, prov)
        rows = {sym: row for sym, (row, _, _) in s1.items()}
        min_an = SCAN.get("analyse_min_traded_cr", SCAN["min_traded_cr"])
        liquid = sorted((s for s, (r, _, _) in s1.items() if r["traded_cr"] >= min_an and r["price"] >= SCAN["min_price"]),
                        key=lambda s: -s1[s][0]["traded_cr"])
        cand2 = list(dict.fromkeys(liquid[:SCAN["max_fundamentals"]] + [s for s in track if s in s1]))
        print(f"stage 2: fundamentals for {len(cand2)} stocks trading ≥ ₹{min_an} Cr/day (+ yours)")

        def do2(sym):
            row, h, t = s1[sym]
            try:
                return sym, full_analysis(sym, row["name"], h, t, prov, newsp, market, with_news=False)
            except Exception:
                traceback.print_exc()
                return sym, None

        with ThreadPoolExecutor(SCAN["workers"]) as ex:
            for i, (sym, d) in enumerate(ex.map(do2, cand2), 1):
                if d:
                    details[sym] = d
                    rows[sym] = row_from(d, rows[sym])
                if i % 100 == 0:
                    print(f"  {i}/{len(cand2)}")
        ranked = sorted(details, key=lambda s: -(details[s]["overall"] or 0))
        cand3 = list(dict.fromkeys(ranked[:SCAN["news_candidates"]] + list(track) + event_syms))
        for s in cand3:
            if s in s1:
                hist_of[s] = (s1[s][1], s1[s][2])
    else:
        if mode == "symbols":
            cand3 = [s.strip().upper() for s in (symbols or []) if s.strip()]
        elif mode == "portfolio":
            cand3 = list(track)
        else:
            prev = sorted((r for r in rows.values() if (r.get("stage") or 0) >= 2), key=lambda r: -(r.get("overall") or 0))
            cand3 = list(dict.fromkeys(list(track) + [r["symbol"] for r in prev[:SCAN["news_candidates"]]] + event_syms))
    # prices for anything not already loaded (non-full modes, BSE-only event stocks)
    for sym in cand3:
        if sym in hist_of:
            continue
        try:
            h = prov.history(sym)
            if h is None or len(h) < 60:
                if sym in track or mode == "symbols":
                    failed.append(sym)
                continue
            t = scoring.technicals(h, bench)
            hist_of[sym] = (h, t)
            base = rows.get(sym) or {"symbol": sym, "name": (track.get(sym) or {}).get("name") or sym.replace(".BO", "")}
            base.update({"price": round(t["price"], 2), "traded_cr": round(liquidity(h), 2), "ret_12m": round(t["ret_12m"], 1),
                         "exchange": "BSE" if sym.endswith(".BO") else "NSE"})
            rows[sym] = base
        except Exception as e:
            print(f"{sym}: {e}")
            if sym in track or mode == "symbols":
                failed.append(sym)
    cand3 = [s for s in cand3 if s in hist_of]

    print(f"stage 3: news + filings for {len(cand3)} stocks")
    ev_types = {}
    for e in events:
        ev_types.setdefault(e["symbol"], set()).add(e["type"])
    for sym in cand3:
        h, t = hist_of[sym]
        try:
            name = (rows.get(sym) or {}).get("name") or sym
            d = full_analysis(sym, name, h, t, prov, newsp, market, code_of.get(sym, ""), True, ann_by_sym.get(sym, []))
            details[sym] = d
            rows[sym] = row_from(d, rows.get(sym) or {"symbol": sym})
            if sym in ev_types:
                rows[sym]["event"] = ",".join(sorted(ev_types[sym]))
        except Exception:
            traceback.print_exc()
            failed.append(sym)

    # ---- latest news for every possible top pick (any cap filter) that stage 3 did not already cover
    if mode in ("full", "quick") and not sample:
        assign_caps(rows)
        done = set(cand3)
        extra = [s_ for s_ in news_pool(rows, SCAN.get("news_per_cap", 25) if mode == "full" else SCAN.get("news_per_cap_quick", 12))
                 if s_ not in done and s_ in details]
        print(f"stage 3b: latest news for {len(extra)} more pick candidates")

        def do3(sym):
            try:
                return sym, add_news(sym, details[sym], newsp, market, code_of.get(sym, ""), ann_by_sym.get(sym, []))
            except Exception:
                traceback.print_exc()
                return sym, None
        with ThreadPoolExecutor(4) as ex:
            for sym, d in ex.map(do3, extra):
                if d:
                    details[sym] = d
                    rows[sym] = row_from(d, rows[sym])

    sd = OUT / "stocks"
    sd.mkdir(parents=True, exist_ok=True)
    if mode == "full":
        for p in sd.glob("*.json"):
            p.unlink()
    for sym, d in details.items():
        jsave(sd / fname(sym), d)

    # events page: attach the score each event stock got
    for e in events:
        r = rows.get(e["symbol"]) or {}
        e.update({"signal": r.get("signal"), "score": r.get("overall")})
    if mode == "quick":  # keep this morning's volume spikes (they come from the end-of-day BSE file)
        seen = {(e["symbol"], e["type"]) for e in events}
        events += [e for e in jload(OUT / "events.json", {}).get("events", []) if e["type"] == "volume" and (e["symbol"], e["type"]) not in seen]
    if mode in ("full", "quick"):
        jsave(OUT / "events.json", {"updated": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), "events": events[:200]}, compact=False)

    # ---- screener.in (results date, shareholding) for the stocks that matter, then the full entry checklist
    if not sample:
        try:
            cand = sorted((s_ for s_, r in rows.items() if (r.get("stage") or 0) >= 2 and r.get("signal") in ("BUY", "HOLD")
                           and (r.get("expected_return") or 0) >= 12 and (r.get("traded_cr") or 0) >= SCAN["min_traded_cr"]),
                          key=lambda s_: -((rows[s_].get("overall") or 0) * 0.55 + (rows[s_].get("entry") or 0) * 0.45))
            order = list(dict.fromkeys(list(track) + cand[:250]))
            ctx["screener"] = screener.refresh([s_ for s_ in order if not s_.endswith(".BO")], ctx.get("screener") or {},
                                               max_fetch=150 if mode == "full" else 25)
            jsave(OUT / "screener.json", ctx["screener"])
        except Exception:
            traceback.print_exc()
    rebuild_entries(details, rows, ctx)
    for sym, d in details.items():
        jsave(sd / fname(sym), d)

    assign_caps(rows)
    picks, wait = choose_picks(rows, details)
    ipos = []
    if mode in ("full", "quick"):
        try:
            ipos = ipo_mod.run(details, market, newsp)
            jsave(OUT / "ipos.json", {"updated": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), "ipos": ipos}, compact=False)
            print(f"IPOs analysed: {len(ipos)}")
        except Exception:
            traceback.print_exc()
    else:
        ipos = jload(OUT / "ipos.json", {}).get("ipos", [])
    meta.update({
        "updated": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"), "mode": mode, "slot": slot,
        "duration_min": round((dt.datetime.now(IST) - started).total_seconds() / 60, 1),
        "target_cagr": S["target_cagr"], "sample": sample, "failed": failed[:50],
        "scanned": len(rows), "analysed": sum(1 for r in rows.values() if (r.get("stage") or 0) >= 2),
        "deep": sum(1 for r in rows.values() if r.get("stage") == 3), "events": len(events),
        "mood": market.get("mood"), "bias": market.get("bias"),
        "weights": S["weights"], "thresholds": S["thresholds"], "scan": SCAN,
        "entry_checks": [{"k": k, "label": v[0], "group": v[1], "w": v[2], "cap": v[3]} for k, v in entry_mod.CHECKS.items()],
        "hold_caps": entry_mod.HOLD_CAP,
        "market_regime": ctx.get("market"),
        "entry_sources": {"delivery_days": len(next(iter((ctx.get("delivery") or {}).values()), [])),
                          "deals_30d": len(ctx.get("deals") or []), "screener_cached": len(ctx.get("screener") or {})},
    })
    if mode == "full":
        meta["last_full_scan"] = meta["updated"]
    save_state(rows, meta, picks, wait)

    # ---- encrypted portfolio report + delivery
    key = os.environ.get("PORTFOLIO_KEY")
    if key:  # lets the website check the user ID + password without storing them anywhere public
        jsave(OUT / "lock.enc.json", secure.encrypt({"ok": True, "user": os.environ.get("LOGIN_USER", "")}, key))
    if portfolios and key:
        hist = []
        old = jload(OUT / "report.enc.json", None)
        if old:
            try:
                hist = secure.decrypt(old, key).get("history", [])
            except Exception:
                hist = []
        rep = rpt.build(portfolios, rows, details, picks, market, hist[0] if hist else None, ipos,
                        slot or {"full": "Full scan", "quick": "Update", "portfolio": "Portfolio fetch"}.get(mode, "Update"), S["target_cagr"])
        hist = [rep] + hist[:14]
        jsave(OUT / "report.enc.json", secure.encrypt({"history": hist}, key))
        if slot or os.environ.get("SEND_ALWAYS") == "1":
            site = os.environ.get("SITE_URL", "")
            sent = rpt.send(rpt.as_text(rep, site), f"StockPilot {rep['slot']} report · {rep['time']}")
            print(f"report sent via: {', '.join(sent) or 'nothing configured'}")
    print(f"done in {meta['duration_min']} min: {len(rows)} stocks, {len(picks)} picks, {len(events)} events, {len(failed)} failed")


if __name__ == "__main__":
    args = sys.argv[1:]
    slot = None
    if "--slot" in args:
        i = args.index("--slot")
        slot = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    if args and args[0] in ("full", "quick", "portfolio", "symbols"):
        main(args[0], args[1:], slot)
    else:
        main("symbols" if args else "quick", args, slot)
