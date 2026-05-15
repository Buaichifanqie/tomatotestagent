from __future__ import annotations

from pathlib import Path

import typer

skill_app = typer.Typer(name="skill", help="Manage test skills", no_args_is_help=True)


@skill_app.command("list")
def skill_list() -> None:
    """List all registered skills."""
    from testagent.skills.registry import SkillRegistry

    registry = SkillRegistry()
    skills = registry.list_all()

    if not skills:
        typer.echo("No skills registered.")
        return

    typer.echo(f"{'Name':<30} {'Version':<12} {'Description'}")
    typer.echo("-" * 80)
    for s in skills:
        typer.echo(f"{s.name:<30} {s.version:<12} {s.description}")


@skill_app.command("create")
def skill_create(
    template: str = typer.Option("api_test", "--template", "-t", help="Skill template name"),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Output directory"),  # noqa: B008
) -> None:
    """Create a new skill from a template."""
    from testagent.skills.templates import TEMPLATES

    if template not in TEMPLATES:
        typer.echo(f"Unknown template: {template}. Available: {', '.join(TEMPLATES)}")
        raise typer.Exit(1)

    content = TEMPLATES[template]
    filepath = output / f"{template}.md"
    filepath.write_text(content, encoding="utf-8")
    typer.echo(f"Created skill at {filepath}")
