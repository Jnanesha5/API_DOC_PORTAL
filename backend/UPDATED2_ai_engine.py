"""
ai_engine.py
------------
This is the AI/Innovation layer of the portal. It is responsible for three
distinct AI-driven jobs, each justified below:

1. generate_documentation(endpoint)
   -> Turns a sparse, structured endpoint definition into human-readable
      developer documentation (purpose, parameters explained in plain
      English, usage notes, gotchas). Useful because hand-writing docs for
      every route is the #1 thing API teams skip; an LLM can read the
      shape of a schema and explain *why* a field exists, not just its type.

2. generate_test_cases(endpoint)
   -> Produces concrete request/response pairs covering happy-path, auth
      failure, validation failure, and not-found scenarios, as runnable
      pytest-style assertions. Useful because manually enumerating edge
      cases is repetitive and error-prone; an LLM can reason about which
      edge cases a given schema implies (e.g. a required "email" field
      implies a malformed-email test case).

3. explain_error(endpoint, status_code, response_payload)
   -> Given a real failed response captured by the "Try it" tester, the
      AI explains in plain language what went wrong and what the caller
      should change. Useful because raw error JSON ("422 Unprocessable
      Entity") doesn't tell a junior developer *what to fix*; the AI
      bridges that gap, which is the "actionable output" the brief asks for.

This module talks to any OpenAI-compatible chat completions endpoint, so it
works unmodified with OpenAI, Azure OpenAI, or NVIDIA NIM (NIM exposes the
same /v1/chat/completions contract). If no API key is configured, every
function falls back to a deterministic, template-based generator so the
whole portal still works end-to-end offline / in front of judges with no
internet access.
"""
import json
import os
import re
from typing import Dict, Any, List

AI_BASE_URL = os.environ.get("AI_BASE_URL", "https://api.openai.com/v1")
AI_API_KEY = os.environ.get("AI_API_KEY", "").strip()
AI_MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "45"))

USE_AI = bool(AI_API_KEY)

if USE_AI:
    from openai import OpenAI
    _client = OpenAI(base_url=AI_BASE_URL, api_key=AI_API_KEY, timeout=AI_TIMEOUT_SECONDS)


def _call_llm(system_prompt: str, user_prompt: str, json_mode: bool = True) -> str:
    """Single shared call point so every AI feature uses the same client/model."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    request = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1200,
        "timeout": AI_TIMEOUT_SECONDS,
    }
    if json_mode:
        request["response_format"] = {"type": "json_object"}

    try:
        resp = _client.chat.completions.create(**request)
    except Exception:
        if not json_mode:
            raise
        request.pop("response_format", None)
        resp = _client.chat.completions.create(**request)
    return resp.choices[0].message.content


def _loads_json(raw: str) -> Any:
    """Accept strict JSON plus the occasional fenced JSON block from non-OpenAI providers."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return json.loads(match.group(1))
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and start < end:
            return json.loads(raw[start:end + 1])
        raise


def ai_status() -> Dict[str, Any]:
    return {
        "enabled": USE_AI,
        "base_url": AI_BASE_URL,
        "model": AI_MODEL,
        "api_key_present": bool(AI_API_KEY),
        "timeout_seconds": AI_TIMEOUT_SECONDS,
    }


# ----------------------------------------------------------------------
# 1. DOCUMENTATION GENERATION
# ----------------------------------------------------------------------

