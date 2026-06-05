"""
scraper/http_client.py
Uses curl_cffi to impersonate a real Chrome TLS fingerprint,
bypassing Cloudflare's bot detection without a proxy or browser.
"""
import time
import logging
from typing import Optional
from curl_cffi import requests as cffi_requests

logger = logging.getLogger(__name__)

BASE_URL = "https://secure.meetcontrol.com/divemeets/system"
REQUEST_DELAY = 1.5

_session = cffi_requests.Session(impersonate="chrome124")
_last_request_time = 0.0


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
        resp = _session.get(url, timeout=30)
        _last_request_time = time.time()

        if resp.status_code == 200:
            return resp.text
        else:
            logger.warning(f"HTTP {resp.status_code} for {url}")
            return None

    except Exception as e:
        logger.error(f"Request failed for {url}: {e}")
        _last_request_time = time.time()
        return None
