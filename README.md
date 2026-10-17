# OpenCharge

Search real hospital prices in Maryland and the DC area, read directly from the
machine-readable files hospitals must publish under federal price transparency rules.

> No price is ever shown without a visible path back to the hospital's own source row.
> If we don't have the data, we say so. We never estimate.

*Built from public CMS price transparency files; not medical or billing advice.*

**Status:** Phase 1 in progress — repository skeleton and source registry.

## Run it locally

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you).

```bash
uv sync                 # create .venv with Python 3.12 and install dependencies
uv run pytest           # run the test suite
uv run ruff check .     # lint
uv run black --check .  # formatting
uv run mypy             # type check
```

## Layout

| Path | What lives there |
|---|---|
| `sources/hospitals.yaml` | The hospital registry: file URLs, format, parser, service area |
| `src/opencharge/` | Pipeline: `ingest` → `parse` → `normalize` → `validate` → `load`, then `query` and `api` |
| `tests/fixtures/cms/` | CMS's official v3 sample files, used by fast parser tests |
| `docs/DECISIONS.md` | Every deliberate trade-off, with what would make us revisit it |
| `scripts/peek.py` | One-off research script that sampled the first 2 MB of candidate files |
