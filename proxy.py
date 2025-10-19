# proxy.py — FastAPI Tesla relay: direct-first + scrape.do fallback + diagnostics
import os, json, time, urllib.parse, requests
from fastapi import FastAPI, Response, Query

app = FastAPI()

ENDPOINT = "https://www.tesla.com/inventory/api/v1/inventory-results"
SCRAPE_DO_TOKEN = os.environ.get("SCRAPE_DO_TOKEN")  # Render → Environment'da tanımlı olmalı

# ---------- Basit kök & sağlık ----------
@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/health", "/inv", "/diag/direct", "/diag/sd"]}

@app.get("/health")
def health():
    return {"ok": True, "has_token": bool(SCRAPE_DO_TOKEN)}

# ---------- Yardımcılar ----------
def build_query(model="my", market="TR", language="tr", offset=0, count=50, outsideSearch=True):
    return {
        "query": {
            "model": model,
            "condition": "new",
            "arrangeby": "Price",
            "order": "asc",
            "market": market,
            "language": language
        },
        "offset": offset,
        "count": count,
        "outsideOffset": 0,
        "outsideSearch": outsideSearch
    }

def build_tesla_url(q: dict) -> str:
    return f"{ENDPOINT}?query={urllib.parse.quote(json.dumps(q, separators=(',',':')))}"

def fetch_direct(url: str, timeout=15):
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache", "Pragma": "no-cache",
    }
    return requests.get(url, headers=headers, timeout=timeout)

def fetch_scrapedo(url: str, timeout=25):
    if not SCRAPE_DO_TOKEN:
        content = b'{"error":"SCRAPE_DO_TOKEN not set"}'
        return Response(status_code=502, content=content, media_type="application/json")
    api = f"https://api.scrape.do/?token={SCRAPE_DO_TOKEN}&url={urllib.parse.quote(url)}"
    return requests.get(api, headers={"Accept": "application/json"}, timeout=timeout)

def merge_results(j):
    res = j.get("results")
    if isinstance(res, list): return res
    out = []
    if isinstance(res, dict):
        for k in ("exact","approximate","approximateOutside","outside"):
            v = res.get(k)
            if isinstance(v, list): out.extend(v)
    return out

# ---------- Ana envanter ----------
@app.get("/inv")
def inv(model: str="my", market: str="TR", language: str="tr",
        offset: int=0, count: int=50, outsideSearch: bool=True):

    q = build_query(model, market, language, offset, count, outsideSearch)
    url = build_tesla_url(q)

    # 1) Direkt Tesla
    try:
        dr = fetch_direct(url, timeout=15)
        if dr.status_code == 200:
            print("INV: DIRECT 200")
            return Response(dr.content, media_type="application/json", status_code=200,
                            headers={"x-proxy-source": "direct"})
        else:
            print(f"INV: DIRECT status {dr.status_code} -> fallback")
    except requests.RequestException as e:
        print("INV: DIRECT error ->", e)

    # 2) scrape.do fallback
    try:
        sr = fetch_scrapedo(url, timeout=25)
        if isinstance(sr, Response):
            # Token eksik ise buraya düşer
            print("INV: SCRAPEDO early Response ->", sr.status_code)
            return sr
        print(f"INV: SCRAPEDO status {sr.status_code}")
        return Response(sr.content, media_type="application/json", status_code=sr.status_code,
                        headers={"x-proxy-source": "scrapedo"})
    except requests.RequestException as e:
        print("INV: SCRAPEDO error ->", e)
        return Response(status_code=504,
                        content=f'{{"error":"proxy timeout","detail":"{str(e)}"}}',
                        media_type="application/json")

# ---------- Teşhis: Tesla'ya doğrudan ----------
@app.get("/diag/direct")
def diag_direct():
    url = build_tesla_url(build_query())
    start = time.time()
    try:
        r = fetch_direct(url, timeout=15)
        elapsed = round(time.time()-start, 2)
        return {"stage":"direct", "status": r.status_code, "elapsed_s": elapsed, "len": len(r.content)}
    except requests.RequestException as e:
        elapsed = round(time.time()-start, 2)
        return {"stage":"direct", "error": str(e), "elapsed_s": elapsed}

# ---------- Teşhis: scrape.do üzerinden (serbest URL) ----------
@app.get("/diag/sd")
def diag_sd(url: str = Query(..., description="Mutlaka tam URL ver"), timeout: int = 15):
    if not SCRAPE_DO_TOKEN:
        return {"error":"SCRAPE_DO_TOKEN not set"}
    api = f"https://api.scrape.do/?token={SCRAPE_DO_TOKEN}&url={urllib.parse.quote(url)}"
    start = time.time()
    try:
        r = requests.get(api, timeout=timeout)
        elapsed = round(time.time()-start, 2)
        return {"stage":"scrapedo", "target": url, "status": r.status_code, "elapsed_s": elapsed, "len": len(r.content)}
    except requests.RequestException as e:
        elapsed = round(time.time()-start, 2)
        return {"stage":"scrapedo", "target": url, "error": str(e), "elapsed_s": elapsed}
