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

    async def search_google(self, query: str, count: int = 10) -> list[dict]:
        """Search Google via the cloud browser and extract organic results with snippets."""
        results = []
        try:
            await self._ensure_browser()
            page = await self._browser.new_page()
            search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}&hl=en"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)

            import re
            from urllib.parse import urlparse, parse_qs

            # Try to extract result blocks (title + snippet pairs)
            result_blocks = await page.query_selector_all("div.g, div[data-hveid]")
            for block in result_blocks[:count * 2]:
                try:
                    link_el = await block.query_selector("a[href]")
                    if not link_el:
                        continue
                    href = await link_el.get_attribute("href")
                    title_el = await block.query_selector("h3")
                    title = await title_el.inner_text() if title_el else ""
                    snippet_el = await block.query_selector("div[data-sncf], span.aCOpRe, span.VuuXrf, div.lEBKkf")
                    snippet = await snippet_el.inner_text() if snippet_el else ""
                    if not href or not title:
                        continue
                    if href.startswith("/url?"):
                        parsed = urlparse(href)
                        qs = parse_qs(parsed.query)
                        href = qs.get("q", [href])[0]
                    if href.startswith("http") and "google.com" not in href:
                        results.append({
                            "title": title.strip()[:200],
                            "url": href,
                            "snippet": snippet.strip()[:300] if snippet else "",
                        })
                        if len(results) >= count:
                            break
                except Exception:
                    continue

            # Fallback: extract from all links if block-based didn't find enough
            if len(results) < count:
                links = await page.query_selector_all("a[href]")
                seen_urls = {r["url"] for r in results}
                for link in links:
                    try:
                        href = await link.get_attribute("href")
                        text = await link.inner_text()
                        if not href or not text:
                            continue
                        text = text.strip()
                        if len(text) < 10:
                            continue
                        if href.startswith("/url?"):
                            parsed = urlparse(href)
                            qs = parse_qs(parsed.query)
                            href = qs.get("q", [href])[0]
                        if href.startswith("http") and "google.com" not in href and href not in seen_urls:
                            seen_urls.add(href)
                            results.append({
                                "title": text[:200],
                                "url": href,
                                "snippet": "",
                            })
                            if len(results) >= count:
                                break
                    except Exception:
                        continue

            await page.close()
        except Exception as e:
            logger.warning(f"CloudBrowser Google search failed: {e}")
        return results

    async def scrape_business_directory(self, url: str) -> list[dict]:
        """Navigate to a business directory page and extract listings with phone numbers."""
        import re
        listings = []
        try:
            await self._ensure_browser()
            page = await self._browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            body_text = await page.inner_text("body")
            lines = [l.strip() for l in body_text.split("\n") if l.strip()]

            # Try structured extraction from common directory patterns
            cards = await page.query_selector_all(
                "[class*='listing'], [class*='result'], [class*='business'], "
                "[class*='card'], [class*='item'], li[class*='listing'], "
                "div[class*='listing'], tr[class*='listing']"
            )
            if cards:
                for card in cards[:50]:
                    try:
                        card_text = await card.inner_text()
                        card_html = await card.inner_html() if hasattr(card, 'inner_html') else ""
                        phone = ""
                        phone_match = re.search(
                            r'(\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4})',
                            card_text
                        )
                        if phone_match:
                            phone = phone_match.group(1).strip()
                        # First line is usually the name
                        name = (card_text.split("\n")[0] or "").strip()[:200]
                        if name and len(name) > 2 and name not in ("Home", "About", "Contact", "Listings"):
                            listings.append({"name": name, "phone": phone, "source": url})
                    except Exception:
                        continue

            # Fallback: extract name + phone pairs from raw text
            if not listings:
                phone_pattern = re.compile(
                    r'([A-Z][a-zA-Z\s&.\'-]{2,60}?)\s*[:\-]?\s*((?:\+?\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}))'
                )
                for match in phone_pattern.finditer(body_text):
                    name = match.group(1).strip()
                    phone = match.group(2).strip()
                    if name and phone and len(name) > 2:
                        listings.append({"name": name, "phone": phone, "source": url})

            await page.close()
        except Exception as e:
            logger.warning(f"CloudBrowser directory scrape failed for {url}: {e}")
        return listings

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
