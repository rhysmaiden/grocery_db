"""Shared scraper plumbing: HTTP session, raw dump IO, retries."""

import gzip
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from curl_cffi import requests

RAW_DIR = Path("data/raw")
TZ = ZoneInfo("Australia/Sydney")

# Polite delay between catalogue page requests, seconds.
REQUEST_DELAY = 0.4
RETRIES = 4

# Wall-clock budget for one chain's scrape; keeps a slow/tarpitted chain
# from eating the whole CI job (which would lose the other chains' day).
_deadline: float | None = None


class ChainTimeout(RuntimeError):
    pass


def set_deadline(seconds: float | None):
    global _deadline
    _deadline = None if seconds is None else time.monotonic() + seconds


def today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def new_session() -> requests.Session:
    # Chrome TLS impersonation: required for Aldi (Akamai rejects plain
    # clients on TLS fingerprint) and keeps us unremarkable elsewhere.
    return requests.Session(impersonate="chrome")


def fetch(session, method: str, url: str, *, ok_html=False, **kwargs):
    """Request with retries and backoff. Raises after RETRIES failures."""
    last_err = None
    for attempt in range(RETRIES):
        if _deadline is not None and time.monotonic() > _deadline:
            raise ChainTimeout("chain scrape exceeded its time budget")
        try:
            resp = session.request(method, url, timeout=60, **kwargs)
            if resp.status_code == 200:
                ctype = resp.headers.get("content-type", "")
                if ok_html or "json" in ctype:
                    return resp
                last_err = RuntimeError(f"non-JSON response ({ctype}) from {url}")
            else:
                last_err = RuntimeError(f"HTTP {resp.status_code} from {url}")
        except Exception as err:
            last_err = err
        time.sleep(2**attempt)
    raise last_err


def raw_path(chain: str, date: str) -> Path:
    return RAW_DIR / chain / f"{date}.json.gz"


def save_raw(chain: str, date: str, payload) -> Path:
    path = raw_path(chain, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fp:
        json.dump(payload, fp)
    return path


def load_raw(chain: str, date: str):
    with gzip.open(raw_path(chain, date), "rt") as fp:
        return json.load(fp)


def pause():
    time.sleep(REQUEST_DELAY)
