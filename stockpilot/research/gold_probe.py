"""One-off probe: what does allindiabullion.com serve to a script, and how does it compare with gold x USD/INR?"""
import re, json
import requests, yfinance as yf
UA = {"User-Agent": "Mozilla/5.0 (StockPilot personal research)"}
out = {}
for url in ["https://www.allindiabullion.com/", "https://allindiabullion.com/"]:
    try:
        r = requests.get(url, headers=UA, timeout=30)
        out[url] = {"status": r.status_code, "len": len(r.text), "final": r.url}
        open("aib.html", "w").write(r.text)
        t = re.sub(r"<[^>]+>", " ", r.text)
        out[url]["gold_hits"] = [m.group(0) for m in re.finditer(r".{0,80}(24K|24 K|999).{0,120}", t)][:15]
        out[url]["dates"] = sorted(set(re.findall(r"20\d\d-\d\d-\d\d", r.text)))[-10:]
        out[url]["links"] = sorted(set(re.findall(r'(?:href|src)="([^"]+)"', r.text)))[:200]
        out[url]["api"] = sorted(set(re.findall(r'["\'](/?[\w/.-]*(?:api|json|rate|price)[\w/.?=&-]*)["\']', r.text, re.I)))[:80]
        break
    except Exception as e:
        out[url] = {"error": str(e)}
g = yf.download("GC=F", period="5d", progress=False)["Close"].iloc[-1].item()
fx = yf.download("INR=X", period="5d", progress=False)["Close"].iloc[-1].item()
out["intl"] = {"gold_usd_oz": g, "usdinr": fx, "inr_per_10g_no_duty": g * fx / 31.1035 * 10}
json.dump(out, open("gold_probe.json", "w"), indent=1)
print(json.dumps(out, indent=1)[:6000])
