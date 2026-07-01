"""
main.py — FastAPI backend for the AI-Powered API Documentation & Testing Portal.

Endpoints:
  POST /upload            -> parse an uploaded file (json/yaml/py) into endpoints
  POST /parse-text        -> parse pasted JSON or freeform "METHOD /path" text
  GET  /endpoints         -> list endpoints currently loaded for this session
  POST /load-sample       -> load the starter dataset (data/endpoints.json)
  POST /generate-docs     -> AI-generate documentation for one endpoint
  POST /generate-tests    -> AI-generate test cases for one endpoint
  POST /try-it            -> actually call a real URL + return + AI-explain any error
  POST /explain-error      -> AI-explain a status code / payload pair directly
"""
import json
import os
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import parser as ep_parser
from . import UPDATED_ai_engine

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DATA_PATH = os.path.join(APP_DIR, "..", "data", "endpoints.json")

app = FastAPI(title="AI API Documentation & Testing Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store keyed by a session id the frontend generates once per run.
# (Simple by design — this is a hackathon-scope portal, not a multi-tenant DB.)
_SESSIONS: Dict[str, List[Dict[str, Any]]] = {}


class TextInput(BaseModel):
    session_id: str
    text: str


class EndpointRef(BaseModel):
    session_id: str
    endpoint_index: int


class TryItInput(BaseModel):
    base_url: str
    method: str
    path: str
    headers: Optional[Dict[str, str]] = None
    body: Optional[Dict[str, Any]] = None
    query_params: Optional[Dict[str, Any]] = None
    endpoint: Optional[Dict[str, Any]] = None  # original definition, for AI error explain


class ExplainErrorInput(BaseModel):
    endpoint: Dict[str, Any]
    status_code: int
    response_payload: Any


def _get_session(session_id: str) -> List[Dict[str, Any]]:
    if session_id not in _SESSIONS:
        raise HTTPException(404, f"No data loaded for session '{session_id}'. Upload or load sample data first.")
    return _SESSIONS[session_id]


@app.get("/health")
def health():
    return {"status": "ok", "ai_enabled": UPDATED_ai_engine.USE_AI}


@app.post("/upload")
async def upload_file(session_id: str, file: UploadFile = File(...)):
    raw = (await file.read()).decode("utf-8", errors="replace")
    try:
        endpoints = ep_parser.parse_file(file.filename, raw)
    except Exception as e:
        raise HTTPException(400, f"Failed to parse '{file.filename}': {e}")
    _SESSIONS[session_id] = endpoints
    return {"count": len(endpoints), "endpoints": endpoints}


@app.post("/parse-text")
def parse_text(payload: TextInput):
    try:
        endpoints = ep_parser.parse_pasted_text(payload.text)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not endpoints:
        raise HTTPException(400, "No endpoints could be parsed from the provided text.")
    _SESSIONS[payload.session_id] = endpoints
    return {"count": len(endpoints), "endpoints": endpoints}


@app.post("/load-sample")
def load_sample(session_id: str):
    if not os.path.exists(SAMPLE_DATA_PATH):
        raise HTTPException(500, "Sample dataset not found on server.")
    with open(SAMPLE_DATA_PATH) as f:
        data = json.load(f)
    endpoints = data["endpoints"]
    _SESSIONS[session_id] = endpoints
    return {"count": len(endpoints), "endpoints": endpoints, "error_code_reference": data.get("error_code_reference", [])}


@app.get("/endpoints")
def get_endpoints(session_id: str):
    return {"endpoints": _get_session(session_id)}


@app.post("/generate-docs")
def generate_docs(ref: EndpointRef):
    endpoints = _get_session(ref.session_id)
    if not (0 <= ref.endpoint_index < len(endpoints)):
        raise HTTPException(400, "endpoint_index out of range")
    doc = UPDATED_ai_engine.generate_documentation(endpoints[ref.endpoint_index])
    return {"documentation": doc}


@app.post("/generate-tests")
def generate_tests(ref: EndpointRef):
    endpoints = _get_session(ref.session_id)
    if not (0 <= ref.endpoint_index < len(endpoints)):
        raise HTTPException(400, "endpoint_index out of range")
    tests = UPDATED_ai_engine.generate_test_cases(endpoints[ref.endpoint_index])
    return {"test_cases": tests}


@app.post("/explain-error")
def explain_error(payload: ExplainErrorInput):
    explanation = UPDATED_ai_engine.explain_error(payload.endpoint, payload.status_code, payload.response_payload)
    return {"explanation": explanation}


@app.post("/try-it")
async def try_it(payload: TryItInput):
    """Fires a real HTTP request at base_url+path so users can test live APIs,
    then (on error) automatically attaches an AI explanation of what went wrong."""
    url = payload.base_url.rstrip("/") + payload.path
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.request(
                payload.method.upper(),
                url,
                headers=payload.headers or {},
                params=payload.query_params or {},
                json=payload.body if payload.body else None,
            )
    except httpx.RequestError as e:
        return {
            "ok": False,
            "network_error": str(e),
            "ai_explanation": UPDATED_ai_engine.explain_error(
                payload.endpoint or {"method": payload.method, "path": payload.path},
                0, {"error": "network_error", "detail": str(e)},
            ),
        }

    try:
        body = resp.json()
    except Exception:
        body = resp.text

    result = {
        "ok": resp.status_code < 400,
        "status_code": resp.status_code,
        "response_body": body,
        "headers": dict(resp.headers),
    }

    if resp.status_code >= 400:
        result["ai_explanation"] = UPDATED_ai_engine.explain_error(
            payload.endpoint or {"method": payload.method, "path": payload.path},
            resp.status_code, body,
        )

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