def generate_documentation(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    if USE_AI:
        try:
            return _ai_generate_documentation(endpoint)
        except Exception as e:
            return _fallback_documentation(endpoint, error=str(e))
    return _fallback_documentation(endpoint)


def _ai_generate_documentation(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    system = (
        "You are a senior API technical writer. Given a structured endpoint "
        "definition, produce clear developer documentation. Respond ONLY with "
        "a JSON object with keys: summary (1-2 sentences), detailed_description "
        "(2-4 sentences explaining purpose and behavior), parameters_explained "
        "(object mapping each param/field name to a plain-English explanation), "
        "sample_request (realistic JSON or curl string), sample_response "
        "(realistic JSON matching response_body), usage_notes (array of strings: "
        "gotchas, rate limits, auth notes, idempotency notes)."
    )
    user = json.dumps(endpoint, indent=2)
    raw = _call_llm(system, user)
    doc = _loads_json(raw)
    doc["_generated_by"] = f"live-ai ({AI_MODEL})"
    return doc


def _fallback_documentation(endpoint: Dict[str, Any], error: str = None) -> Dict[str, Any]:
    method, path = endpoint["method"], endpoint["path"]
    resource = path.strip("/").split("/")[2] if len(path.strip("/").split("/")) > 2 else path
    action = {"GET": "Retrieves", "POST": "Creates", "PUT": "Replaces",
              "PATCH": "Partially updates", "DELETE": "Deletes"}.get(method, "Operates on")

    params_explained = {}
    for k in (endpoint.get("query_params") or {}):
        params_explained[k] = f"'{k}' narrows or identifies the {resource} being requested."
    for k in (endpoint.get("request_body") or {}) if isinstance(endpoint.get("request_body"), dict) else {}:
        params_explained[k] = f"'{k}' is a field supplied in the request body."

    notes = [f"Authentication is {'required' if endpoint.get('auth_required') else 'not required'} for this endpoint."]
    if 429 in (endpoint.get("error_codes") or []):
        notes.append("This endpoint is rate-limited; back off and retry on HTTP 429.")
    if endpoint.get("error_codes"):
        notes.append(f"Possible error responses: {', '.join(str(c) for c in endpoint['error_codes'])}.")

    doc = {
        "summary": f"{action} {resource} via {method} {path}.",
        "detailed_description": (
            f"This endpoint performs a {method} operation on the '{resource}' resource. "
            f"{endpoint.get('description') or 'No further description was provided in the source definition.'}"
        ),
        "parameters_explained": params_explained or {"(none)": "This endpoint takes no parameters."},
        "sample_request": _sample_payload(endpoint.get("request_body")),
        "sample_response": _sample_payload(endpoint.get("response_body")),
        "usage_notes": notes,
        "_generated_by": "template-fallback" if not error else f"template-fallback (AI error: {error})",
    }
    return doc


def _sample_payload(schema: Any) -> Any:
    if schema is None:
        return None
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            out[k] = _sample_value(k, v)
        return out
    if isinstance(schema, list):
        return [_sample_payload(schema[0])] if schema else []
    return schema


def _sample_value(key: str, type_hint: Any) -> Any:
    hint = str(type_hint).lower()
    if "uuid" in hint:
        return "a1b2c3d4-0000-4000-8000-000000000000"
    if "datetime" in hint or "iso 8601" in hint:
        return "2026-06-30T10:00:00Z"
    if "integer" in hint or "int" in hint:
        return 1
    if "boolean" in hint:
        return True
    if "enum" in hint:
        return hint.split("enum:")[-1].split("|")[0].strip(") ")
    if isinstance(type_hint, list):
        return [_sample_value(key, type_hint[0])] if type_hint else []
    return f"sample_{key}"


# ----------------------------------------------------------------------
# 2. TEST CASE GENERATION
# ----------------------------------------------------------------------

def generate_test_cases(endpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    if USE_AI:
        try:
            return _ai_generate_test_cases(endpoint)
        except Exception as e:
            return _fallback_test_cases(endpoint, error=str(e))
    return _fallback_test_cases(endpoint)


def _ai_generate_test_cases(endpoint: Dict[str, Any]) -> List[Dict[str, Any]]:
    system = (
        "You are a QA engineer writing automated API test cases. Given a "
        "structured endpoint definition, generate 4-6 test cases covering "
        "happy path, missing/invalid auth (if auth_required), validation "
        "errors, and not-found (if path has an id param). Respond ONLY with "
        "a JSON object: {\"test_cases\": [ {name, description, request, "
        "expected_status, expected_response_shape, pytest_snippet}, ... ]}. "
        "pytest_snippet should be a short runnable httpx/requests based test."
    )
    user = json.dumps(endpoint, indent=2)
    raw = _call_llm(system, user)
    data = _loads_json(raw)
    test_cases = data["test_cases"]
    for test_case in test_cases:
        test_case.setdefault("_generated_by", f"live-ai ({AI_MODEL})")
    return test_cases


def _fallback_test_cases(endpoint: Dict[str, Any], error: str = None) -> List[Dict[str, Any]]:
    method, path = endpoint["method"], endpoint["path"]
    cases = []
    tag = "" if not error else f" (AI unavailable: {error})"

    cases.append({
        "name": "happy_path_success",
        "description": f"{method} {path} returns a successful response for valid input.{tag}",
        "request": {"method": method, "path": path, "body": _sample_payload(endpoint.get("request_body"))},
        "expected_status": 201 if method == "POST" else 200,
        "expected_response_shape": list((endpoint.get("response_body") or {}).keys()),
        "pytest_snippet": _pytest_snippet(endpoint, "200/201 happy path", expected=201 if method == "POST" else 200),
    })

    if endpoint.get("auth_required"):
        cases.append({
            "name": "missing_auth_rejected",
            "description": f"Calling {method} {path} without an Authorization header is rejected.",
            "request": {"method": method, "path": path, "headers": {}},
            "expected_status": 401,
            "expected_response_shape": ["error", "message"],
            "pytest_snippet": _pytest_snippet(endpoint, "missing auth", expected=401, no_auth=True),
        })

    if isinstance(endpoint.get("request_body"), dict) and endpoint["request_body"]:
        cases.append({
            "name": "validation_error_on_missing_required_field",
            "description": f"{method} {path} rejects a payload missing required fields.",
            "request": {"method": method, "path": path, "body": {}},
            "expected_status": 422 if 422 in endpoint.get("error_codes", []) else 400,
            "expected_response_shape": ["error", "details"],
            "pytest_snippet": _pytest_snippet(endpoint, "invalid payload", expected=422, empty_body=True),
        })

    if "{" in path:
        cases.append({
            "name": "not_found_for_invalid_id",
            "description": f"{method} {path} returns 404 for an id that does not exist.",
            "request": {"method": method, "path": path.replace(path[path.find("{"):path.find("}")+1], "nonexistent-id-000")},
            "expected_status": 404,
            "expected_response_shape": ["error", "message"],
            "pytest_snippet": _pytest_snippet(endpoint, "not found", expected=404, bad_id=True),
        })

    if method == "DELETE":
        cases.append({
            "name": "double_delete_conflict_or_idempotent",
            "description": f"Deleting the same resource twice via {path} should not 500.",
            "request": {"method": method, "path": path},
            "expected_status": 404,
            "expected_response_shape": ["error"],
            "pytest_snippet": _pytest_snippet(endpoint, "repeat delete", expected=404, bad_id=False),
        })

    return cases


def _pytest_snippet(endpoint, label, expected, no_auth=False, empty_body=False, bad_id=False) -> str:
    method = endpoint["method"].lower()
    path = endpoint["path"]
    if bad_id and "{" in path:
        seg = path[path.find("{"):path.find("}")+1]
        path = path.replace(seg, "nonexistent-id-000")
    headers = "{}" if no_auth else '{"Authorization": "Bearer TEST_TOKEN"}'
    body = "{}" if empty_body else json.dumps(_sample_payload(endpoint.get("request_body")) or {})
    return (
        f"def test_{label.replace(' ', '_')}():\n"
        f"    resp = client.{method}(\n"
        f"        \"{path}\",\n"
        f"        headers={headers},\n"
        + (f"        json={body},\n" if method in ("post", "put", "patch") else "")
        + f"    )\n"
        f"    assert resp.status_code == {expected}\n"
    )


# ----------------------------------------------------------------------
# 3. ERROR EXPLANATION
# ----------------------------------------------------------------------

def explain_error(endpoint: Dict[str, Any], status_code: int, response_payload: Any) -> Dict[str, Any]:
    if USE_AI:
        try:
            return _ai_explain_error(endpoint, status_code, response_payload)
        except Exception as e:
            return _fallback_explain_error(endpoint, status_code, response_payload, error=str(e))
    return _fallback_explain_error(endpoint, status_code, response_payload)


def _ai_explain_error(endpoint, status_code, response_payload) -> Dict[str, Any]:
    system = (
        "You are an API support engineer. Given the endpoint definition, the "
        "HTTP status code returned, and the raw response body, explain in "
        "plain English what went wrong and the exact next action the caller "
        "should take. Respond ONLY with a JSON object: {\"likely_cause\": str, "
        "\"plain_english_explanation\": str, \"suggested_fix\": str, "
        "\"severity\": \"low\"|\"medium\"|\"high\"}."
    )
    user = json.dumps({
        "endpoint": endpoint, "status_code": status_code, "response_payload": response_payload,
    }, indent=2, default=str)
    raw = _call_llm(system, user)
    explanation = _loads_json(raw)
    explanation["_generated_by"] = f"live-ai ({AI_MODEL})"
    return explanation


_KNOWN_CAUSES = {
    400: ("Malformed request", "The request body or query string did not match the expected format.",
          "Check field names/types against the documentation and re-send."),
    401: ("Missing or invalid credentials", "The server could not verify who is making this request.",
          "Attach a valid Authorization: Bearer <token> header and retry."),
    403: ("Insufficient permissions", "You are authenticated, but not allowed to perform this action.",
          "Use an account/token with the right role/scope for this resource."),
    404: ("Resource not found", "The id or path you requested does not exist on the server.",
          "Double-check the id/path, or create the resource first."),
    409: ("Conflict with existing state", "The request conflicts with the current state of the resource.",
          "Re-fetch the latest resource state and resolve the conflict before retrying."),
    422: ("Semantic validation failure", "The request was well-formed JSON but failed business validation rules.",
          "Review the 'details' field of the response and correct the offending fields."),
    429: ("Rate limit exceeded", "You have sent too many requests in a short window.",
          "Back off using exponential delay and retry after the Retry-After header value."),
    500: ("Server-side failure", "Something went wrong on the server that is not your fault.",
          "Retry shortly; if it persists, contact the API provider with the request id."),
}


def _fallback_explain_error(endpoint, status_code, response_payload, error: str = None) -> Dict[str, Any]:
    cause, explanation, fix = _KNOWN_CAUSES.get(
        status_code,
        ("Unrecognized status code", "This status code isn't in the common-error reference table.", "Check API provider documentation for this specific code."),
    )
    severity = "high" if status_code >= 500 else ("medium" if status_code in (401, 403, 429) else "low")
    return {
        "likely_cause": cause,
        "plain_english_explanation": explanation,
        "suggested_fix": fix,
        "severity": severity,
        "_generated_by": "template-fallback" if not error else f"template-fallback (AI error: {error})",
    }
