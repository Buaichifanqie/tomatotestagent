# TestAgent — AI 编码助手指令

> 本文件是 AI 编码助手在每次会话启动时自动读取的项目规则文件。所有规则必须直接执行，不可忽略。

---

## 项目身份

TestAgent 是面向 App(iOS/Android)/Web/API 全平台的 AI 测试智能体平台，通过 Planner→Executor→Analyzer 三层 Agent 协作、MCP 工具调用、RAG 知识检索、Skills 技能编排和 Harness 沙箱执行，实现测试全生命周期自主化。

技术栈：Python 3.12+ / FastAPI / Celery+Redis / SQLAlchemy 2.x / Typer+Rich / ChromaDB(MVP)→Milvus(V1.0) / Meilisearch / Playwright / httpx / Docker / MCP Python SDK / OpenAI GPT-4o + 本地模型(Qwen2.5/Ollama)

测试领域原语：测试计划(TestPlan) / 测试用例(TestTask) / 断言(assertion_results) / 覆盖率(coverage) / 缺陷(Defect) / 失败分类(bug/flaky/environment/configuration) / 自愈(self_healing)

---

## 构建与运行命令

```bash
# 环境准备
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 数据库迁移
alembic upgrade head

# 启动 Gateway 服务
testagent serve --host 0.0.0.0 --port 8000

# 启动 Celery Worker
celery -A testagent.gateway.app worker --loglevel=info --concurrency=4

# 启动 Redis（Docker）
docker run -d --name testagent-redis -p 6379:6379 redis:7-alpine

# CLI 核心命令
testagent init --project my-app --type web+api
testagent run --skill api_smoke_test --env staging
testagent run --skill web_smoke_test --url https://staging.myapp.com
testagent run --plan ./test-plan.json
testagent chat
testagent ci --skill regression --exit-code
testagent skill list
testagent skill create --template api_test
testagent mcp add jira-server --config ./jira.json
testagent rag index --source ./docs
testagent report --session <session_id>

# 测试
pytest tests/unit/ -v
pytest tests/integration/ -v --timeout=60
pytest tests/e2e/ -v --timeout=300
pytest --cov=testagent --cov-report=term-missing

# 代码质量
ruff check . --fix
ruff format .
mypy testagent/ --strict

# Docker 沙箱镜像构建
docker build -f docker/Dockerfile.harness -t testagent/harness:latest .
docker build -f docker/Dockerfile.api_runner -t testagent/api-runner:latest .
docker build -f docker/Dockerfile.web_runner -t testagent/web-runner:latest .
```

---

## 目录结构与模块职责

```
testagent/
├── testagent/gateway/          # TestGateway 调度层：FastAPI 路由、WebSocket 会话、MCP 注册发现与路由
├── testagent/agent/            # Agent Runtime：ReAct Loop、上下文组装、Planner/Executor/Analyzer 实现
├── testagent/mcp_servers/      # MCP Server 实现：Playwright/API/Jira/Git/Database Server
├── testagent/rag/              # RAG Pipeline：摄入、分块、Embedding、向量/全文检索、RRF 融合、重排
├── testagent/harness/          # Harness 执行引擎：沙箱编排、Docker/MicroVM/本地三级隔离、Runner 插件
├── testagent/skills/           # Skill Engine：加载、解析、校验、注册、匹配、两层注入执行
├── testagent/models/           # 数据模型：TestSession/TestPlan/TestTask/TestResult/Defect/SkillDefinition/MCPConfig
├── testagent/db/               # 数据库访问层：Engine、Repository、迁移辅助
├── testagent/llm/              # LLM Provider 抽象层：ILLMProvider 接口、OpenAI/本地模型适配
├── testagent/cli/              # CLI 交互层：Typer 命令、Rich 输出格式化
├── testagent/config/           # 配置管理：Pydantic Settings、环境变量、默认常量
├── testagent/common/           # 公共工具：结构化日志、统一异常、安全脱敏
├── skills/                     # Skill Markdown 定义文件（Git 管理，与代码分离）
├── configs/                    # 配置模板：mcp.json.template、rag_config.yaml.template
├── tests/                      # 测试：unit/integration/e2e 三级
└── docker/                     # Docker 镜像定义：harness/api_runner/web_runner
```

