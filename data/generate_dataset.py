"""
Generates data/endpoints.json — the starter dataset of sample API endpoint
definitions used by the portal (parser input + demo data).

Run: python generate_dataset.py
"""
import json

RESOURCES = [
    "users", "products", "orders", "payments", "invoices", "categories",
    "reviews", "carts", "addresses", "notifications", "auth", "wishlists",
    "coupons", "shipments", "suppliers", "warehouses", "tickets", "messages",
    "subscriptions", "reports",
]

ERROR_CODES_COMMON = [
    {"code": 400, "meaning": "Bad Request - validation failed on input fields"},
    {"code": 401, "meaning": "Unauthorized - missing or invalid auth token"},
    {"code": 403, "meaning": "Forbidden - caller lacks permission for this resource"},
    {"code": 404, "meaning": "Not Found - resource id does not exist"},
    {"code": 409, "meaning": "Conflict - duplicate resource or state conflict"},
    {"code": 422, "meaning": "Unprocessable Entity - semantic validation error"},
    {"code": 429, "meaning": "Too Many Requests - rate limit exceeded"},
    {"code": 500, "meaning": "Internal Server Error - unexpected server failure"},
]


def field_block(resource):
    return {
        "id": "string (uuid)",
        "name": "string",
        "created_at": "string (ISO 8601 datetime)",
        "updated_at": "string (ISO 8601 datetime)",
        "status": "string (enum: active|inactive|pending)",
    }


def build_endpoints():
    endpoints = []
    for resource in RESOURCES:
        singular = resource[:-1] if resource.endswith("s") else resource

        # 1. GET list
        endpoints.append({
            "method": "GET",
            "path": f"/api/v1/{resource}",
            "description": f"List all {resource}, supports pagination and filtering.",
            "request_body": None,
            "query_params": {"page": "integer (default 1)", "limit": "integer (default 20)", "sort": "string (optional)"},
            "response_body": {"items": [field_block(resource)], "page": "integer", "total": "integer"},
            "auth_required": resource not in ["categories"],
            "error_codes": [400, 401, 429, 500],
        })

        # 2. POST create
        endpoints.append({
            "method": "POST",
            "path": f"/api/v1/{resource}",
            "description": f"Create a new {singular} record.",
            "request_body": {"name": "string (required)", "metadata": "object (optional)"},
            "query_params": {},
            "response_body": field_block(resource),
            "auth_required": True,
            "error_codes": [400, 401, 403, 409, 422, 500],
        })

        # 3. GET by id
        endpoints.append({
            "method": "GET",
            "path": f"/api/v1/{resource}/{{{singular}_id}}",
            "description": f"Retrieve a single {singular} by its unique id.",
            "request_body": None,
            "query_params": {},
            "response_body": field_block(resource),
            "auth_required": resource not in ["categories"],
            "error_codes": [401, 404, 500],
        })

        # 4. PUT update
        endpoints.append({
            "method": "PUT",
            "path": f"/api/v1/{resource}/{{{singular}_id}}",
            "description": f"Update an existing {singular} record (full replace).",
            "request_body": {"name": "string (required)", "status": "string (enum: active|inactive|pending)"},
            "query_params": {},
            "response_body": field_block(resource),
            "auth_required": True,
            "error_codes": [400, 401, 403, 404, 422, 500],
        })

        # 5. DELETE
        endpoints.append({
            "method": "DELETE",
            "path": f"/api/v1/{resource}/{{{singular}_id}}",
            "description": f"Delete a {singular} record permanently.",
            "request_body": None,
            "query_params": {},
            "response_body": {"deleted": "boolean", "id": "string (uuid)"},
            "auth_required": True,
            "error_codes": [401, 403, 404, 409, 500],
        })

    # Add explicit error_codes meaning lookup as separate section
    return endpoints


def main():
    endpoints = build_endpoints()
    out = {
        "error_code_reference": ERROR_CODES_COMMON,
        "endpoints": endpoints,
    }
    with open("endpoints.json", "w") as f:
        json.dump(out, f, indent=2)
    print(f"Generated {len(endpoints)} endpoints -> endpoints.json")


if __name__ == "__main__":
    main()
