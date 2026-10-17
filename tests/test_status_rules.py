"""The thresholds behind the status page, at their exact boundaries."""

from types import SimpleNamespace

import pytest

from opencharge.api.views import freshness_of, pill_for, quarantine_summary

LOADED_FILE = SimpleNamespace(status="loaded")
OK_FETCH = SimpleNamespace(outcome="unchanged", error=None)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, "fresh"),
        (89, "fresh"),
        (90, "aging"),
        (180, "aging"),
        (181, "stale"),
        (289, "stale"),
        (None, "unknown"),
    ],
)
def test_freshness_thresholds(age: int | None, expected: str) -> None:
    assert freshness_of(age) == expected


@pytest.mark.parametrize(
    ("pct", "status", "tone"),
    [
        (0.0, "Loaded", "green"),
        (9.9, "Loaded", "green"),
        (10.0, "Loaded with gaps", "amber"),
        (50.0, "Loaded with gaps", "amber"),
        (50.1, "Needs review", "red"),
        (53.2, "Needs review", "red"),  # GBMC's real rate
    ],
)
def test_pill_thresholds(pct: float, status: str, tone: str) -> None:
    label, color, _ = pill_for(LOADED_FILE, OK_FETCH, pct, has_drift=False)
    assert (label, color) == (status, tone)


def test_drift_and_failed_fetch_need_review_whatever_the_rate() -> None:
    assert pill_for(LOADED_FILE, OK_FETCH, 0.0, has_drift=True)[:2] == ("Needs review", "red")
    failed = SimpleNamespace(outcome="failed", error="HTTP 404")
    label, tone, reason = pill_for(LOADED_FILE, failed, 0.0, has_drift=False)
    assert (label, tone) == ("Needs review", "red")
    assert reason and "HTTP 404" in reason


def test_not_fetched() -> None:
    assert pill_for(None, None, None, has_drift=False)[:2] == ("Not fetched", "grey")


def test_quarantine_summary_names_the_main_cause_in_plain_words() -> None:
    text = quarantine_summary(
        "GBMC", 53.2, {"code_present": 172184, "payer_resolved_if_negotiated": 11002}
    )
    assert text.startswith(
        "We set aside 53.2% of GBMC's rows, mostly items listed without a billing code."
    )
