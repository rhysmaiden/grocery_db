"""Coles scraper.

Fetch logic ported from hotprices-au (MIT). Two transports:

- ColesSession (curl_cffi): works from residential IPs. The homepage HTML
  embeds __NEXT_DATA__ with the API subscription key and Next.js build id,
  then category pages are plain JSON endpoints.
- ColesBrowserSession (camoufox stealth Firefox): required from datacenter
  IPs (CI), where the bot wall serves HTML challenges on the _next/data
  endpoints regardless of TLS fingerprint. Category JSON is fetched through
  the browser's own request context so it carries the full fingerprint.
  Needs the 'coles-browser' extra + 'python -m camoufox fetch'.

scrape() tries curl_cffi first and falls back to the browser; set
GDB_COLES_BROWSER=1 (as CI does) to go straight to the browser.
"""

import json
import os
import re

from . import common

CHAIN = "coles"
STORE_ID = "0584"  # hotprices-au default; national pricing
SKIP_CATEGORIES = {"down-down", "back-to-school"}  # duplicate products

_CATEGORIES_QUERY = """
    query GetShopProductsMenu($storeId: BrandedId!, $withCampaignLinks: Boolean!, $campaignCount: Int) {
        menuItems: productCategories(
            storeId: $storeId
            withCampaignLinks: $withCampaignLinks
            campaignCount: $campaignCount
        ) {
            items: catalogGroupView {
                id
                name
                seoToken
            }
        }
    }
"""


RESETS_MAX = 3


class ColesSession:
    def __init__(self):
        self.resets = 0
        self.session = common.new_session()
        self._bootstrap()

    def _bootstrap(self):
        resp = common.fetch(
            self.session,
            "GET",
            "https://www.coles.com.au",
            ok_html=True,
            headers={"Accept-Language": "en-AU,en;q=0.9"},
        )
        m = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            resp.text,
            re.S,
        )
        if not m:
            raise RuntimeError("No __NEXT_DATA__ on Coles homepage (bot wall?)")
        next_data = json.loads(m.group(1))
        self.api_key = next_data["runtimeConfig"]["BFF_API_SUBSCRIPTION_KEY"]
        self.build_id = next_data["buildId"]

    def get_categories(self) -> list[dict]:
        resp = common.fetch(
            self.session,
            "POST",
            "https://www.coles.com.au/api/graphql",
            headers={
                "ocp-apim-subscription-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "query": _CATEGORIES_QUERY,
                "variables": {
                    "storeId": f"COL:{STORE_ID}",
                    "withCampaignLinks": True,
                    "campaignCount": 0,
                },
                "operationName": "GetShopProductsMenu",
            },
        )
        items = resp.json()["data"]["menuItems"]["items"]
        return [c for c in items if c["seoToken"] not in SKIP_CATEGORIES]

    def get_category_page(self, slug: str, page: int) -> dict:
        try:
            return self._fetch_category_page(slug, page)
        except common.ChainTimeout:
            raise
        except RuntimeError:
            # Bot wall serves HTML on the data endpoint; a fresh session
            # (new TLS fingerprint + cookies + key) sometimes gets through.
            if self.resets >= RESETS_MAX:
                raise
            self.resets += 1
            print(f"coles: bot wall on {slug} p{page}, resetting session "
                  f"({self.resets}/{RESETS_MAX})")
            self.session = common.new_session()
            self._bootstrap()
            return self._fetch_category_page(slug, page)

    def _fetch_category_page(self, slug: str, page: int) -> dict:
        resp = common.fetch(
            self.session,
            "GET",
            f"https://www.coles.com.au/_next/data/{self.build_id}/en/browse/{slug}.json",
            params={"slug": slug, "page": page},
            headers={"ocp-apim-subscription-key": self.api_key},
        )
        return resp.json()

    def close(self):
        pass


class ColesBrowserSession:
    """Stealth-browser transport (hotprices-au's approach, MIT)."""

    def __init__(self):
        from camoufox.sync_api import Camoufox

        self._camoufox_cls = Camoufox
        self.resets = 0
        self._cm = None
        self._launch()

    def _launch(self):
        self._cm = self._camoufox_cls(headless=True, enable_cache=True, humanize=True)
        self._browser = self._cm.__enter__()
        self.context = self._browser.new_context()
        self.page = self.context.new_page()
        self.api = self.context.request
        self.page.goto(
            "https://www.coles.com.au", wait_until="domcontentloaded", timeout=120_000
        )
        # Bot protection may interpose redirects; the data script appearing
        # means we landed on the real site.
        self.page.locator("script#__NEXT_DATA__").wait_for(
            state="attached", timeout=120_000
        )
        next_data = json.loads(
            self.page.evaluate(
                "() => document.getElementById('__NEXT_DATA__').textContent"
            )
        )
        self.api_key = next_data["runtimeConfig"]["BFF_API_SUBSCRIPTION_KEY"]
        self.build_id = next_data["buildId"]

    def _reset(self):
        self.close()
        self._launch()

    def close(self):
        try:
            self._cm.__exit__(None, None, None)
        except Exception:
            pass

    def get_categories(self) -> list[dict]:
        resp = self.api.post(
            "https://www.coles.com.au/api/graphql",
            headers={
                "ocp-apim-subscription-key": self.api_key,
                "Content-Type": "application/json",
            },
            data=json.dumps(
                {
                    "query": _CATEGORIES_QUERY,
                    "variables": {
                        "storeId": f"COL:{STORE_ID}",
                        "withCampaignLinks": True,
                        "campaignCount": 0,
                    },
                    "operationName": "GetShopProductsMenu",
                }
            ),
        )
        if not resp.ok:
            raise RuntimeError(f"coles categories: HTTP {resp.status}")
        items = resp.json()["data"]["menuItems"]["items"]
        return [c for c in items if c["seoToken"] not in SKIP_CATEGORIES]

    def get_category_page(self, slug: str, page: int) -> dict:
        common.check_deadline()
        resp = self._get(slug, page)
        if "json" not in resp.headers.get("content-type", ""):
            if self.resets >= RESETS_MAX:
                raise RuntimeError(f"coles bot wall persists after {self.resets} browser resets")
            self.resets += 1
            print(f"coles: bot wall on {slug} p{page}, restarting browser "
                  f"({self.resets}/{RESETS_MAX})")
            self._reset()
            resp = self._get(slug, page)
            if "json" not in resp.headers.get("content-type", ""):
                raise RuntimeError("coles bot wall on retry after browser restart")
        if not resp.ok:
            raise RuntimeError(f"coles {slug} p{page}: HTTP {resp.status}")
        return resp.json()

    def _get(self, slug: str, page: int):
        return self.api.get(
            f"https://www.coles.com.au/_next/data/{self.build_id}/en/browse/{slug}.json"
            f"?slug={slug}&page={page}",
            headers={"ocp-apim-subscription-key": self.api_key},
        )


