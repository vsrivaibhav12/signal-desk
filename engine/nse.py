"""NSE's public JSON endpoints (results calendar, trading holidays). NSE wants a browser-like visit to its home page first,
and blocks some networks, so every call here is short, bounded and expected to fail sometimes."""
import requests

HOME = "https://www.nseindia.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
HOME_TIMEOUT, API_TIMEOUT, ATTEMPTS = 6, 10, 2          # worst case about 30 seconds, and it runs off the main thread


def get_json(url, params=None, referer=HOME):
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get(HOME, timeout=HOME_TIMEOUT)
    except requests.RequestException:
        pass
    last = None
    for _ in range(ATTEMPTS):
        try:
            r = s.get(url, params=params, timeout=API_TIMEOUT, headers={"Referer": referer, "Accept": "application/json"})
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
    raise last or RuntimeError("NSE gave no answer")
