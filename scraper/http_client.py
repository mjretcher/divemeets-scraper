"""
scraper/http_client.py
Shared HTTP client using Playwright to bypass Cloudflare JS challenges.
A single persistent browser context is reused across all requests.
"""
import time
import logging
import atexit
from typing import Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://secure.meetcontrol.com/divemeets/system"

REQUEST_DELAY = 1.5

_playwright = None
_browser = None
_context = None
_page = None
_last_request_time = 0.0


def _init():
    global _playwright, _browser, _context, _page
    if _page is not None:
        return
    from playwright.sync_api import sync_playwright
    logger.info("Launching Playwright browser...")
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=True)
    _context = _browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 800},
    )
    _page = _context.new_page()
    logger.info("Browser ready.")


def _shutdown():
    global _playwright, _browser, _context, _page
    try:
        if _page:
            _page.close()
        if _context:
            _context.close()
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _page = _context = _browser = _playwright = None


atexit.register(_shutdown)


def get_html(path: str, params: dict = None, full_url: str = None) -> Optional[str]:
    global _last_request_time
    _init()

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
        response = _page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        _last_request_time = time.time()

        if response is None or not response.ok:
            status = response.status if response else "no response"
            logger.warning(f"HTTP {status} for {url}")
            return None

        # Wait for Cloudflare challenge to resolve if needed
        if "just a moment" in _page.title().lower():
            logger.info("Cloudflare challenge detected, waiting...")
            _page.wait_for_function(
                "() => !document.title.toLowerCase().includes('just a moment')",
                timeout=15_000,
            )

        return _page.content()

    except Exception as e:
        logger.error(f"Request failed for {url}: {e}")
        _last_request_time = time.time()
        return None
