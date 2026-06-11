"""Coles scraper.

Fetch logic ported from hotprices-au (MIT) but using curl_cffi instead of a
stealth browser: the homepage HTML embeds __NEXT_DATA__ with the API
subscription key and Next.js build id, then category pages are plain JSON
endpoints. If Coles' bot wall starts rejecting curl_cffi, the fallback is
hotprices-au's camoufox approach.
"""

import json
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


class ColesSession:
    def __init__(self):
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
        resp = common.fetch(
            self.session,
            "GET",
            f"https://www.coles.com.au/_next/data/{self.build_id}/en/browse/{slug}.json",
            params={"slug": slug, "page": page},
            headers={"ocp-apim-subscription-key": self.api_key},
        )
        return resp.json()


def scrape(quick: bool = False) -> list[dict]:
    coles = ColesSession()
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
