from __future__ import annotations

from pathlib import Path  # noqa: TC003

import typer
from rich.console import Console
from rich.table import Table

_console = Console()


def rag_index(
    source: Path = typer.Argument(help="Source directory or file to index"),  # noqa: B008
    collection: str | None = typer.Option(None, "--collection", "-c", help="Target collection name"),
) -> None:
    """Index documents into RAG collection."""
    if not source.exists():
        typer.echo(f"Source path does not exist: {source}")
        raise typer.Exit(1)

    collection_name = collection or source.name.lower().replace(" ", "_")

    async def _index() -> int:
        from testagent.config.settings import get_settings
        from testagent.rag.factories import create_pipeline

        settings = get_settings()
        pipeline = create_pipeline(settings)
        return await pipeline.ingest(str(source), collection=collection_name)

    import asyncio

    count = asyncio.run(_index())
    typer.echo(f"Indexed {count} documents into collection '{collection_name}'")


def rag_query(
    query: str = typer.Argument(help="Search query"),
    collection: str = typer.Option(..., "--collection", "-c", help="Collection to search"),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of results"),
) -> None:
    """Query documents from RAG collection."""

    async def _query() -> list[dict[str, object]]:
        from testagent.config.settings import get_settings
        from testagent.rag.factories import create_pipeline

        settings = get_settings()
        pipeline = create_pipeline(settings)
        raw = await pipeline.query(query, collection=collection, top_k=top_k)
        return [
            {
                "source": r.doc_id,
                "content": r.content,
                "score": r.score,
            }
            for r in raw
        ]

    import asyncio

    results = asyncio.run(_query())

    if not results:
        typer.echo("No results found.")
        return

    table = Table(title=f"RAG Query Results (collection: {collection})")
    table.add_column("#", justify="right")
    table.add_column("Source", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Snippet")

    for i, r in enumerate(results, 1):
        raw_score: object = r.get("score", 0.0)
        score = 0.0
        if raw_score is not None:
            try:
                score = float(str(raw_score))
            except (ValueError, TypeError):
                score = 0.0
        snippet = str(r.get("content", ""))[:80].replace("\n", " ")
        table.add_row(
            str(i),
            str(r.get("source", "-")),
            f"{score:.3f}",
            snippet,
        )

    _console.print(table)
