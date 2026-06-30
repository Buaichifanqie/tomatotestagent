"""清除指定 App 的 Context Memory 历史用例。

Usage:
    python scripts/clear_app_memory.py tv.danmaku.bili
"""
import asyncio
import sys

from testagent.config.settings import load_settings
from testagent.rag.factories import create_pipeline


async def clear_app_memory(app_id: str) -> None:
    settings = load_settings()
    pipeline = create_pipeline(settings)

    # Search for all docs with this app_id
    results = await pipeline.search(
        query=app_id,
        collection="app_test_cases",
        n_results=100,
        metadata_filter={"app_id": app_id},
    )

    if not results:
        print(f"No historical cases found for {app_id}")
        return

    doc_ids = [r.doc_id for r in results]
    print(f"Found {len(doc_ids)} document(s) for {app_id}, deleting...")

    await pipeline.vector_store.delete(doc_ids)
    try:
        await pipeline.fulltext.delete(doc_ids)
    except Exception as e:
        print(f"  [fulltext delete skipped: {e}]")

    print(f"Cleared {len(doc_ids)} documents for {app_id}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/clear_app_memory.py <app_id>")
        print("Example: python scripts/clear_app_memory.py tv.danmaku.bili")
        sys.exit(1)
    asyncio.run(clear_app_memory(sys.argv[1]))
