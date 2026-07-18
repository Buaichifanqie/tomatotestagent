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
    element_source: str = typer.Option(
        "multimodal", "--element-source", "-es",
        help="元素识别策略: multimodal (默认/现有多模态方案), yolo (本地YOLO+OCR), yolo_with_dom (DOM+YOLO混合)",
    ),
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
        element_source=element_source,
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


# ── YOLO 训练命令 ──────────────────────────────────────────────────


@app_typer.command()
def train_yolo(
    dataset: str = typer.Argument(..., help="数据集名称"),
    model_name: str = typer.Option("", "--name", "-n", help="模型名称（默认自动生成）"),
    epochs: int = typer.Option(100, "--epochs", "-e", help="训练轮数"),
    batch_size: int = typer.Option(16, "--batch-size", "-b", help="批次大小"),
    device: str = typer.Option("cpu", "--device", "-d", help="训练设备 (cpu / cuda:0)"),
    base_model: str = typer.Option("yolov8n.pt", "--base-model", help="基础模型权重"),
) -> None:
    """训练 YOLO 模型。在数据集上训练自定义 UI 元素检测模型。"""
    from testagent.vision_local.dataset_manager import DatasetManager
    from testagent.vision_local.trainer import YOLOTrainer
    from testagent.vision_local.model_manager import ModelManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    ds_manager = DatasetManager(datasets_dir=settings.yolo_datasets_dir)

    # 验证数据集
    ds_path = ds_manager.get_dataset_path(dataset)
    if not ds_path.exists():
        typer.echo(f"Error: 数据集 '{dataset}' 不存在于 {settings.yolo_datasets_dir}")
        raise typer.Exit(1)
    yaml_path = ds_path / "data.yaml"
    if not yaml_path.exists():
        # 尝试自动生成
        classes = []
        if (ds_path / "classes.txt").exists():
            classes = ds_path.joinpath("classes.txt").read_text(encoding="utf-8").strip().split("\n")
        ds_manager._generate_data_yaml(ds_path, classes)

    model_name = model_name or f"{dataset}_yolov8_{epochs}ep"
    typer.echo(f"开始训练: {model_name}")
    typer.echo(f"  数据集: {dataset} ({ds_path})")
    typer.echo(f"  轮数: {epochs}")
    typer.echo(f"  批次: {batch_size}")
    typer.echo(f"  设备: {device}")

    trainer = YOLOTrainer(base_model=base_model, device=device)

    def _on_progress(current: int, total: int) -> None:
        pct = current / total * 100
        typer.echo(f"\r  进度: [{current}/{total}] {pct:.0f}%", nl=False)

    typer.echo()
    result = asyncio.run(trainer.train(
        data_yaml=str(yaml_path),
        model_name=model_name,
        epochs=epochs,
        batch_size=batch_size,
        progress_callback=_on_progress,
    ))

    typer.echo()
    if result["status"] == "completed":
        typer.echo(f"训练完成!")
        typer.echo(f"  模型: {result['best_model']}")
        typer.echo(f"  mAP50: {result['metrics'].get('mAP50', 0):.3f}")
        typer.echo(f"  Precision: {result['metrics'].get('precision', 0):.3f}")
        typer.echo(f"  Recall: {result['metrics'].get('recall', 0):.3f}")

        # 注册模型
        if result["best_model"]:
            dataset_info = ds_manager.list_datasets()
            ds_classes = []
            for d in dataset_info:
                if d["name"] == dataset:
                    ds_classes = d["classes"]
                    break
            model_mgr = ModelManager(models_dir=settings.yolo_models_dir)
            model_mgr.register_model(
                name=model_name,
                model_path=result["best_model"],
                metrics=result["metrics"],
                dataset_name=dataset,
                classes=ds_classes,
            )
            model_mgr.set_default_model(model_name)
            typer.echo(f"  已注册为默认模型")
    elif result["status"] == "cancelled":
        typer.echo("训练已取消")
    else:
        typer.echo(f"训练失败: {result.get('error', '未知错误')}")
        raise typer.Exit(1)


