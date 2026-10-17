"""The OpenCharge HTTP API.

    uv run --env-file .env uvicorn opencharge.api.app:app --reload
    open http://localhost:8000/docs

create_app() takes its database engine and LLM client as arguments, so tests can pass a
temporary database and a scripted LLM; the module-level `app` reads them from the environment.
"""

import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import Engine, select

from opencharge import PROJECT_ROOT
from opencharge.api import views
from opencharge.api.limits import RateLimiter, TTLCache
from opencharge.api.schemas import (
    NOTICE,
    ErrorOut,
    HospitalPriceDetail,
    ProcedureResponse,
    SearchResponse,
    SourceFileOut,
    StatusResponse,
)
from opencharge.db import make_engine
from opencharge.models import hospital, payer, source_file
from opencharge.query.catalog import CodeCatalog
from opencharge.query.execute import execute
from opencharge.query.interpret import Interpretation, InterpretationError, interpret
from opencharge.query.llm import GroqClient, LLMClient
from opencharge.query.scope import searchable_hospitals

log = logging.getLogger(__name__)

EXAMPLES_DIR = Path(__file__).parent / "examples"
EVAL_RESULTS = PROJECT_ROOT / "evals" / "results.json"
ALL_PRICE_TYPES = ["gross", "cash", "negotiated", "min", "max"]


def _example(name: str) -> dict[int | str, dict[str, Any]]:
    """Response examples for the OpenAPI docs, captured from real data by
    scripts/refresh_api_examples.py (brief: "make sure the examples are real")."""
    path = EXAMPLES_DIR / f"{name}.json"
    if not path.exists():
        return {}
    return {200: {"content": {"application/json": {"example": json.loads(path.read_text())}}}}


ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorOut},
    422: {"model": ErrorOut},
    429: {"model": ErrorOut},
    503: {"model": ErrorOut},
}


class State:
    """Everything the endpoints share, built once at startup."""

    def __init__(self, engine: Engine, llm: LLMClient | None) -> None:
        self.engine = engine
        self.llm = llm
        self.search_cache = TTLCache(ttl_seconds=600)
        self.page_cache = TTLCache(ttl_seconds=300)
        # The LLM-backed endpoint is the expensive one; everything else is a database read.
        self.search_limiter = RateLimiter(limit=10, window_seconds=60)
        self.read_limiter = RateLimiter(limit=120, window_seconds=60)
        self.reload()

    def reload(self) -> None:
        with self.engine.connect() as conn:
            self.catalog = CodeCatalog.build(conn)
            self.payer_names: dict[str, str] = dict(
                conn.execute(select(payer.c.id, payer.c.canonical_name)).all()
            )
            self.hospitals = views.hospitals_out(conn)
        self.search_cache.clear()
        self.page_cache.clear()


