"""Real web discovery — search, business finding, scraping. Works with zero API keys.

Three execution layers, tried in order of cost:
1. Plain HTTP (httpx): DuckDuckGo HTML search, OpenStreetMap Nominatim + Overpass
   business lookup, page scraping. Always available.
2. Browser Harness CLI (``browser-harness`` on PATH): drives the user's running
   Chrome via CDP for pages that need a real browser or a logged-in session.

Every public function returns data instead of raising; failures degrade to
empty results with a logged warning so agents can report precisely what failed.
"""
import asyncio
import html as html_lib
import logging
import re
import shutil
import time
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "SocratesAI/1.0 (local business assistant; contact: admin@socrates.ai)"

# Nominatim usage policy: max 1 request/sec. Serialize ONLY geocoding behind a
# lock with a minimum gap. Overpass mirrors tolerate faster rates and we fail
# over on throttle, so they are not gated by this (it was the bottleneck for
# bulk "find 100 leads" requests).
_geocode_last_call = 0.0
_GEOCODE_MIN_GAP = 1.1
_geocode_cache: dict[str, tuple[float, float, float, float] | None] = {}

# Public Overpass mirrors, tried in order — the main endpoint throttles hard
# under bulk use, so we fail over to mirrors on 429/504.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


async def _geocode_throttle():
    global _geocode_last_call
    elapsed = time.monotonic() - _geocode_last_call
    if elapsed < _GEOCODE_MIN_GAP:
        await asyncio.sleep(_GEOCODE_MIN_GAP - elapsed)
    _geocode_last_call = time.monotonic()


async def _geocode_bbox(client: httpx.AsyncClient, location: str) -> tuple[float, float, float, float] | None:
    """Resolve a location to a (south, north, west, east) bbox, cached per string."""
    key = location.strip().lower()
    if key in _geocode_cache:
        return _geocode_cache[key]
    await _geocode_throttle()
    geo = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": location, "format": "json", "limit": 1},
    )
    geo.raise_for_status()
    places = geo.json()
    if not places:
        _geocode_cache[key] = None
        return None
    bb = places[0]["boundingbox"]  # [south, north, west, east]
    box = (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3]))
    _geocode_cache[key] = box
    return box


async def _overpass_query(client: httpx.AsyncClient, ql: str) -> list[dict]:
    """Run an Overpass QL query, failing over across mirrors on throttle/timeout."""
    last_status = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            resp = await client.post(endpoint, data={"data": ql})
        except Exception as e:
            logger.debug(f"Overpass {endpoint} errored: {e}")
            continue
        last_status = resp.status_code
        if resp.status_code in (429, 504, 502, 503):
            logger.debug(f"Overpass {endpoint} returned {resp.status_code}, trying next mirror")
            await asyncio.sleep(1.0)
            continue
        resp.raise_for_status()
        return resp.json().get("elements", [])
    logger.warning(f"All Overpass mirrors exhausted (last status {last_status})")
    return []

