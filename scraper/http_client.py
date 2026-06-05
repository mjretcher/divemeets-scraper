"""
scraper/http_client.py
Routes all requests through Bright Data's Web Unlocker REST API.
Set BRIGHTDATA_API_KEY env var (or it falls back to the stored CLI key).
"""
import os
import time
import logging
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://secure.meetcontrol.com/divemeets/system"
REQUEST_DELAY = 1.5
_BD_ENDPOINT  = "https://api.brightdata.com/request"
_BD_ZONE      = os.environ.get("BRIGHTDATA_ZONE", "cli_unlocker")

_session = requests.Session()
retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=retry))

_last_request_time = 0.0

def _api_key() -> str:
    key = os.environ.get("BRIGHTDATA_API_KEY", "").strip()
    if key:
        return key
    # Fall back to key stored by the bdata CLI
    import json, pathlib
    creds = pathlib.Path.home() / "Library/Application Support/brightdata-cli/credentials.json"
    if creds.exists():
        return json.loads(creds.read_text()).get("api_key", "")
    raise RuntimeError("BRIGHTDATA_API_KEY not set and no stored CLI credentials found.")

def get_html(path: str, params: dict = None, full_url: str = None) -> Optional[str]:
    global _last_request_time

    if full_url:
        url = full_url
    else:
        url = f"{BASE_URL}/{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    try:
        resp = _session.post(
            _BD_ENDPOINT,
            json={"zone": _BD_ZONE, "url": url, "format": "raw"},
            headers={"Authorization": f"Bearer {_api_key()}"},
            timeout=90,
        )
        _last_request_time = time.time()
        if resp.status_code == 200:
            return resp.text
        logger.warning(f"HTTP {resp.status_code} for {url}: {resp.text[:120]}")
        return None
    except Exception as e:
        logger.error(f"Request failed for {url}: {e}")
        _last_request_time = time.time()
        return None
