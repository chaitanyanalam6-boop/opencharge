from __future__ import annotations

import contextlib
import csv
import io
import ssl
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from typing import NamedTuple

try:
    import certifi
except ImportError:
    certifi = None


MAX_SAMPLE_BYTES = 2 * 1024 * 1024
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"


class Source(NamedTuple):
    sample_id: str
    url: str


SOURCES = (
    Source(
        "mercy_medical_center",
        "https://mdmercy.com/-/media/files/about-mercy/policies-and-documents/520591658_mercymedicalcenter_standardcharges.ashx",
    ),
    Source(
        "johns_hopkins_howard_county",
        "https://www.hopkinsmedicine.org/-/media/patient-care/documents/billing-insurance/charge-fees/522093120_johns-hopkins-howard-county-medical-center_standardcharges.csv",
    ),
    Source(
        "medstar_union_memorial",
        "https://www.medstarhealth.org/-/media/project/mho/medstar/billing-and-insurance/2026/520591685_medstar_union_memorial_hospital_standardcharges.csv",
    ),
    Source(
        "um_st_joseph_medical_center",
        "https://www.umms.org/standard-charges/352445106_UM-St.-Joseph-Medical-Center_standardcharges.csv",
    ),
    Source(
        "greater_baltimore_medical_center",
        "https://www.gbmc.org/sites/default/files/documents/52-6049658_gbmc_standardcharges.csv",
    ),
)

HEADER_MARKERS = {
    "code",
    "description",
    "gross_charge",
    "discounted_cash_price",
    "payer_name",
    "plan_name",
    "negotiated_rate",
    "standard_charge",
    "standard_charge|gross",
}


def read_sample(url: str) -> tuple[int | None, str | None, str | None, bytes, str | None]:
    """Return response metadata and at most MAX_SAMPLE_BYTES from the body."""
    request = urllib.request.Request(
        url,
        headers={
            "Accept-Encoding": "identity",
            "Range": f"bytes=0-{MAX_SAMPLE_BYTES - 1}",
            "User-Agent": "OpenCharge-sample-inspector/0.1",
        },
    )
    response = None
    try:
        context = ssl.create_default_context(
            cafile=certifi.where() if certifi is not None else None
        )
        response = urllib.request.urlopen(request, timeout=60, context=context)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return None, None, None, b"", str(error)

    try:
        status = response.status
        content_type = response.headers.get("Content-Type")
        content_length = response.headers.get("Content-Length")
        sample = response.read(MAX_SAMPLE_BYTES)
        encoding = response.headers.get("Content-Encoding", "").lower()
        if encoding == "gzip":
            with contextlib.suppress(zlib.error):
                sample = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(
                    sample, MAX_SAMPLE_BYTES
                )
        return status, content_type, content_length, sample, None
    finally:
        response.close()


def decode_sample(data: bytes, declared_type: str | None) -> tuple[str, str, str | None]:
    """Decode for inspection while preserving the original sample on disk."""
    try:
        return data.decode("utf-8-sig"), "UTF-8", None
    except UnicodeDecodeError as error:
        charset = "cp1252"
        if declared_type and "charset=" in declared_type.lower():
            charset = declared_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip()
        try:
            return (
                data.decode(charset),
                charset,
                f"not valid UTF-8 (first error byte {error.start})",
            )
        except (LookupError, UnicodeDecodeError):
            return (
                data.decode("utf-8", errors="replace"),
                "UTF-8 with replacement",
                (f"not valid UTF-8 (first error byte {error.start})"),
            )


def normalize_header(value: str) -> str:
    return "_".join(value.strip().lower().replace("\ufeff", "").split())


def find_header(text: str) -> tuple[list[str] | None, int | None, str, list[list[str]]]:
    sample = text[:MAX_SAMPLE_BYTES]
    try:
        dialect = csv.Sniffer().sniff(sample[:65536], delimiters=",\t;|")
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","

    rows: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(sample), delimiter=delimiter):
            rows.append(row)
            if len(rows) >= 100:
                break
    except csv.Error:
        pass

    header: list[str] | None = None
    header_index: int | None = None
    best_score = 0
    for index, row in enumerate(rows):
        normalized = {normalize_header(cell) for cell in row}
        score = len(normalized & HEADER_MARKERS)
        has_code_and_description = "code" in normalized and "description" in normalized
        if has_code_and_description:
            score += 5
        if score > best_score:
            header, header_index, best_score = row, index, score

    return header, header_index, delimiter, rows