**模块边界规则**：模块间通过 `protocol.py` / `base.py` 定义的接口通信，禁止直接引用其他模块内部实现。`skills/` 目录存放 Skill Markdown 定义，`testagent/skills/` 存放 Skill Engine 代码，两者必须分离。

### 关键文件导航

| 文件 | 职责 | 首次修改必读 |
|------|------|-------------|
| `testagent/agent/loop.py` | 核心 ReAct Loop 实现 | ✅ |
| `testagent/agent/context.py` | 上下文组装器（AGENTS/SOUL/TOOLS/MEMORY 四层注入） | ✅ |
| `testagent/agent/protocol.py` | Agent 间消息协议 JSON Schema | ✅ |
| `testagent/gateway/mcp_registry.py` | MCP Server 注册发现 + 健康检查 | 修改 MCP 相关时 |
| `testagent/gateway/mcp_router.py` | MCP 工具调用路由 + 审计日志 | 修改 MCP 相关时 |
| `testagent/rag/fusion.py` | RRF 融合排序实现 | 修改 RAG 相关时 |
| `testagent/harness/orchestrator.py` | 隔离级别决策 + 沙箱编排 | 修改 Harness 相关时 |
| `testagent/harness/sandbox_factory.py` | 根据 isolation_level 创建 Sandbox | 新增隔离级别时 |
| `testagent/skills/parser.py` | YAML Front Matter + Markdown Body 解析 | 新增 Skill 字段时 |
| `testagent/models/base.py` | BaseModel + 通用 Mixin | 新增数据模型时 |

---

## ADR 决策转化规则

### ADR-001：自研 Agent Loop [MVP]

✅ 必须使用自研 ReAct Loop（`while stop_reason != "tool_use"` 单退出条件循环）
✅ Agent Loop 循环体必须永远不变，所有机制（工具扩展、上下文压缩、Skill 注入）在循环前/后叠加为 Harness 逻辑
🚫 禁止引入 LangGraph、CrewAI、LangChain Agent 依赖
🚫 禁止在循环体内添加 break/return 以外的退出路径

```python
async def agent_loop(messages, tools, system, max_rounds=50):
    for _ in range(max_rounds):
        microcompact(messages)
        if estimate_tokens(messages) > token_threshold:
            messages[:] = auto_compact(messages)
        response = await llm_provider.chat(system=system, messages=messages, tools=tools)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return messages
        tool_results = [await dispatch_tool(b.name, b.input) for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": tool_results})
    return messages
```

### ADR-002：MCP Python SDK + Gateway 代理 [MVP]

✅ 必须使用 Anthropic 官方 `mcp` Python SDK，MCP Server 以 stdio 子进程启动
✅ 必须通过 Gateway 的 MCP Registry 统一管理 Server 生命周期和路由
✅ 工具调用必须经过 Gateway 审计日志记录
🚫 禁止直接 HTTP 调用 MCP Server 端点绕过 SDK
🚫 禁止自研 RPC 协议替代 MCP 标准协议

### ADR-003：混合检索架构 [MVP]

✅ 必须实现双路召回：Embedding 向量检索 + BM25 关键词检索
✅ 必须通过 RRF（Reciprocal Rank Fusion）融合排序，k=60
✅ RAG Pipeline 对外暴露统一 `query()` 接口（Facade Pattern）
🚫 禁止使用纯向量检索（API 路径 `/api/v2/orders`、类名 `OrderService` 等精确匹配是刚需）
🚫 禁止引入 LlamaIndex 框架

### ADR-004：三级隔离方案 [MVP+V1.0]

