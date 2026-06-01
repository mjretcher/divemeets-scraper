"""
scraper/http_client.py
Shared HTTP session with rate limiting, retry, and polite headers.
DiveMeets uses PHP/CGI pages — no JS rendering needed.
"""
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BASE_URL = "https://secure.meetcontrol.com/divemeets/system"

# Polite headers — identify as a research bot
HEADERS = {
    "User-Agent": "DiveMeets-Research-Bot/1.0 (diving analytics; respectful crawler)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Seconds between requests — be polite to DiveMeets servers
REQUEST_DELAY = 1.5


def make_session() -> requests.Session:
    """Build a requests session with retry logic."""
    session = requests.Session()
    session.headers.update(HEADERS)

    retry = Retry(
        total=4,
        backoff_factor=2,           # 1s, 2s, 4s, 8s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


_session = make_session()
_last_request_time = 0.0


def get(path: str, params: dict = None, full_url: str = None, timeout: int = 30) -> requests.Response | None:
    """
    GET a DiveMeets page with rate limiting.
    Pass `path` for standard BASE_URL paths, or `full_url` to override.
    Returns Response or None on failure.
    """
    global _last_request_time

    url = full_url if full_url else f"{BASE_URL}/{path}"

    # Enforce delay between requests
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    try:
        resp = _session.get(url, params=params, timeout=timeout)
        _last_request_time = time.time()

        if resp.status_code == 200:
            return resp
        else:
            logger.warning(f"HTTP {resp.status_code} for {url}")
            return None
    except requests.RequestException as e:
        logger.error(f"Request failed for {url}: {e}")
        _last_request_time = time.time()
        return None


def get_html(path: str, params: dict = None, full_url: str = None) -> str | None:
    """Convenience: returns response text or None."""
    resp = get(path, params=params, full_url=full_url)
    return resp.text if resp else None
