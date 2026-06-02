from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from testagent.common.logging import get_logger

logger = get_logger(__name__)
_console = Console()

memory_typer = typer.Typer(name="memory", help="Manage App Context Memory (learned patterns, search, traces)")


@memory_typer.command("list-patterns")
def list_patterns(
    app_id: str = typer.Argument(help="App identifier (e.g. com.bilibili.app)"),
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by review status: pending, approved, rejected"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum number of patterns to show"),
) -> None:
    """List learned patterns for an app."""

    async def _run() -> None:
        from testagent.db.engine import get_session
        from testagent.db.repository import LearnedPatternRepository

        async with get_session() as session:
            repo = LearnedPatternRepository(session)
            patterns = await repo.get_by_app_id(app_id, status_filter=status, limit=limit)

        if not patterns:
            typer.echo("No patterns found.")
            return

        table = Table(title=f"Learned Patterns for {app_id}")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("Pattern", max_width=50)
        table.add_column("Type", style="magenta")
        table.add_column("Confidence", justify="right")
        table.add_column("Status", style="bold")
        table.add_column("Occurrences", justify="right")

        for p in patterns:
            pattern_text = p.pattern
            if len(pattern_text) > 47:
                pattern_text = pattern_text[:47] + "..."

            status_style = {
                "approved": "[green]approved[/green]",
                "rejected": "[red]rejected[/red]",
                "pending": "[yellow]pending[/yellow]",
            }.get(p.review_status, p.review_status)

            table.add_row(
                p.id[:12],
                pattern_text,
                p.pattern_type,
                f"{p.confidence:.2f}",
                status_style,
                str(p.occurrence_count),
            )

        _console.print(table)

    asyncio.run(_run())


@memory_typer.command("approve")
def approve(
    pattern_id: str = typer.Argument(help="Pattern ID to approve"),
) -> None:
    """Approve a learned pattern."""

    async def _run() -> None:
        from testagent.db.engine import get_session
        from testagent.db.repository import LearnedPatternRepository

        async with get_session() as session:
            repo = LearnedPatternRepository(session)
            pattern = await repo.approve(pattern_id)

        if pattern is None:
            typer.echo(f"Pattern '{pattern_id}' not found.")
            raise typer.Exit(1)

        typer.echo(f"Pattern '{pattern_id}' approved successfully.")

    asyncio.run(_run())


