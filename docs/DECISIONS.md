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

---

## 2026-10-16 — Parsers stream with stdlib `csv` and `ijson`; Polars is for bulk work later

**What we found.** The brief names Polars for data processing. Polars cannot stream nested
JSON at all, so the JSON parser needs `ijson` regardless. For CSV, Polars' batched reader is
much faster but reports positions per batch, so the exact source row number (our provenance
promise) has to be rebuilt from batch offsets plus the two-row CMS metadata preamble.

**Decision.** Each parser is a generator yielding one `RawPriceLine` at a time: stdlib `csv`
for CSV tall/wide, `ijson` for JSON. Every record carries the row number it was read from,
taken directly from the reader. Polars comes in where it is genuinely strong: bulk
normalization, validation and loading of already-parsed records.

**Why.** Provenance has to be correct by construction, not by careful arithmetic. Flat memory
use for any file size. The expected cost was speed; measured, it is small: MedStar's 113 MB
wide CSV parses in 8.5 s and Novant's 284 MB JSON in 4.7 s (ijson's C backend).

**Revisit when.** Parse time dominates the weekly run (e.g. 50+ hospitals or multi-GB files
like Holy Cross's 3.7 GB CSV), at which point a Polars CSV path with a row-number test
against the stdlib parser's output becomes worth it.

---

## 2026-10-16 — Public GitHub repository

**Decision.** Publish as a public repo under `chaitanyanalam6-boop/opencharge`.

**Why.** It is a portfolio project; employers need to read it and see CI pass. Nothing
committed is secret: `.env` and `data/` are gitignored, and every hospital file is public.

**Revisit when.** Never for code. Any credential that lands in history must be rotated, not
just deleted.

---

## 2026-10-16 — Parser rules: what counts as a published price

**What we found.** Profiling the three real files and CMS's samples:
- Blank cells and "N/A"-style cells mean different things. Blank = the hospital wrote
  nothing; "N/A" = the hospital wrote that there is no value.
- Every CMS shape can repeat an item-level price (tall files copy gross/cash onto every
  payer row; CMS's own wide and JSON samples list an item once per payer-rate variant).
- Rows like "Bilateral procedure, modifier 50: 150%" are payment-adjustment rules, not
  prices. JSON keeps them in a separate `modifier_information` section; CSV mixes them in.
- A CSV row whose cell count differs from the header has its values in unknown columns.
- JSON has no rows. CMS's own tall sample contradicts itself once (two different `min`
  values for "Basic metabolic panel").

**Decision.**
- Blank cells produce no record. Anything written, including "N/A", produces a record so
  normalize can store it as "published but empty" (NULL rate, never 0).
- A repeated (item, price type, value) is emitted once, credited to the first row it
  appears on, so provenance always points at a real cell.
- Modifier-only rows are skipped in every format and counted as a quirk.
- Misaligned CSV rows become `RejectedRow` (bound for quarantine in Phase 3); no price is
  ever read from them.
- JSON provenance: `source_row_number` is the 1-based item index, plus an RFC 6901 pointer
  (`source_locator`) to the exact value. CSV provenance adds the column name as the locator.
  `source_locator` is an addition to the brief's §4 schema.
- Contradictions (like CMS's min=150 vs 125) are passed through faithfully; flagging them is
  validation's job, not the parser's.

**Why.** The parser's only job is to report what the file says, with an exact address for
every value. Every judgement call happens later, where it is named and tested.

**Revisit when.** We add a price search that needs modifier adjustments (e.g. bilateral
procedures), or when a hospital's file shows a repeated price that differs only in a field
we don't key on.

---

## 2026-10-16 — Normalization rules (codes, money, payers)

**What we found.**
- Novant's CPT codes lost their leading zeros in a spreadsheet: `123U` for `0123U`,
  `100` for anesthesia code `00100` (82,209 codes).
- Mercy writes revenue code `0` on 731 pharmacy rows to mean "none", and reuses one CDM code
  for hundreds of different drugs. Some of its NDCs are 9–10 digits with zeros lost.
- MedStar cash prices carry four decimals (`3793.7662`).
- Payer names vary by hospital: `AETNA [204]`, `Aetna US HealthCare Inc`, `Bcbs` with plan
  `Carefirst Medicare Managed Care Plan`.

**Decision.**
- CPT codes shorter than 5 characters are zero-padded and flagged `leading_zeros_restored`:
  CPT is always exactly 5 characters, so the restoration is deterministic. NDCs are *not*
  padded: a 10-digit NDC has three possible 11-digit forms, so padding could pick the wrong drug.
- All-zero codes are ignored and flagged, so a placeholder never becomes searchable.
- Money: Decimal → integer cents, rounded once, half-up, flagged `sub_cent_rounded`; the raw
  string is kept on every line. "N/A"-style values are NULL, never 0.
- Payers: regex aliases in `sources/payer_aliases.yaml`, tried on the payer name and then on
  payer + plan; insurers before government programs. Ambiguous names (a bare `Bcbs`,
  `AmeriHealth`) are deliberately left unmatched and written to
  `data/review/unmatched_payers.csv`.
- Normalization never drops a line; every oddity is a named issue for Phase 3 validation.

**Revisit when.** The review file shows a payer that recurs across many hospitals, or we
need NDC search (then NDCs need an authoritative lookup, not padding).

---

## 2026-10-16 — Finding: Maryland hospital prices are set by the state, not negotiated

**What we found.** Maryland's HSCRC sets hospital rates for all payers (the all-payer model,
in place since 1977). The real files match this exactly:
- MedStar Union Memorial publishes a "negotiated" dollar for 33 payer/plan blocks; every one
  equals the gross charge.
- MedStar's cash price is exactly 98% of gross: HSCRC's 2% prompt-pay discount.
- Johns Hopkins Howard County publishes insurer rates as 92.3% or 100% of charges: 92.3% is
  100% minus HSCRC's 7.7% public-payer differential (5.7% + 2.0%).
- Mercy publishes no payer-specific rates at all.

**What this means for the product.** In Maryland, the useful comparison is *between
hospitals*, not between insurers at one hospital. Our numbers are still correct: every price
is what the hospital published. But the UI's "insurer comparison" framing needs to explain
this rather than imply negotiation, and professional fees (GBMC's "PB" plans) are where
insurer-specific dollar differences actually appear. DC and Northern Virginia hospitals are
not rate-regulated, so their files should show real negotiated spreads.

**Revisit when.** Designing the results and provenance screens (Phase 6), and when choosing
the next hospitals to add.

---

## 2026-10-16 — Added GBMC (MD, tall) and GWU Hospital (DC, wide); Johns Hopkins not fetchable

**What we found.** Mercy, our only Maryland tall-format hospital, publishes no payer rates,
so payer handling was untested on Maryland tall data. Johns Hopkins Howard County was the
first choice, but hopkinsmedicine.org now answers 403 to every honestly identified client
(its robots.txt allows the path). Surveying other Maryland files: Frederick, Anne Arundel,
CalvertHealth and UMMC publish one "All Payers" figure or none; GBMC publishes real per-payer
dollar rates. For DC, MedStar Georgetown uses the same layout as MedStar Union Memorial;
George Washington University Hospital (UHS) is a different system with different quirks.

**Decision.** Add GBMC and GWU Hospital. Do not fetch Johns Hopkins: we will not disguise
our User-Agent to get past a block (brief §7.6).

**Revisit when.** Hopkins lifts the block, or publishes a contact for data access.

---

## 2026-10-16 — Only MS-DRG counts as DRG; placeholders and "all payers" handled explicitly

**What we found.**
- GWU publishes APR-DRG codes (`589-4`: DRG 589 at severity 4). APR-DRG and MS-DRG reuse
  the same numbers for different conditions.
- More placeholder codes: GBMC CPT `N/A` (on items whose only code column is empty — about
  half of GBMC's price lines), GWU HCPCS `-` and `XXXXX`.
- GBMC publishes facility rates under payer `ALL PAYERS`: in Maryland that is literally the
  rate every insurer pays.
- GWU writes payer names with underscores (`UNITED_HEALTH_CARE`) and with pipes inside them.
- Bug found: wide-CSV payer notes were never attached (the plan name at the end of
  `additional_payer_notes|<payer>|<plan>` was lowercased). Fixed with a regression test.

**Decision.**
- `code_type = DRG` means MS-DRG only. Other DRG families become LOCAL, flagged
  `unsearched_code_type:<family>`, so a DRG search can never return the wrong procedure.
- Placeholder codes (`0`, `N/A`, `-`, `XXXXX`, ...) are ignored and flagged. Items left with no
  code are kept as lines; Phase 3's `code_present` rule decides their fate.
- New canonical payer `all-payers` for rates published once for every insurer.
- Payer text: underscores become spaces before matching.

**Revisit when.** Users need APR-DRG search (then it gets its own code type, not a merge).
