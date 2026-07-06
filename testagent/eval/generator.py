"""Auto-generator for eval task suites.

Scans an Android app from a connected device, discovers its pages,
and generates a YAML evaluation task suite using LLM analysis.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from testagent.common.adb_utils import adb_command

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_TASKS_DIR = _PROJECT_ROOT / "evals" / "tasks"
_SKILLS_DIR = _PROJECT_ROOT / "skills" / "apps"

GENERATE_SYSTEM_PROMPT = """你是一个移动 App 测试专家。你的任务是根据 App 信息生成评测任务。

评测任务是 YAML 格式，每个任务包含：
- instruction: 自然语言指令（告诉 Agent 做什么）
- graders: 评判器配置（state_check 检查 UI 元素，llm_rubric 语义评分）
- scoring: 评分规则
- timeout: 超时时间

请根据 App 的功能点，设计 5-8 个评测任务，覆盖：
1. 核心功能（启动、导航）
2. 主要交互（搜索、播放、购买等）
3. 边缘场景（空搜索、错误处理）

返回 JSON 格式：
{
  "tasks": [
    {
      "id": "appname_feature_name",
      "description": "简短描述",
      "instruction": "给 Agent 的自然语言指令",
      "tags": ["smoke", "core"],
      "timeout": 120,
      "graders": [
        {"type": "state_check", "expect": {"elements_present": ["元素1", "元素2"]}},
        {"type": "llm_rubric", "rubric": "评分标准..."}
      ],
      "scoring": {"mode": "weighted", "pass_threshold": 0.5, "weights": {"state_check": 0.3, "llm_rubric": 0.7}}
    }
  ]
}
"""


def list_installed_packages(device_udid: str) -> list[str]:
    """List third-party packages on the connected device."""
    try:
        result = adb_command(
            device_udid, "shell", "pm", "list", "packages", "-3",
            capture_output=True, text=True, timeout=10,
        )
        return [
            line.replace("package:", "").strip()
            for line in result.stdout.split("\n")
            if line.startswith("package:")
        ]
    except Exception as e:
        raise RuntimeError(f"Failed to list packages: {e}")


async def detect_app_package(
    app_name: str, device_udid: str = "", llm_provider: Any = None
) -> str:
    """Match user's app name to an installed package using LLM.

    Same approach as plan.py's ``_detect_app_package``:
    1. List 3rd-party packages
    2. Ask LLM which one matches the user's app name
    """
    if not device_udid:
        device_udid = _detect_device()
    if not device_udid:
        raise RuntimeError("No device connected — check adb devices")

    packages = list_installed_packages(device_udid)
    if not packages:
        raise RuntimeError("No third-party packages found on device")

    # Use LLM to match app_name → package
    if llm_provider is None:
        from testagent.config.settings import get_settings
        from testagent.llm.openai_provider import OpenAIProvider
        settings = get_settings()
        llm_provider = OpenAIProvider(settings)

    package_list = "\n".join(f"  {p}" for p in packages)
    prompt = (
        f"用户说: {app_name}\n\n"
        f"设备上已安装的第三方应用包名列表:\n{package_list}\n\n"
        f"请从上面的列表中选出最匹配 '{app_name}' 的包名。\n"
        f"只返回包名本身，不要任何其他文字。"
    )

    response = await llm_provider.chat(
        system="你是一个 Android 包名匹配助手。根据用户的 App 名称，从列表中选出对应的包名。",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=100,
    )

    text = ""
    for block in response.content:
        if block.get("type") == "text":
            text += str(block.get("text", "")).strip()

    # Extract package name from response
    for p in packages:
        if p in text:
            return p
    # Fallback: return first match
    raise RuntimeError(
        f"Could not match '{app_name}' to any package.\n"
        f"LLM response: {text}\n"
        f"Available: {packages}"
    )


def _detect_device() -> str:
    """Return the first connected device serial."""
    try:
        result = adb_command("", "devices", capture_output=True, text=True, timeout=5)
        lines = [l.strip() for l in result.stdout.split("\n") if l.strip()]
        for line in lines[1:]:
            if "\tdevice" in line:
                return line.split("\t")[0]
    except Exception:
        pass
    return ""


def get_app_activity(device_udid: str, package: str) -> str:
    """Get the launcher activity for a package."""
    try:
        result = adb_command(
            device_udid, "shell", "dumpsys", "package", package,
            capture_output=True, text=True, timeout=15,
        )
        for line in result.stdout.split("\n"):
            if "android.intent.action.MAIN" in line:
                # Next line has the activity
                continue
            if f"{package}/." in line and "LAUNCHER" in line:
                parts = line.strip().split()
                for p in parts:
                    if f"{package}/" in p:
                        activity = p.split("/")[1]
                        return f".{activity}" if not activity.startswith(".") else activity
        return ".MainActivity"
    except Exception:
        return ".MainActivity"


def read_skill_context(app_name: str) -> str:
    """Read the app's SKILL.md if it exists."""
    skill_file = _SKILLS_DIR / app_name / "SKILL.md"
    if skill_file.exists():
        return skill_file.read_text(encoding="utf-8")
    return ""


