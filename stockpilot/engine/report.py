"""Portfolio reports (pre-market, 1 PM, 3 PM, on demand) and delivery by Telegram / email.

Reports contain your holdings, so they are written encrypted (data/report.enc.json) and only
sent to channels you configure as GitHub secrets.
"""
import datetime as dt
import os
import smtplib
from email.mime.text import MIMEText

import requests

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _action(row, weight, detail=None, min_entry=55):
    """Same call as the website's portfolio table (all entry/exit factors on)."""
    sig = (row or {}).get("signal")
    er = (row or {}).get("expected_return")
    if not row or (row.get("stage") or 0) < 2 or sig in (None, "SCAN", "NO DATA"):
        return "NOT ANALYSED"
    hold = ((detail or {}).get("entry") or {}).get("hold") or {}
    hs, trims, broken = hold.get("score"), hold.get("trim") or [], "trend" in (hold.get("capped_by") or [])
    if sig == "SELL":
        return "EXIT (no rush)" if hs is not None and hs >= 55 and not broken else "EXIT"
    if hs is not None and hs < 35:
        return "CONSIDER EXIT"
    if len(trims) >= 2 and not broken:
        return "BOOK PART PROFIT"
    if sig == "BUY":
        if weight >= 0.08:
            return "HOLD (already large)"
        if (row.get("horizon") or {}).get("term") == "Short term":
            return "SHORT-TERM TRADE"
        e = row.get("entry")
        return "HOLD (add on dip)" if e is not None and e < min_entry else "ADD MORE"
    if er is not None and er < 12:
        return "HOLD / REVIEW"
    return "HOLD"


def build(portfolios, rows, details, picks, market, prev, ipos, slot, target=20):
    prev_sig = {}
    for p in (prev or {}).get("portfolios", []):
        for h in p.get("holdings", []):
            prev_sig[(p["name"], h["symbol"])] = h.get("action") or h.get("signal")
    out = []
    for p in portfolios:
        hs, h_raw = [], {}
        inv = val = 0.0
        for h in p.get("holdings", []):
            sym = h["symbol"]
            r = rows.get(sym) or {}
            px = r.get("price")
            i = h["qty"] * h["avg"]
            v = h["qty"] * px if px else i
            inv += i
            val += v
            h_raw[sym] = h.get("raw")
            hs.append({"symbol": sym, "qty": h["qty"], "avg": h["avg"], "price": px, "invested": i, "value": v, "row": r})
        res = []
        ws = wsv = we = wev = 0.0
        for x in hs:
            w = x["value"] / val if val else 0
            r = x["row"]
            d = details.get(x["symbol"]) or {}
            pts = d.get("points") or {}
            act = _action(r, w, d)
            if r.get("overall") is not None and (r.get("stage") or 0) >= 2:
                ws += r["overall"] * x["value"]; wsv += x["value"]
            if r.get("expected_return") is not None:
                we += r["expected_return"] * x["value"]; wev += x["value"]
            res.append({"symbol": x["symbol"], "qty": x["qty"], "avg": x["avg"], "price": x["price"],
                        "value": round(x["value"]), "weight": round(w * 100, 2),
                        "gain_pct": round((x["price"] / x["avg"] - 1) * 100, 1) if x["price"] else None,
                        "signal": r.get("signal"), "score": r.get("overall"), "hold": r.get("hold"), "raw": h_raw.get(x["symbol"]), "expected_return": r.get("expected_return"),
                        "action": act, "verdict": pts.get("verdict"),
                        "horizon": (r.get("horizon") or {}).get("term"), "period": (r.get("horizon") or {}).get("period"),
                        "horizon_note": (r.get("horizon") or {}).get("note"),
                        "for": (pts.get("for") or [])[:3], "against": (pts.get("against") or [])[:3],
                        "prev_signal": prev_sig.get((p["name"], x["symbol"]))})
        res.sort(key=lambda z: -z["value"])
        er = we / wev if wev else None
        alerts = []
        big = [z for z in res if z["weight"] > 10]
        if big:
            alerts.append("Over 10% in one stock: " + ", ".join(f"{z['symbol']} {z['weight']:.0f}%" for z in big))
        tiny = [z for z in res if z["weight"] < 1]
        if len(tiny) > 5:
            alerts.append(f"{len(tiny)} positions under 1% each ({sum(z['weight'] for z in tiny):.1f}% in total)")
        sect = {}
        for z in res:
            s = (rows.get(z["symbol"]) or {}).get("sector") or "Unknown"
            sect[s] = sect.get(s, 0) + z["weight"]
        alerts += [f"{s} is {w:.0f}% of the portfolio" for s, w in sect.items() if w > 25 and s != "Unknown"]
        out.append({"id": p.get("id"), "name": p["name"], "value": round(val), "invested": round(inv),
                    "gain_pct": round((val / inv - 1) * 100, 1) if inv else None,
                    "health": round(ws / wsv) if wsv else None,
                    "expected_return": round(er, 1) if er is not None else None,
                    "gap": round(er - target, 1) if er is not None else None,
                    "changes": [{"symbol": z["symbol"], "from": z["prev_signal"], "to": z["action"]}
                                for z in res if z["prev_signal"] and z["prev_signal"] != z["action"] and z["prev_signal"] not in ("BUY", "HOLD", "SELL")],
                    "alerts": alerts, "holdings": res})
    m = market or {}
    movers = [x for x in m.get("markets", []) if x.get("chg_1d") is not None and x["key"] in
              ("sp500", "nasdaq", "nikkei", "hangseng", "brent", "usdinr", "us10y", "us_vix")]
    return {"slot": slot, "time": dt.datetime.now(IST).strftime("%Y-%m-%d %H:%M IST"),
            "market": {"mood": m.get("mood"), "bias": m.get("bias"),
                       "movers": [{"label": x["label"], "chg_1d": x["chg_1d"]} for x in movers]},
            "picks": [{"symbol": x["symbol"], "score": x.get("overall"), "expected_return": x.get("expected_return"),
                       "horizon": (x.get("horizon") or {}).get("term")} for x in picks],
            "ipos": [{"name": x["name"], "call": x["call"], "score": x.get("score"), "open": x.get("open"), "close": x.get("close"),
                      "reason": ((x.get("for") if x["call"] == "APPLY" else x.get("against")) or [x.get("verdict")])[0]} for x in (ipos or [])[:6]],
            "portfolios": out}