✅ 必须支持 Docker Container / MicroVM / 本地进程 三级隔离
✅ API 测试和 Web 测试必须使用 Docker Container 隔离 [MVP]
✅ App 测试必须使用 MicroVM 隔离 [V1.0]
✅ 开发调试模式允许使用本地进程（仅限本地开发环境）
✅ 必须通过 `SandboxFactory`（Factory Pattern）创建对应 Sandbox 实例，Strategy Pattern 实现运行时切换
🚫 禁止所有测试统一在 Docker 中执行（App 场景隔离不足）
🚫 禁止引入 Kubernetes Pod 作为隔离单元（MVP 阶段过重）

### ADR-005：Celery + Redis 任务调度 [MVP]

✅ 必须使用 Celery 作为分布式任务队列，Redis 作为 Broker 和 Result Backend
✅ 任务必须持久化到 Redis，支持断点续跑
✅ 必须支持重试（指数退避 2s→4s→8s）、超时、优先级队列
🚫 禁止使用 Python asyncio.Queue 替代（不支持分布式、进程崩溃任务丢失）
🚫 禁止引入 Temporal 工作流引擎（学习曲线陡、本地部署重）

### ADR-006：SQLite(MVP) → PostgreSQL(V1.0) 数据存储 [MVP+V1.0]

✅ MVP 必须使用 SQLite 嵌入式零配置，WAL 模式启用并发读
✅ 必须使用 SQLAlchemy 2.x ORM 抽象，确保 SQLite→PostgreSQL 迁移无需改业务代码
✅ V1.0 必须迁移到 PostgreSQL，启用 JSONB 索引、AsyncSession Pool、pg_trgm 全文检索
✅ 迁移必须通过 Alembic 管理
🚫 禁止引入 MongoDB（缺陷结构化查询能力弱）
🚫 禁止 MVP 阶段强制 PostgreSQL 依赖（本地开发体验差）

---

## 代码风格与 Agent 架构规则

### 代码风格硬约束

✅ Python 3.12+，所有函数参数和返回值必须添加类型标注
✅ 所有 I/O 操作必须使用 `async/await`，禁止同步阻塞调用（`requests`/`time.sleep`/同步文件 I/O）
✅ 必须使用 `httpx.AsyncClient` 替代 `requests`
✅ 必须使用 `pydantic` 做数据校验和 Settings 管理
✅ 必须使用 `ruff` 作为 linter 和 formatter，`mypy --strict` 作为类型检查
✅ 所有异常必须继承 `testagent.common.errors.TestAgentError` 体系
✅ 日志必须使用 `testagent.common.logging` 结构化日志，禁止 `print()` 输出
🚫 禁止引入 LangChain/LangGraph/CrewAI/LlamaIndex 依赖
🚫 禁止使用 `requests` 库（必须用 `httpx`）
🚫 禁止使用 `time.sleep()`（必须用 `asyncio.sleep()`）
🚫 禁止在业务代码中硬编码 API Key / 密码，必须通过环境变量或 Keyring 注入
🚫 禁止在日志或测试报告中记录明文密钥或 PII 数据

### 三层 Agent 职责与隔离

| Agent | 职责 | 上下文窗口 | 工具集 | RAG 访问 | 并发数 |
|-------|------|-----------|-------|---------|-------|
| Planner Agent | 需求解析、策略生成、任务编排 | 128K | MCP: Jira, Git; Skills: 策略类 | 需求库、缺陷库 | 1（串行） |
| Executor Agent | 测试执行、自愈修复、结果收集 | 32K | MCP: Playwright, API; Harness Runner | 定位器库、环境配置 | 1-10（并行） |
| Analyzer Agent | 失败分类、根因分析、缺陷归档 | 64K | MCP: Jira, Git; Skills: 分析类 | 缺陷库、失败模式库 | 1（串行） |