async def explore_app_pages(
    device_udid: str,
    package: str,
    activity: str,
    appium_url: str,
    session_id: str,
    llm_provider: Any,
) -> list[dict]:
    """Navigate to key pages and capture their UI state."""
    from testagent.mcp_servers.appium_server.tools import (
        app_get_source, app_screenshot, app_tap, app_type_text,
    )

    pages = []

    # Helper to capture current page
    async def capture_page(name: str, description: str) -> dict:
        source = await app_get_source(appium_url=appium_url, session_id=session_id)
        scr = await app_screenshot(appium_url=appium_url, session_id=session_id)
        xml = (source or {}).get("source", "") or (source or {}).get("body", {}).get("value", "")
        return {
            "name": name,
            "description": description,
            "source_snippet": xml[:5000] if xml else "",
        }

    # 1. Landing page (app should already be open)
    pages.append(await capture_page("home", "App home page / landing page"))
    await asyncio.sleep(2)

    # 2. Try to find and tap a search button/area
    try:
        # Tap where search typically is (top of screen)
        await app_tap(x=540, y=100, appium_url=appium_url, session_id=session_id)
        await asyncio.sleep(2)
        pages.append(await capture_page("search", "Search / discovery page"))
    except Exception:
        pass

    # 3. Navigate back
    try:
        adb_command(device_udid, "shell", "input", "keyevent", "KEYCODE_BACK",
                    capture_output=True, timeout=5)
        await asyncio.sleep(2)
    except Exception:
        pass

    return pages


async def generate_tasks_with_llm(
    llm_provider: Any,
    package: str,
    app_name: str,
    skill_context: str,
    pages: list[dict],
) -> list[dict]:
    """Use LLM to analyze the app and generate eval tasks."""
    import json

    # Build context for the LLM
    context_parts = [f"App: {app_name}", f"Package: {package}"]

    if skill_context:
        # Extract key features from SKILL.md (first 3000 chars)
        context_parts.append(f"\nSkill knowledge:\n{skill_context[:3000]}")

    if pages:
        context_parts.append("\nDiscovered pages:")
        for p in pages:
            context_parts.append(f"\n--- {p['name']}: {p['description']} ---")
            if p["source_snippet"]:
                context_parts.append(p["source_snippet"][:2000])

    context = "\n".join(context_parts)

    try:
        response = await llm_provider.chat(
            system=GENERATE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
            temperature=0.3,
            max_tokens=4096,
        )

        text = ""
        for block in response.content:
            if block.get("type") == "text":
                text += str(block.get("text", ""))

        # Parse JSON — strip markdown code blocks and handle extra text
        import re as _re
        # Remove markdown code blocks
        cleaned = _re.sub(r"```(?:json)?\s*", "", text).strip()
        cleaned = cleaned.rstrip("`").strip()
        # Find outermost JSON object
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(cleaned[start:end])
            tasks = data.get("tasks", [])
            if tasks:
                return tasks
        # Fallback: try to find JSON array directly
        arr_start = cleaned.find("[")
        arr_end = cleaned.rfind("]") + 1
        if arr_start >= 0 and arr_end > arr_start:
            tasks = json.loads(cleaned[arr_start:arr_end])
            if isinstance(tasks, list):
                return tasks
        raise ValueError(f"No valid tasks found in LLM response: {cleaned[:500]}")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"LLM returned invalid JSON: {e}. Response: {text[:500]}")
    except Exception as e:
        raise RuntimeError(f"LLM task generation failed: {e}")


def write_task_files(app_name: str, tasks: list[dict]) -> Path:
    """Write generated tasks to evals/tasks/<app_name>/."""
    output_dir = _DEFAULT_TASKS_DIR / app_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write suite.yaml
    suite_yaml = {
        "suite": {
            "name": app_name,
            "description": f"{app_name} 自动生成评测套件",
            "version": "1.0.0",
            "default_trials": 3,
            "app": app_name,
            "tags": ["app", "android", "auto-generated"],
        }
    }
    (output_dir / "suite.yaml").write_text(
        yaml.dump(suite_yaml, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    # Write individual task files
    for task in tasks:
        task_id = task.get("id", "unknown")
        # Create subdirectories based on tags
        tags = task.get("tags", [])
        subdir = tags[0] if tags else "general"
        task_dir = output_dir / subdir
        task_dir.mkdir(exist_ok=True)

        task_yaml = {"task": task}
        (task_dir / f"{task_id}.yaml").write_text(
            yaml.dump(task_yaml, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

    return output_dir
