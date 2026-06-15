"""Browser Harness adapter — drives the user's Chrome via the browser-harness CLI.

The harness is a local CLI (``browser-harness`` on PATH) that pipes Python to a
CDP daemon attached to the user's running Chrome. It is NOT an HTTP service —
this adapter wraps the CLI and exposes the operations agents need: navigation,
text extraction, search, and arbitrary harness scripts for logged-in sites.

If ``settings.browser_harness_url`` points at a live HTTP bridge, that is used
first; otherwise the CLI path is the default.
"""
import logging
from typing import Any

import httpx

from app.config import settings
from app.integrations.web_discovery import browser_cli, web_search as _ddg_search

logger = logging.getLogger(__name__)


class BrowserHarnessClient:
    """Web automation for agents: CLI-first with optional HTTP bridge."""

    def __init__(self):
        self.api_key = settings.browser_harness_api_key
        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str | None:
        return settings.browser_harness_url

    @property
    def available(self) -> bool:
        return browser_cli.available or bool(self.base_url)

    async def _http_post(self, path: str, body: dict) -> dict | None:
        """Try the optional HTTP bridge; return None when absent/unreachable."""
        if not self.base_url:
            return None
        try:
            if self._http is None:
                headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
                self._http = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=60.0)
            resp = await self._http.post(path, json=body)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug(f"Browser harness HTTP bridge unavailable ({path}): {e}")
            return None

    async def run_script(self, script: str, timeout: float = 120.0) -> dict:
        """Run a Python snippet in the harness (new_tab, js, click_at_xy, ...)."""
        return await browser_cli.run_script(script, timeout=timeout)

    async def navigate(self, url: str, wait_selector: str | None = None) -> dict:
        via_http = await self._http_post("/navigate", {"url": url, "wait_selector": wait_selector})
        if via_http is not None:
            return via_http
        return await browser_cli.fetch_page(url)

    async def extract_text(self, selector: str) -> dict:
        via_http = await self._http_post("/extract/text", {"selector": selector})
        if via_http is not None:
            return via_http
        script = (
            f"texts = js('Array.from(document.querySelectorAll({selector!r}))"
            f".map(e => e.innerText).join(chr(10))')\n"
            "print(texts[:4000])\n"
        )
        result = await browser_cli.run_script(script)
        return {"texts": [result.get("output", "")]} if result.get("status") == "success" else {"texts": []}

    async def search_google(self, query: str) -> list[dict]:
        """Search the web. Uses DuckDuckGo HTML (keyless) — name kept for compat."""
        result = await _ddg_search(query, count=10)
        return result.get("results", [])

    async def fill_form(self, selector: str, value: str) -> bool:
        via_http = await self._http_post("/form/fill", {"selector": selector, "value": value})
        if via_http is not None:
            return True
        script = (
            f"js('var el = document.querySelector({selector!r}); "
            f"if (el) {{ el.value = {value!r}; el.dispatchEvent(new Event(\"input\", {{bubbles: true}})); }}')\n"
            "print('ok')\n"
        )
        result = await browser_cli.run_script(script)
        return result.get("status") == "success"

    async def click(self, selector: str) -> bool:
        via_http = await self._http_post("/click", {"selector": selector})
        if via_http is not None:
            return True
        script = (
            f"js('var el = document.querySelector({selector!r}); if (el) el.click();')\n"
            "print('ok')\n"
        )
        result = await browser_cli.run_script(script)
        return result.get("status") == "success"

    async def screenshot(self, url: str | None = None) -> bytes | None:
        if url:
            await self.navigate(url)
        result = await browser_cli.run_script("print(capture_screenshot())")
        if result.get("status") == "success":
            return result.get("output", "").encode("utf-8")
        return None


browser_harness = BrowserHarnessClient()