✅ 每个 Agent 以空 `messages=[]` 启动，只有分配的 task prompt 作为第一条消息，完全隔离上下文
✅ Agent 间必须通过 Gateway 的结构化消息协议通信，禁止共享消息历史
✅ 必须实现三层压缩策略：microcompact（每轮去冗余）→ auto_compact（超阈值摘要）→ identity re-injection（压缩后重注身份）
✅ 最大循环轮次 50，token 阈值 100000
✅ 上下文组装顺序：AGENTS.md → SOUL.md → TOOLS.md → Skill Layer 1 注入 → RAG 检索结果
✅ LLM API 429 限流时必须自动排队重试；Planner Agent 优先级最高，Executor 次之，Analyzer 最低
✅ 预算耗尽后仅保留 Planner Agent 核心规划功能，其他 Agent 使用缓存结果或规则引擎

### MCP 通信协议规则

✅ Agent 间消息必须符合 `testagent/agent/protocol.py` 定义的 JSON Schema
✅ 每条消息必须包含 `message_id`（UUID），接收方必须幂等去重
✅ 消息类型限于：`task_assignment` / `result_report` / `query` / `notification` / `ack` / `error`
✅ `sender` 和 `receiver` 必须匹配 `^(planner|executor_\\d+|analyzer|gateway|cli|broadcast)$`
✅ 发送方 30s 未收到 ACK 必须重发，最多 3 次
✅ Gateway 必须校验消息顺序是否符合 Session 状态机：`planning → executing → analyzing`
✅ 所有 Agent 间消息必须持久化到 Redis Stream（append-only），保证至少一次投递
✅ 消息重传 3 次仍失败 → 标记目标 Agent 为 unreachable → 暂停该 Session → 通知用户 + 保存断点快照
🚫 禁止 Agent 间直接通信，必须经由 Gateway 中转

---

## RAG 管道与 Skills 机制规则

### RAG 管道

✅ 文档分块策略：Markdown 按标题层级（`##` 为边界）、代码按函数/类、文本 512 tokens + 64 overlap
✅ 必须双路写入：向量索引（ChromaDB [MVP] / Milvus [V1.0]）+ 全文索引（Meilisearch）
✅ 向量检索必须召回 `top_k * 2` 候选，关键词检索同样 `top_k * 2`，RRF 融合后截取 `top_k`
✅ 检索时必须支持 metadata filters（模块、严重度、时间范围）
✅ V1.0 必须增加 Cross-Encoder 重排序 [V1.0]
✅ Embedding 模型必须支持双模式：`bge-large-zh-v1.5`（本地）/ `text-embedding-3-small`（API）
✅ 分析结果必须写回 RAG，形成知识闭环（越用越准的核心机制）
🚫 禁止仅使用向量检索，API 路径和类名必须通过 BM25 精确匹配
🚫 Embedding 服务不可用时必须降级到纯 BM25 检索，禁止静默失败

### RAG Collection 管理 [MVP]

| Collection | 数据源 | 索引策略 | 访问权限 |
|-----------|--------|---------|---------|
| `req_docs` | 需求文档 PRD | 向量+全文 | Planner |
| `api_docs` | OpenAPI/Swagger 规范 | 向量+全文 | Planner, Executor |
| `defect_history` | 历史缺陷 Jira | 向量+全文+结构化 | Planner, Analyzer |
| `test_reports` | 历史测试报告 | 向量+全文 | Analyzer |
| `locator_library` | UI 定位器库 | 向量+全文 | Executor |
| `failure_patterns` | 失败模式库 | 向量+结构化 | Analyzer |

### Skills 机制

✅ Skill 必须使用 YAML Front Matter + Markdown Body 格式定义，存放在 `skills/` 目录
✅ Front Matter 必填字段：`name`、`version`、`description`、`trigger`、`required_mcp_servers`、`required_rag_collections`
✅ Markdown Body 必须包含：目标、操作流程、断言策略、失败处理
✅ Skill 以 `name+version` 为唯一键注册
✅ 必须实现两层注入：Layer 1 将名称+短描述（~100 tokens/skill）写入系统提示；Layer 2 模型调用 `load_skill()` 时注入完整正文
✅ Skill 加载时必须校验 `required_mcp_servers` 是否在 MCPRegistry 中注册、`required_rag_collections` 是否存在
✅ 匹配规则：trigger 模式匹配 → 关键词加权 → 返回最高分 Skill
🚫 禁止在系统提示中注入所有 Skill 全文（会导致 prompt 膨胀）
🚫 Skill 解析失败必须跳过该 Skill 并日志告警，禁止阻塞启动
🚫 `required_mcp_servers` 未注册时 Skill 必须标记为 "degraded"，禁止静默忽略

