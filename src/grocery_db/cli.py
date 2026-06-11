"""Command line interface.

  gdb scrape <chain|all> [--quick]     fetch catalogue, save raw dump
  gdb ingest <chain|all> [--date D]    raw dump -> change events
  gdb run-daily                        scrape+ingest all chains, R2 sync (CI entry point)
  gdb backfill [coles] [woolies]       import hotprices.org history
  gdb match                            rebuild automatic identical matches
  gdb pull / gdb push                  sync database with R2
  gdb stats                            quick health overview
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import backfill as backfill_mod
from . import db, ingest, r2
from .scrapers import SCRAPERS, common


def _chains(arg: str) -> list[str]:
    return list(SCRAPERS) if arg == "all" else [arg]


# One chain may not eat the whole CI job; the others still need their day.
CHAIN_TIME_BUDGET_S = 45 * 60


def cmd_scrape(args) -> int:
    failed = []
    for chain in _chains(args.chain):
        date = common.today()
        started = datetime.now().isoformat(timespec="seconds")
        try:
            common.set_deadline(CHAIN_TIME_BUDGET_S)
            raw = SCRAPERS[chain].scrape(quick=args.quick)
            path = common.save_raw(chain, date, raw)
            count = len(SCRAPERS[chain].normalize(raw))
            conn = db.connect(args.db)
            ingest.record_run(
                conn, chain, date, started,
                datetime.now().isoformat(timespec="seconds"),
                count, "scraped", str(path),
            )
            conn.close()
            print(f"{chain}: scraped {count} products -> {path}")
        except Exception as err:
            print(f"{chain}: SCRAPE FAILED: {err}", file=sys.stderr)
            failed.append(chain)
        finally:
            common.set_deadline(None)
    return 1 if failed else 0


def cmd_ingest(args) -> int:
    conn = db.connect(args.db)
    failed = []
    for chain in _chains(args.chain):
        date = args.date or common.today()
        try:
            raw = common.load_raw(chain, date)
            items = SCRAPERS[chain].normalize(raw)
            if not args.force:
                ingest.check_staleness(conn, chain, date, len(items))
            stats = ingest.ingest_items(conn, chain, date, items)
            run = conn.execute(
                "SELECT started_at, raw_path FROM scrape_runs WHERE chain=? AND date=?",
                (chain, date),
            ).fetchone()
            ingest.record_run(
                conn, chain, date,
                run["started_at"] if run else datetime.now().isoformat(timespec="seconds"),
                datetime.now().isoformat(timespec="seconds"),
                stats["items"], "ingested",
                run["raw_path"] if run else str(common.raw_path(chain, date)),
            )
            print(f"{chain} {date}: {stats}")
        except FileNotFoundError:
            print(f"{chain}: no raw dump for {date}, skipping", file=sys.stderr)
            failed.append(chain)
        except ingest.StalenessError as err:
            print(f"{chain}: {err}", file=sys.stderr)
            failed.append(chain)
    conn.close()
    return 1 if failed else 0


def cmd_run_daily(args) -> int:
    """CI entry point: pull DB, scrape+ingest every chain, push everything.

    A single chain failing doesn't stop the others — its history just has a
    gap, while the rest keep collecting. Exit code reflects any failure.
    """
    date = common.today()
    r2.pull_db(Path(args.db))
    failed = []
    for chain in SCRAPERS:
        scrape_rc = cmd_scrape(
            argparse.Namespace(chain=chain, quick=False, db=args.db)
        )
        if scrape_rc != 0:
            if not _hotprices_fallback(chain, args.db):
                failed.append(chain)
            continue
        ingest_rc = cmd_ingest(
            argparse.Namespace(chain=chain, date=date, force=False, db=args.db)
        )
        if ingest_rc != 0:
            failed.append(chain)
            continue
        r2.push_raw(chain, date)
    r2.push_db(Path(args.db))
    if failed:
        print(f"FAILED chains: {failed}", file=sys.stderr)
        return 1
    return 0


def _hotprices_fallback(chain: str, db_path: str) -> bool:
    """When a coles/woolies scrape fails (e.g. bot wall on runner IPs),
    import hotprices.org's latest published data so the chain stays gapless.
    Returns True when the day is covered."""
    if chain not in backfill_mod.URLS:
        return False
    print(f"{chain}: falling back to hotprices.org data")
    try:
        path = backfill_mod.download(chain)
        conn = db.connect(db_path)
        stats = backfill_mod.import_file(conn, chain, path)
        conn.close()
        print(f"{chain}: fallback import: {stats}")
        return True
    except Exception as err:
        print(f"{chain}: FALLBACK FAILED: {err}", file=sys.stderr)
        return False


def cmd_push_raw(args) -> int:
    r2.push_raw(args.chain, args.date or common.today())
    return 0


def cmd_daily_ingest(args) -> int:
    """CI ingest job: runs after the parallel per-chain scrape jobs.

    Pulls the DB and each chain's raw dump from R2; chains whose scrape job
    failed (no dump) are covered by the hotprices fallback where possible.
    Single writer, so no SQLite contention with the scrape jobs.
    """
    date = common.today()
    r2.pull_db(Path(args.db))
    failed = []
    for chain in SCRAPERS:
        if not common.raw_path(chain, date).exists() and not r2.pull_raw(chain, date):
            print(f"{chain}: no raw dump on R2 for {date}")
            if not _hotprices_fallback(chain, args.db):
                failed.append(chain)
            continue
        ingest_rc = cmd_ingest(
            argparse.Namespace(chain=chain, date=date, force=False, db=args.db)
        )
        if ingest_rc != 0:
            failed.append(chain)
    r2.push_db(Path(args.db))
    if failed:
        print(f"FAILED chains: {failed}", file=sys.stderr)
        return 1
    return 0


def cmd_logs(args) -> int:
    log = r2.get_log(args.chain, args.date or common.today())
    if log is None:
        print(f"no log for {args.chain} on {args.date or common.today()}", file=sys.stderr)
        return 1
    print(log)
    return 0


def cmd_backfill(args) -> int:
    conn = db.connect(args.db)
    for chain in args.chains:
        path = Path(args.path) if args.path else backfill_mod.download(chain)
        stats = backfill_mod.import_file(conn, chain, path)
        print(f"{chain}: {stats}")
    conn.close()
    return 0


def cmd_match(args) -> int:
    from .matching import identical

    conn = db.connect(args.db)
    stats = identical.rebuild_auto_matches(conn)
    print(f"identical auto-matching: {stats}")
    conn.close()
    return 0


def cmd_pull(args) -> int:
    r2.pull_db(Path(args.db))
    return 0


def cmd_push(args) -> int:
    r2.push_db(Path(args.db))
    return 0


def cmd_stats(args) -> int:
    conn = db.connect(args.db)
    print("products by chain/source:")
    for row in conn.execute(
        """SELECT chain, source, COUNT(*) AS n, MAX(last_seen) AS latest
           FROM products GROUP BY chain, source"""
    ):
        print(f"  {row['chain']:8} {row['source']:20} {row['n']:>7}  last_seen={row['latest']}")
    print("price events by source:")
    for row in conn.execute(
        "SELECT source, COUNT(*) AS n, MIN(date) AS oldest, MAX(date) AS newest FROM price_events GROUP BY source"
    ):
        print(f"  {row['source']:20} {row['n']:>9}  {row['oldest']} .. {row['newest']}")
    print("recent runs:")
    for row in conn.execute(
        "SELECT chain, date, product_count, status FROM scrape_runs ORDER BY date DESC, chain LIMIT 9"
    ):
        print(f"  {row['date']} {row['chain']:8} {row['product_count']:>7} {row['status']}")
    conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gdb", description=__doc__)
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="database path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scrape", help="fetch catalogue(s), save raw dump")
    p.add_argument("chain", choices=[*SCRAPERS, "all"])
    p.add_argument("--quick", action="store_true", help="single page, for testing")
    p.set_defaults(func=cmd_scrape)

    p = sub.add_parser("ingest", help="raw dump -> change events")
    p.add_argument("chain", choices=[*SCRAPERS, "all"])
    p.add_argument("--date", help="dump date (default: today AEST)")
    p.add_argument("--force", action="store_true", help="skip staleness guard")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("run-daily", help="full daily cycle with R2 sync (single machine)")
    p.set_defaults(func=cmd_run_daily)

    p = sub.add_parser("push-raw", help="upload a raw dump to R2 (CI scrape job)")
    p.add_argument("chain", choices=list(SCRAPERS))
    p.add_argument("--date", help="dump date (default: today AEST)")
    p.set_defaults(func=cmd_push_raw)

    p = sub.add_parser("daily-ingest", help="ingest all chains from R2 dumps (CI ingest job)")
    p.set_defaults(func=cmd_daily_ingest)

    p = sub.add_parser("logs", help="show a CI scrape job's live log from R2")
    p.add_argument("chain", choices=list(SCRAPERS))
    p.add_argument("--date", help="log date (default: today AEST)")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("backfill", help="import hotprices.org history")
    p.add_argument("chains", nargs="*", default=["coles", "woolies"],
                   choices=[[], "coles", "woolies"])
    p.add_argument("--path", help="local file instead of downloading")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("match", help="rebuild automatic identical matches")
    p.set_defaults(func=cmd_match)

    p = sub.add_parser("pull", help="download database from R2")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("push", help="upload database to R2")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("stats", help="database health overview")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if args.command == "backfill" and not args.chains:
        args.chains = ["coles", "woolies"]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
