"""Cloud browser — headless Chromium via Playwright for Railway/cloud deployment.

Used as a fallback when the local browser-harness CLI is unavailable. Runs
Chromium headless with no display, sandbox disabled for Docker compatibility.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


class CloudBrowser:
    """Headless Chromium browser for cloud environments.

    Lazy-launches on first use; keeps one browser instance alive for the
    lifetime of the process. Supports navigation, text extraction, and search.
    Call ``available`` before calling anything else — returns False when
    Playwright is not installed or Chromium is missing.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._checked = False
        self._available_flag = False

    @property
    def available(self) -> bool:
        if not self._checked:
            self._available_flag = _PLAYWRIGHT_AVAILABLE and self._probe()
            self._checked = True
        return self._available_flag

    def _probe(self) -> bool:
        try:
            import subprocess
            import shutil
            return shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("chrome") or shutil.which("google-chrome") is not None
        except Exception:
            return False

    async def _ensure_browser(self):
        if self._browser:
            return
        if not self._playwright:
            self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

    async def navigate(self, url: str, timeout: float = 30.0) -> dict:
        """Open a URL and return the page title + text content."""
        try:
            await self._ensure_browser()
            page = await self._browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
            title = await page.title()
            text = await page.inner_text("body")
            await page.close()
            return {"status": "success", "title": title, "text": text[:8000]}
        except Exception as e:
            logger.warning(f"CloudBrowser navigate failed for {url}: {e}")
            return {"status": "error", "message": str(e)}

    async def search_google(self, query: str, count: int = 5) -> list[dict]:
        """Search Google via the cloud browser and extract organic results."""
        results = []
        try:
            await self._ensure_browser()
            page = await self._browser.new_page()
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)

            import re
            from urllib.parse import urlparse, parse_qs

            links = await page.query_selector_all("a")
            for link in links[:50]:
                try:
                    href = await link.get_attribute("href")
                    full_text = await link.inner_text()
                    if not href or not full_text:
                        continue
                    full_text = full_text.strip()
                    if len(full_text) < 10:
                        continue
                    # Decode Google redirect URLs
                    if href.startswith("/url?"):
                        parsed = urlparse(href)
                        qs = parse_qs(parsed.query)
                        href = qs.get("q", [href])[0]
                    if href.startswith("http") and "google.com" not in href:
                        results.append({
                            "title": full_text[:200],
                            "url": href,
                        })
                        if len(results) >= count:
                            break
                except Exception:
                    continue

            await page.close()
        except Exception as e:
            logger.warning(f"CloudBrowser Google search failed: {e}")
        return results

    async def extract_text(self, selector: str, timeout: float = 15.0) -> dict:
        """Extract text from the currently loaded page using a CSS selector."""
        # This is a placeholder — extract_text requires a pre-navigated page in Playwright,
        # which doesn't map cleanly to the CLI adapter's stateless model.
        return {"texts": []}

    async def screenshot(self, url: str | None = None) -> bytes | None:
        """Take a screenshot of a URL."""
        try:
            await self._ensure_browser()
            page = await self._browser.new_page()
            if url:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            screenshot = await page.screenshot()
            await page.close()
            return screenshot
        except Exception as e:
            logger.warning(f"CloudBrowser screenshot failed: {e}")
            return None

    async def close(self):
        """Clean up browser resources."""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None


cloud_browser = CloudBrowser()
