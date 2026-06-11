# grocery-db

Australian grocery price history: **Woolworths, Coles, Aldi**. Daily scrapes
into a SQLite change-event store, synced to Cloudflare R2, browsed with a
local Streamlit dashboard. Cross-chain product matching for "is Aldi actually
cheaper?" questions.

Coles/Woolworths fetch logic ported from
[hotprices-au](https://github.com/Javex/hotprices-au) (MIT, © Mario Zechner /
Javex), which also provides ~2.7 years of backfill history. Aldi scraping is
original (their API launched with online shopping; no public history exists).

## Design

- **Change-event storage**: a `price_events` row only when a price changes;
  `products.first_seen/last_seen` track observation coverage. `attribute_events`
  records name/size changes (shrinkflation). Money is integer cents.
- **Raw dumps**: full gzipped API responses per chain per day, pushed to R2 —
  the DB can always be re-derived.
- **National/default pricing**; `store_id` column reserved for later.
- **Matching**: `match_groups`/`match_members` with `match_type`
  (identical/equivalent), `method` (auto/llm/manual), `confidence`. Decoupled
  from collection; rebuildable anytime.

## Setup

```bash
uv sync                                  # core (scrape/ingest)
uv sync --extra dev                      # everything (dashboard deps are core)
uv run pytest tests/                     # sanity check
```

### One-off: backfill + first scrape

```bash
uv run gdb backfill                  # ~2.7y of Coles+Woolies from hotprices.org
uv run gdb scrape all                # first full scrape (~20-40 min)
uv run gdb ingest all
uv run gdb match                     # build identical cross-chain matches
uv run gdb stats
```

### Daily automation (GitHub Actions + R2)

1. Create an R2 bucket and an API token (Object Read & Write) in Cloudflare.
2. Set repo secrets: `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`,
   `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
3. Seed the remote DB once: `uv run gdb push` (after backfill + first ingest).
4. The `Daily scrape` workflow (1am UTC) then runs `gdb run-daily`:
   pull DB → scrape+ingest each chain (staleness guard: fails if a chain
   returns <50% of its previous catalogue) → push raw dumps + DB.

Note: Coles/Woolies tolerate GitHub runner IPs (proven daily by hotprices-au);
Aldi's Akamai is untested from runners — watch the first scheduled run. If it
fails there, options are a residential proxy for the Aldi leg or running it
locally. All scrapers use `curl_cffi` Chrome TLS impersonation; plain
requests are blocked by Aldi.

### Dashboard

```bash
uv run gdb pull        # fresh DB from R2
uv run streamlit run dashboard/app.py
```

Tabs: product search + price history (with shrinkflation markers),
cross-chain comparison via match groups, data health.

## CLI

```
gdb scrape <chain|all> [--quick]   fetch catalogue -> data/raw/<chain>/<date>.json.gz
gdb ingest <chain|all> [--date D] [--force]
gdb run-daily                      CI entry point (R2 pull/push around scrape+ingest)
gdb backfill [coles|woolies ...] [--path FILE]
gdb match                          rebuild auto identical matches
gdb pull / gdb push                sync data/grocery.db with R2
gdb stats                          health overview
```

## Equivalence matching (Aldi vs the big two)

Aldi is ~90% own-brand, so identical matching barely touches it. Equivalent
products (Farmdale milk ≈ Coles milk) are added as `match_type='equivalent'`
groups with `method='llm'|'manual'` — curated staples first, full catalogue
later. Insert into `match_groups`/`match_members` directly or build tooling
under `src/grocery_db/matching/`.