---

## Harness 与数据模型规则

### Harness 执行引擎

✅ 隔离级别决策优先级：用户显式指定 > 任务类型自动决策（api_test→Docker, web_test→Docker, app_test→MicroVM）
✅ Docker 沙箱必须配置：`--security-opt=no-new-privileges` + read-only rootfs + mem_limit + cpus 限制
✅ 资源配额：API 测试 1CPU/512MB/30s 每请求；Web 测试 2CPU/2GB/无头 Chromium；App 测试 4CPU/4GB [V1.0]
✅ 所有沙箱必须设置硬超时：API 60s、Web 120s、App 180s，超时后强制 `docker kill`
✅ 每 10 分钟必须扫描 exited 容器 + dangling 镜像并自动清理
✅ 磁盘使用超 80% 阈值必须暂停新任务创建，超 90% 紧急清理所有非活跃容器
✅ Runner 必须实现 `IRunner` 抽象类：`setup()` / `execute()` / `teardown()` / `collect_results()`
✅ 沙箱销毁时必须自动清理所有临时数据（用后即焚）
✅ MCP Server 崩溃时：Gateway 每 30s 心跳检测 → 连续 3 次失败标记 unhealthy → 自动拉起最多 3 次 → 超限标记不可用并通知用户
🚫 禁止测试沙箱访问内网非被测服务（网络白名单隔离）
🚫 禁止本地进程模式用于生产或 CI 环境（仅限开发调试）

### 数据模型约定

✅ 所有模型必须继承 `testagent.models.base.BaseModel`，包含 `id`(UUID PK)、`created_at`
✅ 必须使用 SQLAlchemy 2.x 声明式映射 + `Mapped` 类型标注
✅ Session 状态机必须严格遵循：`pending → planning → executing → analyzing → completed/failed`
✅ Task 状态必须严格遵循：`queued → running → passed/failed/flaky/skipped/retrying`
✅ Defect 分类必须使用：`bug` / `flaky` / `environment` / `configuration`
✅ Defect 严重度必须使用：`critical` / `major` / `minor` / `trivial`
✅ 迁移必须通过 Alembic 管理，禁止手动修改数据库 Schema
✅ MVP 使用 SQLite + WAL 模式 + JSON1 Extension + FTS5 Extension
✅ V1.0 迁移 PostgreSQL 必须启用 JSONB 索引 + GIN 索引 + AsyncSession Pool
✅ 每个 Executor Agent 在独立数据库 schema/database 中运行（并发数据隔离）
🚫 禁止绕过 Repository 层直接操作 Engine/Session
🚫 禁止在模型层编写业务逻辑
🚫 禁止并行 Executor Agent 共享数据库 schema（必须独立隔离防止 flaky）

### 预置 Skill 清单

| Skill 名称 | 描述 | 涉及测试类型 | 预置阶段 | required_mcp_servers | required_rag_collections |
|------------|------|-------------|---------|---------------------|------------------------|
| `api_smoke_test` | API 冒烟测试 | API | MVP | api_server, database_server | api_docs, defect_history |
| `api_regression_test` | API 回归测试（含边界值/异常值） | API | MVP | api_server, database_server | api_docs, defect_history |
| `web_smoke_test` | Web 页面冒烟测试 | Web | MVP | playwright_server | req_docs, locator_library |
| `app_smoke_test` | App 核心流程冒烟测试 | App | MVP | appium_server | req_docs, locator_library |
| `web_visual_test` | Web 视觉回归测试 | Web | V1.0 | playwright_server | test_reports |
| `full_regression_test` | 全量回归测试编排 | All | V1.0 | api_server, playwright_server | api_docs, defect_history |

