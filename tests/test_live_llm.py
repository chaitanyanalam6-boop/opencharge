"""End-to-end against the real LLM. Run with:  uv run --env-file .env pytest -m llm

Skipped without GROQ_API_KEY and excluded from CI: it costs money, needs a secret, and its
output can vary. The deterministic guardrail tests live in test_query.py.
"""

import os

import pytest
from sqlalchemy import select

from opencharge.db import make_engine
from opencharge.models import payer
from opencharge.query.catalog import CodeCatalog
from opencharge.query.execute import execute
from opencharge.query.interpret import interpret
from opencharge.query.llm import GroqClient

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="GROQ_API_KEY not set"),
]


@pytest.fixture(scope="module")
def setup():  # type: ignore[no-untyped-def]
    engine = make_engine()
    with engine.connect() as conn:
        catalog = CodeCatalog.build(conn)
        payers = dict(conn.execute(select(payer.c.id, payer.c.canonical_name)).all())
    if not catalog.entries:
        pytest.skip("no data loaded; run python -m opencharge.ingest --all")
    return engine, catalog, payers, GroqClient()


@pytest.mark.parametrize(
    ("question", "code", "payer_id"),
    [
        ("knee MRI near Baltimore with Aetna", "73721", "aetna"),
        ("how much is a CT scan of the head without contrast in DC", "70450", None),
        ("total knee replacement surgery cost with CareFirst", "27447", "carefirst"),
    ],
)
def test_real_questions(setup, question: str, code: str, payer_id: str | None) -> None:  # type: ignore[no-untyped-def]
    engine, catalog, payers, llm = setup
    result = interpret(question, llm, catalog, payers)
    assert result.code == code
    assert result.payer == payer_id
    with engine.connect() as conn:
        search = execute(conn, result)
    assert search.results or search.gaps
