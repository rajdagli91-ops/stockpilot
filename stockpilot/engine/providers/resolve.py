"""Find the exchange symbol for a holding the website could not recognise (company name or ISIN from a broker file).

Uses Yahoo Finance's public search. NSE is preferred; if the company only trades on BSE, the BSE symbol (".BO")
is used. A name match must share most of its words with the company's listed name, so a vague name does not
silently map to the wrong company; those come back as not found and the website asks you for the symbol.
"""
import re
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
GENERIC = {"ltd", "limited", "the", "india", "indian", "co", "company", "corp", "corporation", "inc", "pvt", "private",
           "and", "of", "industries", "industry", "enterprises", "eq", "be", "nse", "bse"}


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in GENERIC and len(w) > 1}


# ---- broker short names: "CONTAINER CORPN.(I)L", "HOUSING & URBAN DEVP", "TATA MOT PASS VEHI", "RELIANCE INFRASTRUEQ"
SKIP = {"ltd", "limited", "l", "the", "of", "and", "co", "company", "eq", "be", "inc"}


def _toks(s: str, raw: bool = False) -> list:
    s = re.sub(r"\(i\)", " india ", (s or "").lower())
    out = []
    for w in re.findall(r"[a-z0-9]+", s):
        if raw and len(w) > 5 and w.endswith("eq"):
            w = w[:-2]  # "INFRASTRUEQ": series code glued to a truncated word
        if w not in SKIP and not (raw and len(w) >= 3 and "limited".startswith(w)):  # "LIMITE" cut short
            out.append(w)
    return out


def _abbr(t: str, w: str) -> bool:
    """t is w shortened: a prefix (MOT -> motors) or same start + letters in order (CORPN -> corporation)."""
    if w.startswith(t):
        return True
    if len(t) < 3 or t[:3] != w[:3]:
        return False
    it = iter(w)
    return all(ch in it for ch in t)


def abbrev_match(raw: str, names: list) -> str | None:
    """names: [(symbol, company name)]. Every word of the short name must match a word of the company name, in order."""
    rt = _toks(raw, True)
    if not rt or (len(rt) == 1 and len(rt[0]) < 5):
        return None
    best, best_cov, tie = None, 0.0, False
    for sym, name in names:
        nt = _toks(name)
        if not nt:
            continue
        i = 0
        for w in nt:
            if i < len(rt) and _abbr(rt[i], w):
                i += 1
        if i < len(rt):
            continue
        cov = len(rt) / len(nt)
        if cov < 0.5:
            continue
        if cov > best_cov:
            best, best_cov, tie = sym, cov, False
        elif cov == best_cov:
            tie = True
    return None if tie else best


def _search(q: str) -> list:
    for host in ("query2", "query1"):
        try:
            r = requests.get(f"https://{host}.finance.yahoo.com/v1/finance/search",
                             params={"q": q, "quotesCount": 10, "newsCount": 0, "lang": "en-IN", "region": "IN"},
                             headers=UA, timeout=15)
            if r.status_code == 200:
                return r.json().get("quotes") or []
        except Exception as e:
            print(f"resolve search {q}: {e}")
    return []


def _pick(quotes: list, name: str | None):
    eq = [q for q in quotes if q.get("quoteType") == "EQUITY" and str(q.get("symbol", "")).endswith((".NS", ".BO"))]
    if name:
        want = _words(name)

        def ok(q):
            got = _words(q.get("longname") or q.get("shortname") or "")
            common = len(want & got)
            # most of your words are in its name AND most of its name is in your words ("Tata" is not TCS)
            return bool(want and got) and common / len(want) >= 0.6 and common / len(got) >= 0.5
        eq = [q for q in eq if ok(q)]
    nse = [q for q in eq if q["symbol"].endswith(".NS")]
    best = (nse or eq or [None])[0]
    if not best:
        return None
    sym = best["symbol"]
    return {"symbol": sym[:-3] if sym.endswith(".NS") else sym, "exchange": "NSE" if sym.endswith(".NS") else "BSE",
            "name": best.get("longname") or best.get("shortname") or ""}


def resolve(raw: str, isin: str | None = None, names: list | None = None) -> dict:
    """{'symbol', 'exchange', 'name'} or {'status': 'notfound'}. names: NSE [(symbol, name)] for short-name matching."""
    if isin and re.fullmatch(r"IN[A-Z0-9]{10}", isin.upper()):
        hit = _pick(_search(isin.upper()), None)  # an ISIN is exact: no name check needed
        if hit and hit["exchange"] == "BSE" and hit["name"]:
            nse = _pick(_search(hit["name"]), hit["name"])  # same company may also trade on NSE
            if nse and nse["exchange"] == "NSE":
                return nse
        if hit:
            return hit
    if names:
        sym = abbrev_match(raw, names)
        if sym:
            return {"symbol": sym, "exchange": "NSE", "name": dict(names).get(sym, "")}
    q = re.sub(r"\b(ltd|limited|eq|be)\b\.?", " ", raw or "", flags=re.I).strip()
    if q:
        hit = _pick(_search(q), q)
        if hit:
            return hit
        # a bare ticker typed by the broker (e.g. a BSE-only symbol)
        if re.fullmatch(r"[A-Za-z0-9&-]{2,20}", q):
            hit = _pick(_search(q + ".BO"), None) or _pick(_search(q + ".NS"), None)
            if hit:
                return hit
    return {"status": "notfound"}


VERSION = 2  # bump to re-try earlier "not found" results after the matcher improves


def resolve_all(items: list, cache: dict, delay: float = 0.8, names: list | None = None) -> dict:
    """items: [{'key', 'raw', 'isin'}]. Updates and returns cache {key: result}; 'notfound' is retried after a week."""
    import datetime as dt
    today = dt.date.today().isoformat()
    for it in items:
        c = cache.get(it["key"])
        if c and (c.get("symbol") or (c.get("v") == VERSION and c.get("checked", "") >= (dt.date.today() - dt.timedelta(days=7)).isoformat())):
            continue
        res = resolve(it.get("raw") or "", it.get("isin"), names)
        res["checked"], res["v"] = today, VERSION
        cache[it["key"]] = res
        print(f"resolve {it['raw']!r}: {res.get('symbol') or 'not found'} {res.get('exchange') or ''}")
        time.sleep(delay)
    return cache
