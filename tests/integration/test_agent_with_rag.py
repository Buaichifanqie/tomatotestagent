from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import testagent.agent.context as context_module
from testagent.agent.context import AgentType, ContextAssembler
from testagent.agent.planner import PlannerAgent
from testagent.common.errors import RAGDegradedError
from testagent.rag.embedding import IEmbeddingService
from testagent.rag.fulltext import IFullTextSearch
from testagent.rag.pipeline import RAGPipeline
from testagent.rag.vector_store import IVectorStore

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings


@pytest.mark.asyncio
async def test_agent_with_rag_context(
    test_settings: TestAgentSettings,
    mock_llm_provider: MagicMock,
) -> None:
    """
    验证 Agent 具备知识检索能力:

    1. 创建 mock Embedding / VectorStore / FullText 服务, 模拟 RAG 检索返回文档
    2. 创建 RAGPipeline + ContextAssembler, 注入 PlannerAgent
    3. 执行带有 rag_query 的规划任务
    4. 验证 system_prompt 中包含 "# Retrieved Knowledge" 节
    5. 验证 messages 中包含 "[RAG Context]" 消息块
    """
    mock_embedding = MagicMock(spec=IEmbeddingService)
    mock_embedding.embed = AsyncMock(return_value=[0.1] * 10)
    mock_embedding.embed_batch = AsyncMock(return_value=[[0.1] * 10])

    mock_vector_store = MagicMock(spec=IVectorStore)
    mock_vector_store.search = AsyncMock(
        return_value=[
            {
                "id": "doc_v1",
                "score": 0.92,
                "metadata": {"collection": "req_docs"},
                "document": "Login API requires username and password.",
            },
            {
                "id": "doc_v2",
                "score": 0.88,
                "metadata": {"collection": "req_docs"},
                "document": "Password must be at least 8 characters.",
            },
        ]
    )

    mock_fulltext = MagicMock(spec=IFullTextSearch)
    mock_fulltext.search = AsyncMock(
        return_value=[
            {
                "id": "doc_f1",
                "score": 0.85,
                "metadata": {"collection": "req_docs"},
                "document": "Login endpoint: POST /api/v1/login",
            },
        ]
    )

    rag_pipeline = RAGPipeline(
        embedding_service=mock_embedding,
        vector_store=mock_vector_store,
        fulltext=mock_fulltext,
    )

    context_assembler = ContextAssembler(
        settings=test_settings,
        rag_pipeline=rag_pipeline,
    )

    agent = PlannerAgent(llm=mock_llm_provider, context_assembler=context_assembler)

    result = await agent.execute(
        {
            "task_type": "plan",
            "requirement": "User login feature",
            "rag_query": "login API authentication",
        }
    )

    call_kwargs = mock_llm_provider.chat.call_args
    assert call_kwargs is not None, "LLM provider should have been called"
    system_prompt: str = call_kwargs.kwargs.get("system", "")

    assert "# Retrieved Knowledge" in system_prompt, "system_prompt should contain RAG knowledge section"
    assert "req_docs" in system_prompt, "system_prompt should contain collection name from RAG"
    assert "Login API" in system_prompt, "system_prompt should contain RAG document content"

    messages: list[dict[str, Any]] = call_kwargs.kwargs.get("messages", [])
    rag_context_msgs = [m for m in messages if isinstance(m.get("content"), str) and "[RAG Context]" in m["content"]]
    assert len(rag_context_msgs) >= 1, (
        f"messages should contain at least 1 [RAG Context] block, got {len(rag_context_msgs)}"
    )

    assert result["agent_type"] == "planner"
    assert "plan" in result


