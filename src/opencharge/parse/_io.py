"""Opening raw files: compression and text encoding."""

import gzip
import zipfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from opencharge.models import Compression


@contextmanager
def open_binary(path: Path, compression: Compression = Compression.NONE) -> Iterator[BinaryIO]:
    """Open a stored file as a byte stream, transparently unwrapping gzip or a one-file zip."""
    if compression is Compression.GZIP:
        with gzip.open(path, "rb") as f:
            yield f  # type: ignore[misc]
    elif compression is Compression.ZIP:
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) != 1:
                raise ValueError(f"expected one file in {path.name}, found {len(members)}")
            with archive.open(members[0]) as f:
                yield f  # type: ignore[misc]
    else:
        with path.open("rb") as f:
            yield f


def decoded_lines(stream: BinaryIO, quirks: Counter[str]) -> Iterator[str]:
    """Yield text lines, decoding each as UTF-8 and falling back to Windows-1252.

    Hospital files that claim to be UTF-8 often contain Windows-1252 bytes (curly quotes from
    Excel). Decoding line by line means one bad byte never takes down the whole file; every
    fallback is counted in `quirks` so it shows up in the run report.
    """
    for index, raw in enumerate(stream):
        if index == 0 and raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
            quirks["utf8_bom"] += 1
        try:
            yield raw.decode("utf-8")
        except UnicodeDecodeError:
            quirks["cp1252_fallback_lines"] += 1
            # cp1252 leaves five bytes undefined; "replace" keeps the row rather than losing it.
            yield raw.decode("cp1252", errors="replace")
