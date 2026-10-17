"""Download each registry hospital's file into data/raw/ and record what happened.

For every hospital, one fetch_attempt row records the outcome:
  new        bytes we have never seen from this hospital -> stored + source_file row
  unchanged  same sha256 as a file we already hold       -> nothing else written
  failed     HTTP error, timeout, or not a data file     -> error recorded, run continues
  blocked    robots.txt disallows the URL                -> never requested

Files are streamed to disk in 1 MB chunks and hashed as they arrive, so a 4 GB file uses
the same memory as a 4 KB one.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import Connection, Engine, insert, select, update

from opencharge import PROJECT_ROOT
from opencharge.ingest.polite import PoliteClient
from opencharge.models import (
    Compression,
    FetchOutcome,
    RunStatus,
    fetch_attempt,
    hospital,
    ingest_run,
    source_file,
)
from opencharge.registry import FileFormat, HospitalEntry, Registry

log = logging.getLogger(__name__)

DEFAULT_RAW_DIR = PROJECT_ROOT / "data" / "raw"
CHUNK_BYTES = 1024 * 1024
SNIFF_BYTES = 512
FORMAT_EXTENSIONS = {
    FileFormat.JSON: ".json",
    FileFormat.CSV_TALL: ".csv",
    FileFormat.CSV_WIDE: ".csv",
}


class NotADataFile(Exception):
    """The server answered 200 but sent something that cannot be a price file."""


@dataclass
class Download:
    sha256: str
    byte_size: int
    path: Path
    compression: Compression
    http_status: int


@dataclass
class HospitalResult:
    hospital_id: str
    outcome: FetchOutcome
    http_status: int | None = None
    error: str | None = None
    byte_size: int | None = None


@dataclass
class RunSummary:
    run_id: str
    status: RunStatus
    results: list[HospitalResult] = field(default_factory=list)

    def count(self, outcome: FetchOutcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome)


def sniff_compression(head: bytes) -> Compression:
    if head.startswith(b"\x1f\x8b"):
        return Compression.GZIP
    if head.startswith(b"PK\x03\x04"):
        return Compression.ZIP
    return Compression.NONE


def _check_looks_like_data(head: bytes, content_type: str) -> None:
    # Hospitals' CDNs often answer a moved file with a 200 HTML page instead of a 404.
    if not head:
        raise NotADataFile("empty response body")
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if "text/html" in content_type.lower() or stripped.startswith((b"<!doctype html", b"<html")):
        raise NotADataFile(f"got an HTML page, not a data file (Content-Type: {content_type!r})")


def download(polite: PoliteClient, url: str, dest_dir: Path, file_format: FileFormat) -> Download:
    """Stream url to dest_dir/<sha256><ext>. Raises httpx.HTTPError or NotADataFile."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    partial = dest_dir / f".partial-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with polite.stream(url) as response:
            response.raise_for_status()
            status = response.status_code
            content_type = response.headers.get("Content-Type", "")
            with partial.open("wb") as out:
                # iter_bytes undoes Content-Encoding: gzip, so the hash is of the file itself,
                # not of however the server chose to compress it in transit.
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    if len(head) < SNIFF_BYTES:
                        head += chunk[: SNIFF_BYTES - len(head)]
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
        _check_looks_like_data(head, content_type)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    compression = sniff_compression(head)
    extension = {Compression.GZIP: ".gz", Compression.ZIP: ".zip"}.get(
        compression, FORMAT_EXTENSIONS[file_format]
    )
    sha = digest.hexdigest()
    final = dest_dir / f"{sha}{extension}"
    if final.exists():
        # Content-addressed: an identical file is already on disk.
        partial.unlink()
    else:
        partial.replace(final)
    return Download(sha, size, final, compression, status)


def sync_hospitals(conn: Connection, registry: Registry) -> None:
    """Make the hospital table match the registry (insert new, update changed)."""
    existing = set(conn.scalars(select(hospital.c.id)))
    for h in registry.hospitals:
        row = {
            "name": h.name,
            "address": h.address.street,
            "city": h.address.city,
            "state": h.address.state,
            "zip": h.address.zip,
            "lat": h.lat,
            "lon": h.lon,
            "ccn": h.ccn,
            "type": h.type,
            "in_service_area": h.in_service_area,
        }
        if h.id in existing:
            conn.execute(update(hospital).where(hospital.c.id == h.id).values(**row))
        else:
            conn.execute(insert(hospital).values(id=h.id, **row))


