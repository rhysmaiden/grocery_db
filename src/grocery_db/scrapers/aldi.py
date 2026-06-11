"""Aldi scraper.

Hits api.aldi.com.au directly (verified 2026-06). Pagination limit must be
one of 12/16/24/30/32/48/60. servicePoint is a store code; Aldi pricing is
effectively national but we record the code used. Prices arrive in cents.
"""

import re

from . import common

CHAIN = "aldi"
API = "https://api.aldi.com.au/v3/product-search"
SERVICE_POINT = "G452"
PAGE_SIZE = 60


def scrape(quick: bool = False) -> dict:
    session = common.new_session()
    items = []
    offset = 0
    total = None
    while True:
        resp = common.fetch(
            session,
            "GET",
            API,
            params={
                "currency": "AUD",
                "serviceType": "walk-in",
                "limit": PAGE_SIZE,
                "offset": offset,
                "sort": "relevance",
                "servicePoint": SERVICE_POINT,
            },
        )
        data = resp.json()
        if total is None:
            total = data["meta"]["pagination"]["totalCount"]
        page_items = data["data"]
        items.extend(page_items)
        offset += PAGE_SIZE
        print(f"aldi: {min(offset, total)}/{total}")
        if quick or offset >= total or not page_items:
            break
        common.pause()
    return {"chain": CHAIN, "servicePoint": SERVICE_POINT, "items": items}


def _parse_display_price(display: str | None) -> int | None:
    """'$4.99' -> 499 cents."""
    if not display:
        return None
    m = re.search(r"\$([0-9.]+)", display)
    return int(round(float(m.group(1)) * 100)) if m else None


def normalize(raw: dict) -> list[dict]:
    from .. import units

    out = []
    for item in raw["items"]:
        price = item.get("price") or {}
        # notForSale is true for the whole walk-in catalogue (no online
        # shopping); prices are still valid, so only a missing price skips.
        price_c = price.get("amountRelevant", price.get("amount"))
        if price_c is None:
            continue
        quantity, unit = units.parse_str_unit(item.get("sellingSize"))
        if quantity is None:
            quantity, unit = units.parse_str_unit(item.get("quantityUnit"))
        quantity, unit = units.normalise(quantity, unit)
        cats = item.get("categories") or []
        out.append(
            {
                "sku": item["sku"],
                "name": item.get("name"),
                "brand": item.get("brandName"),
                "description": None,
                "price_c": int(price_c),
                "was_price_c": _parse_display_price(price.get("wasPriceDisplay")),
                "is_member_price": False,
                "quantity": quantity,
                "unit": unit,
                "is_weighted": item.get("weightType") not in (None, "0"),
                "category": " > ".join(c["name"] for c in cats) or None,
                "url": f"https://www.aldi.com.au/product/{item['urlSlugText']}"
                if item.get("urlSlugText")
                else None,
            }
        )
    return out