@app_typer.command()
def list_datasets(
    detail: bool = typer.Option(False, "--detail", help="显示详细信息"),
) -> None:
    """列出可用的 YOLO 训练数据集。"""
    from testagent.vision_local.dataset_manager import DatasetManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    ds_manager = DatasetManager(datasets_dir=settings.yolo_datasets_dir)
    datasets = ds_manager.list_datasets()

    if not datasets:
        typer.echo("暂无数据集。")
        typer.echo(f"数据集目录: {settings.yolo_datasets_dir}")
        return

    for ds in datasets:
        typer.echo(f"\n名称: {ds['name']}")
        typer.echo(f"  路径: {ds['path']}")
        typer.echo(f"  图片: {ds['image_count']} (训练 {ds['train_images']}, 验证 {ds['val_images']})")
        typer.echo(f"  标注: {ds['label_count']}")
        if detail:
            typer.echo(f"  类别: {', '.join(ds['classes']) if ds['classes'] else '(无)'}")
            if ds["description"]:
                typer.echo(f"  描述: {ds['description']}")


@app_typer.command()
def list_models(
    detail: bool = typer.Option(False, "--detail", help="显示详细信息"),
) -> None:
    """列出已训练的 YOLO 模型。"""
    from testagent.vision_local.model_manager import ModelManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    model_mgr = ModelManager(models_dir=settings.yolo_models_dir)
    models = model_mgr.list_models()

    if not models:
        typer.echo("暂无已训练的模型。")
        return

    for m in models:
        default_tag = " [默认]" if m.get("is_default") else ""
        typer.echo(f"\n模型: {m['name']}{default_tag}")
        typer.echo(f"  路径: {m['path']}")
        if m.get("size"):
            size_mb = m["size"] / (1024 * 1024)
            typer.echo(f"  大小: {size_mb:.1f} MB")
        metrics = m.get("metrics", {})
        if metrics:
            typer.echo(f"  mAP50: {metrics.get('mAP50', 'N/A')}")
            typer.echo(f"  Precision: {metrics.get('precision', 'N/A')}")
            typer.echo(f"  Recall: {metrics.get('recall', 'N/A')}")
        if detail:
            if m.get("dataset_name"):
                typer.echo(f"  数据集: {m['dataset_name']}")
            if m.get("classes"):
                typer.echo(f"  类别: {', '.join(m['classes'])}")


@app_typer.command()
def select_model(
    name: str = typer.Argument(..., help="模型名称"),
) -> None:
    """选择默认 YOLO 模型。"""
    from testagent.vision_local.model_manager import ModelManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    model_mgr = ModelManager(models_dir=settings.yolo_models_dir)
    ok = model_mgr.set_default_model(name)
    if ok:
        typer.echo(f"默认模型已切换为: {name}")
    else:
        typer.echo(f"Error: 模型 '{name}' 不存在")
        raise typer.Exit(1)


@app_typer.command()
def create_dataset(
    name: str = typer.Argument(..., help="数据集名称"),
    classes: str = typer.Option("", "--classes", "-c",
        help='类别列表(可选)，JSON格式。不传则使用常用Android UI类型默认值'),
    description: str = typer.Option("", "--desc", help="数据集描述"),
) -> None:
    """创建新的 YOLO 训练数据集。

    如果不指定 --classes，默认使用以下常用 Android UI 元素类型：

        Button, TextView, ImageView, EditText, ImageButton,
        CheckBox, RadioButton, Switch, ToggleButton, Spinner,
        SeekBar, ProgressBar, SearchView, TabLayout, Chip,
        CardView, Toolbar, WebView, RecyclerView, ListView

    注意：自动标注时会根据实际 DOM XML 自动发现类型并更新 classes.txt，
    所以即使现在不指定全部类型也没关系，采集过程中会自动补充。
    """
    from testagent.vision_local.dataset_manager import DatasetManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    ds_manager = DatasetManager(datasets_dir=settings.yolo_datasets_dir)

    import json
    DEFAULT_CLASSES = [
        "Button", "TextView", "ImageView", "EditText", "ImageButton",
        "CheckBox", "RadioButton", "Switch", "ToggleButton", "Spinner",
        "SeekBar", "ProgressBar", "SearchView", "TabLayout", "Chip",
        "CardView", "Toolbar", "WebView", "RecyclerView", "ListView",
        "ViewPager", "BottomNavigationView", "FloatingActionButton",
    ]
    class_list = json.loads(classes) if classes else DEFAULT_CLASSES

    try:
        ds_path = ds_manager.create_dataset(
            name=name,
            description=description,
            classes=class_list,
        )
        typer.echo(f"数据集创建成功: {ds_path}")
        typer.echo(f"  类别 ({len(class_list)} 种): {', '.join(class_list)}")
        typer.echo(f"  提示: 自动标注时会自动发现新的类型并补充")
    except FileExistsError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(1)


