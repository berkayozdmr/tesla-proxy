# proxy.py — FastAPI ile Tesla inventory relay (TR için)
import json, urllib.parse, requests
from fastapi import FastAPI, Response

app = FastAPI()

ENDPOINT = "https://www.tesla.com/inventory/api/v1/inventory-results"

@app.get("/inv")
def inv(model: str="my", market: str="TR", language: str="tr",
        offset: int=0, count: int=50, outsideSearch: bool=True):
    query = {
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
    url = f"{ENDPOINT}?query={urllib.parse.quote(json.dumps(query, separators=(',',':')))}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8"
    }
    r = requests.get(url, headers=headers, timeout=15)
    return Response(content=r.content, media_type="application/json", status_code=r.status_code)
