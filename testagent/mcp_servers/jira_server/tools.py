from __future__ import annotations

from typing import Any

import httpx


async def jira_create_issue(
    base_url: str,
    auth_token: str,
    project_key: str,
    summary: str,
    issuetype: str = "Task",
    description: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    custom_fields: dict[str, object] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    fields: dict[str, object] = {
        "project": {"key": project_key},
        "summary": summary,
        "issuetype": {"name": issuetype},
    }
    if description is not None:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }
    if priority is not None:
        fields["priority"] = {"name": priority}
    if assignee is not None:
        fields["assignee"] = {"id": assignee}
    if labels is not None:
        fields["labels"] = labels
    if custom_fields is not None:
        for key, value in custom_fields.items():
            fields[key] = value

    payload: dict[str, object] = {"fields": fields}
    url = f"{base_url.rstrip('/')}/rest/api/2/issue"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.post(url, headers=headers, json=payload)

    if response.status_code not in (200, 201):
        error_body = _extract_error(response)
        return {"error": f"Jira API error ({response.status_code}): {error_body}"}

    data: dict[str, Any] = response.json()
    return {
        "id": data.get("id", ""),
        "key": data.get("key", ""),
        "self": data.get("self", ""),
        "project_key": project_key,
        "summary": summary,
        "issuetype": issuetype,
    }


async def jira_search_issues(
    base_url: str,
    auth_token: str,
    jql: str,
    max_results: int = 50,
    fields: list[str] | None = None,
    start_at: int = 0,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    params: dict[str, str | int | list[str]] = {
        "jql": jql,
        "maxResults": max_results,
        "startAt": start_at,
    }
    if fields is not None:
        params["fields"] = fields

    url = f"{base_url.rstrip('/')}/rest/api/2/search"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.get(url, headers=headers, params=params)

    if response.status_code != 200:
        error_body = _extract_error(response)
        return {"error": f"Jira API error ({response.status_code}): {error_body}"}

    data: dict[str, Any] = response.json()
    return {
        "total": data.get("total", 0),
        "start_at": data.get("startAt", 0),
        "max_results": data.get("maxResults", 0),
        "issues": [
            {
                "id": issue.get("id", ""),
                "key": issue.get("key", ""),
                "self": issue.get("self", ""),
                "fields": issue.get("fields", {}),
            }
            for issue in data.get("issues", [])
        ],
    }


async def jira_update_issue(
    base_url: str,
    auth_token: str,
    issue_key: str,
    summary: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee: str | None = None,
    labels: list[str] | None = None,
    custom_fields: dict[str, object] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    fields: dict[str, object] = {}
    if summary is not None:
        fields["summary"] = summary
    if description is not None:
        fields["description"] = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}],
                }
            ],
        }
    if priority is not None:
        fields["priority"] = {"name": priority}
    if assignee is not None:
        fields["assignee"] = {"id": assignee}
    if labels is not None:
        fields["labels"] = labels
    if custom_fields is not None:
        for key, value in custom_fields.items():
            fields[key] = value

    payload: dict[str, object] = {"fields": fields} if fields else {}

    url = f"{base_url.rstrip('/')}/rest/api/2/issue/{issue_key}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        response = await client.put(url, headers=headers, json=payload)

    if response.status_code not in (200, 204):
        error_body = _extract_error(response)
        return {"error": f"Jira API error ({response.status_code}): {error_body}"}

    transition_result: dict[str, Any] = {}
    if status is not None:
        transition_result = await _transition_issue(base_url, auth_token, issue_key, status)

    result: dict[str, Any] = {
        "success": True,
        "issue_key": issue_key,
    }
    if transition_result:
        result["transition"] = transition_result
    return result


async def _transition_issue(
    base_url: str,
    auth_token: str,
    issue_key: str,
    status_name: str,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    transitions_url = f"{base_url.rstrip('/')}/rest/api/2/issue/{issue_key}/transitions"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        trans_response = await client.get(transitions_url, headers=headers)

    if trans_response.status_code != 200:
        return {"error": f"Could not fetch transitions: {trans_response.status_code}"}

    transitions_data: dict[str, Any] = trans_response.json()
    target_transition: dict[str, Any] | None = None
    for t in transitions_data.get("transitions", []):
        if t.get("to", {}).get("name", "").lower() == status_name.lower():
            target_transition = t
            break

    if target_transition is None:
        return {"error": f"No transition found to status '{status_name}'"}

    transition_payload: dict[str, object] = {"transition": {"id": target_transition["id"]}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        post_response = await client.post(transitions_url, headers=headers, json=transition_payload)

    if post_response.status_code not in (200, 204):
        error_body = _extract_error(post_response)
        return {"error": f"Transition failed ({post_response.status_code}): {error_body}"}

    return {"status": status_name, "transition_id": target_transition["id"]}


def _extract_error(response: httpx.Response) -> str:
    try:
        body: dict[str, Any] = response.json()
        messages = body.get("errorMessages", [])
        errors = body.get("errors", {})
        parts: list[str] = []
        if messages:
            parts.extend(str(m) for m in messages)
        if errors:
            parts.extend(f"{k}: {v}" for k, v in errors.items())
        return "; ".join(parts) if parts else response.text
    except Exception:
        return response.text[:500]
