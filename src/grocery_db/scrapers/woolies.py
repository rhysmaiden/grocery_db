"""Woolworths scraper. Fetch logic ported from hotprices-au (MIT)."""

import json

from . import common

CHAIN = "woolies"
PAGE_SIZE = 36  # larger sizes error out

SKIP_CATEGORY_IDS = {
    "specialsgroup",  # duplicate products
    "1_A363395",  # Everyday Market (100k+ products)
    "1_DEA3ED5",  # Home & Lifestyle
    "1_B863F57",  # Electronics
}


def _category_request(cat_id: str, page: int) -> dict:
    return {
        "categoryId": cat_id,
        "pageNumber": page,
        "pageSize": PAGE_SIZE,
        "sortType": "Name",
        "url": "/shop/browse/fruit-veg",
        "location": "/shop/browse/fruit-veg",
        "formatObject": '{"name":"Fruit & Veg"}',
        "isSpecial": False,
        "isBundle": False,
        "isMobile": False,
        "filters": [{"Items": [{"Term": "Woolworths"}], "Key": "SoldBy"}],
        "token": "",
        "gpBoost": 0,
        "isHideUnavailableProducts": False,
        "enableAdReRanking": False,
        "groupEdmVariants": True,
        "categoryVersion": "v2",
    }


def scrape(quick: bool = False) -> list[dict]:
    session = common.new_session()
    common.fetch(session, "GET", "https://www.woolworths.com.au", ok_html=True)
    resp = common.fetch(
        session, "GET", "https://www.woolworths.com.au/apis/ui/PiesCategoriesWithSpecials"
    )
    categories = [
        c
        for c in resp.json()["Categories"]
        if c["NodeId"] not in SKIP_CATEGORY_IDS and c["Description"] != "Front of Store"
    ]
    if quick:
        categories = categories[:1]
    for cat in categories:
        cat_id = cat["NodeId"]
        print(f"woolies: category {cat_id} ({cat['Description']})")
        bundles = []
        page = 1
        while True:
            resp = common.fetch(
                session,
                "POST",
                "https://www.woolworths.com.au/apis/ui/browse/category",
                json=_category_request(cat_id, page),
            )
            data = resp.json()
            bundles.extend(data["Bundles"])
            if (
                quick
                or len(bundles) >= data["TotalRecordCount"]
                or not data["Bundles"]
            ):
                break
            page += 1
            common.pause()
        cat["Products"] = bundles
    return categories


def normalize(raw: list[dict]) -> list[dict]:
    from .. import units

    out = []
    for cat in raw:
        category = cat.get("Description")
        for bundle in cat.get("Products", []):
            products = bundle.get("Products") or []
            if not products:
                continue
            item = products[0]

            price = item.get("Price")
            size = item.get("PackageSize")
            # Out of stock items may only carry WasPrice
            if price is None and not item.get("IsInStock") and item.get("WasPrice"):
                price = item["WasPrice"]
            if price is None or (size or "").lower() == "min. 250g":
                price = item.get("CupPrice")
                size = item.get("CupMeasure")
            if price is None:
                continue

            quantity, unit = units.parse_str_unit(size)
            if quantity is None and (item.get("Unit") or "").lower() == "each":
                quantity, unit = 1, "ea"
            quantity, unit = units.normalise(quantity, unit)

            price_c = int(round(price * 100))
            was = item.get("WasPrice")
            was_c = int(round(was * 100)) if was else None
            out.append(
                {
                    "sku": str(item["Stockcode"]),
                    "name": item.get("Name"),
                    "brand": item.get("Brand"),
                    "description": item.get("Description"),
                    "price_c": price_c,
                    "was_price_c": was_c if was_c and was_c > price_c else None,
                    "is_member_price": False,
                    "quantity": quantity,
                    "unit": unit,
                    "is_weighted": False,
                    "category": category,
                    "url": f"https://www.woolworths.com.au/shop/productdetails/{item['Stockcode']}",
                }
            )
    return out
