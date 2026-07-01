"""
app.py — Streamlit frontend for the AI-Powered API Documentation & Testing Portal.

Run alongside the FastAPI backend:
    uvicorn backend.main:app --reload --port 8000
    streamlit run frontend/app.py
"""
import json
import os
import uuid

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

st.set_page_config(page_title="AI API Doc & Testing Portal", layout="wide")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "endpoints" not in st.session_state:
    st.session_state.endpoints = []

SID = st.session_state.session_id

st.title("🧩 AI-Powered API Documentation & Testing Portal")
st.caption(
    "Upload route definitions or paste an endpoint → get AI-generated docs, "
    "sample requests/responses, test cases, and plain-English error explanations."
)

# Backend health check
try:
    health = requests.get(f"{BACKEND_URL}/health", timeout=3).json()
    ai_on = health.get("ai_enabled")
    st.sidebar.success(f"Backend connected ✅  |  AI mode: {'LIVE (LLM)' if ai_on else 'TEMPLATE FALLBACK (no API key set)'}")
except Exception:
    st.sidebar.error("⚠️ Backend not reachable at " + BACKEND_URL + ". Start it with `uvicorn backend.main:app --reload`.")
    st.stop()

# -------------------------------------------------------------------
# Step 1: Get data in
# -------------------------------------------------------------------
st.header("1. Load your API definitions")
tab_upload, tab_paste, tab_sample = st.tabs(["📁 Upload file", "✏️ Paste text/JSON", "📦 Use starter dataset"])

with tab_upload:
    st.write("Supports: OpenAPI/Swagger (`.json`/`.yaml`), our own `endpoints.json` schema, or a FastAPI/Flask `.py` source file.")
    uploaded = st.file_uploader("Upload a file", type=["json", "yaml", "yml", "py"])
    if uploaded and st.button("Parse uploaded file"):
        files = {"file": (uploaded.name, uploaded.getvalue())}
        r = requests.post(f"{BACKEND_URL}/upload", params={"session_id": SID}, files=files)
        if r.ok:
            st.session_state.endpoints = r.json()["endpoints"]
            st.success(f"Parsed {len(st.session_state.endpoints)} endpoint(s).")
        else:
            st.error(r.json().get("detail", "Failed to parse file."))

with tab_paste:
    st.write("Paste raw JSON (`{\"endpoints\": [...]}` or OpenAPI) **or** plain lines like `GET /api/v1/users - list users`.")
    pasted = st.text_area("Paste here", height=160, placeholder="GET /api/v1/orders - list all orders\nPOST /api/v1/orders - create an order")
    if st.button("Parse pasted text"):
        r = requests.post(f"{BACKEND_URL}/parse-text", json={"session_id": SID, "text": pasted})
        if r.ok:
            st.session_state.endpoints = r.json()["endpoints"]
            st.success(f"Parsed {len(st.session_state.endpoints)} endpoint(s).")
        else:
            st.error(r.json().get("detail", "Failed to parse text."))

with tab_sample:
    st.write("Loads the starter dataset (100 realistic CRUD endpoints across 20 resources) so you can demo the portal instantly.")
    if st.button("Load starter dataset"):
        r = requests.post(f"{BACKEND_URL}/load-sample", params={"session_id": SID})
        if r.ok:
            st.session_state.endpoints = r.json()["endpoints"]
            st.success(f"Loaded {len(st.session_state.endpoints)} sample endpoints.")
        else:
            st.error(r.text)

endpoints = st.session_state.endpoints

# -------------------------------------------------------------------
# Step 2: Pick an endpoint and act on it
# -------------------------------------------------------------------
if not endpoints:
    st.info("⬆️ Load some endpoints above to get started.")
    st.stop()

st.header("2. Explore an endpoint")

labels = [f"{e['method']}  {e['path']}" for e in endpoints]
idx = st.selectbox("Choose an endpoint", range(len(labels)), format_func=lambda i: labels[i])
endpoint = endpoints[idx]

col1, col2 = st.columns([1, 1])
with col1:
    st.subheader("Raw definition")
    st.json(endpoint)
with col2:
    st.subheader("At a glance")
    st.markdown(f"""
- **Method:** `{endpoint['method']}`
- **Path:** `{endpoint['path']}`
- **Auth required:** {"🔒 Yes" if endpoint.get('auth_required') else "🔓 No"}
- **Known error codes:** {", ".join(str(c) for c in endpoint.get('error_codes', [])) or "—"}
""")

action_tabs = st.tabs(["📄 AI Documentation", "🧪 AI Test Cases", "🚀 Try it live", "❓ Explain an error"])