@app_typer.command()
def import_images(
    dataset: str = typer.Argument(..., help="数据集名称"),
    source: str = typer.Argument(..., help="图片源目录路径"),
    split: str = typer.Option("train", "--split", "-s", help="数据集划分 (train/val)"),
) -> None:
    """从目录导入图片到数据集。"""
    from testagent.vision_local.dataset_manager import DatasetManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    ds_manager = DatasetManager(datasets_dir=settings.yolo_datasets_dir)

    try:
        count = ds_manager.add_images(dataset, source, split=split)
        typer.echo(f"成功导入 {count} 张图片到数据集 '{dataset}'/{split}")
    except FileNotFoundError as e:
        typer.echo(f"Error: {e}")
        raise typer.Exit(1)


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


# ── 训练数据采集 ──────────────────────────────────────────────────

@app_typer.command()
def capture_training_data(
    dataset: str = typer.Argument(..., help="数据集名称"),
    device_udid: str = typer.Option("", "--device-udid", "-d", help="设备UDID"),
    app: str = typer.Option("", "--app", "-p", help='App名称或包名（如"哔哩哔哩"或包名），不传则自动检测'),
    appium_url: str = typer.Option("http://localhost:4723", "--appium-url", "-u", help="Appium服务器URL"),
    max_pages: int = typer.Option(50, "--max-pages", "-n", help="最大采集页数"),
    interactive: bool = typer.Option(True, "--interactive/--batch", help="交互模式（每页按回车继续）或自动连续采集"),
    delay: float = typer.Option(2.0, "--delay", help="自动模式每页间隔秒数"),
) -> None:
    """从设备自动采集训练数据（截图+DOM自动标注）。

    **交互模式（默认）**：每采集一页按回车继续，方便手动切换App页面。

    **自动模式（--batch）**：自动连续采集，适用于自动页面遍历脚本。

    采集的数据会自动使用 DOM 树信息生成 YOLO 格式标签，
    完全无需人工标注！

    工作流程：
      1. 连接设备并启动App（支持按名称自动搜索包名）
      2. 每页截图 + dump DOM XML
      3. 从XML解析元素坐标 → 自动生成YOLO标签
      4. 保存到数据集

    示例:
      # 按App名称自动匹配包名
      testagent app capture-training-data bilibili_ui -p 哔哩哔哩 -d emulator-5554

      # 直接指定包名
      testagent app capture-training-data bilibili_ui -p tv.danmaku.bili -d emulator-5554
    """
    from testagent.vision_local.dataset_manager import DatasetManager
    from testagent.config.settings import get_settings

    settings = get_settings()
    ds_manager = DatasetManager(datasets_dir=settings.yolo_datasets_dir)

    # 自动创建数据集（如果不存在）
    ds_path = ds_manager.get_dataset_path(dataset)
    if not ds_path.exists():
        typer.echo(f"数据集 '{dataset}' 不存在，自动创建（使用默认类别列表）...")
        ds_manager.create_dataset(name=dataset)
        DEFAULT_CLASSES = [
            "Button", "TextView", "ImageView", "EditText", "ImageButton",
            "CheckBox", "RadioButton", "Switch", "ToggleButton", "Spinner",
            "SeekBar", "ProgressBar", "SearchView",
        ]
        (ds_path / "classes.txt").write_text("\n".join(DEFAULT_CLASSES), encoding="utf-8")
        typer.echo(f"  已创建，默认 {len(DEFAULT_CLASSES)} 种类别")
        typer.echo(f"  提示: 采集过程中会自动发现新的类型并补充到 classes.txt")

    typer.echo(f"开始采集训练数据 → 数据集: {dataset}")
    typer.echo(f"  设备: {device_udid or '默认'}")
    typer.echo(f"  模式: {'交互式' if interactive else f'自动(间隔{delay}s)'}")
    typer.echo(f"  最大页数: {max_pages}")
    typer.echo()

    # 用 asyncio.run 包装整个采集流程
    import asyncio
    import httpx
    import subprocess
    import xml.etree.ElementTree as ET
    from testagent.mcp_servers.appium_server.tools import (
        app_screenshot, app_get_source, app_exec,
    )
    from testagent.mcp_servers.shared_cache import get_screenshot

    async def _do_capture() -> int:
        """异步采集主循环。"""
        nonlocal ds_manager, ds_path, dataset, device_udid, app
        nonlocal appium_url, max_pages, interactive, delay

        # ── 自动检测 App 包名 ───────────────────────────────────
        app_package = ""
        if app:
            if "." in app and not app.startswith("."):
                app_package = app
                typer.echo(f"  App 包名: {app_package}")
            else:
                typer.echo(f"  搜索 App: 「{app}」...")
                from testagent.llm.local_provider import LLMProviderFactory
                result = subprocess.run(
                    ["adb", "-s", device_udid, "shell", "pm", "list", "packages", "-3"],
                    capture_output=True, text=True, timeout=10,
                )
                packages = [ln[8:] for ln in result.stdout.strip().split("\n") if ln.startswith("package:")]
                typer.echo(f"  设备上有 {len(packages)} 个第三方应用")
                _settings = get_settings()
                provider = LLMProviderFactory.create(_settings)
                pkg_list = "\n".join(f"  {p}" for p in packages)
                prompt = f"用户想在App「{app}」上采集训练数据。\n设备已安装:\n{pkg_list}\n\n返回最匹配的包名（仅包名）。"
                response = await provider.chat(
                    system="你是一个Android工程师，根据App名称匹配包名。",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
                for block in response.content:
                    if block.get("type") == "text":
                        matched = str(block.get("text", "")).strip()
                        if matched in packages:
                            app_package = matched
                            typer.echo(f"  匹配到: {app_package}")
                            break
                if not app_package:
                    typer.echo("  ⚠️ 未匹配到，使用第一个应用")
                    app_package = packages[0] if packages else ""

        # ── 创建 Appium 会话 ────────────────────────────────────
        from testagent.common.appium_manager import ensure_android_home
        from testagent.platform.factory import PlatformFactory
        android_home = ensure_android_home()
        typer.echo(f"  连接到 Appium: {appium_url}")
        typer.echo(f"  设备: {device_udid or 'emulator-5554'}")
        if android_home:
            typer.echo(f"  ANDROID_HOME: {android_home}")
        platform_obj = PlatformFactory.create("android")
        caps = platform_obj.build_capabilities(udid=device_udid)
        caps["appium:androidHome"] = android_home or "C:\\Users\\kongwenshuo\\AppData\\Local\\Android\\Sdk"
        capabilities = {"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}}
        session_id: str | None = None
        try:
            resp = httpx.post(f"{appium_url}/session", json=capabilities, timeout=30)
            typer.echo(f"  响应状态: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                session_id = data.get("value", {}).get("sessionId") or data.get("sessionId")
            else:
                typer.echo(f"  Appium 错误: {resp.text[:300]}")
                return -1
        except httpx.ConnectError:
            typer.echo("  Error: 无法连接到 Appium，请确认已启动")
            return -1
        if not session_id:
            return -1
        typer.echo(f"  会话已创建: {session_id[:12]}...")

        # ── 用 ADB 启动 App（绕过 Appium 的安全限制）───────────
        if app_package:
            typer.echo(f"  启动 {app_package}...")
            subprocess.run(
                ["adb", "-s", device_udid, "shell", "am", "force-stop", app_package],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["adb", "-s", device_udid, "shell", "monkey", "-p", app_package,
                 "-c", "android.intent.category.LAUNCHER", "1"],
                capture_output=True, timeout=10,
            )
            import time
            time.sleep(4)

        collected = 0
        try:
            for page_num in range(1, max_pages + 1):
                typer.echo(f"\n--- 第 {page_num} 页 ---")

                # 截图
                try:
                    scr_result = await app_screenshot(
                        appium_url=appium_url, session_id=session_id
                    )
                    screenshot_id = scr_result.get("screenshot_id", "")
                    if not screenshot_id:
                        typer.echo("  截图失败（无 screenshot_id）")
                        continue
                    b64_data = get_screenshot(screenshot_id)
                    if not b64_data:
                        typer.echo("  截图数据为空")
                        continue
                    import base64
                    image_data = base64.b64decode(b64_data)
                except Exception as e:
                    typer.echo(f"  截图失败: {e}")
                    if interactive:
                        typer.echo("  按 Enter 重试...", nl=False)
                        input()
                    continue

                # 获取 DOM XML
                dom_xml = ""
                try:
                    src_result = await app_get_source(
                        appium_url=appium_url, session_id=session_id
                    )
                    dom_xml = src_result.get("source", "")
                except Exception as e:
                    typer.echo(f"  DOM获取失败: {e}")

                if not dom_xml:
                    typer.echo("  无DOM数据，跳过")
                    if interactive:
                        typer.echo("  按 Enter 继续...", nl=False)
                        input()
                    continue

                # 获取屏幕尺寸
                img_w, img_h = 1080, 2400
                try:
                    wm_result = await app_exec(
                        command="shell wm size",
                        appium_url=appium_url,
                        session_id=session_id,
                    )
                    import re
                    stdout = wm_result.get("stdout", "")
                    match = re.search(r"Override size:\s*(\d+)x(\d+)", stdout)
                    if not match:
                        match = re.search(r"Physical size:\s*(\d+)x(\d+)", stdout)
                    if match:
                        img_w, img_h = int(match.group(1)), int(match.group(2))
                except Exception:
                    pass

                # 自动标注 + 保存
                filename = f"page_{page_num:04d}.png"
                result = ds_manager.save_auto_labeled_sample(
                    dataset_name=dataset,
                    image_data=image_data,
                    image_filename=filename,
                    dom_xml=dom_xml,
                    image_width=img_w,
                    image_height=img_h,
                    split="train",
                )

                ec = result["element_count"]
                collected += 1
                types = result.get("element_types", [])
                texts = result.get("element_texts", [])
                typer.echo(f"  保存: {filename} ({ec} 个元素) 类型: {set(types)}")
                if texts:
                    displayed = [t for t in texts if t][:5]
                    if displayed:
                        typer.echo(f"  文字: {displayed}")

                if interactive and page_num < max_pages:
                    typer.echo()
                    typer.echo("  请手动切换到下一个页面，然后按 Enter 继续...", nl=False)
                    input()
                elif not interactive and page_num < max_pages:
                    # ── 自动导航到下一个页面 ──
                    # 从 DOM XML 中提取可点击元素，随机选一个点击
                    import re
                    import random
                    # 解析 XML 获取可点击元素
                    clickable_items = []
                    try:
                        root = ET.fromstring(dom_xml.encode("utf-8"))
                        def _find_clickable(node: ET.Element, depth: int = 0):
                            if depth > 25:
                                return
                            bounds = node.get("bounds", "")
                            clk = node.get("clickable", "false") == "true"
                            if clk and bounds:
                                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
                                if m:
                                    x1, y1, x2, y2 = int(m[1]), int(m[2]), int(m[3]), int(m[4])
                                    if (x2-x1) > 20 and (y2-y1) > 20:
                                        type_short = (node.get("class","") or "").split(".")[-1]
                                        # 过滤布局容器
                                        if type_short not in ("FrameLayout","LinearLayout",
                                            "RelativeLayout","ViewGroup","View","ScrollView",
                                            "RecyclerView","ListView","ViewPager"):
                                            clickable_items.append({
                                                "x": (x1+x2)//2, "y": (y1+y2)//2,
                                                "type": type_short,
                                                "bounds": bounds,
                                            })
                            for child in node:
                                _find_clickable(child, depth + 1)
                        _find_clickable(ET.fromstring(dom_xml.encode("utf-8")))
                    except Exception:
                        pass

                    if clickable_items:
                        # 随机选择 70% 概率走"导航"型元素（底部Tab、列表项等），30% 纯随机
                        candidates = clickable_items.copy()
                        # 优先选带文字的可点击元素
                        import random as _random
                        item = _random.choice(candidates)
                        typer.echo(f"  点击: [{item['type']}] ({item['x']},{item['y']})")
                        subprocess.run(
                            ["adb", "-s", device_udid, "shell", "input", "tap",
                             str(item["x"]), str(item["y"])],
                            capture_output=True, timeout=5,
                        )
                        import time
                        time.sleep(2.5)
                    else:
                        # 无可点击元素：滑动
                        typer.echo("  滑动浏览")
                        subprocess.run(
                            ["adb", "-s", device_udid, "shell", "input", "swipe",
                             str(img_w//2), str(int(img_h*0.7)),
                             str(img_w//2), str(int(img_h*0.3)), "500"],
                            capture_output=True, timeout=5,
                        )
                        import time
                        time.sleep(2.0)

        except KeyboardInterrupt:
            typer.echo("\n\n采集已暂停")
        finally:
            if session_id:
                try:
                    httpx.delete(f"{appium_url}/session/{session_id}", timeout=10)
                except Exception:
                    pass

        return collected

    collected = asyncio.run(_do_capture())
    if collected < 0:
        # 会话创建失败
        raise typer.Exit(1)

    typer.echo(f"\n✅ 采集完成！共采集 {collected} 页训练数据")
    typer.echo(f"\n  数据集: {dataset}")
    typer.echo(f"  图片: {ds_path}/images/train/")
    typer.echo(f"  标签: {ds_path}/labels/train/")
    typer.echo(f"  元数据: {ds_path}/metadata/train/")
    typer.echo(f"\n  现在可以开始训练:")
    typer.echo(f"    testagent app train-yolo {dataset} --epochs 100")
    typer.echo()
    typer.echo("  训练完成后用 --element-source yolo 运行测试:")
    typer.echo(f"    testagent app plan <需求> -f android --element-source yolo")
