from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import httpx

from testagent.common.logging import get_logger
from testagent.harness.runners.base import BaseRunner, RunnerError

if TYPE_CHECKING:
    from testagent.models.result import TestResult

try:
    import jsonschema  # type: ignore[import-untyped]  # noqa: F401

    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    HAS_JSONSCHEMA = False

logger = get_logger(__name__)

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class HTTPRunner(BaseRunner):
    runner_type = "api_test"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._base_url: str = ""
        self._request_log: list[dict[str, object]] = []

    async def setup(self, config: dict[str, object]) -> None:
        self._validate_config(config, ["base_url"])
        base_url = config["base_url"]
        if not isinstance(base_url, str):
            raise RunnerError("base_url must be a string", code="INVALID_CONFIG")
        self._base_url = base_url.rstrip("/")
        timeout_val = config.get("timeout", 30)
        timeout: float = timeout_val if isinstance(timeout_val, (int, float)) else 30.0
        raw_headers = config.get("headers", {})
        headers: dict[str, str] = dict(cast("dict[str, str]", raw_headers)) if isinstance(raw_headers, dict) else {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        )
        logger.info("HTTPRunner setup", extra={"base_url": self._base_url, "timeout": timeout})

    async def execute(self, test_script: str) -> TestResult:
        if self._client is None:
            raise RunnerError("Runner not setup, call setup() first", code="RUNNER_NOT_SETUP")

        script = self._parse_script(test_script)
        method = script.get("method", "GET").upper()
        path = script.get("path", "/")
        headers = dict(script.get("headers", {}))
        body = script.get("body")
        assertions = script.get("assertions", {})

        if method not in HTTP_METHODS:
            raise RunnerError(f"Invalid HTTP method: {method}", code="INVALID_METHOD")

        start_ms = self._now_ms()

        try:
            response = await self._client.request(method, path, headers=headers, json=body)
            duration_ms = self._now_ms() - start_ms
            response_body = self._parse_response_body(response)

            log_entry: dict[str, object] = {
                "method": method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            }
            self._request_log.append(log_entry)

            assertion_results: dict[str, object] = {}
            all_passed = self._validate_assertions(response, response_body, assertions, assertion_results)

            status = "passed" if all_passed else "failed"
            return self._make_result(
                status=status,
                duration_ms=round(duration_ms, 2),
                assertion_results=assertion_results,
                logs=json.dumps(log_entry, ensure_ascii=False),
            )

        except httpx.TimeoutException:
            duration_ms = self._now_ms() - start_ms
            return self._make_result(
                status="failed",
                duration_ms=round(duration_ms, 2),
                assertion_results={"error": "Request timed out"},
                logs="Request timed out",
            )
        except httpx.HTTPError as e:
            duration_ms = self._now_ms() - start_ms
            return self._make_result(
                status="error",
                duration_ms=round(duration_ms, 2),
                assertion_results={"error": str(e)},
                logs=f"HTTP error: {e}",
            )

    async def teardown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("HTTPRunner teardown complete")

    async def collect_results(self) -> TestResult:
        combined_logs = json.dumps(self._request_log, ensure_ascii=False) if self._request_log else ""
        return self._make_result(
            status="passed",
            logs=combined_logs,
            artifacts={"total_requests": len(self._request_log)},
        )

    def _parse_script(self, test_script: str) -> dict[str, Any]:
        try:
            script = json.loads(test_script)
            if not isinstance(script, dict):
                raise RunnerError("Test script must be a JSON object", code="INVALID_SCRIPT")
            return script
        except json.JSONDecodeError as e:
            raise RunnerError(f"Invalid JSON test script: {e}", code="INVALID_SCRIPT") from e

    def _parse_response_body(self, response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                return response.json()
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
        return response.text

    def _validate_assertions(
        self,
        response: httpx.Response,
        response_body: Any,
        assertions: dict[str, Any],
        results: dict[str, object],
    ) -> bool:
        all_passed = True

        if "status_code" in assertions:
            expected = assertions["status_code"]
            passed = response.status_code == expected
            results["status_code"] = {"expected": expected, "actual": response.status_code, "passed": passed}
            if not passed:
                all_passed = False

        if "status_code_in" in assertions:
            expected_range = assertions["status_code_in"]
            passed = response.status_code in expected_range
            results["status_code_in"] = {"expected": expected_range, "actual": response.status_code, "passed": passed}
            if not passed:
                all_passed = False

        if "headers" in assertions:
            header_results: dict[str, object] = {}
            for key, expected in assertions["headers"].items():
                actual = response.headers.get(key)
                passed = actual == expected
                header_results[key] = {"expected": expected, "actual": actual, "passed": passed}
                if not passed:
                    all_passed = False
            results["headers"] = header_results

        if "json_path" in assertions:
            all_passed = self._validate_json_path(
                response_body, assertions["json_path"], results
            ) and all_passed

        if "json_schema" in assertions and HAS_JSONSCHEMA:
            all_passed = self._validate_json_schema(
                response_body, assertions["json_schema"], results
            ) and all_passed

        if "json_schema" in assertions and not HAS_JSONSCHEMA:
            results["json_schema"] = {
                "passed": False,
                "error": "jsonschema library not installed, run: pip install jsonschema",
            }
            all_passed = False

        if not assertions:
            results["executed"] = {"passed": True, "info": "No assertions defined"}

        return all_passed

    def _validate_json_path(
        self,
        response_body: Any,
        json_path_assertions: dict[str, Any],
        results: dict[str, object],
    ) -> bool:
        all_passed = True
        path_results: dict[str, object] = {}
        for path, expected in json_path_assertions.items():
            actual = self._resolve_json_path(response_body, path)
            passed = actual == expected
            path_results[path] = {"expected": expected, "actual": actual, "passed": passed}
            if not passed:
                all_passed = False
        results["json_path"] = path_results
        return all_passed

    def _resolve_json_path(self, data: Any, path: str) -> Any:
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            else:
                return None
        return current

    def _validate_json_schema(
        self,
        response_body: Any,
        schema: dict[str, Any],
        results: dict[str, object],
    ) -> bool:
        try:
            _js = __import__("jsonschema")
            _js.validate(instance=response_body, schema=schema)
            results["json_schema"] = {"passed": True}
            return True
        except _js.exceptions.ValidationError as e:
            results["json_schema"] = {"passed": False, "error": str(e)}
            return False
        except ImportError:
            results["json_schema"] = {"passed": False, "error": "jsonschema library not installed"}
            return False