def classify_layout(header: list[str] | None) -> str:
    if not header:
        return "unknown (no recognizable CSV header)"
    columns = [normalize_header(column) for column in header]
    payer_plan_groups = {
        tuple(parts[1:-1])
        for column in columns
        for parts in [column.split("|")]
        if len(parts) >= 4
        and parts[0] in {"standard_charge", "median_amount", "10th_percentile", "90th_percentile"}
        and parts[-1] in {"negotiated_dollar", "negotiated_percentage", "negotiated_algorithm"}
    }
    payer_columns = [
        column
        for column in columns
        if any(word in column for word in ("payer", "plan", "insurance"))
    ]
    separate_payer_fields = any("payer" in col for col in columns) and any(
        "plan" in col for col in columns
    )
    rate_columns = [
        column for column in columns if any(word in column for word in ("rate", "price", "charge"))
    ]
    payer_rate_columns = [
        column
        for column in rate_columns
        if any(word in column for word in ("payer", "plan", "insurance"))
    ]
    if payer_plan_groups:
        if len(payer_plan_groups) == 1 and "all_payers" in payer_plan_groups.pop()[0]:
            return "wide-like (aggregate all-payers group; not payer-specific)"
        return "wide (payer/plan-specific columns)"
    if len(payer_columns) > 2 or len(payer_rate_columns) > 1:
        return "wide (payer/plan-specific columns)"
    if separate_payer_fields and rate_columns:
        return "tall (payer/plan and rate represented as fields)"
    if payer_columns and rate_columns:
        return "likely tall (payer field plus rate field)"
    return "unclear from header"


def inspect_source(source: Source) -> tuple[str, int | None, str]:
    status, content_type, content_length, data, error = read_sample(source.url)
    sample_path = SAMPLES_DIR / f"{source.sample_id}.txt"
    sample_path.write_bytes(data)

    print(f"\n### {source.sample_id}")
    print(f"URL: {source.url}")
    if error:
        print(f"Request error: {error}")
        return source.sample_id, status, "request error"

    print(f"HTTP status: {status}")
    print(f"Content-Type: {content_type or '(not supplied)'}")
    print(f"Content-Length: {content_length or '(not supplied)'}")
    print(f"Bytes saved: {len(data)} (maximum {MAX_SAMPLE_BYTES})")
    print(f"Sample: {sample_path.relative_to(SAMPLES_DIR.parents[1])}")

    text, encoding, encoding_note = decode_sample(data, content_type)
    print(f"Decoded as: {encoding}")
    if encoding_note:
        print(f"Encoding note: {encoding_note}")

    print("First 3 lines (raw text):")
    lines = text.splitlines()
    for line_number in range(3):
        line = lines[line_number] if line_number < len(lines) else "<no line>"
        print(f"  {line_number + 1}: {line!r}")

    header, header_index, delimiter, rows = find_header(text)
    if header is None:
        print("CSV header: not identified")
        print("Column count: unknown")
        print("Columns: []")
        layout = "unknown (no recognizable CSV header)"
    else:
        print(f"CSV delimiter: {delimiter!r}")
        if header_index:
            print(f"Metadata/preamble rows before header: {header_index}")
        else:
            print("Metadata/preamble rows before header: none detected")
        print(f"Header row sample index: {header_index}")
        print(f"Column count: {len(header)}")
        print(f"Columns: {header!r}")
        layout = classify_layout(header)
    print(f"Layout: {layout}")
    if data.startswith(b"\xef\xbb\xbf"):
        print("Encoding detail: UTF-8 BOM present")
    if b"\x00" in data[:4096]:
        print("Weirdness: NUL bytes found near sample start (possibly binary/compressed content)")
    print(f"Rows parsed from initial sample: {len(rows)}")
    return source.sample_id, status, layout


def main() -> int:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    results = [inspect_source(source) for source in SOURCES]

    print("\n### Summary")
    print(f"{'ID':38} {'HTTP':>6}  Layout")
    print("-" * 88)
    for sample_id, status, layout in results:
        print(f"{sample_id:38} {str(status or 'ERR'):>6}  {layout}")
    return 0 if all(status is not None for _, status, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
