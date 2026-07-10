from __future__ import annotations

import sys

import typer

app_typer = typer.Typer(name="app", help="Android App 测试命令")


def _interactive_help() -> None:
    """交互式帮助：用上下键选择选项，回车查看详情，q 退出。"""
    import msvcrt

    options = [
        ("platform",      "目标平台: android / ios（必填）"),
        ("requirement",   "产品需求文档路径 或 自然语言需求描述"),
        ("--name/-n",     "自定义计划名称"),
        ("--app-package", "App 标识（Android→packageName, iOS→bundleId）"),
        ("--app-activity","[Android only] App launch activity"),
        ("--app-id",      "App Context Memory 标识，默认同 app-package"),
        ("--device-udid/-d", "目标设备序列号"),
        ("--appium-port",  "Appium 服务器端口（默认 4723）"),
        ("--auto-yes/-y", "跳过确认直接执行"),
        ("--resume/-r",   "恢复中断的计划"),
    ]

    details = {
        "platform": (
            "目标平台，必填参数。\n"
            "  - android：Android App 测试（UiAutomator2）\n"
            "  - ios：iOS App 测试（XCUITest）\n\n"
            "  示例：\n"
            '    testagent app plan "测试首页" -f android -p com.example.app\n'
            '    testagent app plan "测试首页" -f ios -p com.example.app -d XXXXXXXXXXXX'
        ),
        "requirement": (
            "位置参数，必填（除非使用 --resume）。\n"
            "  可以是：\n"
            "    - 产品需求文档的文件路径（如 docs/需求.md）\n"
            "    - 自然语言需求描述文本\n\n"
            "  示例：\n"
            "    testagent app plan \"需求文档.md\"\n"
            "    testagent app plan \"用户登录功能测试\""
        ),
        "--name/-n": (
            "自定义计划名称，用于报告目录和报告标题。\n"
            "  如果不指定，自动从需求文档文件名生成。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" --name my-plan\n"
            "    testagent app plan \"需求.md\" -n v2.0-regression"
        ),
        "--app-package": (
            "App 标识符。\n"
            "  - Android：packageName（如 com.example.app）\n"
            "  - iOS：bundleId（如 com.example.app）\n"
            "  如果连接了设备，会自动检测。手动指定可跳过检测。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" -p com.example.app -f android\n"
            "    testagent app plan \"需求.md\" -p com.example.app -f ios"
        ),
        "--app-activity": (
            "[Android only] App 的启动 Activity。\n"
            "  通常不需要指定，Appium 会自动使用默认 Activity。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" -a .MainActivity"
        ),
        "--app-id": (
            "App Context Memory 标识，默认同 app-package。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" --app-id com.example.app"
        ),
        "--device-udid/-d": (
            "目标设备序列号（UDID）。\n"
            "  多设备并行测试时必须指定，确保每个终端连接到正确的设备。\n"
            "  可通过 `adb devices`（Android）或 `xcrun xctrace list devices`（iOS）查看。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" -d emulator-5554\n"
            "    testagent app plan \"需求.md\" -d 00008020-XXXXXXXXXXXX\n\n"
            "  多设备并行：\n"
            "    终端1: testagent app plan \"视频播放\" -d <udid1> --name video\n"
            "    终端2: testagent app plan \"搜索功能\" -d <udid2> --name search"
        ),
        "--appium-port": (
            "Appium 服务器端口（默认 4723）。\n\n"
            "  多设备场景：每台设备用独立 Appium 时分别指定不同端口。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" --appium-port 4723\n"
            "    testagent app plan \"需求.md\" --appium-port 4724"
        ),
        "--auto-yes/-y": (
            "跳过确认步骤，生成用例后直接执行。\n"
            "  适用于 CI/CD 或批量执行场景。\n\n"
            "  示例：\n"
            "    testagent app plan \"需求.md\" -y\n"
            "    testagent app plan \"需求.md\" -p com.example.app -y"
        ),
        "--resume/-r": (
            "恢复之前中断的测试计划。\n"
            "  中断原因：Ctrl+C、设备死机、进程崩溃等。\n"
            "  已完成的用例不会重复运行。\n\n"
            "  参数值：\n"
            "    latest    — 自动查找最近一次中断的计划\n"
            "    <目录路径> — 指定报告目录路径\n\n"
            "  示例：\n"
            "    testagent app plan --resume latest\n"
            "    testagent app plan -r latest"
        ),
    }

    selected = 0

    def _render() -> None:
        sys.stdout.write("\033[2J\033[H")  # clear screen
        sys.stdout.write("  testagent app plan — 交互式帮助\n")
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


@app_typer.command(add_help_option=False)
def plan(
    requirement: str = typer.Argument(
        "", help="产品需求文档路径 或 自然语言需求描述"
    ),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    platform: str = typer.Option(
        ..., "--platform", "-f",
        help="目标平台: android / ios（必填）",
    ),
    app_package: str = typer.Option("", "--app-package", "-p", help="App 标识（Android→packageName, iOS→bundleId）"),
    app_activity: str = typer.Option("", "--app-activity", "-a",
        help="[Android only] App launch activity"),
    app_id: str = typer.Option("", "--app-id", help="App Context Memory 标识，默认同 app-package"),
    device_udid: str = typer.Option("", "--device-udid", "-d", help="目标设备序列号"),
    appium_port: int = typer.Option(4723, "--appium-port", help="Appium 服务器端口"),
    auto_yes: bool = typer.Option(False, "--auto-yes", "-y", help="跳过确认直接执行"),
    resume: str = typer.Option("", "--resume", "-r", help="恢复中断的计划"),
    multi_config: str = typer.Option("", "--multi-config", "-m", help="多设备配置文件路径 (YAML)"),
    show_help: bool = typer.Option(False, "--help", "-h", is_eager=True, help="显示交互式帮助"),
) -> None:
    """根据产品需求自动生成、执行测试用例并生成结构化报告。"""
    if show_help:
        _interactive_help()
        return

    if not resume and not requirement:
        typer.echo("Error: 请提供需求文档路径或使用 --resume 恢复中断的计划。")
        raise typer.Exit(1)

    if platform.lower() not in ("android", "ios"):
        typer.echo("Error: --platform / -f 必须是 'android' 或 'ios'")
        raise typer.Exit(1)

    if platform.lower() == "ios" and app_activity:
        typer.echo("  [info] --app-activity 仅 Android 有效，iOS 侧已忽略")

    import asyncio
    from testagent.cli.plan import run_single_plan

    def _log(msg: str) -> None:
        typer.echo(msg)

    if multi_config:
        from testagent.cli.plan import run_multi_device_plan
        result = asyncio.run(run_multi_device_plan(config=multi_config, log_fn=_log))
        return

    result = asyncio.run(run_single_plan(
        requirement,
        platform=platform,
        app_package=app_package,
        app_activity=app_activity,
        app_id=app_id,
        device_udid=device_udid,
        appium_url=f"http://localhost:{appium_port}",
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


@app_typer.command()
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
