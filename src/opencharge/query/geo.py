"""ZIP codes to coordinates, and distances.

ZIP centroids come from the Census Bureau's 2025 ZCTA Gazetteer (public domain), trimmed to
DE, DC, MD, VA and WV (ZIP prefixes 197-268) in sources/zcta_centroids.csv.
"""

import csv
import math
from functools import cache
from pathlib import Path

from opencharge import PROJECT_ROOT

ZCTA_PATH = PROJECT_ROOT / "sources" / "zcta_centroids.csv"
EARTH_RADIUS_MILES = 3958.8


@cache
def zip_centroids(path: Path = ZCTA_PATH) -> dict[str, tuple[float, float]]:
    with path.open(encoding="utf-8", newline="") as f:
        return {row["zip"]: (float(row["lat"]), float(row["lon"])) for row in csv.DictReader(f)}


def locate_zip(zip_code: str) -> tuple[float, float] | None:
    """(lat, lon) of a ZIP's centroid, or None if we don't have it (outside our area)."""
    return zip_centroids().get(zip_code)


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Straight-line distance between two (lat, lon) points, in miles."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))
