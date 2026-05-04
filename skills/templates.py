from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "api_test": """---
name: api_smoke_test
version: "1.0.0"
description: API smoke test skill
trigger: "api.*smoke"
required_mcp_servers:
  - api_server
required_rag_collections:
  - api_docs
---

## Objective

Verify that the core API endpoints are functional and respond correctly.

## Flow

1. Send a GET request to the health endpoint
2. Assert the response status is 200
3. Send a GET request to the list endpoint
4. Assert the response status is 200 and body is a list

## Assertion Strategy

- HTTP status code must be 2xx
- Response body must be valid JSON
- Response time must be under 5s

## Failure Handling

- If health check fails, mark all subsequent tasks as skipped
- Log the full request and response for debugging
""",
    "web_test": """---
name: web_smoke_test
version: "1.0.0"
description: Web smoke test skill
trigger: "web.*smoke"
required_mcp_servers:
  - playwright_server
required_rag_collections:
  - req_docs
  - locator_library
---

## Objective

Verify that the core web pages load and render correctly.

## Flow

1. Navigate to the target URL
2. Wait for the page to fully load
3. Verify the page title is correct
4. Check that all key elements are visible

## Assertion Strategy

- Page title must match expected value
- All key UI elements must be present and visible
- No JavaScript console errors

## Failure Handling

- Take a screenshot on failure
- Capture the browser console logs
- Retry once after a 2s delay
""",
}