@memory_typer.command("reject")
def reject(
    pattern_id: str = typer.Argument(help="Pattern ID to reject"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for rejection"),
) -> None:
    """Reject a learned pattern."""

    async def _run() -> None:
        from testagent.db.engine import get_session
        from testagent.db.repository import LearnedPatternRepository

        async with get_session() as session:
            repo = LearnedPatternRepository(session)
            pattern = await repo.reject(pattern_id, reason=reason)

        if pattern is None:
            typer.echo(f"Pattern '{pattern_id}' not found.")
            raise typer.Exit(1)

        typer.echo(f"Pattern '{pattern_id}' rejected.")

    asyncio.run(_run())


@memory_typer.command("add-pattern")
def add_pattern(
    app_id: str = typer.Argument(help="App identifier"),
    pattern_text: str = typer.Argument(help="Pattern text content"),
    pattern_type: str = typer.Option("behavior", "--type", "-t", help="Pattern type: behavior, workaround, anti_pattern, failure_mode"),
) -> None:
    """Add a new learned pattern."""

    async def _run() -> None:
        from testagent.config.settings import get_settings
        from testagent.db.engine import get_session
        from testagent.db.repository import LearnedPatternRepository
        from testagent.models.learned_pattern import LearnedPattern
        from testagent.rag.factories import create_pipeline

        settings = get_settings()

        new_pattern = LearnedPattern(
            app_id=app_id,
            pattern=pattern_text,
            pattern_type=pattern_type,
            source_type="manual_entry",
            review_status="pending",
        )

        async with get_session() as session:
            repo = LearnedPatternRepository(session)
            created = await repo.create(new_pattern)

        # Write to RAG
        try:
            pipeline = create_pipeline(settings)
            await pipeline.write_back(
                content=pattern_text,
                collection="app_learned_patterns",
                metadata={
                    "app_id": app_id,
                    "pattern_type": pattern_type,
                    "pattern_id": created.id,
                },
                chunk_size=256,
            )
        except Exception as exc:
            logger.warning("Failed to write pattern to RAG: %s", exc, exc_info=exc)
            typer.echo(f"Warning: pattern saved to DB but RAG write-back failed: {exc}")

        typer.echo(f"Pattern '{created.id}' added successfully.")

    asyncio.run(_run())


@memory_typer.command("search")
def search(
    app_id: str = typer.Argument(help="App identifier"),
    query: str = typer.Argument(help="Search query"),
    search_type: str | None = typer.Option(None, "--type", "-t", help="Search type: case, pattern, or omit for both"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results per collection"),
) -> None:
    """Search App Context Memory for test cases and learned patterns."""

    async def _run() -> None:
        from testagent.config.settings import get_settings
        from testagent.rag.factories import create_pipeline

        settings = get_settings()
        pipeline = create_pipeline(settings)

        collections: dict[str, str] = {}
        if search_type is None or search_type == "case":
            collections["Test Cases"] = "app_test_cases"
        if search_type is None or search_type == "pattern":
            collections["Learned Patterns"] = "app_learned_patterns"

        has_results = False
        for label, collection in collections.items():
            filters = {"app_id": app_id}
            results = await pipeline.query(
                query_text=query,
                collection=collection,
                top_k=top_k,
                filters=filters,
            )

            if not results:
                typer.echo(f"No results found in {label}.")
                continue

            has_results = True
            table = Table(title=f"Search Results: {label} (query: {query})")
            table.add_column("#", justify="right")
            table.add_column("Doc ID", style="cyan", max_width=20)
            table.add_column("Score", justify="right")
            table.add_column("Content")

            for i, r in enumerate(results, 1):
                snippet = r.content[:80].replace("\n", " ")
                table.add_row(str(i), r.doc_id[:20], f"{r.score:.3f}", snippet)

            _console.print(table)

        if not has_results:
            typer.echo("No results found.")

    asyncio.run(_run())


@memory_typer.command("set-version")
def set_version(app_id: str, version: str) -> None:
    """Set the current version for an app."""

    async def _run() -> None:
        from testagent.db.engine import get_session
        from testagent.db.repository import AppVersionRepository

        async with get_session() as session:
            repo = AppVersionRepository(session)
            old = await repo.get_by_app_id(app_id)
            await repo.upsert(app_id, version, updated_by="cli")
            if old:
                typer.echo(f"  {app_id}: {old.current_version} -> {version}")
            else:
                typer.echo(f"  {app_id}: version set to {version}")

    asyncio.run(_run())


@memory_typer.command("stats")
def stats(app_id: str) -> None:
    """View App Memory statistics."""

    async def _run() -> None:
        from testagent.db.engine import get_session
        from testagent.db.repository import (
            AppVersionRepository,
            LearnedPatternRepository,
            RetrievalTraceRepository,
            TestCaseRecordRepository,
        )

        async with get_session() as session:
            # Version
            av_repo = AppVersionRepository(session)
            av = await av_repo.get_by_app_id(app_id)
            version = av.current_version if av else "not set"

            # Case stats
            tcr_repo = TestCaseRecordRepository(session)
            records = await tcr_repo.get_by_app_id(app_id, limit=1000)
            case_count = len(records)
            avg_confidence = sum(r.confidence for r in records) / case_count if case_count else 0

            # Top tags
            tag_counts: dict[str, int] = {}
            for r in records:
                for tag in r.tags.split(","):
                    tag = tag.strip()
                    if tag:
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
            top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:5]

            # Pattern stats
            lp_repo = LearnedPatternRepository(session)
            patterns = await lp_repo.get_by_app_id(app_id, limit=1000)
            approved = sum(1 for p in patterns if p.review_status == "approved")
            pending = sum(1 for p in patterns if p.review_status == "pending")
            rejected = sum(1 for p in patterns if p.review_status == "rejected")

            # Adoption score (from recent traces)
            rt_repo = RetrievalTraceRepository(session)
            traces = await rt_repo.get_by_app_id(app_id, limit=10)
            scores = [t.adoption_score for t in traces if t.adoption_score is not None]
            avg_adoption = sum(scores) / len(scores) if scores else 0.0

        # Display
        typer.echo(f"\nApp Memory Stats: {app_id} (v{version})")
        typer.echo("-" * 50)
        typer.echo(f"  Cases:          {case_count}")
        typer.echo(f"  Avg Confidence: {avg_confidence:.2f}")
        if top_tags:
            tag_str = " ".join(f"{t}({c})" for t, c in top_tags)
            typer.echo(f"  Top Scenarios:  {tag_str}")
        typer.echo(f"  Patterns:       {approved} approved, {pending} pending, {rejected} rejected")
        typer.echo(f"  Adoption Score: {avg_adoption:.2f} ({len(scores)} traces)")
        typer.echo("")

    asyncio.run(_run())


@memory_typer.command("upload-doc")
def upload_doc(
    app_id: str,
    file: str = typer.Argument(help="Document file path"),
    doc_type: str = typer.Option("user_guide", "--doc-type", help="Document type: release_note, user_guide, api_doc"),
) -> None:
    """Upload an app document to App Context Memory."""

    async def _run() -> None:
        from pathlib import Path

        from testagent.config.settings import get_settings
        from testagent.rag.factories import create_pipeline

        path = Path(file)
        if not path.exists():
            typer.echo(f"  File not found: {file}")
            raise typer.Exit(1)

        content = path.read_text(encoding="utf-8")
        if not content.strip():
            typer.echo("  File content is empty")
            raise typer.Exit(1)

        settings = get_settings()
        pipeline = create_pipeline(settings)
        await pipeline.write_back(
            content=content,
            collection="app_documentation",
            metadata={
                "app_id": app_id,
                "doc_type": doc_type,
                "source_file": path.name,
            },
            chunk_size=512,
        )
        typer.echo(f"  Uploaded: {path.name} -> app_documentation ({doc_type})")

    asyncio.run(_run())


@memory_typer.command("trace")
def trace(
    app_id: str = typer.Argument(help="App identifier"),
    days: int = typer.Option(7, "--days", "-d", help="Number of days to look back"),
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum number of traces to show"),
) -> None:
    """Show recent retrieval traces for an app."""

    async def _run() -> None:
        from testagent.db.engine import get_session
        from testagent.db.repository import RetrievalTraceRepository

        async with get_session() as session:
            repo = RetrievalTraceRepository(session)
            traces = await repo.get_by_app_id(app_id, limit=limit)

        if not traces:
            typer.echo("No traces found.")
            return

        table = Table(title=f"Retrieval Traces for {app_id} (last {days} days)")
        table.add_column("ID", style="cyan", max_width=12)
        table.add_column("Query", max_width=40)
        table.add_column("Stage", style="magenta")
        table.add_column("Adoption", justify="right")
        table.add_column("Items", justify="right")
        table.add_column("Cases", justify="right")

        for t in traces:
            query_text = t.query
            if len(query_text) > 37:
                query_text = query_text[:37] + "..."

            item_count = len(t.retrieved_items) if t.retrieved_items else 0
            case_count = len(t.generated_case_ids) if t.generated_case_ids else 0
            adoption = f"{t.adoption_score:.2f}" if t.adoption_score is not None else "-"

            table.add_row(
                t.id[:12],
                query_text,
                t.query_stage,
                adoption,
                str(item_count),
                str(case_count),
            )

        _console.print(table)

    asyncio.run(_run())
