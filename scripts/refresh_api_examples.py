"""Capture real API responses as the OpenAPI examples (brief §5: "make sure they are real").

Runs the API in-process against the local database and saves trimmed responses to
src/opencharge/api/examples/. Nothing is typed by hand: every value comes from a hospital file.

    uv run python scripts/refresh_api_examples.py
"""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from opencharge.api.app import EXAMPLES_DIR, create_app

# A confirmed interpretation of "knee MRI near Baltimore with Aetna", run without the LLM so
# the example is reproducible.
KNEE_MRI_AETNA = {
    "code_type": "CPT",
    "code": "73721",
    "description_terms": ["mri", "knee", "lower extremity", "joint"],
    "payer": "aetna",
    "setting": "outpatient",
    "price_types": ["negotiated", "cash"],
    "near_zip": "21201",
    "radius_miles": 25,
    "confidence": 0.92,
    "unresolved": [],
}


def trim(value: Any, keep: int = 2) -> Any:
    """Shorten long lists so the docs stay readable; values themselves are untouched."""
    if isinstance(value, list):
        return [trim(v, keep) for v in value[:keep]]
    if isinstance(value, dict):
        return {k: trim(v, keep) for k, v in value.items()}
    return value


def save(name: str, data: Any) -> None:
    path = EXAMPLES_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {path.relative_to(Path.cwd())}")


def main() -> None:
    EXAMPLES_DIR.mkdir(exist_ok=True)
    with TestClient(create_app(use_env_llm=False)) as client:
        search = client.post("/api/search/run", json=KNEE_MRI_AETNA).json()
        search["question"] = "knee MRI near Baltimore with Aetna"
        save("search", trim(search))

        save("procedure", trim(client.get("/api/procedure/73721?code_type=CPT").json()))

        detail = client.get("/api/hospital/medstar-union-memorial/price/73721?code_type=CPT").json()
        detail["payers"] = detail["payers"][:4]
        save("hospital_price", trim(detail, keep=4))

        file_id = detail["source_file"]["id"]
        save("source", client.get(f"/api/sources/{file_id}").json())

        status = client.get("/api/status").json()
        save("status", trim(status, keep=5))


if __name__ == "__main__":
    main()