# Map common industry words to OSM tag queries. Fallback is a name regex match.
_OSM_INDUSTRY_TAGS: dict[str, str] = {
    "restaurant": '["amenity"="restaurant"]',
    "cafe": '["amenity"="cafe"]',
    "coffee": '["amenity"="cafe"]',
    "bar": '["amenity"="bar"]',
    "dentist": '["amenity"="dentist"]',
    "doctor": '["amenity"="doctors"]',
    "clinic": '["amenity"="clinic"]',
    "pharmacy": '["amenity"="pharmacy"]',
    "gym": '["leisure"="fitness_centre"]',
    "fitness": '["leisure"="fitness_centre"]',
    "salon": '["shop"="hairdresser"]',
    "hairdresser": '["shop"="hairdresser"]',
    "beauty": '["shop"="beauty"]',
    "plumber": '["craft"="plumber"]',
    "electrician": '["craft"="electrician"]',
    "builder": '["craft"="builder"]',
    "carpenter": '["craft"="carpenter"]',
    "lawyer": '["office"="lawyer"]',
    "attorney": '["office"="lawyer"]',
    "accountant": '["office"="accountant"]',
    "estate agent": '["office"="estate_agent"]',
    "real estate": '["office"="estate_agent"]',
    "insurance": '["office"="insurance"]',
    "marketing": '["office"="advertising_agency"]',
    "hotel": '["tourism"="hotel"]',
    "guesthouse": '["tourism"="guest_house"]',
    "car dealer": '["shop"="car"]',
    "car repair": '["shop"="car_repair"]',
    "mechanic": '["shop"="car_repair"]',
    "bakery": '["shop"="bakery"]',
    "butcher": '["shop"="butcher"]',
    "supermarket": '["shop"="supermarket"]',
    "florist": '["shop"="florist"]',
    "vet": '["amenity"="veterinary"]',
    "veterinary": '["amenity"="veterinary"]',
    "school": '["amenity"="school"]',
    "driving school": '["amenity"="driving_school"]',
    "bank": '["amenity"="bank"]',
    "travel agency": '["shop"="travel_agency"]',
    "security": '["office"="security"]',
    "it": '["office"="it"]',
    "software": '["office"="it"]',
}

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", raw))).strip()


async def web_search(query: str, count: int = 10) -> dict:
    """Search the web via DuckDuckGo's HTML endpoint. No API key required."""
    count = max(1, min(int(count or 10), 30))
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )
            resp.raise_for_status()
            results = _parse_duckduckgo_html(resp.text)[:count]
            if results:
                return {"status": "success", "results": results, "count": len(results)}
            return {"status": "no_results", "results": [], "query": query}
    except Exception as e:
        logger.warning(f"DuckDuckGo search failed for {query!r}: {e}")
        return {"status": "error", "results": [], "message": f"Web search failed: {e}"}


