"""NSE HTTP client with cookie bootstrap."""

from __future__ import annotations

import requests

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
})


def _ensure_cookies() -> None:
    if SESSION.cookies:
        return
    SESSION.get("https://www.nseindia.com", timeout=30)


def nse_get(path: str, *, params: dict | None = None) -> dict:
    _ensure_cookies()
    url = f"https://www.nseindia.com{path}"
    resp = SESSION.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.json()