def as_text(rep, site_url=""):
    def pc(x):
        return "—" if x is None else f"{x:+.1f}%"

    def inr(x):
        return f"₹{x / 1e7:.2f} Cr" if x >= 1e7 else f"₹{x / 1e5:.2f} L"
    m = rep["market"]
    L = [f"StockPilot · {rep['slot']} · {rep['time']}"]
    if m.get("mood") is not None:
        L.append(f"Global mood {m['mood']}/100 ({m['bias']}): " + ", ".join(f"{x['label']} {pc(x['chg_1d'])}" for x in m["movers"]))
    if rep["picks"]:
        L.append("Top picks: " + ", ".join(f"{x['symbol']} ({x['score']:.0f}, {x.get('horizon') or ''})" for x in rep["picks"] if x.get("score") is not None))
    if rep.get("ipos"):
        L.append("IPOs: " + " | ".join(f"{x['name']} → {x['call']}" + (f" ({x['reason']})" if x.get("reason") else "") for x in rep["ipos"]))
    for p in rep["portfolios"]:
        L.append("")
        L.append(f"■ {p['name']}: {inr(p['value'])} ({pc(p['gain_pct'])} on cost) · health {p['health'] or '—'}/100 · "
                 f"expected {pc(p['expected_return'])}/yr" + (f" ({abs(p['gap']):.1f} pts short of target)" if (p['gap'] or 0) < 0 else ""))
        if p["changes"]:
            L.append("Changed: " + ", ".join(f"{c['symbol']} {c['from']}→{c['to']}" for c in p["changes"]))
        for act in ("EXIT", "EXIT (no rush)", "CONSIDER EXIT", "BOOK PART PROFIT", "ADD MORE", "SHORT-TERM TRADE", "HOLD / REVIEW"):
            hs = [h for h in p["holdings"] if h["action"] == act]
            if not hs:
                continue
            L.append(f"{act} ({len(hs)}):")
            for h in hs[:8]:
                why = (h["against"] if act != "ADD MORE" else h["for"])[:2]
                L.append(f" • {h['symbol']} {h['weight']:.1f}% · score {h['score'] or '—'}" + (f" · {h['horizon']} ({h['period']})" if h.get('horizon') else "") + (f" — {'; '.join(why)}" if why else ""))
                if h.get("horizon_note") and act in ("ADD MORE", "SHORT-TERM TRADE", "EXIT", "EXIT (no rush)"):
                    L.append(f"   {h['horizon_note']}")
        for a in p["alerts"][:4]:
            L.append(f"! {a}")
    if site_url:
        L += ["", site_url]
    L.append("Rule-based signals, not investment advice.")
    return "\n".join(L)


def send(text, subject):
    sent = []
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if tok and chat:
        try:
            for i in range(0, len(text), 3900):
                requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              data={"chat_id": chat, "text": text[i:i + 3900], "disable_web_page_preview": "true"}, timeout=20).raise_for_status()
            sent.append("telegram")
        except Exception as e:
            print(f"telegram failed: {e}")
    user, pw, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"), os.environ.get("REPORT_EMAIL")
    if user and pw and to:
        try:
            msg = MIMEText(text, "plain", "utf-8")
            msg["Subject"], msg["From"], msg["To"] = subject, user, to
            with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465")), timeout=30) as s:
                s.login(user, pw)
                s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
            sent.append("email")
        except Exception as e:
            print(f"email failed: {e}")
    return sent