def _parse_duckduckgo_html(page: str) -> list[dict]:
    """Extract title/url/snippet triples from the DuckDuckGo HTML results page."""
    results = []
    # Result links look like: <a rel="nofollow" class="result__a" href="...">Title</a>
    link_re = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_re = re.compile(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippets = [_strip_tags(s) for s in snippet_re.findall(page)]
    for i, (href, title) in enumerate(link_re.findall(page)):
        url = href
        # DDG wraps URLs: //duckduckgo.com/l/?uddg=<encoded>&rut=...
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            url = qs.get("uddg", [href])[0]
        results.append({
            "title": _strip_tags(title),
            "url": url,
            "snippet": snippets[i] if i < len(snippets) else "",
        })
    return results


async def find_businesses(
    industry: str,
    location: str,
    count: int = 20,
    without_website: bool = False,
) -> dict:
    """Find real businesses by industry + location.

    Primary source when a browser is connected: Google Maps (real phone numbers,
    addresses, and an accurate has-website signal). Falls back to OpenStreetMap
    (Overpass) and a web-search top-up otherwise.

    ``without_website=True`` keeps only businesses with no website listed — the
    highest-value outreach targets.
    """
    count = max(1, min(int(count or 20), 300))
    businesses: list[dict] = []
    source_used = None

    # 1) Google Maps via the browser harness — best data (phones + no-website flag).
    if browser_cli.available:
        try:
            gmaps = await scrape_google_maps(industry, location, count, without_website)
            if gmaps.get("status") == "success":
                businesses = gmaps["businesses"]
                source_used = "google_maps"
        except Exception as e:
            logger.warning(f"Google Maps scrape failed for {industry!r}/{location!r}: {e}")

    # 1a) Google Maps via the WebSocket bridge (remote harness).
    if not businesses:
        from app.services.browser_harness_bridge import bridge as harness_bridge
        if harness_bridge.client_available:
            try:
                gmaps = await scrape_google_maps_bridge(
                    harness_bridge, industry, location, count, without_website
                )
                if gmaps.get("status") == "success":
                    businesses = gmaps["businesses"]
                    source_used = "google_maps"
            except Exception as e:
                logger.warning(f"Bridge Google Maps scrape failed for {industry!r}/{location!r}: {e}")

    # 1b) Google Maps / web search via cloud browser (Playwright on Railway).
    if not businesses:
        from app.integrations.cloud_browser import cloud_browser
        if cloud_browser.available:
            try:
                results = await cloud_browser.search_google(
                    f"{industry} in {location} contact phone email", count=20
                )
                if results:
                    seen_names = {b["name"].lower() for b in businesses}
                    for r in results:
                        name = r["title"][:200]
                        if name.lower() not in seen_names:
                            seen_names.add(name.lower())
                            businesses.append({
                                "name": name,
                                "website": r["url"],
                                "source": "cloud_search",
                            })
                    source_used = source_used or "cloud_search"
            except Exception as e:
                logger.warning(f"Cloud browser search failed: {e}")

    # 2) OpenStreetMap fallback.
    if len(businesses) < count:
        try:
            fetch = count * 3 if without_website else count
            osm = await _overpass_businesses(industry, location, min(fetch, 300))
            if without_website:
                osm = [b for b in osm if not b.get("website")]
            have = {b["name"].lower() for b in businesses}
            for b in osm:
                if b["name"].lower() not in have:
                    have.add(b["name"].lower())
                    businesses.append(b)
            source_used = source_used or "openstreetmap"
        except Exception as e:
            logger.warning(f"Overpass lookup failed for {industry!r}/{location!r}: {e}")

    # 3) Web-search top-up (only when NOT filtering for no-website, since these are URLs).
    if len(businesses) < count and not without_website:
        topup = await web_search(f"{industry} companies in {location} contact", count=count - len(businesses))
        seen = {b["name"].lower() for b in businesses}
        for r in topup.get("results", []):
            name = r["title"].split(" - ")[0].split(" | ")[0].strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                businesses.append({
                    "name": name[:200],
                    "website": r["url"],
                    "snippet": r["snippet"][:300],
                    "source": "web_search",
                })

    # 3b) Directory fallback for without_website mode — search business directories
    #     via DuckDuckGo (always available) + cloud browser (when available).
    if len(businesses) < count and without_website:
        try:
            seen_names = {b["name"].lower() for b in businesses}
            needed = count - len(businesses)
            dir_queries = [
                f"{industry} in {location} phone contact number",
                f"{industry} {location} directory phone",
                f"list of {industry} in {location} contact details",
                f"{industry} {location} yellow pages phone",
            ]
            for dq in dir_queries:
                if len(businesses) >= count:
                    break
                topup = await web_search(dq, count=15)
                for r in topup.get("results", []):
                    name = r["title"].split(" - ")[0].split(" | ")[0].strip()
                    if not name or name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())
                    snippet = r.get("snippet", "") or ""
                    phone = ""
                    m = re.search(_PHONE_RE, snippet)
                    if m:
                        phone = m.group(0).strip()
                    businesses.append({
                        "name": name[:200],
                        "phone": phone,
                        "email": "",
                        "website": r.get("url", ""),
                        "snippet": snippet[:300] if snippet else "",
                        "source": "directory_search",
                    })
                    if len(businesses) >= count:
                        break

            # Phone enrichment: scrape top result URLs to extract phone numbers for
            # leads that were found from snippets (which often omit the phone).
            phone_count = sum(1 for b in businesses if b.get("phone"))
            if phone_count < (needed // 3) and businesses:
                url_targets = [b for b in businesses if not b.get("phone") and b.get("website")]
                for target in url_targets[:5]:
                    try:
                        page = await asyncio.wait_for(scrape_url(target["website"]), timeout=8.0)
                        text = page.get("content", "")
                        m = re.search(_PHONE_RE, text)
                        if m:
                            target["phone"] = m.group(0).strip()
                            phone_count += 1
                            if phone_count >= (needed // 3):
                                break
                    except Exception:
                        continue

            # Enhance with cloud browser directory scraping if available
            from app.integrations.cloud_browser import cloud_browser
            if cloud_browser.available and len(businesses) < count:
                dir_google = await cloud_browser.search_google(
                    f"{industry} in {location} yellow pages directory phone", count=10
                )
                for r in dir_google:
                    name = r.get("title", "")[:200]
                    if not name or name.lower() in seen_names:
                        continue
                    seen_names.add(name.lower())
                    businesses.append({
                        "name": name,
                        "phone": "",
                        "email": "",
                        "website": r.get("url", ""),
                        "snippet": r.get("snippet", "")[:300],
                        "source": "directory_search",
                    })
                    if len(businesses) >= count:
                        break

                # Scrape directory pages for structured listings
                dir_urls = [
                    r["url"] for r in dir_google
                    if any(d in r["url"].lower() for d in
                           ["yellowpages", "brabys", "sayellow", "cylex", "yellosa"])
                ]
                for ds in dir_urls[:3]:
                    if len(businesses) >= count:
                        break
                    try:
                        listings = await cloud_browser.scrape_business_directory(ds)
                        for dl in listings:
                            dl_name = dl["name"]
                            if dl_name.lower() in seen_names:
                                continue
                            seen_names.add(dl_name.lower())
                            businesses.append({
                                "name": dl_name[:200],
                                "phone": dl.get("phone", ""),
                                "email": "",
                                "website": "",
                                "source": "directory",
                            })
                            if len(businesses) >= count:
                                break
                    except Exception:
                        continue

            source_used = source_used or "directory_search"
        except Exception as e:
            logger.warning(f"Directory fallback search failed: {e}")

    businesses = businesses[:count]

    # Phone enrichment: when Google Maps wasn't the source, scrape business
    # websites to fill in missing phone numbers.
    if source_used != "google_maps":
        phone_count = sum(1 for b in businesses if b.get("phone"))
        if phone_count < max(3, count // 4):
            url_targets = [
                b for b in businesses
                if not b.get("phone") and b.get("website")
                and not str(b.get("website", "")).startswith("has_website")
            ]
            max_scrape = min(8, len(url_targets))
            scraped = 0
            for target in url_targets[:max_scrape]:
                try:
                    page = await asyncio.wait_for(scrape_url(target["website"]), timeout=10.0)
                    text = page.get("content", "")
                    m = re.search(_PHONE_RE, text)
                    if m:
                        target["phone"] = m.group(0).strip()
                        scraped += 1
                except Exception:
                    continue
            if scraped:
                logger.info("Phone enrichment: scraped %d/%d sites, found %d phones",
                            max_scrape, len(url_targets), scraped)

    if businesses:
        return {"status": "success", "businesses": businesses, "count": len(businesses), "source": source_used}
    hint = (
        " (none without a website were found here — try another city or industry)"
        if without_website else ""
    )
    return {
        "status": "no_results",
        "businesses": [],
        "message": f"No businesses found for '{industry}' in '{location}'{hint}.",
    }


_PHONE_RE = re.compile(r"(?:\+?\d[\d\s().\-]{7,}\d)")


# Field/record delimiters for the scraped payload. The harness js() returns
# `null` for .map(function(){...}) and for object serialization, but handles an
# arrow expression that builds a delimited STRING — so we use that and split it
# in Python. Delimiters are neutralized out of any scraped value first.
_FIELD = "@@F@@"
_RECORD = "@@R@@"


def _build_gmaps_script(query: str, target: int) -> str:
    """Build the browser-harness Python script that scrapes one Maps search.

    JS is embedded as triple-single-quoted raw literals so selector quotes pass
    through untouched. Extraction walks each ``a.hfpxzc`` result anchor, reads
    its card text, and detects the "Website" action button — returning a single
    delimited string (arrow expression, no function keyword, no JSON objects).
    """
    url = "https://www.google.com/maps/search/" + urllib.parse.quote(query)
    max_scrolls = max(12, min(target // 4 + 10, 60))
    # Scrolling the LAST result into view reliably triggers Google Maps' lazy
    # loading of more cards (plain scrollTo often stalls at the first ~8).
    scroll_js = (
        r'''var e=document.querySelectorAll('a.hfpxzc');'''
        r'''if(e.length){e[e.length-1].scrollIntoView();}'''
        r'''var f=document.querySelector('div[role="feed"]');if(f){f.scrollTo(0,f.scrollHeight);}'''
    )
    count_js = r'''document.querySelectorAll('a.hfpxzc').length'''
    # Single arrow expression per anchor → "name@@F@@web@@F@@url@@F@@info",
    # records joined by @@R@@.
    extract_js = (
        r"""Array.from(document.querySelectorAll('a.hfpxzc')).map(a=>"""
        r"""(a.getAttribute('aria-label')||'').split('@@').join(' ')"""
        r"""+'@@F@@'+((a.closest('div[jsaction]')&&a.closest('div[jsaction]').querySelector('a[data-value="Website"]'))?'1':'0')"""
        r"""+'@@F@@'+a.href"""
        r"""+'@@F@@'+(a.closest('div[jsaction]')?(a.closest('div[jsaction]').innerText||'').split('\n').join(' | ').split('@@').join(' ').slice(0,260):'')"""
        r""").join('@@R@@')"""
    )
    lines = [
        "import time",
        f"new_tab({url!r})",
        "wait_for_load()",
        "time.sleep(3)",
        "prev = -1",
        "stale = 0",
        f"for _i in range({max_scrolls}):",
        f"    js(r'''{scroll_js}''')",
        "    time.sleep(1.6)",
        f"    cnt = js(r'''{count_js}''')",
        "    try:",
        "        cnt = int(cnt)",
        "    except Exception:",
        "        cnt = 0",
        f"    if cnt >= {target}:",
        "        break",
        "    if cnt == prev:",
        "        stale += 1",
        "    else:",
        "        stale = 0",
        "    if stale >= 4:",  # give lazy-load several chances before giving up
        "        break",
        "    prev = cnt",
        f"data = js(r'''{extract_js}''')",
        "print('RESULTS_JSON_START')",
        "print(data if isinstance(data, str) else '')",
        "print('RESULTS_JSON_END')",
    ]
    return "\n".join(lines) + "\n"


async def scrape_google_maps(industry: str, location: str, count: int = 40, without_website: bool = False) -> dict:
    """Scrape Google Maps for real businesses via the user's Chrome (browser harness).

    Extracts name, phone (from the card), maps URL, and whether the listing has a
    website. Returns up to `count` businesses, filtered to no-website when asked.
    """
    if not browser_cli.available:
        return {"status": "browser_unavailable", "businesses": []}

    query = f"{industry} in {location}"
    target = count * 3 if without_website else int(count * 1.3) + 2
    script = _build_gmaps_script(query, min(target, 200))
    result = await browser_cli.run_script(script, timeout=150.0)
    if result.get("status") != "success":
        return {"status": result.get("status", "error"), "businesses": [], "detail": result.get("error") or result.get("message")}

    out = result.get("output", "")
    raw = _between(out, "RESULTS_JSON_START", "RESULTS_JSON_END")
    if not raw or raw == "None":
        return {"status": "no_results", "businesses": []}

    businesses = []
    seen: set[str] = set()
    for rec in raw.split(_RECORD):
        fields = rec.split(_FIELD)
        if len(fields) < 4:
            continue
        name = fields[0].strip()
        has_website = fields[1].strip() == "1"
        maps_url = fields[2].strip()
        info = fields[3]
        if not name or name.lower() in seen:
            continue
        if without_website and has_website:
            continue
        seen.add(name.lower())
        phone_match = _PHONE_RE.search(info)
        phone = phone_match.group(0).strip() if phone_match else None
        businesses.append({
            "name": name[:200],
            "phone": phone,
            "email": None,  # Google Maps does not expose email
            "website": "has_website" if has_website else None,
            "address": _guess_address(info),
            "maps_url": maps_url,
            "source": "google_maps",
        })
        if len(businesses) >= count:
            break

    if not businesses:
        return {"status": "no_results", "businesses": []}
    return {"status": "success", "businesses": businesses, "count": len(businesses)}


async def scrape_google_maps_bridge(
    bridge: Any, industry: str, location: str, count: int = 40, without_website: bool = False
) -> dict:
    """Scrape Google Maps via the WebSocket bridge (remote harness)."""
    query = f"{industry} in {location}"
    target = count * 3 if without_website else int(count * 1.3) + 2
    script = _build_gmaps_script(query, min(target, 200))
    result = await bridge.run_script_safe(script, timeout=300.0)
    if result.get("status") != "success":
        return {"status": result.get("status", "error"), "businesses": [], "detail": result.get("message") or result.get("error")}

    out = result.get("output", "")
    raw = _between(out, "RESULTS_JSON_START", "RESULTS_JSON_END")
    if not raw or raw == "None":
        return {"status": "no_results", "businesses": []}

    businesses = []
    seen: set[str] = set()
    for rec in raw.split(_RECORD):
        fields = rec.split(_FIELD)
        if len(fields) < 4:
            continue
        name = fields[0].strip()
        has_website = fields[1].strip() == "1"
        maps_url = fields[2].strip()
        info = fields[3]
        if not name or name.lower() in seen:
            continue
        if without_website and has_website:
            continue
        seen.add(name.lower())
        phone_match = _PHONE_RE.search(info)
        phone = phone_match.group(0).strip() if phone_match else None
        businesses.append({
            "name": name[:200],
            "phone": phone,
            "email": None,
            "website": "has_website" if has_website else None,
            "address": _guess_address(info),
            "maps_url": maps_url,
            "source": "google_maps",
        })
        if len(businesses) >= count:
            break

    if not businesses:
        return {"status": "no_results", "businesses": []}
    return {"status": "success", "businesses": businesses, "count": len(businesses)}


def _between(text: str, start: str, end: str) -> str:
    if start in text and end in text:
        return text.split(start, 1)[1].split(end, 1)[0].strip()
    return ""


_RATING_RE = re.compile(r"^\d(?:\.\d)?\(\d+\)$")  # e.g. "4.8(26)"


def _guess_address(info: str) -> str | None:
    """Pull a plausible address segment out of a Google Maps card's text.

    Cards read like "Category · 278 Main Road" — the address is the part after
    the middot. Skip rating tokens and phone numbers.
    """
    def _bad(seg: str) -> bool:
        return (
            _RATING_RE.match(seg) or _PHONE_RE.fullmatch(seg) or len(seg) < 6
            or seg.startswith("R ") or seg.startswith("$")  # price range
            or "Open" in seg or "Closed" in seg or "Closes" in seg or "Opens" in seg
        )

    for chunk in info.split("|"):
        chunk = chunk.strip()
        if "·" in chunk:
            tail = chunk.split("·")[-1].strip()
            if not _bad(tail):
                return tail[:200]
    # Fallback: a segment with a street word.
    for p in (s.strip() for s in info.split("|")):
        if _RATING_RE.match(p) or _PHONE_RE.fullmatch(p) or len(p) < 6:
            continue
        if re.search(r"\d", p) and any(w in p.lower() for w in
            (" st", " rd", " ave", "street", "road", "drive", " dr", "blvd", "lane", " ln", " way", "suite")):
            return p[:200]
    return None


async def _overpass_businesses(industry: str, location: str, count: int) -> list[dict]:
    """Query OSM for named businesses in the located area (geocode cached + mirror failover)."""
    async with httpx.AsyncClient(timeout=35.0, headers={"User-Agent": USER_AGENT}) as client:
        box = await _geocode_bbox(client, location)
        if not box:
            return []
        south, north, west, east = box

        tag = _OSM_INDUSTRY_TAGS.get(industry.strip().lower())
        if tag:
            selector = f'nwr{tag}["name"]'
        else:
            safe = re.sub(r'[^\w\s-]', "", industry)
            selector = f'nwr["name"~"{safe}",i]'

        ql = (
            f"[out:json][timeout:20];"
            f"({selector}({south},{west},{north},{east}););"
            f"out tags center {min(count * 2, 400)};"
        )
        elements = await _overpass_query(client, ql)

    businesses = []
    seen: set[str] = set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        addr_parts = [
            tags.get("addr:housenumber"), tags.get("addr:street"),
            tags.get("addr:suburb"), tags.get("addr:city"), tags.get("addr:postcode"),
        ]
        businesses.append({
            "name": name,
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "email": tags.get("email") or tags.get("contact:email"),
            "website": tags.get("website") or tags.get("contact:website"),
            "address": ", ".join(p for p in addr_parts if p) or None,
            "source": "openstreetmap",
        })
        if len(businesses) >= count:
            break
    return businesses


async def scrape_url(url: str, max_chars: int = 4000) -> dict:
    """Fetch a URL and return readable text content."""
    try:
        async with httpx.AsyncClient(
            timeout=25.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            page = resp.text
        # Drop script/style blocks before stripping tags.
        page = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", page, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"\s+", " ", _strip_tags(page)).strip()
        return {"status": "success", "url": url, "content": text[:max_chars]}
    except Exception as e:
        logger.warning(f"Scrape failed for {url}: {e}")
        return {"status": "error", "url": url, "message": f"Failed to fetch: {e}"}


class BrowserHarnessCLI:
    """Runs Python snippets through the locally installed ``browser-harness`` CLI.

    The harness connects to the user's running Chrome over CDP — it can search,
    navigate, click, and read pages exactly like the user, including sites the
    user is already logged into. See hermes-agent/browser-harness for helpers
    (new_tab, wait_for_load, page_info, js, capture_screenshot, click_at_xy...).
    """

    def __init__(self):
        self._path: str | None = None
        self._checked = False

    @property
    def available(self) -> bool:
        if not self._checked:
            self._path = shutil.which("browser-harness")
            self._checked = True
        return self._path is not None

    async def run_script(self, script: str, timeout: float = 120.0, max_output: int = 400_000) -> dict:
        """Pipe a Python script to the browser-harness CLI and return its output.

        ``max_output`` is generous because scraping payloads (e.g. 100+ Google
        Maps cards) easily exceed tens of KB — truncating mid-payload would drop
        the closing marker and silently lose every result.
        """
        if not self.available:
            return {
                "status": "not_installed",
                "message": "browser-harness CLI not found on PATH.",
            }
        try:
            proc = await asyncio.create_subprocess_exec(
                self._path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(script.encode("utf-8")), timeout=timeout
            )
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            if proc.returncode == 0:
                return {"status": "success", "output": out[:max_output]}
            return {"status": "error", "output": out[:max_output], "error": err[:4000]}
        except asyncio.TimeoutError:
            return {"status": "timeout", "message": f"Browser task exceeded {timeout}s"}
        except Exception as e:
            logger.warning(f"browser-harness run failed: {e}")
            return {"status": "error", "message": str(e)}

    async def fetch_page(self, url: str) -> dict:
        """Open a URL in the user's Chrome and return the page text + title."""
        script = (
            f"new_tab({url!r})\n"
            "wait_for_load()\n"
            "print(page_info())\n"
            "print(js('document.body.innerText.slice(0, 4000)'))\n"
        )
        return await self.run_script(script)


browser_cli = BrowserHarnessCLI()