def fetch_one(
    conn: Connection, polite: PoliteClient, entry: HospitalEntry, run_id: str, raw_dir: Path
) -> HospitalResult:
    url = str(entry.mrf_url)
    result = HospitalResult(entry.id, FetchOutcome.FAILED)
    source_file_id: str | None = None

    if not polite.allowed(url):
        result.outcome = FetchOutcome.BLOCKED
        result.error = "disallowed by robots.txt"
    else:
        try:
            dl = download(polite, url, raw_dir / entry.id, entry.format)
        except httpx.HTTPStatusError as error:
            result.http_status = error.response.status_code
            result.error = f"HTTP {error.response.status_code}"
        except (httpx.HTTPError, NotADataFile) as error:
            result.error = f"{type(error).__name__}: {error}"
        else:
            result.http_status = dl.http_status
            result.byte_size = dl.byte_size
            source_file_id = conn.scalar(
                select(source_file.c.id).where(
                    source_file.c.hospital_id == entry.id, source_file.c.sha256 == dl.sha256
                )
            )
            if source_file_id is not None:
                result.outcome = FetchOutcome.UNCHANGED
            else:
                result.outcome = FetchOutcome.NEW
                source_file_id = str(uuid.uuid4())
                conn.execute(
                    insert(source_file).values(
                        id=source_file_id,
                        hospital_id=entry.id,
                        ingest_run_id=run_id,
                        url=url,
                        published_date=None,
                        fetched_at=datetime.now(UTC),
                        sha256=dl.sha256,
                        byte_size=dl.byte_size,
                        format=entry.format.value,
                        compression=dl.compression.value,
                        parser_name=entry.parser,
                        local_path=str(dl.path),
                        status="fetched",
                    )
                )

    conn.execute(
        insert(fetch_attempt).values(
            id=str(uuid.uuid4()),
            ingest_run_id=run_id,
            hospital_id=entry.id,
            url=url,
            attempted_at=datetime.now(UTC),
            outcome=result.outcome.value,
            http_status=result.http_status,
            error=result.error,
            source_file_id=source_file_id,
        )
    )
    return result


def run_fetch(
    engine: Engine,
    registry: Registry,
    polite: PoliteClient,
    hospital_ids: list[str] | None = None,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> RunSummary:
    """Fetch every selected hospital. One hospital failing never stops the others."""
    entries = registry.hospitals
    if hospital_ids is not None:
        unknown = set(hospital_ids) - {h.id for h in entries}
        if unknown:
            raise ValueError(f"not in registry: {sorted(unknown)}")
        entries = [h for h in entries if h.id in hospital_ids]

    run_id = str(uuid.uuid4())
    with engine.begin() as conn:
        sync_hospitals(conn, registry)
        conn.execute(
            insert(ingest_run).values(
                id=run_id, started_at=datetime.now(UTC), status=RunStatus.RUNNING.value
            )
        )

    summary = RunSummary(run_id, RunStatus.RUNNING)
    for entry in entries:
        log.info("fetching %s from %s", entry.id, entry.mrf_url)
        # One transaction per hospital, so a crash mid-run keeps the hospitals already done.
        with engine.begin() as conn:
            result = fetch_one(conn, polite, entry, run_id, raw_dir)
        log.info(
            "%s: %s%s", entry.id, result.outcome.value, f" ({result.error})" if result.error else ""
        )
        summary.results.append(result)

    problems = summary.count(FetchOutcome.FAILED) + summary.count(FetchOutcome.BLOCKED)
    summary.status = RunStatus.FAILED if problems else RunStatus.SUCCESS
    notes = ", ".join(f"{o.value}={summary.count(o)}" for o in FetchOutcome)
    with engine.begin() as conn:
        conn.execute(
            update(ingest_run)
            .where(ingest_run.c.id == run_id)
            .values(finished_at=datetime.now(UTC), status=summary.status.value, notes=notes)
        )
    return summary
