from __future__ import annotations

import sys

import typer

app_typer = typer.Typer(name="app", help="多平台测试命令集（Android / Web / API）")

# ── tpilot: Android App 测试子命令组 ──
tpilot_typer = typer.Typer(
    name="tpilot",
    help="Android App 自动化测试：AI 生成测试用例、执行生成报告、重跑失败用例",
)
app_typer.add_typer(tpilot_typer)


def _interactive_help() -> None:
    """交互式帮助：用上下键选择选项，回车查看详情，q 退出。"""
    import msvcrt

    options = [
        ("requirement",   "产品需求文档路径 或 自然语言需求描述"),
        ("--name/-n",     "自定义计划名称"),
        ("--app-package", "App package name（如 com.example.app）"),
        ("--app-activity","App launch activity"),
        ("--app-id",      "App 标识（如 com.example.app），默认使用 app-package"),
        ("--auto-yes/-y", "跳过确认步骤，直接执行"),
        ("--resume/-r",   "恢复中断的测试计划"),
        ("--multi-config/-m", "多设备并行测试配置文件路径 (YAML)"),
    ]

    details = {
        "requirement": (
            "位置参数，必填（除非使用 --resume）。\n"
            "  可以是：\n"
            "    - 产品需求文档的文件路径（如 docs/需求.md）\n"
            "    - 自然语言需求描述文本\n\n"
            "  示例：\n"
            "    testagent app tpilot plan \"需求文档.md\"\n"
            "    testagent app tpilot plan \"用户登录功能测试\""
        ),
        "--name/-n": (
            "自定义计划名称，用于报告目录和报告标题。\n"
            "  如果不指定，自动从需求文档文件名生成。\n\n"
            "  示例：\n"
            "    testagent app tpilot plan \"需求.md\" --name my-plan\n"
            "    testagent app tpilot plan \"需求.md\" -n v2.0-regression"
        ),
        "--app-package": (
            "Android App 的 package name。\n"
            "  如果连接了设备，会自动检测。手动指定可跳过检测。\n\n"
            "  示例：\n"
            "    testagent app tpilot plan \"需求.md\" -p com.example.app\n"
            "    testagent app tpilot plan \"需求.md\" -p com.tencent.mm"
        ),
        "--app-activity": (
            "App 的启动 Activity。\n"
            "  通常不需要指定，Appium 会自动使用默认 Activity。\n\n"
            "  示例：\n"
            "    testagent app tpilot plan \"需求.md\" -a .MainActivity"
        ),
        "--app-id": (
            "App 标识符，用于 App Context Memory（历史用例、学习模式）。\n"
            "  默认使用 app-package 的值。\n\n"
            "  示例：\n"
            "    testagent app tpilot plan \"需求.md\" --app-id com.example.app"
        ),
        "--auto-yes/-y": (
            "跳过确认步骤，生成用例后直接执行。\n"
            "  适用于 CI/CD 或批量执行场景。\n\n"
            "  示例：\n"
            "    testagent app tpilot plan \"需求.md\" -y\n"
            "    testagent app tpilot plan \"需求.md\" --app-package com.example.app -y"
        ),
        "--resume/-r": (
            "恢复之前中断的测试计划。\n"
            "  中断原因可以是：Ctrl+C 手动暂停、设备死机、进程崩溃等。\n"
            "  已完成的用例不会重复运行，被中断的用例会重新执行。\n\n"
            "  参数值：\n"
            "    latest    — 自动查找最近一次中断的计划\n"
            "    <目录路径> — 指定报告目录路径\n\n"
            "  示例：\n"
            "    testagent app tpilot plan --resume latest\n"
            "    testagent app tpilot plan --resume reports/2026-06-11-015824-my-app/\n"
            "    testagent app tpilot plan -r latest"
        ),
        "--multi-config/-m": (
            "多设备并行测试配置文件路径（YAML 格式）。\n"
            "  指定后，系统会读取配置，自动为每台手机启动独立的 Appium 实例，\n"
            "  并行执行测试计划，每台设备在 TUI 面板中独立显示。\n\n"
            "  配置文件格式：\n"
            "    assignments:\n"
            "      - device: \"emulator-5554\"\n"
            "        plan: \"plans/login.yaml\"\n"
            "      - device: \"192.168.1.100:5555\"\n"
            "        plan: \"plans/pay.yaml\"\n\n"
            "  如果未指定 --multi-config，系统自动进入交互式菜单模式：\n"
            "    1. 自动扫描 adb devices 发现已连接设备\n"
            "    2. 让用户选择设备并分配测试计划\n"
            "    3. 并行执行\n\n"
            "  示例：\n"
            "    testagent app tpilot plan -m configs/multi_device.yaml\n"
            "    testagent app tpilot plan  # 交互式菜单模式"
        ),
    }

    selected = 0

    def _render() -> None:
        sys.stdout.write("\033[2J\033[H")  # clear screen
        sys.stdout.write("  testagent app tpilot plan — 交互式帮助\n")
        sys.stdout.write("  ↑↓ 移动  Enter 查看详情  q 退出\n\n")
        for i, (key, desc) in enumerate(options):
            prefix = "  > " if i == selected else "    "
            sys.stdout.write(f"{prefix}{key:20s} {desc}\n")
        sys.stdout.write("\n")
        sys.stdout.flush()

    _render()

    while True:
        ch = msvcrt.getwch()
        if ch == "q" or ch == "\x1b":  # q or Escape
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.flush()
            break
        elif ch == "\r":  # Enter
            key = options[selected][0]
            sys.stdout.write(f"\n{'='*60}\n")
            sys.stdout.write(f"  {key}\n")
            sys.stdout.write(f"{'='*60}\n")
            sys.stdout.write(details.get(key, "暂无详细说明。") + "\n")
            sys.stdout.write(f"\n  按任意键返回...\n")
            msvcrt.getwch()
            _render()
        elif ch in ("\x00", "\xe0"):  # Arrow key prefix on Windows
            ch2 = msvcrt.getwch()
            if ch2 == "H":  # Up
                selected = (selected - 1) % len(options)
            elif ch2 == "P":  # Down
                selected = (selected + 1) % len(options)
            _render()