---

## 常见模式与反模式

### ✅ 正确模式

```python
# Agent Loop：循环体不变，机制叠加
async def agent_loop(messages, tools, system, max_rounds=50):
    for _ in range(max_rounds):
        microcompact(messages)
        response = await llm_provider.chat(system=system, messages=messages, tools=tools)
        if response.stop_reason != "tool_use":
            return messages
        tool_results = [await dispatch_tool(b.name, b.input) for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": tool_results})

# RRF 融合：双路召回统一排序
def rrf_fusion(vector_results, keyword_results, k=60):
    scores = {}
    for rank, r in enumerate(vector_results):
        scores[r.doc_id] = scores.get(r.doc_id, 0) + 1.0 / (k + rank + 1)
    for rank, r in enumerate(keyword_results):
        scores[r.doc_id] = scores.get(r.doc_id, 0) + 1.0 / (k + rank + 1)
    return sorted(scores, key=scores.get, reverse=True)

# 隔离级别：Factory + Strategy
level = orchestrator.decide_isolation(task)
sandbox = sandbox_factory.create(level)
```

### 🚫 反模式

```python
# 反模式：在循环体中添加 break 退出
if some_condition:
    break  # 违反 ADR-001

# 反模式：纯向量检索
results = vector_store.search(query_vector, top_k=5)  # 违反 ADR-003

# 反模式：同步阻塞调用
import requests; r = requests.get(url)  # 违反 async 优先

# 反模式：Agent 直接通信
executor.send_message(analyzer, result)  # 必须经 Gateway

# 反模式：硬编码密钥
API_KEY = "sk-xxxxx"  # 必须用环境变量
```

---

## Do / Don't 清单

### ✅ Do（必须执行）

1. ✅ 所有新增函数必须添加完整类型标注（参数 + 返回值）
2. ✅ 所有 I/O 操作必须使用 async/await
3. ✅ 新增 MCP Server 必须实现 `list_tools` / `call_tool` / `list_resources` 三个方法 + `health_check` 端点
4. ✅ 新增 Runner 必须实现 `IRunner` 抽象类的四个方法
5. ✅ 新增 LLM Provider 必须实现 `ILLMProvider` 接口（`chat()` / `embed()`）
6. ✅ Skill 文件必须存放在 `skills/` 目录，遵循 YAML Front Matter + Markdown Body 规范
7. ✅ Agent 间消息必须携带 `message_id` 用于幂等去重
8. ✅ 测试失败必须实现指数退避重试（2s→4s→8s），最多 3 次
9. ✅ MCP 工具调用必须经 Gateway 审计日志记录
10. ✅ RAG 检索必须实现向量+关键词双路召回 + RRF 融合
11. ✅ 数据模型变更必须通过 Alembic 迁移脚本管理
12. ✅ 测试代码必须覆盖 unit / integration / e2e 三级

### 🚫 Don't（禁止执行）

1. 🚫 禁止引入 LangChain / LangGraph / CrewAI / LlamaIndex 依赖
2. 🚫 禁止使用 `requests` 库（必须用 `httpx`）
3. 🚫 禁止使用 `time.sleep()`（必须用 `asyncio.sleep()`）
4. 🚫 禁止 Agent 间直接通信（必须经 Gateway 中转）
5. 🚫 禁止在系统提示中注入所有 Skill 全文（必须两层注入）
6. 🚫 禁止仅使用纯向量检索（必须混合检索）
7. 🚫 禁止硬编码 API Key / 密码（必须环境变量或 Keyring）
8. 🚫 禁止在日志或报告中记录明文密钥或 PII
9. 🚫 禁止在模型层编写业务逻辑
10. 🚫 禁止生产环境使用本地进程隔离模式
11. 🚫 禁止绕过 Repository 层直接操作数据库
12. 🚫 禁止 Skill 解析失败时阻塞系统启动

