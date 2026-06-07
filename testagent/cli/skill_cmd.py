from __future__ import annotations

from pathlib import Path

import typer

from testagent.skills.scaffold import SkillScaffold

skill_app = typer.Typer(name="skill", help="Manage test skills", no_args_is_help=True)


@skill_app.command("list")
def skill_list(
    show_apps: bool = typer.Option(False, "--apps", "-a", help="Show App Skills only"),
) -> None:
    """List all registered skills."""
    from testagent.skills.registry import SkillRegistry
    from testagent.skills.app_skill_loader import AppSkillLoader

    if show_apps:
        # 只显示 App Skills
        skills_dir = Path("skills")
        apps_dir = skills_dir / "apps"
        loader = AppSkillLoader(apps_dir=apps_dir)
        apps = loader.list_apps()

        if not apps:
            typer.echo("No App Skills found.")
            typer.echo("Create one with: testagent skill create --name <app_name> --template app_test --output skills/apps")
            return

        typer.echo(f"{'App Name':<20} {'Version':<12} {'Sub-skills':<10} {'Description'}")
        typer.echo("-" * 80)
        for app_name in apps:
            app_skills = loader.load_app(app_name)
            if not app_skills:
                continue
            main = next((s for s in app_skills if s.is_main), app_skills[0])
            sub_count = len([s for s in app_skills if not s.is_main])
            desc = str(main.meta.get("description", ""))
            typer.echo(f"{app_name:<20} {main.version:<12} {sub_count:<10} {desc}")
    else:
        # 显示通用 Skills
        registry = SkillRegistry()
        skills = registry.list_all()

        if not skills:
            typer.echo("No skills registered.")
            return

        typer.echo(f"{'Name':<30} {'Version':<12} {'Description'}")
        typer.echo("-" * 80)
        for s in skills:
            typer.echo(f"{s.name:<30} {s.version:<12} {s.description}")

        # 同时显示 App Skills 数量
        skills_dir = Path("skills")
        apps_dir = skills_dir / "apps"
        if apps_dir.exists():
            loader = AppSkillLoader(apps_dir=apps_dir)
            app_count = len(loader.list_apps())
            if app_count > 0:
                typer.echo("")
                typer.echo(f"Also {app_count} App Skill(s) available. Use --apps to list them.")


@skill_app.command("create")
def skill_create(
    name: str = typer.Option(..., "--name", "-n", help="Skill name, also used as directory name"),
    template: str = typer.Option(
        "api_test",
        "--template",
        "-t",
        help="Template type: api_test/web_test/app_test/empty",
    ),
    output_dir: str = typer.Option("skills", "--output", "-o", help="Output directory for the skill scaffold"),
) -> None:
    """
    Create a Skill project scaffold.

    Generates a skills/<name>/ directory with:
    - SKILL.md: YAML Front Matter + Markdown Body template
    - README.md: Skill usage instructions

    Pre-fills required_mcp_servers and required_rag_collections based on the chosen template.
    """
    scaffold = SkillScaffold()

    try:
        result = scaffold.generate(name=name, template=template, output_dir=output_dir)
    except ValueError as exc:
        typer.echo(f"Error: {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"Created skill scaffold at: {result.skill_dir}")
    typer.echo(f"  SKILL.md  : {result.skill_md_path}")
    typer.echo(f"  README.md : {result.readme_path}")
    typer.echo("")
    typer.echo("To register and use this skill:")
    typer.echo("  testagent skill list")
    typer.echo(f"  testagent run --skill {name} --env staging")


@skill_app.command("generate")
def skill_generate(
    app: str = typer.Option(..., "--app", "-a", help="App name or package name to generate Skill for"),
    prd: str = typer.Option(None, "--prd", "-p", help="Path to PRD document for enhancement"),
    output_dir: str = typer.Option("skills/apps", "--output", "-o", help="Output directory"),
) -> None:
    """Generate an App Skill using AI exploration."""
    typer.echo(f"Generating App Skill for: {app}")
    if prd:
        typer.echo(f"PRD document: {prd}")
    typer.echo("This feature will be implemented in Phase 3.")
    typer.echo("For now, manually create the skill with:")
    typer.echo(f"  mkdir -p {output_dir}/{app}")
    typer.echo(f"  # Create {output_dir}/{app}/SKILL.md with App Skill format")