@tpilot_typer.command(add_help_option=False)
def plan(
    requirement: str = typer.Argument(
        "", help="产品需求文档路径 或 自然语言需求描述"
    ),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    app_package: str = typer.Option("", "--app-package", "-p", help="App package name"),
    app_activity: str = typer.Option("", "--app-activity", "-a", help="App launch activity"),
    app_id: str = typer.Option("", "--app-id", help="App 标识（如 com.example.app），默认使用 app-package"),
    auto_yes: bool = typer.Option(
        False, "--auto-yes", "-y", help="跳过确认步骤，直接执行"
    ),
    resume: str = typer.Option(
        "", "--resume", "-r",
        help="恢复中断的计划。传入报告目录路径，或 'latest' 恢复最近的。"
    ),
    multi_config: str = typer.Option(
        "", "--multi-config", "-m",
        help="多设备配置文件路径 (YAML)。指定后并行执行多个计划。"
    ),
    show_help: bool = typer.Option(
        False, "--help", "-h", is_eager=True,
        help="显示交互式帮助。"
    ),
) -> None:
    """根据产品需求自动生成、执行测试用例并生成结构化报告。"""
    if show_help:
        _interactive_help()
        return

    import asyncio
    from testagent.cli.plan import run_single_plan

    def _log(msg: str) -> None:
        typer.echo(msg)

    # Multi-device mode: --multi-config YAML
    if multi_config:
        from testagent.cli.plan import run_multi_device_plan
        asyncio.run(run_multi_device_plan(config=multi_config, log_fn=_log))
        return

    # Interactive multi-device menu: no arguments at all
    if not requirement and not resume:
        from testagent.cli.plan import run_multi_device_plan
        asyncio.run(run_multi_device_plan(log_fn=_log))
        return

    result = asyncio.run(run_single_plan(
        requirement,
        app_package=app_package,
        app_activity=app_activity,
        app_id=app_id,
        auto_yes=auto_yes,
        name=name,
        log_fn=_log,
        resume_dir=resume,
    ))

    if result.status == "failed":
        typer.echo(f"\n  ! 失败: {result.error}")
        raise typer.Exit(1)

    typer.echo(f"\n  状态: {result.status}")
    stat_line = f"  用例: {result.case_count} (通过 {result.passed}, 失败 {result.failed}"
    if result.aborted:
        stat_line += f", 未运行 {result.aborted}"
    stat_line += ")"
    typer.echo(stat_line)
    typer.echo(f"  耗时: {result.duration}")
    typer.echo(f"  报告: {result.report_path}")


