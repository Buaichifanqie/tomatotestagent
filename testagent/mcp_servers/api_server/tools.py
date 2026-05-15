from __future__ import annotations

import re
import time
from typing import Any

import httpx


async def api_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=body,
        )
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    try:
        response_body = response.json()
    except Exception:
        response_body = response.text

    return {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": response_body,
        "duration_ms": duration_ms,
    }


def _validate_type(value: object, expected_type: str) -> bool:
    type_map: dict[str, Any] = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }
    py_type = type_map.get(expected_type)
    if py_type is None:
        return True
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, py_type)


def _validate_schema_node(
    instance: object,
    schema: dict[str, object],
    path: str = "$",
) -> list[str]:
    errors: list[str] = []

    schema_type = schema.get("type")
    if isinstance(schema_type, str) and not _validate_type(instance, schema_type):
        errors.append(f"{path}: expected type '{schema_type}', got '{type(instance).__name__}'")
        return errors

    if isinstance(instance, dict):
        req = schema.get("required", [])
        required = [str(k) for k in req] if isinstance(req, list) else []
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required field '{key}'")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, value in instance.items():
                prop_schema = properties.get(key)
                if isinstance(prop_schema, dict):
                    child_path = f"{path}.{key}"
                    errors.extend(_validate_schema_node(value, prop_schema, child_path))

        pattern_props = schema.get("patternProperties")
        if isinstance(pattern_props, dict):
            for key, value in instance.items():
                for pattern, prop_schema in pattern_props.items():
                    try:
                        if re.match(str(pattern), key):
                            child_path = f"{path}.{key}"
                            errors.extend(_validate_schema_node(value, prop_schema, child_path))
                    except re.error:
                        pass

    if isinstance(instance, (str, int, float)):
        if isinstance(instance, str):
            min_len = schema.get("minLength")
            max_len = schema.get("maxLength")
            if isinstance(min_len, int) and len(instance) < min_len:
                errors.append(f"{path}: string length {len(instance)} < minLength {min_len}")
            if isinstance(max_len, int) and len(instance) > max_len:
                errors.append(f"{path}: string length {len(instance)} > maxLength {max_len}")
            pattern = schema.get("pattern")
            if isinstance(pattern, str):
                try:
                    if not re.match(pattern, instance):
                        errors.append(f"{path}: string '{instance}' does not match pattern '{pattern}'")
                except re.error:
                    pass

        if isinstance(instance, (int, float)) and not isinstance(instance, bool):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if isinstance(minimum, (int, float)) and instance < minimum:
                errors.append(f"{path}: value {instance} < minimum {minimum}")
            if isinstance(maximum, (int, float)) and instance > maximum:
                errors.append(f"{path}: value {instance} > maximum {maximum}")

    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and instance not in enum_vals:
        errors.append(f"{path}: value '{instance}' not in enum {enum_vals}")

    if isinstance(instance, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, item in enumerate(instance):
                child_path = f"{path}[{idx}]"
                errors.extend(_validate_schema_node(item, items_schema, child_path))

    return errors


async def api_validate_schema(
    response_body: dict[str, object],
    schema: dict[str, object] | None = None,
    schema_url: str | None = None,
) -> dict[str, Any]:
    if schema is not None:
        errors = _validate_schema_node(response_body, schema)
        return {"valid": len(errors) == 0, "errors": errors}

    if schema_url is not None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
            response = await client.get(schema_url)
            response.raise_for_status()
            fetched_schema: dict[str, object] = response.json()
            errors = _validate_schema_node(response_body, fetched_schema)
            return {"valid": len(errors) == 0, "errors": errors}

    return {"valid": True, "errors": []}


def _deep_compare(
    a: object,
    b: object,
    ignore_fields: set[str],
    path: str = "$",
) -> list[str]:
    diffs: list[str] = []

    if type(a) is not type(b):
        diffs.append(f"{path}: type mismatch ({type(a).__name__} vs {type(b).__name__})")
        return diffs

    if isinstance(a, dict) and isinstance(b, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}"
            if child_path in ignore_fields:
                continue
            if key not in a:
                diffs.append(f"{child_path}: missing in response_a")
            elif key not in b:
                diffs.append(f"{child_path}: missing in response_b")
            else:
                diffs.extend(_deep_compare(a[key], b[key], ignore_fields, child_path))
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: length mismatch ({len(a)} vs {len(b)})")
        for idx in range(min(len(a), len(b))):
            child_path = f"{path}[{idx}]"
            diffs.extend(_deep_compare(a[idx], b[idx], ignore_fields, child_path))
    elif a != b:
        diffs.append(f"{path}: value mismatch ({a!r} vs {b!r})")

    return diffs


async def api_compare_response(
    response_a: dict[str, object],
    response_b: dict[str, object],
    ignore_fields: list[str] | None = None,
) -> dict[str, Any]:
    ignore_set: set[str] = set()
    for f in ignore_fields or []:
        if f.startswith("$."):
            ignore_set.add(f)
        elif f.startswith("$"):
            ignore_set.add("$." + f[1:])
        else:
            ignore_set.add("$." + f)
    diffs = _deep_compare(response_a, response_b, ignore_set)
    return {"match": len(diffs) == 0, "diff_fields": diffs}