---

## 测试、安全与运维约束

### 测试要求

✅ 单元测试必须使用 `pytest` + `pytest-asyncio`，所有 async 函数用 `@pytest.mark.asyncio` 标注
✅ 集成测试必须启动真实 Redis 和 ChromaDB 实例（Docker Compose），禁止全 Mock
✅ E2E 测试必须覆盖完整链路：需求输入 → 计划生成 → 执行 → 分析 → 缺陷归档
✅ 新增功能必须同步编写单元测试，覆盖率门槛 ≥ 80%
✅ MCP Server 测试必须实现独立启动和 `health_check` 验证
✅ Harness 测试必须验证 Docker 沙箱的安全配置（`no-new-privileges` / read-only）
✅ CI 模式必须使用 `testagent ci --exit-code`，失败时返回非零退出码
✅ 测试结果不一致时必须自动串行重试该任务对比结果；仍不一致则标记 flaky 并通知 Analyzer

### 安全红线

✅ LLM API Key 必须通过环境变量注入 + 操作系统 Keyring 加密存储
✅ 配置文件中 Key 必须用 `***` 脱敏显示
✅ 测试环境禁止使用生产真实数据
✅ RAG 索引的生产数据必须脱敏后方可入库
✅ 数据保留策略默认 90 天自动清理
✅ 所有 Agent 决策和工具调用必须记录审计日志，保留 1 年
✅ MCP Server 调用必须 API Token + IP 白名单双重验证
✅ LLM API 429 限流时必须自动排队重试，超限降级到本地模型
🚫 禁止在代码、日志、报告中暴露真实用户数据（手机号/身份证/邮箱）
🚫 禁止测试沙箱访问内网非被测服务

### Git 工作流

✅ 分支命名：`feat/F-XXX-description` / `fix/F-XXX-description` / `refactor/description`
✅ Commit 格式：`type(scope): description`，type 限于 `feat/fix/refactor/test/docs/chore`
✅ PR 必须关联功能编号（如 F-G01），必须通过 CI（lint + typecheck + unit test）
🚫 禁止直接 push 到 `main` 分支

### 性能约束

| 指标 | MVP 目标 | V1.0 目标 |
|------|---------|----------|
| 测试计划生成延迟 | < 30s | < 15s |
| 单条 API 测试执行 | < 5s | < 3s |
| 单条 Web 测试执行 | < 30s | < 20s |
| 并发执行路数 | 5 | 10 |
| 失败分类延迟 | < 10s | < 5s |
| CLI 启动时间 | < 3s | < 2s |
| RAG 检索延迟 | < 2s | < 1s |
| 内存占用（空闲） | < 512MB | < 512MB |
| 内存占用（5/10并行） | < 4GB | < 8GB |

### MVP / V1.0 阶段标注汇总

| 功能/约束 | MVP | V1.0 |
|----------|-----|------|
| 数据库 | SQLite + WAL | PostgreSQL + JSONB + GIN |
| 向量库 | ChromaDB（嵌入式） | Milvus（分布式） |
| App 测试 | 不支持 | Appium MCP Server + MicroVM |
| MicroVM 隔离 | 不支持 | Firecracker |
| Web Dashboard | 不支持（CLI only） | React + Ant Design + ECharts |
| 并行路数 | 最多 5 路 | 最多 10 路 |
| Cross-Encoder 重排 | 不支持 | 支持 |
| 断点续跑 | 基础重试 | 执行快照 + 恢复 |
| 实时进度上报 | 不支持 | WebSocket 推送 |
| Skills 开发 SDK | 不支持 | 脚手架 + 规范 |
| Embedding 模型 | bge-large-zh-v1.5 本地 | + text-embedding-3-small API |
| 自愈定位降级 | CSS→XPath 两级 | CSS→XPath→语义定位三级 |