@tpilot_typer.command()
def replay(
    app_id: str = typer.Option(..., "--app-id", help="App 标识（如 com.example.app）"),
    stats: bool = typer.Option(False, "--stats", help="显示待重跑用例统计"),
    case_ids: str = typer.Option("", "--case-ids", help="只重跑指定用例，逗号分隔"),
    with_prerequisites: bool = typer.Option(False, "--with-prerequisites", help="先执行前置用例链"),
    mark_resolved: str = typer.Option("", "--mark-resolved", help="手动标记为已解决，逗号分隔"),
    include_blocked: bool = typer.Option(False, "--include-blocked", help="包含 BLOCKED 状态的用例"),
    cleanup_days: int = typer.Option(0, "--cleanup-days", help="清理 N 天前已解决的记录（默认 30）"),
) -> None:
    """重跑之前失败的测试用例，生成 delta 报告。"""
    import asyncio
    asyncio.run(_replay_command(
        app_id=app_id,
        stats_only=stats,
        case_ids=case_ids,
        with_prerequisites=with_prerequisites,
        mark_resolved=mark_resolved,
        include_blocked=include_blocked,
        cleanup_days=cleanup_days,
    ))


async def _replay_command(
    app_id: str,
    stats_only: bool,
    case_ids: str,
    with_prerequisites: bool,
    mark_resolved: str,
    include_blocked: bool,
    cleanup_days: int,
) -> None:
    from datetime import UTC, datetime
    from testagent.db import get_session, init_db
    from testagent.db.repository import FailedReplayRepository
    from testagent.plan.delta_report import DeltaReportGenerator
    from testagent.plan.replay_manager import execute_replay, get_pending

    await init_db()

    async with get_session() as session:
        repo = FailedReplayRepository(session)

        # -- Stats mode --
        if stats_only:
            pending = await get_pending(app_id, repo, include_blocked=include_blocked)
            typer.echo(f"Pending failed cases for {app_id}: {len(pending)}")
            for r in pending:
                typer.echo(f"  [{r.last_replay_status}] {r.test_case_id}: {r.test_case_name} (replayed {r.replay_count}x)")
            return

        # -- Mark resolved --
        if mark_resolved:
            ids = [s.strip() for s in mark_resolved.split(",") if s.strip()]
            now = datetime.now(UTC)
            count = 0
            for tc_id in ids:
                record = await repo.get_by_app_and_case_id(app_id, tc_id)
                if record:
                    await repo.update(record.id, {
                        "resolved": 1,
                        "resolved_at": now,
                        "last_replay_status": "PASSED",
                    })
                    count += 1
                    typer.echo(f"  Marked resolved: {tc_id}")
            typer.echo(f"Marked {count}/{len(ids)} cases as resolved.")
            return

        # -- Cleanup --
        if cleanup_days > 0:
            days = cleanup_days if cleanup_days > 0 else 30
            deleted = await repo.cleanup_resolved(app_id, days=days)
            typer.echo(f"Cleaned up {deleted} resolved records older than {days} days.")
            return

        # -- Replay --
        case_id_list = [s.strip() for s in case_ids.split(",") if s.strip()] if case_ids else None

        pending = await get_pending(app_id, repo, include_blocked=include_blocked)
        if not pending:
            typer.echo(f"No pending failed cases for {app_id}.")
            return

        typer.echo(f"Replaying {len(pending)} failed cases for {app_id}...")

        # Build executor function
        from testagent.plan.execution_engine import ExecutionEngine
        from testagent.plan.models import PlanConfig

        config = PlanConfig()
        engine = ExecutionEngine(config)

        async def executor_func(tcs):
            return await engine.execute_all(tcs)

        summary = await execute_replay(
            app_id=app_id,
            repository=repo,
            executor_func=executor_func,
            case_ids=case_id_list,
            with_prerequisites=with_prerequisites,
        )

        # Reload records for the report
        report_records = []
        all_tc_ids = (
            summary["details"]["fixed"]
            + summary["details"]["still_failed"]
            + summary["details"].get("blocked", [])
            + summary["details"].get("skipped", [])
        )
        for tc_id in all_tc_ids:
            # Query without resolved filter to get just-resolved records too
            from testagent.models.failed_replay import FailedCaseReplay
            from sqlalchemy import select
            stmt = (
                select(FailedCaseReplay)
                .where(FailedCaseReplay.app_id == app_id)
                .where(FailedCaseReplay.test_case_id == tc_id)
                .order_by(FailedCaseReplay.created_at.desc())
            )
            result = await session.execute(stmt)
            record = result.scalars().first()
            if record:
                report_records.append({
                    "test_case_id": record.test_case_id,
                    "test_case_name": record.test_case_name,
                    "original_error_message": record.original_error_message,
                    "last_replay_error_message": record.last_replay_error_message,
                    "last_replay_status": record.last_replay_status,
                    "replay_count": record.replay_count,
                })

        report_gen = DeltaReportGenerator()
        json_path, html_path = report_gen.generate(app_id, summary, report_records)

        typer.echo(f"\nReplay complete:")
        typer.echo(f"  Total replayed: {summary['total_replayed']}")
        typer.echo(f"  Fixed: {summary['fixed']}")
        typer.echo(f"  Still failed: {summary['still_failed']}")
        typer.echo(f"  Blocked: {summary.get('blocked', 0)}")
        typer.echo(f"  Skipped: {summary.get('skipped', 0)}")
        typer.echo(f"\nDelta report: {html_path}")


