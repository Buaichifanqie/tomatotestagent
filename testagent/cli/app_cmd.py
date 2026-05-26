from __future__ import annotations

import typer

app_typer = typer.Typer(name="app", help="Android App 测试命令")


@app_typer.command()
def plan(
    requirement: str = typer.Argument(
        ..., help="产品需求文档路径 或 自然语言需求描述"
    ),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    app_package: str = typer.Option("", "--app-package", "-p", help="App package name"),
    app_activity: str = typer.Option("", "--app-activity", "-a", help="App launch activity"),
    auto_yes: bool = typer.Option(
        False, "--auto-yes", "-y", help="跳过确认步骤，直接执行"
    ),
) -> None:
    """根据产品需求自动生成、执行测试用例并生成结构化报告。"""
    from testagent.cli.plan import plan_command as _plan_command

    _plan_command(
        requirement,
        name=name,
        app_package=app_package,
        app_activity=app_activity,
        auto_yes=auto_yes,
    )
