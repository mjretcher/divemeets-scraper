"""
scraper/http_client.py
HTTP client that routes all requests through Bright Data's Web Unlocker API,
bypassing Cloudflare and other bot-detection on DiveMeets.

Requires env var:  BRIGHTDATA_API_KEY
Optional env var:  BRIGHTDATA_ZONE  (default: cli_unlocker)
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

# Polite delay between requests (seconds)
REQUEST_DELAY = 1.5

# Bright Data Web Unlocker endpoint
_BD_ENDPOINT = "https://api.brightdata.com/request"
_BD_ZONE = os.environ.get("BRIGHTDATA_ZONE", "cli_unlocker")


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session


_session = _make_session()
_last_request_time = 0.0


def get_html(path: str, params: dict = None, full_url: str = None) -> Optional[str]:
    global _last_request_time

    api_key = os.environ.get("BRIGHTDATA_API_KEY")
    if not api_key:
        raise RuntimeError(
            "BRIGHTDATA_API_KEY environment variable is not set. "
            "Get your key from https://brightdata.com/cp/setting and set it "
            "as a GitHub Actions secret named BRIGHTDATA_API_KEY."
        )

    # Build target URL
    if full_url:
        url = full_url
    else:
        url = f"{BASE_URL}/{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            url = f"{url}?{qs}"

    # Enforce polite delay
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    try:
        resp = _session.post(
            _BD_ENDPOINT,
            json={"zone": _BD_ZONE, "url": url, "format": "raw"},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        _last_request_time = time.time()

        if resp.status_code == 200:
            return resp.text
        else:
            logger.warning(f"Bright Data returned HTTP {resp.status_code} for {url}: {resp.text[:200]}")
            return None

    except requests.RequestException as e:
        logger.error(f"Request failed for {url}: {e}")
        _last_request_time = time.time()
        return None