# --- Documentation tab ---
with action_tabs[0]:
    if st.button("Generate documentation", key="gen_docs"):
        with st.spinner("Generating documentation..."):
            r = requests.post(f"{BACKEND_URL}/generate-docs", json={"session_id": SID, "endpoint_index": idx})
        if r.ok:
            doc = r.json()["documentation"]
            st.markdown(f"### {doc.get('summary', '')}")
            st.write(doc.get("detailed_description", ""))

            st.markdown("**Parameters explained:**")
            for k, v in (doc.get("parameters_explained") or {}).items():
                st.markdown(f"- `{k}`: {v}")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Sample request**")
                st.json(doc.get("sample_request"))
            with c2:
                st.markdown("**Sample response**")
                st.json(doc.get("sample_response"))

            if doc.get("usage_notes"):
                st.markdown("**Usage notes / gotchas:**")
                for n in doc["usage_notes"]:
                    st.markdown(f"- ⚠️ {n}")

            if doc.get("_generated_by"):
                st.caption(f"Source: {doc['_generated_by']}")
        else:
            st.error(r.text)

# --- Test cases tab ---
with action_tabs[1]:
    if st.button("Generate test cases", key="gen_tests"):
        with st.spinner("Generating test cases..."):
            r = requests.post(f"{BACKEND_URL}/generate-tests", json={"session_id": SID, "endpoint_index": idx})
        if r.ok:
            tests = r.json()["test_cases"]
            st.success(f"Generated {len(tests)} test case(s) — copy these into your test suite.")
            for t in tests:
                with st.expander(f"✅ {t['name']}  —  expects {t['expected_status']}"):
                    st.write(t.get("description", ""))
                    st.markdown("**Request:**")
                    st.json(t.get("request"))
                    st.markdown("**Expected response shape:**")
                    st.write(t.get("expected_response_shape"))
                    st.markdown("**Pytest snippet:**")
                    st.code(t.get("pytest_snippet", ""), language="python")
        else:
            st.error(r.text)

# --- Try it live tab ---
with action_tabs[2]:
    st.write("Point this at a **real, running** API to actually call the endpoint. Failed calls are auto-explained by the AI.")
    base_url = st.text_input("Base URL", placeholder="https://api.yourservice.com")
    path_to_call = st.text_input("Path", value=endpoint["path"])
    headers_raw = st.text_area("Headers (JSON)", value='{"Authorization": "Bearer YOUR_TOKEN"}' if endpoint.get("auth_required") else "{}")
    body_raw = st.text_area("Body (JSON, for POST/PUT/PATCH)", value=json.dumps(endpoint.get("request_body") or {}, indent=2))

    if st.button("Send request"):
        try:
            headers = json.loads(headers_raw) if headers_raw.strip() else {}
            body = json.loads(body_raw) if body_raw.strip() and endpoint["method"] in ("POST", "PUT", "PATCH") else None
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON in headers/body: {e}")
            headers, body = None, None

        if base_url and headers is not None:
            with st.spinner("Calling endpoint..."):
                r = requests.post(f"{BACKEND_URL}/try-it", json={
                    "base_url": base_url, "method": endpoint["method"], "path": path_to_call,
                    "headers": headers, "body": body, "endpoint": endpoint,
                })
            if r.ok:
                result = r.json()
                status = result.get("status_code", "—")
                if result.get("ok"):
                    st.success(f"✅ {status} — request succeeded")
                else:
                    st.error(f"❌ {status} — request failed")
                st.markdown("**Response body:**")
                st.json(result.get("response_body") or result.get("network_error"))
                if "ai_explanation" in result:
                    exp = result["ai_explanation"]
                    st.markdown("### 🤖 What this means & what to do next")
                    st.markdown(f"**Likely cause:** {exp.get('likely_cause')}")
                    st.write(exp.get("plain_english_explanation"))
                    st.markdown(f"**Suggested fix:** {exp.get('suggested_fix')}")
                    st.markdown(f"**Severity:** `{exp.get('severity')}`")
            else:
                st.error(r.text)
        elif not base_url:
            st.warning("Enter a base URL to test against.")

# --- Explain error tab ---
with action_tabs[3]:
    st.write("Paste a status code + response body you got from anywhere, and get a plain-English explanation + fix.")
    status_code = st.number_input("HTTP status code", min_value=100, max_value=599, value=422)
    payload_raw = st.text_area("Response payload (JSON or text)", value='{"error": "validation_error", "details": {"email": "is required"}}')
    if st.button("Explain this error"):
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = payload_raw
        with st.spinner("Analyzing..."):
            r = requests.post(f"{BACKEND_URL}/explain-error", json={
                "endpoint": endpoint, "status_code": int(status_code), "response_payload": payload,
            })
        if r.ok:
            exp = r.json()["explanation"]
            st.markdown(f"**Likely cause:** {exp.get('likely_cause')}")
            st.write(exp.get("plain_english_explanation"))
            st.markdown(f"**Suggested fix:** {exp.get('suggested_fix')}")
            st.markdown(f"**Severity:** `{exp.get('severity')}`")
        else:
            st.error(r.text)

st.divider()
st.caption("Session ID: " + SID + "  |  Data resets when the backend restarts (in-memory store).")