def scrape(quick: bool = False) -> list[dict]:
    if os.environ.get("GDB_COLES_BROWSER"):
        return _scrape_with(ColesBrowserSession(), quick)
    try:
        return _scrape_with(ColesSession(), quick)
    except common.ChainTimeout:
        raise
    except Exception as err:
        print(f"coles: curl_cffi transport failed ({err}); trying stealth browser")
        try:
            browser = ColesBrowserSession()
        except ImportError:
            print("coles: camoufox not installed (uv sync --extra coles-browser)")
            raise err from None
        return _scrape_with(browser, quick)


def _scrape_with(coles, quick: bool) -> list[dict]:
    try:
        categories = coles.get_categories()
        if quick:
            categories = categories[:1]
        for cat in categories:
            slug = cat["seoToken"]
            print(f"coles: category {slug}")
            products = []
            page = 1
            while True:
                data = coles.get_category_page(slug, page)
                results = data["pageProps"]["searchResults"]
                products.extend(results["results"])
                if quick or len(products) >= results["noOfResults"] or not results["results"]:
                    break
                page += 1
                common.pause()
            cat["Products"] = products
        return categories
    finally:
        coles.close()


def _quantity_and_unit(item) -> tuple[float | None, str | None]:
    from .. import units

    size = item.get("size") or item.get("description")
    quantity, unit = _parse_coles_size(size)
    if quantity is not None:
        return quantity, unit
    unit_data = item["pricing"].get("unit") or {}
    if "ofMeasureUnits" in unit_data and unit_data.get("quantity"):
        return unit_data["quantity"], unit_data["ofMeasureUnits"]
    return None, None


def _parse_coles_size(size: str | None) -> tuple[float | None, str | None]:
    """Coles-specific size formats, then the generic parser."""
    from .. import units

    if not size:
        return None, None
    size = size.lower()
    patterns = [
        r"^.* (?P<quantity>[0-9]+)(?P<unit>[a-z]+):(pack(?P<count>[0-9]+)|(?P<each>ea))",
        r"^.* (?P<count>[0-9]+)pk can (?P<quantity>[0-9]+)(?P<unit>[a-z]+)",
        r"^.* (?P<quantity>[0-9]+)(?P<unit>[a-z]+) \(?(?P<count>[0-9]+)pk\)?(:ctn)?(?P<ctn_count>[0-9]+)?",
    ]
    for pattern in patterns:
        matched = re.match(pattern, size)
        if not matched:
            continue
        groups = matched.groupdict()
        if groups["unit"] not in units.GLOBAL_UNITS:
            continue
        count = groups.get("ctn_count") or groups.get("count")
        if count:
            count = float(count)
        elif groups.get("each"):
            count = 1.0
        else:
            continue
        return float(groups["quantity"]) * count, groups["unit"]
    return units.parse_str_unit(size)


def normalize(raw: list[dict]) -> list[dict]:
    from .. import units

    out = []
    for cat in raw:
        category = cat.get("name")
        for item in cat.get("Products", []):
            if item.get("_type") in ("SINGLE_TILE", "CONTENT_ASSOCIATION") and item.get("adId"):
                continue  # ad tile, not a product
            pricing = item.get("pricing")
            if not pricing or pricing.get("now") is None:
                continue
            quantity, unit = _quantity_and_unit(item)
            quantity, unit = units.normalise(quantity, unit)
            was = pricing.get("was")
            price_c = int(round(pricing["now"] * 100))
            was_c = int(round(was * 100)) if was else None
            out.append(
                {
                    "sku": str(item["id"]),
                    "name": item.get("name"),
                    "brand": item.get("brand"),
                    "description": item.get("description"),
                    "price_c": price_c,
                    "was_price_c": was_c if was_c and was_c > price_c else None,
                    "is_member_price": False,
                    "quantity": quantity,
                    "unit": unit,
                    "is_weighted": (pricing.get("unit") or {}).get("isWeighted", False),
                    "category": category,
                    "url": f"https://www.coles.com.au/product/{item['id']}",
                }
            )
    return out
