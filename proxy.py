# proxy.py — Tesla relay: direct-first + scrape.do fallback (render/super/geocode + retry)
import os, json, time, urllib.parse, requests
from fastapi import FastAPI, Response, Query

app = FastAPI()

ENDPOINT = "https://www.tesla.com/inventory/api/v1/inventory-results"
SCRAPE_DO_TOKEN = os.environ.get("SCRAPE_DO_TOKEN")  # Render Environment'da tanımlı olmalı

@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/health", "/inv", "/diag/direct", "/diag/sd"]}

@app.get("/health")
def health():
    return {"ok": True, "has_token": bool(SCRAPE_DO_TOKEN)}

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
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    return requests.get(url, headers=headers, timeout=timeout)

def fetch_scrapedo(url: str, timeout=45, render=False, super_gw=False, geocode=None, retries=3):
    """
    scrape.do çağrısı: render/super/geocode parametre destekli + retry/backoff
    """
    if not SCRAPE_DO_TOKEN:
        return Response(status_code=502, content=b'{"error":"SCRAPE_DO_TOKEN not set"}', media_type="application/json")

    base = "https://api.scrape.do/?token=" + SCRAPE_DO_TOKEN + "&url=" + urllib.parse.quote(url)
    params = []
    if render:   params.append("render=true")   # JS render (headless)
    if super_gw: params.append("super=true")    # residential/mobile
    if geocode:  params.append("geoCode=" + urllib.parse.quote(geocode))
    api = base + ("&" + "&".join(params) if params else "")

    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(api, headers={"Accept": "application/json"}, timeout=timeout)
            return r
        except requests.RequestException as e:
            last_exc = e
            time.sleep(min(2 ** attempt, 6))  # 2s, 4s, 6s backoff
    # tüm denemeler başarısız
    raise last_exc

def merge_results(j):
    res = j.get("results")
    if isinstance(res, list): return res
    out = []
    if isinstance(res, dict):
        for k in ("exact", "approximate", "approximateOutside", "outside"):
            v = res.get(k)
            if isinstance(v, list): out.extend(v)
    return out

@app.get("/inv")
def inv(
    model: str = "my",
    market: str = "TR",
    language: str = "tr",
    offset: int = 0,
    count: int = 50,
    outsideSearch: bool = True,
    mode: str = "auto",                 # auto | sdonly | direct
    direct_timeout: int = 15,
    sd_timeout: int = 60,               # 45→60
    sd_render: bool = True,             # JS render varsayılan: açık
    sd_super: bool = True,              # super gateway varsayılan: açık
    sd_geocode: str = "DE"              # çıkış ülke (DE/NL/GB/US vs.)
):
    q = build_query(model, market, language, offset, count, outsideSearch)
    url = build_tesla_url(q)

    # sdonly: direkt scrape.do
    if mode.lower() == "sdonly":
        try:
            sr = fetch_scrapedo(url, timeout=sd_timeout, render=sd_render, super_gw=sd_super, geocode=sd_geocode)
            if isinstance(sr, Response):
                print("INV: SCRAPEDO early Response ->", sr.status_code)
                return sr
            print(f"INV: SCRAPEDO-only status {sr.status_code}")
            return Response(sr.content, media_type="application/json", status_code=sr.status_code,
                            headers={"x-proxy-source":"scrapedo"})
        except requests.RequestException as e:
            print("INV: SCRAPEDO-only error ->", e)
            return Response(status_code=504, content=f'{{"error":"sdonly timeout","detail":"{str(e)}"}}',
                            media_type="application/json")

    # direct-only
    if mode.lower() == "direct":
        try:
            dr = fetch_direct(url, timeout=direct_timeout)
            print(f"INV: DIRECT-only status {dr.status_code}")
            return Response(dr.content, media_type="application/json", status_code=dr.status_code,
                            headers={"x-proxy-source":"direct"})
        except requests.RequestException as e:
            print("INV: DIRECT-only error ->", e)
            return Response(status_code=504, content=f'{{"error":"direct timeout","detail":"{str(e)}"}}',
                            media_type="application/json")

    # auto: önce direct, olmazsa scrape.do
    try:
        dr = fetch_direct(url, timeout=direct_timeout)
        if dr.status_code == 200:
            print("INV: DIRECT 200")
            return Response(dr.content, media_type="application/json", status_code=200,
                            headers={"x-proxy-source":"direct"})
        else:
            print(f"INV: DIRECT status {dr.status_code} -> fallback to scrape.do")
    except requests.RequestException as e:
        print("INV: DIRECT error ->", e)

    try:
        sr = fetch_scrapedo(url, timeout=sd_timeout, render=sd_render, super_gw=sd_super, geocode=sd_geocode)
        if isinstance(sr, Response):
            print("INV: SCRAPEDO early Response ->", sr.status_code)
            return sr
        print(f"INV: SCRAPEDO status {sr.status_code}")
        return Response(sr.content, media_type="application/json", status_code=sr.status_code,
                        headers={"x-proxy-source":"scrapedo"})
    except requests.RequestException as e:
        print("INV: SCRAPEDO error ->", e)
        return Response(status_code=504, content=f'{{"error":"proxy timeout","detail":"{str(e)}"}}',
                        media_type="application/json")

# Teşhis uçları
@app.get("/diag/direct")
def diag_direct():
    url = build_tesla_url(build_query())
    start = time.time()
    try:
        r = fetch_direct(url, timeout=15)
        return {"stage":"direct","status":r.status_code,"elapsed_s":round(time.time()-start,2),"len":len(r.content)}
    except requests.RequestException as e:
        return {"stage":"direct","error":str(e)}

@app.get("/diag/sd")
def diag_sd(url: str = Query(...), timeout: int = 20, render: bool = True, super_gw: bool = True, geocode: str = "DE"):
    if not SCRAPE_DO_TOKEN:
        return {"error":"SCRAPE_DO_TOKEN not set"}
    base = "https://api.scrape.do/?token=" + SCRAPE_DO_TOKEN + "&url=" + urllib.parse.quote(url)
    params = []
    if render:   params.append("render=true")
    if super_gw: params.append("super=true")
    if geocode:  params.append("geoCode=" + urllib.parse.quote(geocode))
    api = base + ("&" + "&".join(params) if params else "")
    start = time.time()
    try:
        r = requests.get(api, timeout=timeout)
        return {"stage":"scrapedo","target":url,"status":r.status_code,"elapsed_s":round(time.time()-start,2),"len":len(r.content),"api":api}
    except requests.RequestException as e:
        return {"stage":"scrapedo","target":url,"error":str(e)}
