# Decisions log

Calls we made on purpose, with what we knew at the time. Newest at the bottom. Each entry
says what would make us revisit it, so a later reader can tell a deliberate trade-off from
an accident.

---

## 2026-10-16 — JSON coverage comes from an out-of-area hospital, flagged and excluded from search

**What we found.** The brief needs one hospital in each CMS template shape (CSV wide, CSV
tall, JSON). We read the `/cms-hpt.txt` index of roughly 60 health-system domains across
Maryland, DC and Northern Virginia, plus web searches for the remainder. Every machine-readable
file we could reach in that area is CSV. Several systems (Johns Hopkins, LifeBridge, Adventist,
Children's National, Howard University) blocked or failed the index fetch; the files they link
from their price pages are also CSV. The only nearby system publishing JSON is Novant Health,
and all of its hospitals are in North and South Carolina.

**Decision.**
- Add Novant Health Charlotte Orthopedic Hospital (NC, ~284 MB JSON) to the registry with
  `in_service_area: false`. It is ingested and parsed like any other file but never appears
  in search results.
- `in_service_area` is a real column and a real filter: every user-facing query starts from
  `query/scope.py::searchable_hospitals()`, and `tests/test_scope.py` proves an out-of-area
  hospital never comes back. Default is `true`.
- CMS's official v3 sample files (JSON, CSV tall, CSV wide), pinned to commit `5333564a` of
  github.com/CMSgov/hospital-price-transparency, live in `tests/fixtures/cms/` for fast unit
  tests that run on every push. The Novant file is the integration test: real, large, messy.

**Why.** A parser only tested on a 26 KB official sample has never met real data: the Novant
file shows percentage-only rates, payer naming quirks and a size that forces streaming.
Flagging it rather than calling it a Maryland price keeps the product promise intact.
Keeping the CMS sample too means CI stays fast and does not depend on a third-party server.

**Revisit when.** Any MD/DC/NoVA hospital starts publishing JSON: swap it in and retire the
Novant entry. Also revisit if Novant's file stops being reachable, or if we expand the
service area to the Carolinas.

---

## 2026-10-16 — Python 3.12, managed by uv

**What we found.** The local venv was on Python 3.14 with nothing installed. The brief and
the Railway deployment target 3.12.

**Decision.** Pin `requires-python = ">=3.12,<3.13"` and manage the interpreter, venv and
lockfile with `uv`. CI uses the same `uv.lock`.

**Why.** Same interpreter in dev, CI and production. A newer local Python than production
lets 3.13+ syntax or wheels pass locally and fail on deploy.

**Revisit when.** Railway and every dependency (DuckDB, Polars, Groq SDK) ship stable wheels
for a newer Python and we have a reason to want its features.

---

## 2026-10-16 — Project lives outside iCloud-synced folders (`~/code/opencharge`)

**What we found.** The repo started in `~/Desktop`, which iCloud "Desktop & Documents" syncs.
macOS set the `hidden` flag on the editable-install `.pth` file in `.venv`, and current
Python 3.12 deliberately skips hidden `.pth` files, so `import opencharge` broke with no code
change. Ingest will also put multi-GB files in `data/raw/`, which iCloud would try to upload.

**Decision.** Move the repo to `~/code/opencharge` and rebuild `.venv` there. Keep
`pythonpath = ["src"]` in the pytest config as a second line of defence.

**Why.** Build tooling and large data files should not sit in a folder a sync service
rewrites. Moving it fixes the cause instead of repeatedly clearing the flag.

**Revisit when.** Never for local dev; deployment runs on Railway, not this machine.

---

## 2026-10-16 — Tables via `create_all` until Phase 3; Alembic arrives with Postgres

**What we found.** Phase 1 only needs a local DuckDB file. Alembic reaches DuckDB through the
third-party `duckdb-engine` dialect, whose `ALTER TABLE` support is limited, and there is no
deployed database yet whose data a migration would need to protect.

**Decision.** `metadata.create_all()` creates tables in Phase 1–2. Alembic is introduced in
Phase 3, alongside `price_line` and the Postgres target, with the then-current schema as its
baseline migration.

**Why.** Migrations protect data that already exists somewhere you can't just rebuild. Until
Phase 3 every local database is disposable (`rm data/opencharge.duckdb` and re-ingest).

**Revisit when.** Phase 3 starts, or earlier if a local DuckDB file becomes expensive to rebuild.

---

## 2026-10-16 — Per-hospital fetch outcomes get their own `fetch_attempt` table

**What we found.** Brief §4 has `source_file` (files we hold) and `ingest_run` (one row per
run), but nowhere to record that one hospital returned 404, was unchanged, or was disallowed by
robots.txt. Screen 4 needs exactly that per-hospital status.

**Decision.** Add `fetch_attempt`: one row per hospital per run with `outcome`
(`new` | `unchanged` | `failed` | `blocked`), HTTP status, error text, and a link to the
`source_file` row when a new file arrived. `source_file` only ever holds files we downloaded.

**Why.** Keeps `source_file` meaning one thing (every row has a real checksum), keeps
"unchanged" history instead of discarding it, and makes "last outcome per hospital" a single
query.

**Revisit when.** Fetching moves to a job queue with its own attempt history.
