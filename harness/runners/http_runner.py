from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from testagent.harness.sandbox import ISandbox

import httpx

from testagent.common.logging import get_logger
from testagent.harness.runners.base import BaseRunner, RunnerError

if TYPE_CHECKING:
    from testagent.models.result import TestResult

try:
    import jsonschema  # noqa: F401

    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    HAS_JSONSCHEMA = False

logger = get_logger(__name__)

HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class HTTPRunner(BaseRunner):
    runner_type = "api_test"

    def __init__(self) -> None:
        super().__init__()
        self._client: httpx.AsyncClient | None = None
        self._base_url: str = ""
        self._request_log: list[dict[str, object]] = []
        self._docker_result: dict[str, object] | None = None

    async def setup(
        self,
        config: dict[str, object],
        sandbox: ISandbox | None = None,
        sandbox_id: str | None = None,
    ) -> None:
        await super().setup(config, sandbox=sandbox, sandbox_id=sandbox_id)
        self._validate_config(config, ["base_url"])
        base_url = config["base_url"]
        if not isinstance(base_url, str):
            raise RunnerError("base_url must be a string", code="INVALID_CONFIG")
        self._base_url = base_url.rstrip("/")

        if self._in_docker_mode:
            return

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
        if self._in_docker_mode:
            return await self._execute_docker(test_script)
        return await self._execute_local(test_script)

    async def _execute_local(self, test_script: str) -> TestResult:
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

    async def _execute_docker(self, test_script: str) -> TestResult:
        script_content = self._generate_docker_exec_script(test_script)
        container_path = await self._write_script(script_content)

        from testagent.harness.sandbox import RESOURCE_PROFILES

        profile = RESOURCE_PROFILES.get("api_test")
        timeout = profile.timeout if profile else 60

        start_ms = self._now_ms()
        output = await self._run_in_sandbox(f"python3 {container_path}", timeout=timeout)
        duration_ms = self._now_ms() - start_ms

        return self._parse_docker_output(output, duration_ms=duration_ms)

    async def teardown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("HTTPRunner teardown complete")

    async def collect_results(self) -> TestResult:
        if self._docker_result is not None:
            status = self._docker_result.get("status", "passed")
            assertion_results = cast("dict[str, object]", self._docker_result.get("assertion_results", {}))
            logs = str(self._docker_result.get("logs", ""))
            artifacts = cast("dict[str, object]", self._docker_result.get("artifacts", {}))
            return self._make_result(
                status=str(status),
                logs=logs,
                assertion_results=assertion_results,
                artifacts=artifacts,
            )
        combined_logs = json.dumps(self._request_log, ensure_ascii=False) if self._request_log else ""
        return self._make_result(
            status="passed",
            logs=combined_logs,
            artifacts={"total_requests": len(self._request_log)},
        )

    def _generate_docker_exec_script(self, test_script: str) -> str:
        script = self._parse_script(test_script)
        method = script.get("method", "GET").upper()
        path = script.get("path", "/")
        headers = script.get("headers", {})
        body = script.get("body")
        assertions = script.get("assertions", {})

        body_json = json.dumps(body) if body is not None else "None"
        headers_json = json.dumps(headers)
        assertions_json = json.dumps(assertions)

        return f"""import json, sys, traceback
import httpx

base_url = {self._base_url!r}
method = {method!r}
path = {path!r}
headers = {headers_json}
body = {body_json}
assertions = {assertions_json}

result = {{"logs": "", "assertion_results": {{}}, "artifacts": {{}}}}

try:
    client = httpx.Client(base_url=base_url, timeout=30, follow_redirects=True)
    response = client.request(method, path, headers=headers, json=body)

    entry = {{
        "method": method,
        "path": path,
        "status_code": response.status_code,
    }}
    result["logs"] = json.dumps(entry)

    content_type = response.headers.get("content-type", "")
    response_body = None
    if "application/json" in content_type:
        try:
            response_body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            response_body = response.text
    else:
        response_body = response.text

    all_passed = True
    assertion_results = {{}}

    if "status_code" in assertions:
        expected = assertions["status_code"]
        passed = response.status_code == expected
        assertion_results["status_code"] = {{"expected": expected, "actual": response.status_code, "passed": passed}}
        if not passed:
            all_passed = False

    if "status_code_in" in assertions:
        expected_range = assertions["status_code_in"]
        passed = response.status_code in expected_range
        si_result = {{"expected": expected_range, "actual": response.status_code, "passed": passed}}
        assertion_results["status_code_in"] = si_result
        if not passed:
            all_passed = False

    if "headers" in assertions:
        header_results = {{}}
        for key, expected_val in assertions["headers"].items():
            actual = response.headers.get(key)
            passed = actual == expected_val
            header_results[key] = {{"expected": expected_val, "actual": actual, "passed": passed}}
            if not passed:
                all_passed = False
        assertion_results["headers"] = header_results

    if "json_path" in assertions and response_body is not None:
        path_results = {{}}
        for jp, expected_val in assertions["json_path"].items():
            parts = jp.split(".")
            current = response_body
            for part in parts:
                if isinstance(current, dict):
                    current = current.get(part)
                elif isinstance(current, list) and part.isdigit():
                    idx = int(part)
                    current = current[idx] if 0 <= idx < len(current) else None
                else:
                    current = None
                    break
            actual = current
            passed = actual == expected_val
            path_results[jp] = {{"expected": expected_val, "actual": actual, "passed": passed}}
            if not passed:
                all_passed = False
        assertion_results["json_path"] = path_results

    if not assertions:
        assertion_results["executed"] = {{"passed": True, "info": "No assertions defined"}}

    result["assertion_results"] = assertion_results
    result["status"] = "passed" if all_passed else "failed"
    result["artifacts"] = {{"status_code": response.status_code}}

except Exception as e:
    result["status"] = "error"
    result["assertion_results"] = {{"error": str(e)}}
    result["logs"] = traceback.format_exc()

print(json.dumps(result))
"""

    def _parse_docker_output(
        self,
        output: dict[str, object],
        *,
        duration_ms: float = 0.0,
    ) -> TestResult:
        stdout = str(output.get("stdout", "")).strip()
        stderr = str(output.get("stderr", "")).strip()
        exit_code = output.get("exit_code", 0)
        if isinstance(exit_code, int) and exit_code != 0:
            return self._make_result(
                status="error",
                duration_ms=round(duration_ms, 2),
                logs=stderr or f"Non-zero exit code: {exit_code}",
                assertion_results={"error": stderr or f"exit_code={exit_code}"},
            )

        try:
            data = json.loads(stdout)
            self._docker_result = data
            status = str(data.get("status", "passed"))
            assertion_results = data.get("assertion_results", {})
            logs = str(data.get("logs", ""))
            artifacts = data.get("artifacts", {})
            return self._make_result(
                status=status,
                duration_ms=round(duration_ms, 2),
                assertion_results=assertion_results if isinstance(assertion_results, dict) else {},
                logs=logs,
                artifacts=artifacts if isinstance(artifacts, dict) else {},
            )
        except (json.JSONDecodeError, ValueError) as e:
            return self._make_result(
                status="error",
                duration_ms=round(duration_ms, 2),
                logs=stdout,
                assertion_results={"error": f"Failed to parse Docker output: {e}"},
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
            all_passed = self._validate_json_path(response_body, assertions["json_path"], results) and all_passed

        if "json_schema" in assertions and HAS_JSONSCHEMA:
            all_passed = self._validate_json_schema(response_body, assertions["json_schema"], results) and all_passed

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
