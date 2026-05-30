from __future__ import annotations

from pathlib import Path

import typer

config_app = typer.Typer(
    name="config",
    help="查看和配置 LLM / 多模态模型 API",
    no_args_is_help=True,
)

_ENV_FILE = Path.cwd() / ".env"

# ── helpers ─────────────────────────────────────────────────────────


def _get_settings() -> dict[str, str]:
    """Return effective settings as a flat key-value dict.

    Reads from ``TestAgentSettings`` (which merges env vars + .env +
    ``configs/vision_config.json``), then overlays raw .env values so
    the user sees both the effective config AND what's stored in .env.
    """
    from testagent.config.settings import get_settings

    s = get_settings()
    raw: dict[str, str] = {}
    for field in s.__class__.model_fields:
        val = getattr(s, field)
        if isinstance(val, str):
            raw[f"TESTAGENT_{field.upper()}"] = val
        else:
            # SecretStr – extract value
            try:
                raw[f"TESTAGENT_{field.upper()}"] = val.get_secret_value()  # type: ignore[union-attr]
            except Exception:
                raw[f"TESTAGENT_{field.upper()}"] = ""
    return raw


def _read_env() -> dict[str, str]:
    """Read raw .env file into a dict."""
    env: dict[str, str] = {}
    if not _ENV_FILE.exists():
        return env
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        env[key.strip()] = val.strip()
    return env


def _write_env(updates: dict[str, str]) -> None:
    """Merge updates into .env, preserving other keys and comments."""
    existing = _read_env()
    existing.update(updates)
    lines: list[str] = []
    written: set[str] = set()
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                lines.append(f"{key}={updates[key]}")
                written.add(key)
            else:
                lines.append(line)
    for key, val in updates.items():
        if key not in written:
            lines.append(f"{key}={val}")
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


_ENV_KEY_MAP = {
    "llm_provider": "TESTAGENT_LLM_PROVIDER",
    "openai_api_key": "TESTAGENT_OPENAI_API_KEY",
    "openai_base_url": "TESTAGENT_OPENAI_BASE_URL",
    "openai_model": "TESTAGENT_OPENAI_MODEL",
    "vision_api_key": "TESTAGENT_VISION_API_KEY",
    "vision_api_url": "TESTAGENT_VISION_API_URL",
    "vision_model": "TESTAGENT_VISION_MODEL",
}

_DISPLAY_LABELS = {
    "llm_provider": "Provider",
    "openai_api_key": "API Key",
    "openai_base_url": "Base URL",
    "openai_model": "Model",
    "vision_api_key": "API Key",
    "vision_api_url": "API URL",
    "vision_model": "Model",
}

_KEY_GROUP = {
    "LLM": ["llm_provider", "openai_api_key", "openai_base_url", "openai_model"],
    "Vision / 多模态": ["vision_api_key", "vision_api_url", "vision_model"],
}


def _mask(val: str, field: str, show_secrets: bool) -> str:
    if not val:
        return "<未设置>"
    if not show_secrets and "api_key" in field:
        return val[:8] + "***" if len(val) > 8 else "***"
    return val


# ── show ────────────────────────────────────────────────────────────


@config_app.command()
def show(
    show_secrets: bool = typer.Option(
        False, "--show-secrets", "-s", help="显示完整的 API Key（默认隐藏）"
    ),
) -> None:
    """显示当前生效的 LLM 和 Vision API 配置。"""
    effective = _get_settings()

    for group_name, fields in _KEY_GROUP.items():
        typer.echo(f"── {group_name} 配置 ──────────────────────────────")
        for f in fields:
            env_key = _ENV_KEY_MAP[f]
            val = effective.get(env_key, "")
            typer.echo(f"  {_DISPLAY_LABELS[f]}:     {_mask(val, f, show_secrets)}")
        typer.echo("")

    typer.echo(f"  配置文件: {_ENV_FILE}")


# ── set (interactive) ───────────────────────────────────────────────


