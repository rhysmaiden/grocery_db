import pytest

from grocery_db import db, ingest


def make_item(**overrides):
    item = {
        "sku": "123",
        "name": "Test Milk 2L",
        "brand": "Farmhouse",
        "description": "milk",
        "price_c": 310,
        "was_price_c": None,
        "is_member_price": False,
        "quantity": 2000.0,
        "unit": "ml",
        "is_weighted": False,
        "category": "Dairy",
        "url": "https://example.com/123",
        "image_url": "https://example.com/123.jpg",
    }
    item.update(overrides)
    return item


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    yield conn
    conn.close()


def test_first_ingest_creates_product_and_event(conn):
    stats = ingest.ingest_items(conn, "coles", "2026-06-01", [make_item()])
    assert stats == {"products_new": 1, "price_events": 1, "attribute_events": 0, "items": 1}


def test_same_price_no_new_event(conn):
    ingest.ingest_items(conn, "coles", "2026-06-01", [make_item()])
    stats = ingest.ingest_items(conn, "coles", "2026-06-02", [make_item()])
    assert stats["price_events"] == 0
    assert stats["products_new"] == 0
    # last_seen advanced even though no event written
    row = conn.execute("SELECT last_seen FROM products").fetchone()
    assert row["last_seen"] == "2026-06-02"


def test_price_change_creates_event(conn):
    ingest.ingest_items(conn, "coles", "2026-06-01", [make_item()])
    stats = ingest.ingest_items(conn, "coles", "2026-06-02", [make_item(price_c=250)])
    assert stats["price_events"] == 1
    events = conn.execute("SELECT date, price_c FROM price_events ORDER BY date").fetchall()
    assert [(e["date"], e["price_c"]) for e in events] == [
        ("2026-06-01", 310),
        ("2026-06-02", 250),
    ]


def test_shrinkflation_creates_attribute_event(conn):
    ingest.ingest_items(conn, "coles", "2026-06-01", [make_item()])
    stats = ingest.ingest_items(
        conn, "coles", "2026-06-02", [make_item(quantity=1800.0)]
    )
    assert stats["attribute_events"] == 1
    ev = conn.execute("SELECT * FROM attribute_events").fetchone()
    assert ev["field"] == "quantity"
    assert ev["old_value"] == "2000.0"
    assert ev["new_value"] == "1800.0"


def test_reingest_same_day_idempotent(conn):
    ingest.ingest_items(conn, "coles", "2026-06-01", [make_item()])
    ingest.ingest_items(conn, "coles", "2026-06-01", [make_item()])
    assert conn.execute("SELECT COUNT(*) AS n FROM price_events").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"] == 1


def test_duplicate_sku_in_snapshot_ignored(conn):
    items = [make_item(category="Dairy"), make_item(category="Specials")]
    stats = ingest.ingest_items(conn, "coles", "2026-06-01", items)
    assert stats["items"] == 1


def test_staleness_guard(conn):
    ingest.ingest_items(conn, "coles", "2026-06-01", [make_item(sku=str(i)) for i in range(10)])
    ingest.record_run(conn, "coles", "2026-06-01", "t", "t", 10, "ingested", "x")
    with pytest.raises(ingest.StalenessError):
        ingest.check_staleness(conn, "coles", "2026-06-02", 4)
    # 50%+ is fine
    ingest.check_staleness(conn, "coles", "2026-06-02", 5)


def test_first_scrape_of_backfilled_product_rebaselines_silently(conn):
    ingest.ingest_items(
        conn, "coles", "2026-06-01", [make_item(name="Hotprices Name")],
        source="hotprices_backfill",
    )
    stats = ingest.ingest_items(
        conn, "coles", "2026-06-02", [make_item(name="Scraper Name")]
    )
    assert stats["attribute_events"] == 0
    row = conn.execute("SELECT name, source FROM products").fetchone()
    assert row["name"] == "Scraper Name"
    assert row["source"] == "scrape"
    # subsequent scrape-vs-scrape changes are real events again
    stats = ingest.ingest_items(
        conn, "coles", "2026-06-03", [make_item(name="Renamed Product")]
    )
    assert stats["attribute_events"] == 1