def create_app(
    engine: Engine | None = None, llm: LLMClient | None = None, *, use_env_llm: bool = True
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        chosen_llm = llm
        if chosen_llm is None and use_env_llm and os.environ.get("GROQ_API_KEY"):
            chosen_llm = GroqClient()
        app.state.oc = State(engine or make_engine(), chosen_llm)
        yield

    app = FastAPI(
        title="OpenCharge API",
        version="0.1.0",
        description=(
            "Real hospital prices from public CMS price transparency files, each traceable to "
            "the source file and row it came from. If a hospital doesn't publish a price, the "
            "API says so; it never estimates. " + NOTICE
        ),
        lifespan=lifespan,
    )
    origins = [
        o for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o
    ]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"]
    )

    def state(request: Request) -> State:
        return request.app.state.oc

    def limited(which: str) -> Callable[[Request], None]:
        def dependency(request: Request) -> None:
            limiter = getattr(state(request), f"{which}_limiter")
            retry_after = limiter.check(client_key(request))
            if retry_after is not None:
                raise HTTPException(
                    429,
                    "Too many requests; please wait a moment.",
                    headers={"Retry-After": str(int(retry_after) + 1)},
                )

        return dependency

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    StateDep = Annotated[State, Depends(state)]

    # --- search -------------------------------------------------------------------------

    @app.get(
        "/api/search",
        response_model=SearchResponse,
        responses={**ERRORS, **_example("search")},
        dependencies=[Depends(limited("search"))],
        summary="Ask a question; get the model's interpretation, prices and gaps",
    )
    def search(
        s: StateDep,
        q: Annotated[
            str,
            Query(min_length=1, max_length=300, examples=["knee MRI near Baltimore with Aetna"]),
        ],
    ) -> SearchResponse:
        key = " ".join(q.lower().split())
        cached = s.search_cache.get(key)
        if cached is not None:
            return cached
        if s.llm is None:
            raise HTTPException(503, "Search is unavailable: no language model is configured.")
        try:
            interpretation = interpret(q, s.llm, s.catalog, s.payer_names)
        except InterpretationError as error:
            raise HTTPException(422, str(error)) from error
        response = run_search(s, interpretation, question=q)
        s.search_cache.set(key, response)
        return response

    @app.post(
        "/api/search/run",
        response_model=SearchResponse,
        responses=ERRORS,
        dependencies=[Depends(limited("read"))],
        summary="Run a corrected interpretation directly (the 'Correct this' button); no LLM",
    )
    def search_run(s: StateDep, interpretation: Interpretation) -> SearchResponse:
        if (
            interpretation.code is None
            or (interpretation.code_type, interpretation.code) not in s.catalog.entries
        ):
            raise HTTPException(422, "That code isn't published by any hospital we cover.")
        if interpretation.payer is not None and interpretation.payer not in s.payer_names:
            raise HTTPException(422, f"Unknown payer {interpretation.payer!r}.")
        # The person confirmed it, so it runs regardless of the model's confidence.
        confirmed = interpretation.model_copy(update={"confidence": 1.0, "unresolved": []})
        return run_search(s, confirmed, question=None)

    # --- procedure, hospital price, source ----------------------------------------------

    @app.get(
        "/api/procedure/{code}",
        response_model=ProcedureResponse,
        responses={**ERRORS, **_example("procedure")},
        dependencies=[Depends(limited("read"))],
        summary="Every in-area hospital's prices for one code",
    )
    def procedure(
        s: StateDep,
        code: str,
        code_type: Annotated[str | None, Query(pattern="^(CPT|HCPCS|DRG)$")] = None,
    ) -> ProcedureResponse:
        key = f"procedure:{code_type}:{code}"
        if (cached := s.page_cache.get(key)) is not None:
            return cached
        entry = resolve_code(s, code, code_type)
        interpretation = whole_area(entry.code_type, entry.code)
        with s.engine.connect() as conn:
            result = execute(conn, interpretation)
        response = ProcedureResponse(
            code_type=entry.code_type,
            code=entry.code,
            descriptions=[d for d, _ in entry.descriptions.most_common(5)],
            hospitals=views.hospital_prices(
                result.results, s.hospitals, s.payer_names, ALL_PRICE_TYPES
            ),
            gaps=views.gaps_out(result.gaps, s.hospitals),
        )
        s.page_cache.set(key, response)
        return response

    @app.get(
        "/api/hospital/{hospital_id}/price/{code}",
        response_model=HospitalPriceDetail,
        responses={**ERRORS, **_example("hospital_price")},
        dependencies=[Depends(limited("read"))],
        summary="Full payer table and provenance for one code at one hospital",
    )
    def hospital_price(
        s: StateDep,
        hospital_id: str,
        code: str,
        code_type: Annotated[str | None, Query(pattern="^(CPT|HCPCS|DRG)$")] = None,
    ) -> HospitalPriceDetail:
        if hospital_id not in s.hospitals or not is_searchable(s, hospital_id):
            raise HTTPException(404, "No such hospital.")
        entry = resolve_code(s, code, code_type)
        interpretation = whole_area(entry.code_type, entry.code)
        with s.engine.connect() as conn:
            result = execute(conn, interpretation)
            lines = [r for r in result.results if r.hospital_id == hospital_id]
            if not lines:
                raise HTTPException(
                    404, f"{entry.code_type} {entry.code} is not in this hospital's file."
                )
            by_type = {
                t: [r for r in lines if r.price_type == t] for t in ("gross", "cash", "negotiated")
            }
            file = conn.execute(
                select(source_file).where(source_file.c.id == lines[0].provenance.source_file_id)
            ).one()
            source = views.source_file_out(file)
            example = next((r for r in by_type["negotiated"] if r.rate_cents is not None), lines[0])
            return HospitalPriceDetail(
                hospital=s.hospitals[hospital_id],
                code_type=entry.code_type,
                code=entry.code,
                descriptions=sorted({r.description for r in lines}),
                gross=views.summarize(by_type["gross"], s.payer_names),
                cash=views.summarize(by_type["cash"], s.payer_names),
                payers=views.payer_table(conn, hospital_id, by_type["negotiated"], s.payer_names),
                source_file=source,
                validation_checks=views.VALIDATION_CHECKS,
                caveats=views.caveats(s.hospitals[hospital_id].state, source),
                example_line=views.raw_vs_normalized(conn, example),
            )

    @app.get(
        "/api/sources/{file_id}",
        response_model=SourceFileOut,
        responses={**ERRORS, **_example("source")},
        dependencies=[Depends(limited("read"))],
        summary="Metadata and link for one source file",
    )
    def source(s: StateDep, file_id: str) -> SourceFileOut:
        with s.engine.connect() as conn:
            row = conn.execute(select(source_file).where(source_file.c.id == file_id)).first()
        if row is None:
            raise HTTPException(404, "No such source file.")
        return views.source_file_out(row)

    # --- status -------------------------------------------------------------------------

    @app.get(
        "/api/status",
        response_model=StatusResponse,
        responses={**ERRORS, **_example("status")},
        dependencies=[Depends(limited("read"))],
        summary="Pipeline status: last run per hospital, KPIs, coverage, what needs attention",
    )
    def status(s: StateDep, response: Response) -> StatusResponse:
        response.headers["Cache-Control"] = "public, max-age=300"
        if (cached := s.page_cache.get("status")) is not None:
            return cached
        answer_quality = json.loads(EVAL_RESULTS.read_text()) if EVAL_RESULTS.exists() else None
        with s.engine.connect() as conn:
            result = views.status(conn, answer_quality)
        s.page_cache.set("status", result)
        return result

    return app