@config_app.command()
def set(
    llm_provider: str | None = typer.Option(
        None, "--llm-provider", help="LLM provider (openai / local)"
    ),
    llm_api_key: str | None = typer.Option(
        None, "--llm-api-key", help="LLM API Key"
    ),
    llm_base_url: str | None = typer.Option(
        None, "--llm-base-url", help="LLM Base URL"
    ),
    llm_model: str | None = typer.Option(
        None, "--llm-model", help="LLM 模型名称"
    ),
    vision_api_key: str | None = typer.Option(
        None, "--vision-api-key", help="Vision / 多模态 API Key"
    ),
    vision_api_url: str | None = typer.Option(
        None, "--vision-base-url", help="Vision API Base URL"
    ),
    vision_model: str | None = typer.Option(
        None, "--vision-model", help="Vision 模型名称"
    ),
) -> None:
    """配置 LLM 和 Vision 多模态模型的 API 信息。

    如果提供了任何 -- 选项，则以非交互模式只更新对应字段。
    否则进入交互式向导，逐一提示输入。

    设置的环境变量写入 .env 文件，Vision 的 URL 和 Model 也可通过
    ``configs/vision_config.json`` 配置。
    """
    effective = _get_settings()
    env_raw = _read_env()
    updates: dict[str, str] = {}

    # ── 非交互模式：只更新命令行指定的字段 ──────────
    has_flags = any([
        llm_provider, llm_api_key, llm_base_url, llm_model,
        vision_api_key, vision_api_url, vision_model,
    ])

    if has_flags:
        if llm_provider is not None:
            updates["TESTAGENT_LLM_PROVIDER"] = llm_provider
        if llm_api_key is not None:
            updates["TESTAGENT_OPENAI_API_KEY"] = llm_api_key
        if llm_base_url is not None:
            updates["TESTAGENT_OPENAI_BASE_URL"] = llm_base_url
        if llm_model is not None:
            updates["TESTAGENT_OPENAI_MODEL"] = llm_model
        if vision_api_key is not None:
            updates["TESTAGENT_VISION_API_KEY"] = vision_api_key
        if vision_api_url is not None:
            updates["TESTAGENT_VISION_API_URL"] = vision_api_url
        if vision_model is not None:
            updates["TESTAGENT_VISION_MODEL"] = vision_model
    else:
        # ── 交互模式 ─────────────────────────────────
        typer.echo("配置 LLM 和 Vision 多模态模型 API")
        typer.echo("（直接回车表示保留当前值）")
        typer.echo("")

        # Helper: get current effective value, with a display default
        def _current(field: str, fallback: str = "") -> str:
            env_key = _ENV_KEY_MAP[field]
            val = effective.get(env_key, "")
            return val if val else fallback

        # LLM
        typer.echo("── LLM 配置 ──")
        val = typer.prompt(
            "  Provider (openai / local)",
            default=_current("llm_provider", "openai"),
            show_default=False,
        )
        updates["TESTAGENT_LLM_PROVIDER"] = val

        current_key = _current("openai_api_key")
        val = typer.prompt("  API Key", default=current_key or "", show_default=False, hide_input=True)
        if val:
            updates["TESTAGENT_OPENAI_API_KEY"] = val

        val = typer.prompt(
            "  Base URL",
            default=_current("openai_base_url", "https://api.deepseek.com"),
            show_default=False,
        )
        updates["TESTAGENT_OPENAI_BASE_URL"] = val

        val = typer.prompt(
            "  Model",
            default=_current("openai_model", "deepseek-v4-flash"),
            show_default=False,
        )
        updates["TESTAGENT_OPENAI_MODEL"] = val

        typer.echo("")
        typer.echo("── Vision / 多模态配置 ──")
        current_key = _current("vision_api_key")
        val = typer.prompt("  API Key", default=current_key or "", show_default=False, hide_input=True)
        if val:
            updates["TESTAGENT_VISION_API_KEY"] = val

        val = typer.prompt(
            "  Base URL",
            default=_current("vision_api_url", "https://ark.cn-beijing.volces.com/api/v3"),
            show_default=False,
        )
        updates["TESTAGENT_VISION_API_URL"] = val

        val = typer.prompt(
            "  Model",
            default=_current("vision_model", "doubao-seed-2-0-lite-260428"),
            show_default=False,
        )
        updates["TESTAGENT_VISION_MODEL"] = val

    _write_env(updates)

    # ── 重置全局 settings 缓存，使配置立即生效 ──────
    from testagent.config.settings import reset_settings

    reset_settings()

    typer.echo("")
    typer.echo("✅ 配置已保存到 .env")
    ctx = typer.Context(config_app)
    ctx.invoke(show, show_secrets=False)
