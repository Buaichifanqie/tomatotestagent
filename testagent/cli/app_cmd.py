from __future__ import annotations

import typer

app_typer = typer.Typer(name="app", help="Android App 测试命令")


@app_typer.command()
def plan(
    requirement: str = typer.Argument(
        "", help="产品需求文档路径 或 自然语言需求描述"
    ),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    app_package: str = typer.Option("", "--app-package", "-p", help="App package name"),
    app_activity: str = typer.Option("", "--app-activity", "-a", help="App launch activity"),
    app_id: str = typer.Option("", "--app-id", help="App 标识（如 com.bilibili.app），默认使用 app-package"),
    auto_yes: bool = typer.Option(
        False, "--auto-yes", "-y", help="跳过确认步骤，直接执行"
    ),
    resume: str = typer.Option(
        "", "--resume", "-r",
        help="恢复中断的测试计划。传入报告目录路径，或 'latest' 恢复最近的计划。"
    ),
) -> None:
    """根据产品需求自动生成、执行测试用例并生成结构化报告。"""
    import asyncio
    from testagent.cli.plan import run_single_plan

    # Validation
    if not resume and not requirement:
        typer.echo("Error: 请提供需求文档路径或使用 --resume 恢复中断的计划。")
        raise typer.Exit(1)

    def _log(msg: str) -> None:
        typer.echo(msg)

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


@app_typer.command()
def replay(
    app_id: str = typer.Option(..., "--app-id", help="App 标识（如 tv.danmaku.bili）"),
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
