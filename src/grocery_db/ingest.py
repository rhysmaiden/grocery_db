"""Turn a raw daily dump into change events.

For each normalized item:
- upsert the product (advance last_seen; diff tracked attributes into
  attribute_events so e.g. shrinkflation stays queryable)
- insert a price_events row only if the price differs from the latest event.

Re-running the same day is idempotent thanks to UNIQUE(product_id, date, store_id).
"""

import sqlite3

# Attribute changes worth recording as events. Category/description/url churn
# is noisy (products appear under multiple categories) and updated silently.
TRACKED_ATTRIBUTES = ("name", "brand", "quantity", "unit")

STALENESS_RATIO = 0.5


class StalenessError(RuntimeError):
    pass


def check_staleness(conn: sqlite3.Connection, chain: str, date: str, count: int):
    """Fail loudly if today's catalogue shrank by more than half — that's a
    broken/changed API, not a real delisting wave."""
    row = conn.execute(
        """SELECT product_count FROM scrape_runs
           WHERE chain = ? AND date < ? AND status = 'ingested'
           ORDER BY date DESC LIMIT 1""",
        (chain, date),
    ).fetchone()
    if row and row["product_count"] and count < row["product_count"] * STALENESS_RATIO:
        raise StalenessError(
            f"{chain}: got {count} products, previous run had {row['product_count']} "
            f"(below {STALENESS_RATIO:.0%} threshold) — refusing to ingest"
        )


def ingest_items(
    conn: sqlite3.Connection,
    chain: str,
    date: str,
    items: list[dict],
    source: str = "scrape",
) -> dict:
    stats = {"products_new": 0, "price_events": 0, "attribute_events": 0, "items": 0}
    seen_skus = set()
    for item in items:
        if item["sku"] in seen_skus:
            continue  # product listed under multiple categories
        seen_skus.add(item["sku"])
        stats["items"] += 1

        row = conn.execute(
            "SELECT * FROM products WHERE chain = ? AND sku = ?", (chain, item["sku"])
        ).fetchone()
        if row is None:
            cur = conn.execute(
                """INSERT INTO products
                   (chain, sku, name, brand, description, quantity, unit,
                    is_weighted, category, url, image_url, first_seen, last_seen, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    chain,
                    item["sku"],
                    item["name"],
                    item["brand"],
                    item["description"],
                    item["quantity"],
                    item["unit"],
                    int(item["is_weighted"]),
                    item["category"],
                    item["url"],
                    item.get("image_url"),
                    date,
                    date,
                    source,
                ),
            )
            product_id = cur.lastrowid
            stats["products_new"] += 1
        else:
            product_id = row["id"]
            if date >= row["last_seen"]:
                # Attribute change events only across days; same-day re-ingest
                # still refreshes the columns but must stay event-idempotent.
                if date > row["last_seen"]:
                    for field in TRACKED_ATTRIBUTES:
                        old, new = row[field], item[field]
                        if new is not None and old != new:
                            conn.execute(
                                """INSERT INTO attribute_events
                                   (product_id, date, field, old_value, new_value)
                                   VALUES (?, ?, ?, ?, ?)""",
                                (product_id, date, field, str(old), str(new)),
                            )
                            stats["attribute_events"] += 1
                conn.execute(
                    """UPDATE products SET name = ?, brand = ?, description = ?,
                       quantity = ?, unit = ?, is_weighted = ?, category = ?,
                       url = ?, image_url = ?, last_seen = ? WHERE id = ?""",
                    (
                        item["name"],
                        item["brand"],
                        item["description"],
                        item["quantity"] if item["quantity"] is not None else row["quantity"],
                        item["unit"] if item["unit"] is not None else row["unit"],
                        int(item["is_weighted"]),
                        item["category"],
                        item["url"],
                        item.get("image_url") or row["image_url"],
                        date,
                        product_id,
                    ),
                )

        last_price = conn.execute(
            """SELECT price_c, was_price_c, is_member_price FROM price_events
               WHERE product_id = ? AND store_id = '' ORDER BY date DESC LIMIT 1""",
            (product_id,),
        ).fetchone()
        changed = (
            last_price is None
            or last_price["price_c"] != item["price_c"]
            or last_price["was_price_c"] != item["was_price_c"]
            or bool(last_price["is_member_price"]) != bool(item["is_member_price"])
        )
        if changed:
            conn.execute(
                """INSERT OR IGNORE INTO price_events
                   (product_id, date, price_c, was_price_c, is_member_price, store_id, source)
                   VALUES (?, ?, ?, ?, ?, '', ?)""",
                (
                    product_id,
                    date,
                    item["price_c"],
                    item["was_price_c"],
                    int(item["is_member_price"]),
                    source,
                ),
            )
            stats["price_events"] += 1
    conn.commit()
    return stats


def record_run(
    conn: sqlite3.Connection,
    chain: str,
    date: str,
    started_at: str,
    finished_at: str,
    count: int,
    status: str,
    raw_path: str,
):
    conn.execute(
        """INSERT INTO scrape_runs
           (chain, date, started_at, finished_at, product_count, status, raw_path)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (chain, date) DO UPDATE SET
             finished_at = excluded.finished_at,
             product_count = excluded.product_count,
             status = excluded.status,
             raw_path = excluded.raw_path""",
        (chain, date, started_at, finished_at, count, status, raw_path),
    )
    conn.commit()
