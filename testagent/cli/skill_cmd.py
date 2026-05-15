from __future__ import annotations

import typer

from testagent.skills.scaffold import SkillScaffold

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
