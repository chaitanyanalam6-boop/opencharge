"""Which hospitals a user search is allowed to see.

Every user-facing query must start from `searchable_hospitals()` rather than the raw
`hospital` table. That keeps the service-area rule in one place: hospitals ingested only to
exercise a parser (in_service_area = false) can never leak into results.
"""

from sqlalchemy import Select, select

from opencharge.models import hospital


def searchable_hospitals() -> Select[tuple[str]]:
    """Select the ids of hospitals that may appear in search results."""
    return select(hospital.c.id).where(hospital.c.in_service_area.is_(True))