# --- helpers --------------------------------------------------------------------------


def client_key(request: Request) -> str:
    """Who to rate-limit. Behind Railway's proxy the client is the first X-Forwarded-For hop,
    but that header is only trusted when TRUST_PROXY=1, since clients can set it themselves."""
    if os.environ.get("TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def run_search(s: State, interpretation: Interpretation, question: str | None) -> SearchResponse:
    if interpretation.needs_confirmation:
        return SearchResponse(
            status="needs_confirmation",
            question=question,
            interpretation=interpretation,
            message="Please confirm or correct this interpretation before we search.",
        )
    payer_name = s.payer_names.get(interpretation.payer) if interpretation.payer else None
    with s.engine.connect() as conn:
        result = execute(conn, interpretation, payer_name)
    rows = views.hospital_prices(
        result.results, s.hospitals, s.payer_names, interpretation.price_types
    )
    return SearchResponse(
        status="ok",
        question=question,
        interpretation=interpretation,
        headline=views.headline(rows, interpretation.price_types),
        hospitals=rows,
        gaps=views.gaps_out(result.gaps, s.hospitals),
        hospitals_in_range=result.hospitals_in_range,
    )


def resolve_code(s: State, code: str, code_type: str | None) -> Any:
    """Find a code in the catalog, inferring its type if unambiguous."""
    code = code.strip().upper()
    if code_type:
        entry = s.catalog.entries.get((code_type, code))
        if entry is None:
            raise HTTPException(404, f"No hospital we cover publishes {code_type} {code}.")
        return entry
    matches = [e for (t, c), e in s.catalog.entries.items() if c == code]
    if not matches:
        raise HTTPException(404, f"No hospital we cover publishes code {code}.")
    if len(matches) > 1:
        types = ", ".join(sorted(e.code_type for e in matches))
        raise HTTPException(422, f"Code {code} exists as several types ({types}); add ?code_type=.")
    return matches[0]


def whole_area(code_type: str, code: str) -> Interpretation:
    """A search for one code across the whole service area, every payer, every price type."""
    try:
        return Interpretation(
            code_type=code_type,  # type: ignore[arg-type]
            code=code,
            description_terms=[code.lower()],
            payer=None,
            setting=None,
            price_types=ALL_PRICE_TYPES,  # type: ignore[arg-type]
            near_zip=None,
            radius_miles=100,
            confidence=1.0,
            unresolved=[],
        )
    except ValidationError as error:
        raise HTTPException(422, "That isn't a valid billing code.") from error


def is_searchable(s: State, hospital_id: str) -> bool:
    with s.engine.connect() as conn:
        return (
            conn.scalar(
                select(hospital.c.id).where(
                    hospital.c.id == hospital_id, hospital.c.id.in_(searchable_hospitals())
                )
            )
            is not None
        )


app = create_app()