@pytest.mark.asyncio
async def test_rag_degradation(
    test_settings: TestAgentSettings,
) -> None:
    """
    验证 Embedding 服务不可用时的降级行为:

    1. 创建 Embedding mock, embed() 抛出 RAGDegradedError
    2. 创建 RAGPipeline + ContextAssembler
    3. 执行 assemble 并验证不崩溃, rag_context 为空
    4. 验证日志输出 "RAG query failed" 告警
    5. 验证 system_prompt 仍包含主要身份和行为准则节
    """
    degraded_embedding = MagicMock(spec=IEmbeddingService)
    degraded_embedding.embed = AsyncMock(
        side_effect=RAGDegradedError(
            "Embedding service unavailable",
            code="EMBED_SERVICE_DOWN",
        ),
    )
    degraded_embedding.embed_batch = AsyncMock(
        side_effect=RAGDegradedError(
            "Embedding service unavailable",
            code="EMBED_SERVICE_DOWN",
        ),
    )

    mock_vector_store = MagicMock(spec=IVectorStore)
    mock_fulltext = MagicMock(spec=IFullTextSearch)
    mock_fulltext.search = AsyncMock(return_value=[])

    rag_pipeline = RAGPipeline(
        embedding_service=degraded_embedding,
        vector_store=mock_vector_store,
        fulltext=mock_fulltext,
    )

    context_assembler = ContextAssembler(
        settings=test_settings,
        rag_pipeline=rag_pipeline,
    )

    with patch.object(context_module.logger, "warning") as mock_warning:
        context = await context_assembler.assemble(
            agent_type=AgentType.PLANNER,
            rag_query="login API",
        )

    assert mock_warning.called, "logger.warning should have been called during RAG degradation"

    matching_calls = [
        args[0]
        for args, _ in mock_warning.call_args_list
        if args and isinstance(args[0], str) and "RAG query failed" in args[0]
    ]
    assert matching_calls, (
        f"should have logged at least one 'RAG query failed' warning, "
        f"got: {[str(a) for a, _ in mock_warning.call_args_list]}"
    )

    assert context.rag_context == [], "rag_context should be empty when embedding service is degraded"
    assert "# Retrieved Knowledge" not in context.system_prompt, (
        "system_prompt should not contain RAG section when degraded"
    )
    assert context.system_prompt, "system_prompt should still be populated despite RAG degradation"
    assert "# Agent Identity" in context.system_prompt
    assert "# Behavioral Guidelines" in context.system_prompt


@pytest.mark.asyncio
async def test_knowledge_loop() -> None:
    """
    验证知识闭环: write_back -> query -> write_back -> query 完整流程

    1. 创建基于 dict 的内存 mock 存储, 追踪 upsert/index 的文档
    2. 创建 RAGPipeline
    3. write_back 之前 query: 结果为空
    4. write_back 写入分析结果
    5. 再次 query: 新知识可被检索到
    6. 再次 write_back 写入额外知识
    7. 第三次 query: 新旧知识均可被检索到
    """
    doc_store: dict[str, dict[str, Any]] = {}

    async def mock_upsert(docs: list[dict[str, Any]]) -> None:
        for doc in docs:
            doc_store[str(doc["id"])] = doc

    async def mock_search(
        query: str | list[float] | None = None,
        query_vector: list[float] | None = None,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for doc_id, doc in list(doc_store.items()):
            meta = doc.get("metadata", {})
            if filters and "collection" in filters and meta.get("collection") != filters["collection"]:
                continue
            results.append(
                {
                    "id": doc_id,
                    "score": 0.5,
                    "metadata": dict(meta),
                    "document": str(doc.get("document", "")),
                }
            )
        return results[:top_k]

    mock_embedding = MagicMock(spec=IEmbeddingService)
    mock_embedding.embed = AsyncMock(return_value=[0.1] * 10)
    mock_embedding.embed_batch = AsyncMock(return_value=[[0.1] * 10])

    mock_vector_store = MagicMock(spec=IVectorStore)
    mock_vector_store.upsert = AsyncMock(side_effect=mock_upsert)
    mock_vector_store.search = AsyncMock(side_effect=mock_search)
    mock_vector_store.delete = AsyncMock()

    mock_fulltext = MagicMock(spec=IFullTextSearch)
    mock_fulltext.index = AsyncMock(side_effect=mock_upsert)
    mock_fulltext.search = AsyncMock(side_effect=mock_search)
    mock_fulltext.delete = AsyncMock()

    rag_pipeline = RAGPipeline(
        embedding_service=mock_embedding,
        vector_store=mock_vector_store,
        fulltext=mock_fulltext,
    )

    results_before = await rag_pipeline.query("login", "req_docs", top_k=3)
    assert len(results_before) == 0, "query before write_back should return empty results"

    await rag_pipeline.write_back(
        content="Login API uses JWT tokens with 30-minute expiry. Error code 401 for invalid credentials.",
        collection="req_docs",
        metadata={"source": "analyzer", "type": "analysis_result"},
    )

    results_after = await rag_pipeline.query("login JWT", "req_docs", top_k=3)
    assert len(results_after) >= 1, f"should find at least 1 result after write_back, got {len(results_after)}"
    found_jwt = any("JWT" in r.content for r in results_after)
    assert found_jwt, "query should retrieve the written-back knowledge content containing 'JWT'"

    await rag_pipeline.write_back(
        content="Login rate limit: 10 requests per minute per IP. Returns 429 status code.",
        collection="req_docs",
        metadata={"source": "analyzer", "type": "analysis_result"},
    )

    results_final = await rag_pipeline.query("login", "req_docs", top_k=5)
    assert len(results_final) >= 2, (
        f"should find at least 2 results after second write_back (JWT + rate limit), got {len(results_final)}"
    )
    all_content = " ".join(r.content for r in results_final)
    assert "JWT" in all_content, "first write_back content (JWT) should still be retrievable"
    assert "rate limit" in all_content.lower(), "second write_back content (rate limit) should be retrievable"
