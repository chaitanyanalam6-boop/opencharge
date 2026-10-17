# OpenCharge

Search real hospital prices in Maryland and the DC area, read directly from the
machine-readable files hospitals must publish under federal price transparency rules.

> No price is ever shown without a visible path back to the hospital's own source row.
> If we don't have the data, we say so. We never estimate.

*Built from public CMS price transparency files; not medical or billing advice.*

**Status:** Phase 3 — validation, quarantine, loading and schema drift detection.

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
uv sync                 # create .venv with Python 3.12 and install dependencies
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run black --check .  # formatting
uv run mypy             # type check
```

### Ingest: fetch, parse, validate, load

```bash
cp .env.example .env    # then set OPENCHARGE_CONTACT_EMAIL; it goes in our User-Agent
uv run --env-file .env python -m opencharge.ingest --all
uv run --env-file .env python -m opencharge.ingest --hospital mercy-medical-center
```

One run downloads each hospital's file to `data/raw/<hospital-id>/<sha256>.<ext>`, then
parses, normalizes and validates every price. Valid prices load into `price_line`; anything
that fails a named rule goes to `quarantined_row` with its raw payload. The database is a
local DuckDB file, `data/opencharge.duckdb`, created and upgraded by Alembic migrations.
Running it again on unchanged files is a no-op.

```bash
uv run python -m opencharge.load --all --reprocess        # reload after changing a rule
uv run python -m opencharge.load --accept-drift <file_id> # accept reviewed column changes
```

### Inspect normalization without loading

```bash
uv run python -m opencharge.normalize --all
```

Reports, per hospital, price lines by type, NULL rates and why, the share of negotiated rates
matched to a canonical payer, and every normalization issue. Unmatched payer names go to
`data/review/unmatched_payers.csv`.

```bash
uv run pytest -m integration   # parse the real files in data/raw/ and trace prices to source
```

### Add a hospital

```bash
uv run --env-file .env python -m opencharge.ingest.discover umms.org
```

Prints a draft `hospitals.yaml` entry for each facility in the system's `cms-hpt.txt`. Fill in
the fields it leaves empty (CCN, address, coordinates, CSV shape, parser) from primary sources;
the registry refuses to load until you do.

## Layout

| Path | What lives there |
|---|---|
| `sources/hospitals.yaml` | The hospital registry: file URLs, format, parser, service area |
| `src/opencharge/` | Pipeline: `ingest` → `parse` → `normalize` → `validate` → `load`, then `query` and `api` |
| `tests/fixtures/cms/` | CMS's official v3 sample files, used by fast parser tests |
| `docs/DECISIONS.md` | Every deliberate trade-off, with what would make us revisit it |
| `scripts/peek.py` | One-off research script that sampled the first 2 MB of candidate files |
