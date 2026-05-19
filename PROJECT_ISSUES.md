# TestAgent 项目问题记录

> **记录时间**: 2026-05-19  
> **版本**: v0.1.0 (MVP)  
> **说明**: 此文件记录了代码审查中发现的问题和待改进点，供后续修复参考。

---

## 一、功能性问题

### 1.1 `testagent chat` 命令的 Agent Loop 缺少工具分发函数

**文件**: `testagent/cli/main.py:140`

```python
result = asyncio.run(agent_loop(messages, tools=[], system=system, llm_provider=llm))
```

**问题**: `agent_loop()` 的签名是 `(messages, tools, system, llm_provider, dispatch_fn=None, ...)`，但 `chat` 命令未传入 `dispatch_fn`。默认的 `_default_dispatch_fn` 使用 `TOOL_HANDLERS` 全局字典，而该字典为空。同时 `tools=[]` 空列表导致 LLM 无法获知可用的工具。因此聊天模式只能做纯文本对话，无法真正执行测试工具调用。

**影响**: 中 — chat 模式可用但功能受限。

### 1.2 `run_session` 函数不收集 Agent 执行结果

**文件**: `testagent/gateway/session.py:396-400`

```python
return {
    "session_id": session_id,
    "status": "completed",
    "tasks": [],
    "duration": "-",
}
```

**问题**: `run_session` 依次调用了 `planner.execute()`、`executor.execute()`、`analyzer.execute()`，但三者的返回值均未被解析和聚合到最终结果中。`tasks` 始终为空列表，`duration` 始终为 `"-"`。这意味着 CLI 执行 `testagent run` 后看不到具体的测试任务结果。

**影响**: 高 — `testagent run` 命令执行完成后无法展示实际的任务通过/失败详情。

### 1.3 Analyzer Agent 的 `_enrich_with_dedup` 存在潜在的类型问题

**文件**: `testagent/agent/analyzer.py:138-166`

**问题**: 当 `self._defect_deduplicator` 为 `None` 时，`_enrich_with_dedup` 方法中的 `assert self._defect_deduplicator is not None` 会在运行时抛出 `AssertionError`，而不是返回优雅的错误信息。

**影响**: 低 — 仅当传入 `defect_deduplicator=None` 时触发，当前默认值即为 `None`。

### 1.4 MicroVM Sandbox 可能存在空实现

**文件**: `testagent/harness/microvm_sandbox.py`

**问题**: MicroVM 隔离级别标记为 V1.0 功能，但 `SandboxFactory` 在 MVP 阶段就注册了 `MicroVMSandbox`。如果该实现是 stub/未完成的，调用时会出错。

**影响**: 低 — MVP 中 `app_test` 类型的任务默认使用 MicroVM，但 MVP 不包含 App 测试，所以不会触发。

---

## 二、API 完整性问题

### 2.1 RAG API 端点返回空结果

**文件**: `testagent/gateway/router.py:343-353`

```python
@router.post("/api/v1/rag/query")
async def rag_query(...):
    return {"data": {"query": query_text, "collection": collection_str, "top_k": top_k, "total": 0, "results": []}}
```

**问题**: RAG 查询 API 返回的是硬编码的空结果，未真正调用 RAG Pipeline 执行检索。

**影响**: 高 — API 消费者无法通过 HTTP 接口获得真实的 RAG 检索结果。

### 2.2 缺陷管理 API 未实现

**文件**: `testagent/gateway/router.py:412-446`

**问题**: `list_defects`、`get_defect`、`update_defect` 三个端点均返回空数据或 404。缺陷查询和管理功能未接入数据库。

**影响**: 中 — 但当前版本缺陷主要通过 CLI 和 Agent 自动归档，API 暂不是主要入口。

### 2.3 测试结果查询 API 未实现

**文件**: `testagent/gateway/router.py:160-173`

```python
@router.get("/api/v1/sessions/{session_id}/results")
async def get_session_results(...):
    return {"data": []}
```

**问题**: 会话结果查询接口返回空列表，未从数据库查询实际结果。

**影响**: 中 — 影响 HTTP 客户端获取测试结果。

---

## 三、配置与环境问题

### 3.1 默认 Embedding 模式可能无法正常工作

