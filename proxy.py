# proxy.py — FastAPI Tesla relay: direct-first, scrape.do fallback
import os, json, urllib.parse, requests
from fastapi import FastAPI, Response

app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/health", "/inv"]}

@app.get("/health")
def health():
    return {"ok": True}

ENDPOINT = "https://www.tesla.com/inventory/api/v1/inventory-results"
SCRAPE_DO_TOKEN = os.environ.get("SCRAPE_DO_TOKEN")  # Render Environment'dan gelecek

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
    r = requests.get(url, headers=headers, timeout=timeout)
    return r

def fetch_scrapedo(url: str, timeout=20):
    if not SCRAPE_DO_TOKEN:
        # Token yoksa 502 döndür ki yanlış yapılandırma anlaşılır olsun
        return Response(status_code=502, content=b'{"error":"SCRAPE_DO_TOKEN not set"}', media_type="application/json")
    api = f"https://api.scrape.do/?token={SCRAPE_DO_TOKEN}&url={urllib.parse.quote(url)}"
    r = requests.get(api, headers={"Accept":"application/json"}, timeout=timeout)
    # requests.Response döndürüyoruz ki aşağıda aynı handling olsun
    return r

def merge_results(j):
    res = j.get("results")
    if isinstance(res, list): return res
    out = []
    if isinstance(res, dict):
        for k in ("exact","approximate","approximateOutside","outside"):
            v = res.get(k)
            if isinstance(v, list): out.extend(v)
    return out

@app.get("/inv")
def inv(model: str="my", market: str="TR", language: str="tr",
        offset: int=0, count: int=50, outsideSearch: bool=True):

    q = build_query(model, market, language, offset, count, outsideSearch)
    url = build_tesla_url(q)

    # 1) Önce direkt Tesla
    try:
        dr = fetch_direct(url, timeout=15)
        if dr.status_code == 200:
            return Response(content=dr.content, media_type="application/json", status_code=200)
        # 403/4xx/5xx ise scrape.do'ya düş
    except requests.RequestException:
        pass

    # 2) scrape.do fallback
    try:
        sr = fetch_scrapedo(url, timeout=25)
        if isinstance(sr, Response):
            # Token yoksa buraya düşer (502)
            return sr
        return Response(content=sr.content, media_type="application/json", status_code=sr.status_code)
    except requests.RequestException as e:
        return Response(status_code=504, content=f'{{"error":"proxy timeout","detail":"{str(e)}"}}', media_type="application/json")
