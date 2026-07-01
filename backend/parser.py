"""
parser.py
---------
Extracts a normalized list of API endpoint definitions from whatever the
user uploads or pastes. Supported inputs:

1. Our own schema:        {"endpoints": [ {method, path, ...}, ... ]}
2. OpenAPI / Swagger:      {"openapi": "3.0.0", "paths": {...}}  (json or yaml)
3. Raw Python source:      FastAPI (@app.get(...)) or Flask (@app.route(...))
4. Freeform pasted text:   "GET /api/v1/users - list users" (one per line)

Everything is normalized to:
{
    "method": "GET",
    "path": "/api/v1/users/{id}",
    "description": "...",
    "request_body": {...} | None,
    "query_params": {...},
    "response_body": {...},
    "auth_required": bool,
    "error_codes": [int, ...]
}
"""
import json
import re
from typing import List, Dict, Any

try:
    import yaml
except ImportError:
    yaml = None

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _empty_record(method="GET", path="/"):
    return {
        "method": method.upper(),
        "path": path,
        "description": "",
        "request_body": None,
        "query_params": {},
        "response_body": {},
        "auth_required": False,
        "error_codes": [],
    }


def parse_file(filename: str, raw_text: str) -> List[Dict[str, Any]]:
    """Main entry point. Detects format from filename/content and dispatches."""
    lower = filename.lower()

    if lower.endswith(".py"):
        return _parse_python_source(raw_text)

    if lower.endswith((".yaml", ".yml")):
        if yaml is None:
            raise RuntimeError("PyYAML not installed; cannot parse YAML files.")
        data = yaml.safe_load(raw_text)
        return _dispatch_dict(data)

    if lower.endswith(".json") or raw_text.strip().startswith("{"):
        data = json.loads(raw_text)
        return _dispatch_dict(data)

    return _parse_freeform_text(raw_text)


def parse_pasted_text(raw_text: str) -> List[Dict[str, Any]]:
    """Used by the Streamlit 'paste raw text / single endpoint' input box."""
    raw_text = raw_text.strip()
    if not raw_text:
        return []
    try:
        data = json.loads(raw_text)
        return _dispatch_dict(data)
    except json.JSONDecodeError:
        return _parse_freeform_text(raw_text)


def _dispatch_dict(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(data, dict):
        raise ValueError("Uploaded JSON/YAML must be an object at the top level.")

    if "endpoints" in data and isinstance(data["endpoints"], list):
        return _normalize_our_schema(data["endpoints"])

    if "paths" in data and isinstance(data["paths"], dict):
        return _parse_openapi(data)

    raise ValueError(
        "Unrecognized JSON/YAML schema. Expected either {'endpoints': [...]} "
        "or an OpenAPI document with a 'paths' key."
    )


def _normalize_our_schema(endpoints: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for ep in endpoints:
        rec = _empty_record(ep.get("method", "GET"), ep.get("path", "/"))
        rec["description"] = ep.get("description", "")
        rec["request_body"] = ep.get("request_body")
        rec["query_params"] = ep.get("query_params", {})
        rec["response_body"] = ep.get("response_body", {})
        rec["auth_required"] = bool(ep.get("auth_required", False))
        rec["error_codes"] = ep.get("error_codes", [])
        normalized.append(rec)
    return normalized


def _parse_openapi(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.lower() not in HTTP_METHODS:
                continue
            rec = _empty_record(method, path)
            rec["description"] = details.get("summary") or details.get("description", "")

            rb = details.get("requestBody", {})
            content = rb.get("content", {})
            if content:
                schema = next(iter(content.values()), {}).get("schema", {})
                rec["request_body"] = schema.get("properties", schema) or None

            params = {}
            for p in details.get("parameters", []):
                if p.get("in") == "query":
                    params[p.get("name")] = p.get("schema", {}).get("type", "string")
            rec["query_params"] = params

            responses = details.get("responses", {})
            ok = responses.get("200") or responses.get("201") or {}
            ok_content = ok.get("content", {})
            if ok_content:
                schema = next(iter(ok_content.values()), {}).get("schema", {})
                rec["response_body"] = schema.get("properties", schema)

            rec["auth_required"] = bool(details.get("security")) or bool(spec.get("security"))

            rec["error_codes"] = sorted({
                int(code) for code in responses.keys()
                if code.isdigit() and not code.startswith("2")
            })
            endpoints.append(rec)
    return endpoints


_PY_DECORATOR_RE = re.compile(
    r"@(?:app|router|bp|blueprint)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_FLASK_ROUTE_RE = re.compile(
    r"@(?:app|bp|blueprint)\.route\(\s*[\"']([^\"']+)[\"'](?:.*?methods\s*=\s*\[([^\]]*)\])?",
    re.IGNORECASE | re.DOTALL,
)
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE)
_AUTH_HINT_RE = re.compile(r"depends\(.*auth|require_auth|login_required|jwt|@auth", re.IGNORECASE)


def _parse_python_source(source: str) -> List[Dict[str, Any]]:
    """Heuristic parser for FastAPI/Flask source files: extracts decorator
    based route definitions plus the function name as a description hint."""
    endpoints = []
    lines = source.splitlines()

    for i, line in enumerate(lines):
        m = _PY_DECORATOR_RE.search(line)
        if m:
            method, path = m.group(1), m.group(2)
            endpoints.append(_record_from_python_block(method, path, lines, i))
            continue

        m2 = _FLASK_ROUTE_RE.search(line)
        if m2:
            path = m2.group(1)
            methods_str = m2.group(2) or "GET"
            methods = [x.strip(" '\"") for x in methods_str.split(",")] if methods_str else ["GET"]
            for method in methods:
                endpoints.append(_record_from_python_block(method, path, lines, i))

    if not endpoints:
        raise ValueError(
            "No FastAPI (@app.get/@router.post...) or Flask (@app.route) "
            "decorators were found in this Python file."
        )
    return endpoints


def _record_from_python_block(method, path, lines, decorator_idx, lookahead=6) -> Dict[str, Any]:
    rec = _empty_record(method, path)
    window = "\n".join(lines[decorator_idx: decorator_idx + lookahead])

    func_match = _DEF_RE.search(window)
    if func_match:
        rec["description"] = f"Auto-detected handler: {func_match.group(1)}()"

    rec["auth_required"] = bool(_AUTH_HINT_RE.search(window))

    path_params = re.findall(r"\{(\w+)\}", path)
    if path_params:
        rec["query_params"] = {p: "path parameter (inferred)" for p in path_params}

    rec["error_codes"] = [401, 404, 422, 500] if rec["auth_required"] else [404, 422, 500]
    return rec


_FREEFORM_LINE_RE = re.compile(
    r"^\s*(GET|POST|PUT|PATCH|DELETE)\s+(\S+)\s*(?:[-\u2013\u2014:]\s*(.*))?$",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_freeform_text(text: str) -> List[Dict[str, Any]]:
    """Last-resort parser for lines like: 'GET /api/v1/users - list users'"""
    endpoints = []
    for m in _FREEFORM_LINE_RE.finditer(text):
        method, path, desc = m.group(1), m.group(2), m.group(3) or ""
        rec = _empty_record(method, path)
        rec["description"] = desc.strip()
        endpoints.append(rec)
    if not endpoints:
        raise ValueError(
            "Could not detect any endpoints. Use format 'METHOD /path - description' "
            "per line, or upload JSON/YAML/OpenAPI/Python."
        )
    return endpoints