**文件**: `testagent/config/settings.py:52`

```python
embedding_mode: str = "local"
```

**文件**: `testagent/rag/factories.py:16`

**问题**: `embedding_mode` 默认为 `"local"`，但 `LocalEmbeddingService` 需要 `sentence-transformers` 库，该库不在 `pyproject.toml` 的依赖列表中。首次使用时，如果没有安装 `sentence-transformers`，会触发 `RAGDegradedError` 并降级到 `SimpleEmbeddingService`（基于哈希的伪 Embedding）。

**影响**: 中 — 功能会降级但不会崩溃，用户可能不知道正在使用伪 Embedding。

### 3.2 `psutil` 为可选依赖但无声明

**文件**: `testagent/gateway/router.py:475-483`

```python
try:
    import psutil
    ...
except ImportError:
    pass
```

**问题**: `psutil` 被用于系统资源监控，但未在 `pyproject.toml` 中声明为可选或必需依赖。

**影响**: 低 — 有 try/except 保护，功能降级安全。

---

## 四、代码质量问题

### 4.1 `run_session` 中的拼写错误

**文件**: `testagent/gateway/session.py:22`

```python
SESSION_TRANSITIONS: dict[str, set[str]] = {
```

**问题**: `TRANSITIONS` 应为 `TRANSITIONS`（缺少 `I`），正确拼写是 `TRANSITIONS`。

**影响**: 低 — 仅是变量名拼写问题，不影响功能。

### 4.2 异常变量命名不统一

**文件**: `testagent/agent/loop.py:90`

```python
except Exception as exc:
```

在 `loop.py` 的其他地方使用了 `e` 作为异常变量名（如 `line 183: exc` vs 其他文件的 `e`），命名不够统一。

**影响**: 低 — Python 规范允许，但不一致。

### 4.3 CLI `chat` 命令使用了错误的 LLM Provider

**文件**: `testagent/cli/main.py:139-140`

```python
from testagent.config.settings import get_settings
from testagent.llm.openai_provider import OpenAIProvider

system = "You are TestAgent, an AI testing assistant."
llm = OpenAIProvider(get_settings())
```

**问题**: `chat` 命令硬编码了 `OpenAIProvider`，未使用 `LLMProviderFactory`。如果用户配置了 `TESTAGENT_LLM_PROVIDER=local`，chat 模式仍会尝试连接 OpenAI。

**影响**: 中 — 限制了 chat 模式的灵活性。

---

## 五、缺失功能

### 5.1 无 `.env.example` 文件

**问题**: 项目根目录没有 `.env.example` 模板文件。新用户需要查看文档来了解需要配置哪些环境变量。

**影响**: 低 — 配置说明在 `USAGE_GUIDE.md` 中有描述。

### 5.2 Meilisearch 健康检查可能失败

**文件**: `docker/docker-compose.dev.yml:53`

```yaml
test: ["CMD", "curl", "-f", "http://localhost:7700/health"]
```

**问题**: Meilisearch 容器中可能没有安装 `curl`。某些版本的 Meilisearch 镜像基于 Alpine，可能不包含 curl。

**影响**: 低 — 健康检查会失败但服务本身可正常工作。

---

## 六、建议改进

### 6.1 Agent 执行结果收集

`run_session` 函数应解析 `planner.execute()`、`executor.execute()`、`analyzer.execute()` 的返回值，提取具体的任务列表、执行状态、分析结果，填入最终返回结果中。

### 6.2 RAG API 接入真实 Pipeline

`/api/v1/rag/query` 端点应通过 `create_pipeline(settings)` 创建 RAG Pipeline 实例，并调用 `pipeline.query()` 方法返回真实结果。

### 6.3 Chat 模式接入 LLMProviderFactory

`testagent chat` 应使用 `LLMProviderFactory.create(settings)` 来创建 LLM Provider，而非硬编码 `OpenAIProvider`。

### 6.4 添加 `sentence-transformers` 依赖

在 `pyproject.toml` 的 `[project.optional-dependencies]` 中增加 `sentence-transformers`，确保本地 Embedding 模式可用。

### 6.5 补充 `.env.example` 文件

创建 `.env.example` 文件，列出所有可配置的环境变量及其说明。