# ── 向后兼容：testagent app plan → 委托给 tpilot plan ──

@app_typer.command(name="plan", hidden=True, help="[已迁移] 请使用 testagent app tpilot plan")
def _plan_backward_compat(
    requirement: str = typer.Argument(
        "", help="产品需求文档路径 或 自然语言需求描述"
    ),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    app_package: str = typer.Option("", "--app-package", "-p", help="App package name"),
    app_activity: str = typer.Option("", "--app-activity", "-a", help="App launch activity"),
    app_id: str = typer.Option("", "--app-id", help="App 标识（如 com.example.app），默认使用 app-package"),
    auto_yes: bool = typer.Option(False, "--auto-yes", "-y", help="跳过确认步骤，直接执行"),
    resume: str = typer.Option("", "--resume", "-r", help="恢复中断的计划。传入报告目录路径，或 'latest' 恢复最近的。"),
    multi_config: str = typer.Option("", "--multi-config", "-m", help="多设备配置文件路径 (YAML)。指定后并行执行多个计划。"),
) -> None:
    """[已迁移] 请使用 testagent app tpilot plan"""
    typer.echo("ℹ️  \x1b[33mtestagent app plan 已迁移到 testagent app tpilot plan\x1b[0m")
    typer.echo("   正在自动跳转...\n")

    # 直接调用 tpilot 下的 plan（同文件内可访问）
    plan(
        requirement=requirement,
        name=name,
        app_package=app_package,
        app_activity=app_activity,
        app_id=app_id,
        auto_yes=auto_yes,
        resume=resume,
        multi_config=multi_config,
    )
