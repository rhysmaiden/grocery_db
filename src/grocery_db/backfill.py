"""Import of hotprices.org published history (Coles + Woolies).

Used two ways:
- one-off initial backfill (~2.7 years of change-points), and
- gap-fill fallback when a CI scrape of coles/woolies fails — their site
  keeps collecting daily, so importing their latest file keeps those chains
  gapless. Imports are idempotent: UNIQUE(product_id, date, store_id) plus
  INSERT OR IGNORE means our own scraped events always win on collisions.

Data format: heissepreise canonical JSON — a list of items each carrying a
priceHistory of change-points (a point only when the price changed). Rows
are tagged source='hotprices_backfill' so they can be quarantined.

No Aldi data exists here or anywhere; Aldi history is ours alone.
"""

import gzip
import json
from pathlib import Path

import sqlite3

SOURCE = "hotprices_backfill"
URLS = {
    "coles": "https://hotprices.org/data/latest-canonical.coles.compressed.json.gz",
    "woolies": "https://hotprices.org/data/latest-canonical.woolies.compressed.json.gz",
}


def download(chain: str, dest_dir: Path = Path("data/backfill")) -> Path:
    from curl_cffi import requests

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{chain}.json.gz"
    print(f"downloading {URLS[chain]}")
    resp = requests.get(URLS[chain], impersonate="chrome", timeout=300)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def import_file(conn: sqlite3.Connection, chain: str, path: Path) -> dict:
    # Downloads may arrive pre-decompressed (transfer-encoding handling),
    # so fall back to plain JSON when the gzip magic is missing.
    try:
        with gzip.open(path, "rt") as fp:
            items = json.load(fp)
    except gzip.BadGzipFile:
        items = json.loads(path.read_text())

    stats = {"products": 0, "price_events": 0, "skipped_existing": 0}
    for item in items:
        history = item.get("priceHistory") or []
        if not history:
            continue
        dates = [h["date"] for h in history]
        sku = str(item["id"])

        row = conn.execute(
            "SELECT id FROM products WHERE chain = ? AND sku = ?", (chain, sku)
        ).fetchone()
        if row is not None:
            product_id = row["id"]
            conn.execute(
                "UPDATE products SET last_seen = ? WHERE id = ? AND last_seen < ?",
                (max(dates), product_id, max(dates)),
            )
        else:
            cur = conn.execute(
                """INSERT INTO products
                   (chain, sku, name, brand, description, quantity, unit,
                    is_weighted, category, url, first_seen, last_seen, source)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                (
                    chain,
                    sku,
                    item.get("name"),
                    item.get("description"),
                    item.get("quantity"),
                    item.get("unit"),
                    int(item.get("isWeighted") or 0),
                    item.get("category"),
                    min(dates),
                    max(dates),
                    SOURCE,
                ),
            )
            product_id = cur.lastrowid
            stats["products"] += 1

        for point in history:
            conn.execute(
                """INSERT OR IGNORE INTO price_events
                   (product_id, date, price_c, was_price_c, is_member_price, store_id, source)
                   VALUES (?, ?, ?, NULL, 0, '', ?)""",
                (product_id, point["date"], int(round(point["price"] * 100)), SOURCE),
            )
            stats["price_events"] += 1
    conn.commit()
    return stats
