# TestAgent V2.0 Vibe Coding 执行手册

> **版本**：v2.0
> **日期**：2026-05-01
> **方法论**：Vibe Coding（AI-First Spec-Driven Development）
> **前提**：V1.0 阶段 Phase 8-14 已全部完成，PostgreSQL + Milvus + 10 路并行 + Dashboard 均已运行
> **核心定位**：从执行者到探索者——Agent 从执行预定义脚本转向自主发现未知缺陷
> **适用项目**：TestAgent — AI 测试智能体平台

***

## 0. 使用指南

### 0.1 本手册是什么

本手册是一份**分阶段的「人与 AI 对话」操作指南**，面向 TestAgent V2.0 创新功能。你只需按 Phase 15→20 顺序，将每个 Step 的「Prompt 模板」复制到 AI 编码助手中执行，即可将项目从 V1.0 升级到 V2.0 全部功能。

### 0.2 V2.0 升级全景图

| 升级维度 | V1.0 现状 | V2.0 目标 | 对应 Phase |
|---------|---------|----------|-----------|
| LLM 适配 | OpenAI + 本地 Ollama | +Claude +Gemini +智能路由 +成本看板 | Phase 15 |
| 探索性测试 | 不支持 | ExplorationAgent + 好奇心驱动 + 状态空间图 | Phase 16 |
| 性能基准 | 不支持 | PerformanceRunner + P95/P99 + 基线管理 | Phase 17 |
| 无障碍合规 | 不支持 | WCAG 2.1 AA + axe-core + 规则引擎 | Phase 18 |
| 设备兼容性 | 单设备测试 | 云真机矩阵 + BrowserStack/SauceLabs | Phase 18 |
| 插件市场 | 不支持 | Plugin Registry + 签名验证 + 沙箱安装 | Phase 19 |
| 团队协作 | 单用户 | 多租户 + RBAC + 共享知识库 + 协作通知 | Phase 19 |
| 并行路数 | 最多 10 路 | 最多 20 路 | Phase 20 |
| RAG 延迟 | <1s | <500ms | Phase 20 |
| CLI 启动 | <2s | <1.5s | Phase 20 |
| 内存占用 | 空闲 <512MB | 空闲 <256MB | Phase 20 |

### 0.3 约定标记

| 标记 | 含义 |
|------|------|
| `ADR-XXX` | 引用 AGENTS.md 中的架构决策规则编号 |
| `TDD §X.Y` | 引用 TestAgent_TDD.md 第 X.Y 节 |
| `PRD §X.Y` | 引用 TestAgent_PRD.md 第 X.Y 节 |
| `F-XXXX` | 引用 PRD 中的功能编号 |
| `V1.0 Step X.Y` | 引用 V1.0 执行手册中的步骤 |
| ⚠️ **创新风险** | V2.0 前沿技术风险标注，含降级方案 |

### 0.4 前置准备

在开始 Phase 15 之前，确保以下条件已满足：

```bash
# V1.0 全量测试全绿
pytest tests/ -v --timeout=120

# PostgreSQL + Milvus + Redis 运行中
docker ps | grep -E "testagent-postgres|testagent-milvus|testagent-redis"

# Dashboard 可访问
curl -s http://localhost:8000/api/v1/health | jq .

# Ollama 运行中（本地模型）
ollama list
```

### 0.5 V2.0 性能目标（来源：PRD §6.1）

| 指标 | V1.0 目标 | V2.0 目标 |
|------|---------|----------|
| 测试计划生成延迟 | < 15s | **< 10s** |
| 单条 API 测试执行 | < 3s | **< 2s** |
| 单条 Web 测试执行 | < 20s | **< 15s** |
| 并发执行路数 | 10 | **20** |
| 失败分类延迟 | < 5s | **< 3s** |
| CLI 启动时间 | < 2s | **< 1.5s** |
| RAG 检索延迟 | < 1s | **< 500ms** |
| 内存占用（空闲） | < 512MB | **< 256MB** |
| 内存占用（满载） | < 8GB（10并行） | **< 12GB（20并行）** |

***

## Phase 15：多 LLM 适配层

> **目标**：实现 Claude/Gemini Provider、LLM Router 智能路由、Ollama 多模型管理、令牌桶优化 + 成本实时看板。此阶段支持 Claude/GPT/本地模型一键切换。

***

### Step 15.1：ILLMProvider 接口扩展与 Claude Provider

**目标**：扩展 ILLMProvider 接口支持多 LLM，实现 Claude Provider。

**依赖**：V1.0 Step 2.2（LLM Provider 抽象层）

**Prompt 模板**：

```
请为 TestAgent 扩展 LLM Provider 接口并实现 Claude Provider。

基于 V1.0 已有的 testagent/llm/ 模块（参照 PRD §3.3 多 LLM 适配和 AGENTS.md 代码风格硬约束）：

1. 扩展 testagent/llm/base.py 的 ILLMProvider 接口：
   class ILLMProvider(Protocol):
       async def chat(self, system: str, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse: ...
       async def embed(self, text: str) -> list[float]: ...
       @property
       def provider_name(self) -> str: ...
       @property
       def model_id(self) -> str: ...
       @property
       def context_window(self) -> int: ...
       @property
       def supports_tool_use(self) -> bool: ...
       @property
       def supports_vision(self) -> bool: ...

   @dataclass
   class LLMResponse:
       content: list[dict]
       stop_reason: str
       usage: TokenUsage
       model: str
       latency_ms: float

   @dataclass
   class TokenUsage:
       input_tokens: int
       output_tokens: int
       total_tokens: int

2. 实现 testagent/llm/claude_provider.py：
   class ClaudeProvider:
       """Anthropic Claude LLM Provider（V2.0）"""
       def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"): ...
       async def chat(self, system, messages, tools=None) -> LLMResponse:
           """调用 Claude Messages API，处理 tool_use 格式差异"""
       async def embed(self, text) -> list[float]:
           """Claude 无原生 embed，降级到 OpenAI embedding 或本地模型"""

3. 处理 Claude ↔ OpenAI 的 tool_use 格式差异：
   - Claude: content=[{"type": "tool_use", "id": "...", "name": "...", "input": {...}}]
   - OpenAI: tool_calls=[{"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}]
   - 统一转换为 ILLMProvider 的 LLMResponse 格式

4. 多 LLM 适配必须通过 ILLMProvider 接口扩展，禁止在业务层硬编码 LLM 切换逻辑（硬约束）

5. 所有 I/O 操作必须使用 async/await（AGENTS.md 硬约束）

6. LLM API Key 必须通过环境变量注入（AGENTS.md 安全红线）

同时编写单元测试 tests/unit/test_claude_provider.py：
- 测试 Claude chat 调用和 tool_use 格式转换
- 测试 embed 降级逻辑
- 测试 provider_name/model_id/context_window 属性
```

**验证检查点**：

```bash
pytest tests/unit/test_claude_provider.py -v
ruff check testagent/llm/
mypy testagent/llm/ --strict
```

**⚠️ 创新风险标注**：Claude API 的 tool_use 格式与 OpenAI 不同，需适配层统一。**降级方案**：格式转换失败时回退到 OpenAI Provider。

***

### Step 15.2：Gemini Provider 与 LLM Router

**目标**：实现 Gemini Provider 和基于任务类型/成本/延迟的 LLM Router 智能路由。

**依赖**：Step 15.1

**Prompt 模板**：

```
请实现 TestAgent Gemini Provider 和 LLM Router 智能路由。

基于 V1.0 已有的 testagent/llm/openai_provider.py 和 Step 15.1 的 Claude Provider（参照 PRD §3.3 多 LLM 适配）：

1. 实现 testagent/llm/gemini_provider.py：
   class GeminiProvider:
       """Google Gemini LLM Provider（V2.0）"""
       def __init__(self, api_key: str, model: str = "gemini-2.0-flash"): ...
       async def chat(self, system, messages, tools=None) -> LLMResponse: ...
       async def embed(self, text) -> list[float]:
           """使用 text-embedding-004"""
       @property
       def supports_vision(self) -> bool:
           return True  # Gemini 原生多模态

2. 实现 testagent/llm/router.py（核心：LLM 智能路由）：
   class LLMRouter:
       """基于任务类型/成本/延迟的 LLM 智能路由"""
       def __init__(self, providers: dict[str, ILLMProvider], default: str = "openai"): ...

       async def route(self, task_type: str, context: RoutingContext) -> ILLMProvider:
           """
           路由决策逻辑：
           1. task_type 映射：
              - "planning" → 优先 Claude/GPT-4o（128K 上下文，推理强）
              - "execution" → 优先 Gemini Flash / GPT-4o-mini（32K，成本低）
              - "analysis" → 优先本地模型（64K，成本最低，敏感数据不外传）
              - "exploration" → 优先 Claude（好奇心推理能力强）
           2. 成本约束：如果日预算接近上限，降级到低成本 Provider
           3. 延迟约束：如果 429 限流，切换到备用 Provider
           4. 数据隐私：如果标记为敏感数据，仅路由到本地模型
           """

       def get_available_providers(self) -> list[str]: ...
       def get_cost_estimate(self, provider: str, input_tokens: int, output_tokens: int) -> float: ...

   @dataclass
   class RoutingContext:
       task_type: str
       estimated_input_tokens: int
       is_sensitive: bool = False
       requires_vision: bool = False
       requires_tool_use: bool = True
       max_latency_ms: int = 30000

3. 多 LLM 适配必须通过 ILLMProvider 接口扩展，禁止在业务层硬编码 LLM 切换逻辑（硬约束）

4. 修改 testagent/agent/context.py 和 testagent/agent/loop.py：
   - llm_provider 替换为 llm_router
   - agent_loop 入参从 llm_provider 改为 llm_router
   - 每次循环开始前调用 llm_router.route(task_type, context) 选择 Provider

5. 遵循 ADR-001（AGENTS.md）：Agent Loop 循环体不变，LLM 路由在循环前决策

同时编写单元测试 tests/unit/test_gemini_provider.py 和 tests/unit/test_llm_router.py：
- 测试 Gemini chat 调用
- 测试 LLM Router 路由决策（各 task_type）
- 测试成本约束降级
- 测试 429 限流切换
- 测试数据隐私路由到本地模型
```

**验证检查点**：

```bash
pytest tests/unit/test_gemini_provider.py tests/unit/test_llm_router.py -v
ruff check testagent/llm/
mypy testagent/llm/ --strict
```

**⚠️ 创新风险标注**：Gemini API 兼容性和路由决策逻辑较复杂。**降级方案**：路由失败时回退到默认 Provider（OpenAI）。

***

### Step 15.3：Ollama 多模型管理与 Provider 注册

**目标**：增强本地模型管理，支持 Ollama 多模型切换和热加载。

**依赖**：V1.0 Step 2.2（Local Provider）

**Prompt 模板**：

```
请增强 TestAgent 本地模型管理，支持 Ollama 多模型切换。

基于 V1.0 已有的 testagent/llm/local_provider.py（参照 PRD §3.3 多 LLM 适配）：

1. 增强 testagent/llm/local_provider.py：
   class OllamaProvider:
       """Ollama 多模型管理 Provider（V2.0）"""
       def __init__(self, base_url: str = "http://localhost:11434"): ...

       async def list_models(self) -> list[ModelInfo]:
           """列出 Ollama 可用模型"""

       async def pull_model(self, model_name: str) -> bool:
           """拉取模型"""

       async def chat(self, system, messages, tools=None, model: str = "qwen2.5:7b") -> LLMResponse:
           """指定模型调用 Ollama API"""

       async def embed(self, text, model: str = "bge-large-zh-v1.5") -> list[float]:
           """指定模型获取 Embedding"""

       @property
       def supports_tool_use(self) -> bool:
           """检测当前模型是否支持 tool_use（qwen2.5 支持，其他视情况）"""

   @dataclass
   class ModelInfo:
       name: str
       size_bytes: int
       quantization: str
       context_window: int
       supports_tools: bool

2. 修改 testagent/config/settings.py：
   - llm_provider: str = "openai"  # "openai"/"claude"/"gemini"/"ollama"
   - ollama_base_url: str = "http://localhost:11434"
   - ollama_default_model: str = "qwen2.5:7b"
   - ollama_embedding_model: str = "bge-large-zh-v1.5"

3. 实现 LLM Provider 注册机制：
   class LLMProviderRegistry:
       """LLM Provider 注册表"""
       _providers: dict[str, type[ILLMProvider]] = {}

       @classmethod
       def register(cls, name: str, provider_cls: type[ILLMProvider]) -> None: ...

       @classmethod
       def create(cls, name: str, settings: TestAgentSettings) -> ILLMProvider: ...

   # 自动注册
   LLMProviderRegistry.register("openai", OpenAIProvider)
   LLMProviderRegistry.register("claude", ClaudeProvider)
   LLMProviderRegistry.register("gemini", GeminiProvider)
   LLMProviderRegistry.register("ollama", OllamaProvider)

4. 所有 I/O 操作必须使用 async/await（AGENTS.md 硬约束）
5. 禁止使用 requests 库，必须用 httpx.AsyncClient（AGENTS.md 硬约束）

同时编写单元测试 tests/unit/test_ollama_provider.py：
- 测试 Ollama list_models / pull_model / chat / embed
- 测试 tool_use 支持检测
- 测试 LLMProviderRegistry 注册和创建
```

**验证检查点**：

```bash
pytest tests/unit/test_ollama_provider.py -v
ruff check testagent/llm/
mypy testagent/llm/ --strict
```

***

### Step 15.4：令牌桶优化与成本实时看板

**目标**：实现令牌桶限流优化和 LLM 成本实时看板。

**依赖**：Step 15.2

**Prompt 模板**：

```
请实现 TestAgent 令牌桶限流优化和 LLM 成本实时看板。

基于 V1.0 已有的 testagent/llm/ 模块和 Step 15.2 的 LLM Router（参照 PRD §3.3 多 LLM 适配和 TDD 难点 7）：

1. 实现 testagent/llm/rate_limiter.py：
   class TokenBucketRateLimiter:
       """令牌桶限流器——按 Provider 独立限流"""
       def __init__(self, rpm: int = 60, tpm: int = 100000): ...

       async def acquire(self, estimated_tokens: int) -> None:
           """获取令牌，429 时自动排队等待"""

       async def handle_429(self, retry_after: int) -> None:
           """处理 429 限流，自动退避"""

   class RateLimiterManager:
       """管理多个 Provider 的限流器"""
       def __init__(self): ...
       def get_limiter(self, provider: str) -> TokenBucketRateLimiter: ...

2. 实现 testagent/llm/cost_tracker.py：
   class CostTracker:
       """LLM 成本追踪器"""
       def __init__(self, redis_client): ...

       async def record_usage(self, provider: str, model: str, usage: TokenUsage) -> None:
           """记录 Token 使用量到 Redis"""
           # key: testagent:cost:{date}:{provider}
           # value: {input_tokens, output_tokens, cost_usd}

       async def get_daily_cost(self, provider: str | None = None) -> dict:
           """获取当日成本统计"""

       async def get_budget_status(self) -> dict:
           """获取预算状态：已用/剩余/百分比"""

       async def check_budget(self, estimated_cost: float) -> bool:
           """检查是否超出日预算上限"""

   PRICING = {
       "openai": {"gpt-4o": {"input": 2.5e-6, "output": 10e-6}},
       "claude": {"claude-sonnet-4-20250514": {"input": 3e-6, "output": 15e-6}},
       "gemini": {"gemini-2.0-flash": {"input": 0.075e-6, "output": 0.3e-6}},
       "ollama": {"*": {"input": 0, "output": 0}},  # 本地免费
   }

3. 修改 LLMRouter：集成 RateLimiterManager 和 CostTracker
   - route() 决策前检查 CostTracker.check_budget()
   - 路由后调用 RateLimiterManager.get_limiter(provider).acquire()

4. 实现 Gateway API：
   - GET /api/v1/llm/costs?days=7 → 成本趋势数据
   - GET /api/v1/llm/budget → 预算状态
   - WebSocket 事件 llm.cost_warning: {provider, daily_cost, budget_limit}

5. 遵循 AGENTS.md 安全红线：LLM API Key 必须通过环境变量注入
6. 遵循 AGENTS.md ADR-005：429 限流时自动排队重试

同时编写单元测试 tests/unit/test_rate_limiter.py 和 tests/unit/test_cost_tracker.py：
- 测试令牌桶限流逻辑
- 测试 429 处理和退避
- 测试成本记录和预算检查
- 测试多 Provider 成本汇总
```

**验证检查点**：

```bash
pytest tests/unit/test_rate_limiter.py tests/unit/test_cost_tracker.py -v
ruff check testagent/llm/
mypy testagent/llm/ --strict
```

***

### Step 15.5：Phase 15 集成验证——多 LLM 一键切换

**目标**：验证 Claude/GPT/Gemini/本地模型可通过 LLM Router 一键切换，成本看板正常工作。

**依赖**：Step 15.1 ~ Step 15.4

**Prompt 模板**：

```
请创建 Phase 15 集成验证，确保多 LLM 适配层完整可用。

创建 tests/integration/test_multi_llm.py：

1. async def test_claude_provider_chat():
   测试 Claude Provider 端到端调用（需 ANTHROPIC_API_KEY）

2. async def test_gemini_provider_chat():
   测试 Gemini Provider 端到端调用（需 GOOGLE_API_KEY）

3. async def test_ollama_provider_chat():
   测试 Ollama Provider 端到端调用（需 Ollama 运行）

4. async def test_llm_router_task_routing():
   测试各 task_type 路由到正确 Provider

5. async def test_llm_router_fallback_on_429():
   模拟 429 限流 → 验证切换到备用 Provider

6. async def test_cost_tracker_daily_cost():
   发送多个 LLM 请求 → 验证成本追踪准确

7. async def test_agent_loop_with_llm_router():
   集成测试：Agent Loop 使用 LLM Router → 验证完整对话流程

8. async def test_cost_dashboard_api():
   测试 GET /api/v1/llm/costs 和 /api/v1/llm/budget
```

**验证检查点**：

```bash
export ANTHROPIC_API_KEY=sk-ant-xxx
export GOOGLE_API_KEY=AIzaxxx
pytest tests/integration/test_multi_llm.py -v --timeout=120
pytest tests/unit/test_claude_provider.py tests/unit/test_gemini_provider.py tests/unit/test_llm_router.py -v
ruff check testagent/llm/
mypy testagent/llm/ --strict
```

***

## Phase 16：自主探索性测试引擎

> **目标**：实现 ExplorationAgent（第四种 Agent 角色），好奇心驱动策略，探索状态空间图，探索终止条件，exploratory_test Skill。此阶段 Agent 可自主发现预定义脚本无法覆盖的缺陷。

***

### Step 16.1：ExplorationAgent 角色定义与 Agent 类型扩展

**目标**：定义 ExplorationAgent 作为第四种 Agent 类型，扩展 Agent 通信协议和上下文组装。

**依赖**：V1.0 Step 2.5（三层 Agent 骨架）

**Prompt 模板**：

```
请为 TestAgent 定义 ExplorationAgent 作为第四种 Agent 类型。

基于 V1.0 已有的 testagent/agent/ 模块（参照 PRD §3.3 自主探索性测试和 AGENTS.md ADR-001）：

1. 扩展 testagent/agent/protocol.py：
   - AgentType 枚举新增：EXPLORER = "explorer"
   - 消息协议 sender/receiver 正则扩展：
     ^(planner|executor_\\d+|analyzer|explorer_\\d+|gateway|cli|broadcast)$
   - 新增消息类型：exploration_report（探索报告）

2. 扩展 testagent/agent/context.py 的 Agent 职责表：
   | Agent | 职责 | 上下文窗口 | 工具集 | RAG 访问 | 并发数 |
   | Explorer Agent | 自主探索应用，发现未知缺陷 | 128K | MCP: Playwright, API; Skill: 探索类 | api_docs, req_docs, locator_library | 1-5（并行） |

   - Explorer Agent 以空 messages=[] 启动
   - 上下文组装顺序不变：AGENTS.md → SOUL.md → TOOLS.md → Skill Layer 1 → RAG

3. 实现 testagent/agent/explorer.py：
   class ExplorationAgent:
       """探索性测试 Agent（V2.0，第四种 Agent 类型）"""
       def __init__(self, llm_router: LLMRouter, mcp_registry: MCPRegistry, rag: RAGPipeline): ...

       async def execute(self, task: AgentTask) -> ExplorationResult:
           """
           探索性测试主循环：
           1. 初始化探索状态空间图
           2. 构建好奇心驱动策略
           3. 进入 Agent Loop（遵循 ADR-001）
           4. 循环体不变，探索策略通过 system prompt + tools 注入
           5. 收集探索结果
           """

4. 自主探索性测试必须遵循 ADR-001 的 Agent Loop 架构，ExplorationAgent 作为第四种 Agent 类型接入现有 ReAct Loop，不可破坏循环体不变原则（硬约束）

5. 遵循 AGENTS.md Agent 架构规则：Agent 间必须通过 Gateway 的结构化消息协议通信，禁止共享消息历史

同时编写单元测试 tests/unit/test_explorer_agent.py：
- 测试 AgentType.EXPLORER 枚举
- 测试消息协议 sender/receiver 正则匹配 explorer_1
- 测试 ExplorationAgent 上下文组装
- 测试 ExplorationAgent.execute 基本流程（mock LLM）
```

**验证检查点**：

```bash
pytest tests/unit/test_explorer_agent.py -v
ruff check testagent/agent/
mypy testagent/agent/ --strict
```

**⚠️ 创新风险标注**：ExplorationAgent 需接入 ReAct Loop 但不能破坏循环体不变原则。**降级方案**：如果探索行为无法在标准 Agent Loop 中表达，回退到"脚本化探索模式"——预生成探索路径后交由 Executor Agent 执行。

***

### Step 16.2：好奇心驱动策略与探索状态空间图

**目标**：实现好奇心驱动探索策略和探索状态空间图（visited nodes + edges）。

**依赖**：Step 16.1

**Prompt 模板**：

```
请实现 TestAgent 好奇心驱动探索策略和探索状态空间图。

基于 V1.0 已有的 testagent/agent/loop.py 和 Step 16.1 的 ExplorationAgent（参照 PRD §3.3 自主探索性测试）：

1. 实现 testagent/agent/exploration_strategy.py：
   class CuriosityDrivenStrategy:
       """好奇心驱动探索策略"""
       def __init__(self, state_graph: ExplorationGraph): ...

       async def next_action(self, current_state: Node, visited: set[str]) -> Action:
           """
           选择下一个探索动作：
           1. 未访问路径优先：选择 graph 中未访问的 edge
           2. 异常输入变异：对已访问节点进行边界值/异常值变异
           3. 边界条件探索：空输入/超长输入/特殊字符/负数
           4. 权重计算：novelty_score = 1.0 / (visit_count + 1)
           5. 返回 novelty_score 最高的 Action
           """

   2. 实现 testagent/agent/exploration_graph.py：
      class ExplorationGraph:
          """探索状态空间图"""
          def __init__(self): ...
          def add_node(self, node: Node) -> None: ...
          def add_edge(self, edge: Edge) -> None: ...
          def get_unvisited_edges(self, from_node: str) -> list[Edge]: ...
          def get_coverage(self) -> float:
              """探索覆盖率 = visited_edges / total_edges"""
          def to_dict(self) -> dict:
              """序列化图结构用于持久化"""

      @dataclass
      class Node:
          id: str
          type: str  # "page" / "api_endpoint" / "form" / "state"
          label: str
          metadata: dict
          visit_count: int = 0

      @dataclass
      class Edge:
          id: str
          source: str
          target: str
          action: str  # "click" / "navigate" / "submit" / "api_call"
          input_data: dict | None = None
          visited: bool = False

   3. 探索终止条件（参照 PRD §3.3 自主探索性测试）：
      class TerminationChecker:
          @staticmethod
          def should_terminate(graph: ExplorationGraph, rounds: int, config: ExplorationConfig) -> bool:
              """
              终止条件（满足任一即停）：
              1. 覆盖率饱和：graph.get_coverage() > config.coverage_threshold (0.85)
              2. 时间预算：rounds > config.max_rounds
              3. 无新发现：连续 N 轮未发现新节点/新缺陷 (N=config.no_discovery_limit=10)
              """

   4. 所有 I/O 操作必须使用 async/await（AGENTS.md 硬约束）

同时编写单元测试 tests/unit/test_exploration_strategy.py 和 tests/unit/test_exploration_graph.py：
- 测试 CuriosityDrivenStrategy.next_action 决策逻辑
- 测试 ExplorationGraph 节点/边管理
- 测试覆盖率计算
- 测试终止条件判断
```

**验证检查点**：

```bash
pytest tests/unit/test_exploration_strategy.py tests/unit/test_exploration_graph.py -v
ruff check testagent/agent/
mypy testagent/agent/ --strict
```

**⚠️ 创新风险标注**：好奇心驱动策略的核心是 novelty_score 计算，需要大量调参。**降级方案**：如果策略效果不佳，回退到"随机探索 + 人工引导"混合模式。

***

### Step 16.3：探索执行集成与 exploratory_test Skill

**目标**：将 ExplorationAgent 集成到 Gateway 调度，创建 exploratory_test Skill。

**依赖**：Step 16.2

**Prompt 模板**：

```
请将 ExplorationAgent 集成到 TestAgent Gateway 调度并创建 exploratory_test Skill。

基于 V1.0 已有的 testagent/gateway/ 和 testagent/agent/ 模块（参照 PRD §3.3 自主探索性测试和 PRD §2.3.3 Skills 机制）：

1. 修改 Gateway 调度逻辑：
   - Session 状态机扩展：planning → exploring → analyzing → completed/failed
   - Gateway 可将任务分配给 explorer_1~N
   - 探索结果通过 exploration_report 消息类型上报

2. 创建 skills/exploratory_test/SKILL.md（参照 PRD §2.3.3 预置 Skills 清单）：
   - name: exploratory_test, version: 1.0.0
   - description: 自主探索性测试，发现"未知未知"缺陷
   - trigger: "探索测试" / "exploratory test" / "发现未知缺陷"
   - required_mcp_servers: [playwright_server, api_server]
   - required_rag_collections: [api_docs, req_docs, locator_library]
   - Markdown Body：
     * 目标：无需预定义脚本，Agent 自主探索应用所有可访问路径
     * 操作流程：
       1. 从 RAG 获取应用结构和 API 文档
       2. 构建初始状态空间图
       3. 好奇心驱动探索循环
       4. 发现异常时记录并继续探索
       5. 满足终止条件后生成探索报告
     * 断言策略：无预定义断言，LLM 自主判断是否异常
     * 失败处理：探索不因单个失败中断，记录所有异常

3. 遵循 AGENTS.md Skills 机制规则：
   - Front Matter 必填字段完整
   - 必须实现两层注入
   - required_mcp_servers 未注册时 Skill 标记为 "degraded"

4. 遵循 ADR-001：Agent Loop 循环体不变，探索策略通过 system prompt 注入

同时编写 E2E 测试 tests/e2e/test_exploratory.py：
- 测试 exploratory_test Skill 加载
- 测试 ExplorationAgent 端到端探索流程
- 测试探索状态空间图生成
- 测试终止条件触发
```

**验证检查点**：

```bash
pytest tests/e2e/test_exploratory.py -v --timeout=300
testagent skill list | grep exploratory_test
ruff check testagent/agent/ testagent/gateway/
mypy testagent/ --strict
```

**⚠️ 创新风险标注**：探索性测试的"发现率"难以量化验证。**降级方案**：设定基准被测应用（如 OWASP WebGoat），验证 Explorer Agent 可发现已知缺陷数量。

***

## Phase 17：性能基准验证

> **目标**：实现 PerformanceRunner、k6/Locust 集成 MCP Server、P50/P95/P99 延迟采集、性能基线管理、performance_sanity_test Skill、Dashboard 性能趋势图。此阶段性能回归可自动发现。

***

### Step 17.1：PerformanceRunner 与 k6/Locust MCP Server

**目标**：实现 PerformanceRunner 插件和 k6/Locust 集成 MCP Server。

**依赖**：V1.0 Step 5.1（IRunner 抽象类）

**Prompt 模板**：

```
请实现 TestAgent PerformanceRunner 和 k6/Locust MCP Server。

基于 V1.0 已有的 testagent/harness/runners/base.py 和 testagent/mcp_servers/（参照 PRD §3.3 性能基准验证）：

1. 实现 testagent/harness/runners/performance_runner.py：
   class PerformanceRunner(BaseRunner):
       """性能测试 Runner（V2.0）"""
       runner_type = "performance_test"

       async def setup(self, sandbox: ISandbox, config: dict) -> None:
           """在沙箱中安装 k6 或 Locust"""
       async def execute(self, sandbox: ISandbox, test_script: str) -> TestResult:
           """执行性能测试脚本，采集 P50/P95/P99 延迟和吞吐量"""
       async def teardown(self, sandbox: ISandbox) -> None: ...
       async def collect_results(self, sandbox: ISandbox) -> TestResult:
           """收集性能指标"""

   @dataclass
   class PerformanceMetrics:
       p50_ms: float
       p95_ms: float
       p99_ms: float
       rps: float  # requests per second
       error_rate: float
       concurrent_users: int
       duration_seconds: float

2. 实现 testagent/mcp_servers/performance_server/：
   server.py:
   class PerformanceMCPServer(BaseMCPServer):
       server_name = "performance_server"

   tools.py:
   async def perf_run_k6(script: str, options: dict | None = None) -> dict:
       """运行 k6 性能测试脚本"""
   async def perf_run_locust(script: str, users: int = 10, duration: str = "60s") -> dict:
       """运行 Locust 性能测试"""
   async def perf_get_metrics(test_id: str) -> dict:
       """获取性能测试指标"""
   async def perf_compare_baselines(test_id: str, baseline_id: str) -> dict:
       """对比当前结果与基线"""

3. 遵循 AGENTS.md Do #3：新增 MCP Server 必须实现 list_tools / call_tool / list_resources + health_check
4. 遵循 AGENTS.md Do #4：新增 Runner 必须实现 IRunner 抽象类的四个方法
5. 所有 I/O 操作必须使用 async/await

同时编写单元测试 tests/unit/test_performance_runner.py 和 tests/unit/test_performance_server.py：
- 测试 PerformanceRunner 协议合规性
- 测试 PerformanceMetrics 数据结构
- 测试 MCP Server 工具列表
- 测试 k6/Locust 脚本执行（mock）
```

**验证检查点**：

```bash
pytest tests/unit/test_performance_runner.py tests/unit/test_performance_server.py -v
ruff check testagent/harness/ testagent/mcp_servers/
mypy testagent/harness/ testagent/mcp_servers/ --strict
```

***

### Step 17.2：性能基线管理与回归检测

**目标**：实现性能基线管理（历史基线对比 + 回归检测）。

**依赖**：Step 17.1

**Prompt 模板**：

```
请实现 TestAgent 性能基线管理和回归检测。

基于 V1.0 已有的 testagent/agent/analyzer.py 和 Step 17.1（参照 PRD §3.3 性能基准验证）：

1. 实现 testagent/agent/performance_baseline.py：
   class PerformanceBaselineManager:
       """性能基线管理器"""
       def __init__(self, session_repo: SessionRepository, defect_repo: DefectRepository): ...

       async def save_baseline(self, endpoint: str, metrics: PerformanceMetrics) -> str:
           """保存性能基线到数据库"""
           # JSONB 存储到 TestResult.artifacts

       async def get_baseline(self, endpoint: str) -> PerformanceMetrics | None:
           """获取最新基线"""

       async def detect_regression(self, endpoint: str, current: PerformanceMetrics) -> RegressionResult:
           """
           检测性能回归：
           1. P95 延迟 > 基线 * 1.2 → 标记 P95 回归
           2. P99 延迟 > 基线 * 1.3 → 标记 P99 回归
           3. 错误率 > 基线 * 1.5 → 标记错误率回归
           4. RPS < 基线 * 0.8 → 标记吞吐量回归
           """

   @dataclass
   class RegressionResult:
       is_regression: bool
       regressions: list[RegressionItem]
       baseline: PerformanceMetrics
       current: PerformanceMetrics

   @dataclass
   class RegressionItem:
       metric: str  # "p95" / "p99" / "error_rate" / "rps"
       baseline_value: float
       current_value: float
       change_pct: float
       severity: str  # "warning" / "critical"

2. 集成到 Analyzer Agent：
   - 当 PerformanceRunner 返回结果时
   - 自动调用 PerformanceBaselineManager.detect_regression()
   - 如果检测到回归，创建 Defect（category="performance"）

3. 遵循 AGENTS.md 数据模型约定：Defect 分类扩展增加 "performance"

同时编写单元测试 tests/unit/test_performance_baseline.py：
- 测试基线保存和获取
- 测试回归检测逻辑
- 测试阈值判断
```

**验证检查点**：

```bash
pytest tests/unit/test_performance_baseline.py -v
ruff check testagent/agent/
mypy testagent/agent/ --strict
```

***

### Step 17.3：performance_sanity_test Skill 与 Dashboard 性能趋势

**目标**：创建 performance_sanity_test Skill，Dashboard 添加性能趋势图。

**依赖**：Step 17.2

**Prompt 模板**：

```
请创建 TestAgent performance_sanity_test Skill 和 Dashboard 性能趋势图。

基于 V1.0 已有的 skills/ 和 dashboard/（参照 PRD §3.3 性能基准验证和 PRD §2.3.3）：

1. 创建 skills/performance_sanity_test/SKILL.md：
   - name: performance_sanity_test, version: 1.0.0
   - description: API 性能基准验证，检测 P95/P99 延迟回归
   - trigger: "性能测试" / "performance test" / "性能基准"
   - required_mcp_servers: [performance_server, api_server]
   - required_rag_collections: [api_docs]
   - Markdown Body：
     * 目标：验证 API 性能指标未发生回归
     * 操作流程：
       1. 从 RAG api_docs 获取所有 Endpoint
       2. 对每个 Endpoint 执行基准负载测试
       3. 采集 P50/P95/P99 延迟和吞吐量
       4. 与历史基线对比
       5. 检测到回归时创建缺陷
     * 断言策略：P95 < 基线*1.2, 错误率 < 1%, RPS > 基线*0.8
     * 失败处理：标记性能回归缺陷，附上对比数据

2. Dashboard 性能趋势图（扩展 src/pages/Dashboard.tsx）：
   - ECharts 折线图：API P95 延迟趋势
   - ECharts 折线图：API 吞吐量趋势
   - 性能回归标记：回归点用红色标记
   - API 路由：GET /api/v1/quality/trends?metric=performance&days=30

3. 前端代码必须使用 React 18 + TypeScript + Ant Design 5.x + ECharts 5（硬约束）

同时编写 E2E 测试 tests/e2e/test_performance.py：
- 测试 performance_sanity_test Skill 加载和执行
- 测试性能基线保存和对比
- 测试性能回归检测
```

**验证检查点**：

```bash
pytest tests/e2e/test_performance.py -v --timeout=300
testagent skill list | grep performance_sanity_test
cd dashboard && npm run build
ruff check . && mypy testagent/ --strict
```

***

## Phase 18：无障碍与兼容性

> **目标**：实现 WCAG 2.1 AA 级合规检测、axe-core 集成、设备矩阵调度、云真机 Provider。此阶段合规和兼容性可自动化验证。

***

### Step 18.1：AccessibilityRunner 与 axe-core 集成

**目标**：实现 AccessibilityRunner 和 axe-core 无障碍检测 MCP Server。

**依赖**：V1.0 Step 5.1（IRunner 抽象类）

**Prompt 模板**：

```
请实现 TestAgent AccessibilityRunner 和 axe-core MCP Server。

基于 V1.0 已有的 testagent/harness/runners/（参照 PRD §3.3 无障碍合规测试）：

1. 实现 testagent/harness/runners/accessibility_runner.py：
   class AccessibilityRunner(BaseRunner):
       """无障碍合规测试 Runner（V2.0）"""
       runner_type = "accessibility_test"

       async def setup(self, sandbox: ISandbox, config: dict) -> None:
           """在沙箱中安装 axe-core"""
       async def execute(self, sandbox: ISandbox, test_script: str) -> TestResult:
           """执行 axe-core 无障碍扫描"""
       async def teardown(self, sandbox: ISandbox) -> None: ...
       async def collect_results(self, sandbox: ISandbox) -> TestResult:
           """收集无障碍违规列表"""

   @dataclass
   class AccessibilityViolation:
       id: str
       impact: str  # "critical" / "serious" / "moderate" / "minor"
       description: str
       help_url: str
       wcag_criteria: list[str]  # ["1.1.1", "2.4.4"]
       element_selector: str
       fix_suggestion: str

2. 实现 testagent/mcp_servers/accessibility_server/：
   tools.py:
   async def a11y_scan_page(url: str, standard: str = "wcag2aa") -> dict:
       """扫描页面无障碍合规性"""
   async def a11y_get_violations(scan_id: str, severity: str | None = None) -> list[dict]:
       """获取无障碍违规列表"""
   async def a11y_get_summary(scan_id: str) -> dict:
       """获取合规摘要：通过/失败/警告数"""

3. 遵循 AGENTS.md Do #3 和 Do #4
4. 所有 I/O 操作必须使用 async/await

同时编写单元测试 tests/unit/test_accessibility_runner.py：
- 测试 AccessibilityRunner 协议合规性
- 测试 AccessibilityViolation 数据结构
- 测试 axe-core 扫描流程（mock）
```

**验证检查点**：

```bash
pytest tests/unit/test_accessibility_runner.py -v
ruff check testagent/harness/ testagent/mcp_servers/
mypy testagent/ --strict
```

***

### Step 18.2：WCAG 2.1 AA 规则引擎与 accessibility_test Skill

**目标**：实现 WCAG 2.1 AA 规则引擎和 accessibility_test Skill。

**依赖**：Step 18.1

**Prompt 模板**：

```
请实现 TestAgent WCAG 2.1 AA 规则引擎和 accessibility_test Skill。

基于 Step 18.1 的 AccessibilityRunner（参照 PRD §3.3 无障碍合规测试）：

1. 实现 testagent/agent/wcag_engine.py：
   class WCAG21Engine:
       """WCAG 2.1 AA 规则引擎"""
       LEVEL_A_CRITERIA = [...]  # 30 条 Level A 准则
       LEVEL_AA_CRITERIA = [...]  # 20 条 Level AA 准则

       async def evaluate(self, violations: list[AccessibilityViolation]) -> ComplianceReport:
           """
           评估合规性：
           1. 将 axe-core 违规映射到 WCAG 准则
           2. 按准则统计通过/失败
           3. 计算合规率
           4. 生成修复建议
           """

   @dataclass
   class ComplianceReport:
       total_criteria: int
       passed_criteria: int
       failed_criteria: int
       compliance_rate: float
       violations_by_criteria: dict[str, list[AccessibilityViolation]]
       fix_suggestions: list[dict]

2. 创建 skills/accessibility_test/SKILL.md：
   - name: accessibility_test, version: 1.0.0
   - description: WCAG 2.1 AA 级无障碍合规测试
   - trigger: "无障碍测试" / "accessibility test" / "WCAG"
   - required_mcp_servers: [accessibility_server, playwright_server]
   - required_rag_collections: [req_docs]
   - Markdown Body：
     * 目标：验证 Web 应用符合 WCAG 2.1 AA 级标准
     * 操作流程：逐页扫描 → 违规收集 → 合规评估 → 报告生成
     * 断言策略：合规率 >= 90%，无 critical 违规
     * 失败处理：生成修复建议，创建缺陷

同时编写单元测试 tests/unit/test_wcag_engine.py：
- 测试 WCAG 准则映射
- 测试合规率计算
- 测试修复建议生成
```

**验证检查点**：

```bash
pytest tests/unit/test_wcag_engine.py -v
testagent skill list | grep accessibility_test
ruff check testagent/agent/ && mypy testagent/ --strict
```

***

### Step 18.3：设备矩阵调度器与云真机 Provider

**目标**：实现设备矩阵调度器和云真机 Provider 抽象（BrowserStack/SauceLabs/Local）。

**依赖**：V1.0 Step 10.2（MicroVM Sandbox）

**Prompt 模板**：

```
请实现 TestAgent 设备矩阵调度器和云真机 Provider。

基于 V1.0 已有的 testagent/harness/ 和 testagent/mcp_servers/appium_server/（参照 PRD §3.3 App 多设备兼容性）：

1. 实现 testagent/harness/device_matrix.py：
   class DeviceMatrixOrchestrator:
       """设备矩阵调度器——多设备并行测试"""
       def __init__(self, provider: IDeviceProvider): ...

       async def execute_on_matrix(self, test_script: str, matrix: DeviceMatrix) -> list[DeviceTestResult]:
           """
           在设备矩阵上并行执行测试：
           1. 解析矩阵配置（设备 + OS + 浏览器组合）
           2. 并行分配到各设备
           3. 收集各设备结果
           4. 生成兼容性报告
           """

   @dataclass
   class DeviceMatrix:
       devices: list[DeviceConfig]

   @dataclass
   class DeviceConfig:
       name: str  # "iPhone 15" / "Pixel 8" / "Chrome 120"
       os: str  # "iOS 17" / "Android 14" / "Windows 11"
       browser: str | None = None
       provider: str = "local"  # "local" / "browserstack" / "saucelabs"

2. 实现 testagent/harness/device_providers/：
   base.py:
   class IDeviceProvider(Protocol):
       async def list_devices(self) -> list[DeviceConfig]: ...
       async def acquire(self, config: DeviceConfig) -> str: ...  # 返回 session_id
       async def release(self, session_id: str) -> None: ...
       async def execute(self, session_id: str, script: str) -> DeviceTestResult: ...

   local_provider.py:
   class LocalDeviceProvider(IDeviceProvider):
       """本地设备/模拟器（使用 Appium）"""

   browserstack_provider.py:
   class BrowserStackProvider(IDeviceProvider):
       """BrowserStack 云真机"""

   saucelabs_provider.py:
   class SauceLabsProvider(IDeviceProvider):
       """SauceLabs 云真机"""

   factory.py:
   class DeviceProviderFactory:
       @staticmethod
       def create(provider_name: str, settings: TestAgentSettings) -> IDeviceProvider: ...

3. 创建 skills/app_compatibility_test/SKILL.md：
   - name: app_compatibility_test, version: 1.0.0
   - description: App 多设备兼容性测试
   - trigger: "兼容性测试" / "compatibility test" / "多设备测试"
   - required_mcp_servers: [appium_server]
   - required_rag_collections: [req_docs, locator_library]

4. 遵循 ADR-004（AGENTS.md）：SandboxFactory + Strategy Pattern
5. 所有 I/O 操作必须使用 async/await，禁止使用 requests

同时编写单元测试 tests/unit/test_device_matrix.py：
- 测试 DeviceMatrixOrchestrator 矩阵调度
- 测试 DeviceProviderFactory 创建逻辑
- 测试 LocalDeviceProvider 基本流程
```

**验证检查点**：

```bash
pytest tests/unit/test_device_matrix.py -v
ruff check testagent/harness/
mypy testagent/harness/ --strict
```

**⚠️ 创新风险标注**：云真机 API（BrowserStack/SauceLabs）可能不稳定或有限流。**降级方案**：优先使用本地设备/模拟器，云真机作为可选扩展。

***

## Phase 19：插件市场与团队协作

> **目标**：实现 Plugin Registry、插件签名验证 + 沙箱安装、多租户数据隔离 + RBAC 权限、共享知识库、协作通知。此阶段支持团队级协作和生态扩展。

***

### Step 19.1：Plugin Registry 与签名验证

**目标**：实现 Plugin Registry，支持第三方 Skills / MCP Server 的市场分发、签名验证和沙箱安装。

**依赖**：V1.0 Step 14.1（Skills SDK）

**Prompt 模板**：

```
请实现 TestAgent Plugin Registry 和插件签名验证。

基于 V1.0 已有的 testagent/skills/ 模块（参照 PRD §3.3 插件市场）：

1. 实现 testagent/plugins/registry.py：
   class PluginRegistry:
       """插件市场注册表"""
       def __init__(self, db_session: AsyncSession): ...

       async def publish(self, plugin: PluginPackage) -> str:
           """发布插件到市场"""
       async def search(self, query: str, plugin_type: str | None = None) -> list[PluginMetadata]: ...
       async def install(self, plugin_id: str, version: str | None = None) -> str:
           """
           安装插件：
           1. 下载插件包
           2. 验证签名（GPG/Ed25519）
           3. 在沙箱中解压和校验（遵循 ADR-004 隔离原则）
           4. 安装到 skills/ 或 mcp_servers/ 目录
           5. 注册到 SkillRegistry 或 MCPRegistry
           """
       async def uninstall(self, plugin_id: str) -> None: ...
       async def list_installed(self) -> list[PluginMetadata]: ...
       async def update(self, plugin_id: str) -> str | None: ...

   @dataclass
   class PluginPackage:
       id: str
       name: str
       version: str
       type: str  # "skill" / "mcp_server"
       author: str
       description: str
       download_url: str
       signature: str  # Ed25519 签名
       checksum: str  # SHA256

2. 实现 testagent/plugins/verifier.py：
   class PluginVerifier:
       """插件签名验证器"""
       async def verify_signature(self, package: PluginPackage, public_key: str) -> bool: ...
       async def verify_checksum(self, package_path: Path, expected: str) -> bool: ...
       async def scan_malicious(self, package_path: Path) -> list[str]:
           """基础恶意扫描：检查危险 API 调用（os.system/subprocess/exec）"""

3. 插件市场安装必须在沙箱中执行，遵循 ADR-004 隔离原则（硬约束）

4. 禁止安装未签名插件（可配置为允许，但默认警告）

5. 所有 I/O 操作必须使用 async/await

同时编写单元测试 tests/unit/test_plugin_registry.py：
- 测试 publish/search/install/uninstall 流程
- 测试签名验证
- 测试恶意扫描
- 测试沙箱安装流程
```

**验证检查点**：

```bash
pytest tests/unit/test_plugin_registry.py -v
ruff check testagent/plugins/
mypy testagent/plugins/ --strict
```

**⚠️ 创新风险标注**：插件恶意扫描是基础实现，无法保证 100% 安全。**降级方案**：增加人工审核环节，高风险插件需管理员批准。

***

### Step 19.2：多租户数据隔离与 RBAC 权限

**目标**：实现团队租户隔离和 RBAC 权限管理。

**依赖**：V1.0 Step 8.2（Engine 双模式）

**Prompt 模板**：

```
请实现 TestAgent 多租户数据隔离和 RBAC 权限管理。

基于 V1.0 已有的 testagent/db/ 和 PostgreSQL（参照 PRD §3.3 团队协作和 AGENTS.md 数据模型约定）：

1. 实现 testagent/gateway/tenant.py：
   class TenantManager:
       """多租户管理器"""
       def __init__(self, db_engine: AsyncEngine): ...

       async def create_tenant(self, name: str, plan: str = "free") -> str:
           """创建租户：创建独立 PostgreSQL schema"""
       async def get_tenant_schema(self, tenant_id: str) -> str:
           """获取租户 schema 名称：tenant_{id}"""
       async def delete_tenant(self, tenant_id: str) -> None: ...

2. 实现 testagent/gateway/rbac.py：
   class RBACManager:
       """RBAC 权限管理"""
       ROLES = {
           "owner": ["*"],  # 所有权限
           "admin": ["session.*", "skill.*", "mcp.*", "rag.*", "team.manage"],
           "member": ["session.*", "skill.read", "mcp.read", "rag.read"],
           "viewer": ["session.read", "skill.read", "rag.read"],
       }

       async def assign_role(self, user_id: str, tenant_id: str, role: str) -> None: ...
       async def check_permission(self, user_id: str, tenant_id: str, action: str) -> bool: ...
       async def list_roles(self, tenant_id: str) -> list[dict]: ...

3. 修改 testagent/db/engine.py：
   - 每个请求从 JWT token 提取 tenant_id
   - 设置 PostgreSQL search_path 到租户 schema
   - 团队协作的数据隔离必须基于 SQLAlchemy 的 schema/database 级别隔离（硬约束）
   - 遵循 AGENTS.md：每个 Executor Agent 在独立数据库 schema 中运行

4. 新增数据模型：
   - Tenant: id, name, plan, created_at
   - User: id, email, tenant_id, role, created_at
   - APIKey: id, user_id, key_hash, permissions

5. 遵循 AGENTS.md 安全红线：API Key 必须脱敏显示

6. 修改 Gateway middleware：每个请求验证 JWT + tenant_id + RBAC

同时编写单元测试 tests/unit/test_tenant.py 和 tests/unit/test_rbac.py：
- 测试租户创建和 schema 隔离
- 测试 RBAC 角色分配和权限检查
- 测试多租户数据隔离
- 测试 API Key 脱敏
```

**验证检查点**：

```bash
pytest tests/unit/test_tenant.py tests/unit/test_rbac.py -v
ruff check testagent/gateway/
mypy testagent/gateway/ --strict
```

***

### Step 19.3：共享知识库与协作通知

**目标**：实现团队共享知识库（RAG Collection 权限控制）和协作通知（Slack/飞书/钉钉）。

**依赖**：Step 19.2

**Prompt 模板**：

```
请实现 TestAgent 共享知识库和协作通知系统。

基于 V1.0 已有的 testagent/rag/ 和 Step 19.2 的 RBAC（参照 PRD §3.3 团队协作）：

1. 扩展 RAG Collection 权限控制：
   - 每个 Collection 可设置 tenant_id（团队专属）或 tenant_id=NULL（全局共享）
   - RAGPipeline.query() 自动注入 tenant_id 过滤
   - 团队管理员可设置 Collection 权限：private / team / public

2. 实现 testagent/mcp_servers/notification_server/：
   server.py:
   class NotificationMCPServer(BaseMCPServer):
       server_name = "notification_server"

   tools.py:
   async def notify_slack(channel: str, message: str, blocks: dict | None = None) -> dict:
       """发送 Slack 通知"""
   async def notify_feishu(webhook_url: str, message: str) -> dict:
       """发送飞书通知"""
   async def notify_dingtalk(webhook_url: str, message: str) -> dict:
       """发送钉钉通知"""
   async def notify_email(to: str, subject: str, body: str) -> dict:
       """发送邮件通知"""

3. 实现通知规则引擎：
   class NotificationRuleEngine:
       """协作通知规则"""
       async def should_notify(self, event_type: str, tenant_id: str) -> list[dict]:
           """
           根据事件类型和团队配置决定通知渠道：
           - session.completed → 通知频道（Slack/飞书）
           - defect.filed → 通知频道 + 邮件
           - performance.regression → 通知频道 + 邮件
           - exploration.new_finding → 通知频道
           """

4. 遵循 AGENTS.md Do #3：新增 MCP Server 必须实现 list_tools / call_tool / list_resources + health_check
5. 所有 I/O 操作必须使用 async/await，必须使用 httpx.AsyncClient

同时编写单元测试 tests/unit/test_notification_server.py 和 tests/unit/test_rag_permissions.py：
- 测试通知发送（mock Slack/飞书/钉钉 API）
- 测试 RAG Collection 权限控制
- 测试通知规则引擎
```

**验证检查点**：

```bash
pytest tests/unit/test_notification_server.py tests/unit/test_rag_permissions.py -v
ruff check testagent/mcp_servers/ testagent/rag/
mypy testagent/ --strict
```

***

### Step 19.4：Dashboard 团队视图

**目标**：Dashboard 增加团队管理、成员管理、共享知识库页面。

**依赖**：Step 19.2, Step 19.3

**Prompt 模板**：

```
请为 TestAgent Dashboard 增加团队协作功能页面。

基于 V1.0 已有的 dashboard/ 和 Step 19.2 的 RBAC（参照 PRD §3.3 团队协作）：

1. 新增 src/pages/Team.tsx（团队管理页面）：
   - 团队信息：名称、计划、成员数
   - 成员列表：邮箱、角色、加入时间
   - 角色管理：Owner/Admin/Member/Viewer
   - 邀请成员：发送邮件邀请
   - API Key 管理：创建/撤销 Team API Key

2. 新增 src/pages/Knowledge.tsx 增强（共享知识库）：
   - 权限控制：private/team/public 标签
   - 团队共享 Collection：所有成员可见
   - 管理员可设置 Collection 权限

3. 新增 src/pages/Settings.tsx 增强：
   - 通知配置：Slack/飞书/钉钉 Webhook 设置
   - 通知规则：事件类型 → 通知渠道映射
   - 插件市场：浏览/安装/卸载插件

4. 修改 MainLayout.tsx：
   - 新增侧边栏菜单项：团队管理
   - 头部显示当前团队和用户角色

5. 前端代码必须使用 React 18 + TypeScript + Ant Design 5.x（硬约束）
6. API Key 显示必须脱敏（AGENTS.md 安全红线）
```

**验证检查点**：

```bash
cd dashboard && npm run build && npm run lint && npx tsc --noEmit
```

***

## Phase 20：性能优化与发布

> **目标**：20 路并行压测 + 瓶颈优化、RAG <500ms、CLI <1.5s、内存空闲 <256MB、全链路 E2E 压力测试、V2.0 Release 打包。

***

### Step 20.1：20 路并行压测与瓶颈优化

**目标**：将并行路数从 10 路扩展到 20 路并完成瓶颈优化。

**依赖**：V1.0 Step 11.1（10 路并行）

**Prompt 模板**：

```
请将 TestAgent 并行执行能力从 10 路扩展到 20 路并完成瓶颈优化。

基于 V1.0 已有的 testagent/harness/resource_scheduler.py（参照 PRD §6.1 V2.0 性能目标：并发执行路数 20）：

1. 修改 Celery 配置：
   - worker_concurrency = 20
   - execution queue concurrency = 20

2. 优化 ResourceScheduler：
   - 支持 20 路资源分配和调度
   - 智能排队：资源不足时按优先级排队
   - 每个任务资源配额动态调整（避免固定分配）

3. 优化数据库连接池：
   - PostgreSQL pool_size = 25（20 Executor + 5 系统预留）
   - max_overflow = 10
   - 每个并行任务使用独立连接

4. 优化 Harness 沙箱管理：
   - Docker 容器预热池：预创建 5 个空闲容器
   - 容器复用：同类任务复用已创建容器
   - 资源隔离强化：cgroup v2 限制

5. 遵循 PRD §6.1 V2.0 性能目标：20 路并行内存 <12GB

6. 遵循 AGENTS.md：每个 Executor Agent 在独立数据库 schema 中运行

7. 创建压测脚本 tests/stress/test_20_way_parallel.py：
   - 提交 20 个独立 API 测试任务
   - 验证全部成功完成
   - 记录总耗时和资源使用
   - 内存峰值 < 12GB
```

**验证检查点**：

```bash
pytest tests/stress/test_20_way_parallel.py -v --timeout=600
# 验证 20 路并行稳定运行
```

***

### Step 20.2：RAG 检索 <500ms 优化

**目标**：优化 RAG 检索延迟到 <500ms（PRD §6.1 V2.0 目标）。

**依赖**：V1.0 Step 9.5（RAG Pipeline V1.0）

**Prompt 模板**：

```
请优化 TestAgent RAG 检索延迟到 <500ms。

基于 V1.0 已有的 testagent/rag/pipeline.py（参照 PRD §6.1 V2.0 性能目标：RAG 检索延迟 <500ms）：

1. Milvus 缓存优化：
   - 实现 query_cache.py：LRU 缓存热门查询结果
   - 缓存 TTL = 300s，最大缓存 10000 条
   - 缓存命中率目标 > 60%

2. 向量检索优化：
   - Milvus 索引类型从 IVF_FLAT 切换到 HNSW（召回率 + 速度平衡）
   - HNSW 参数：M=16, efConstruction=256, efSearch=64
   - 向量检索超时 = 200ms

3. 关键词检索优化：
   - Meilisearch 搜索超时 = 200ms
   - 热门 Collection 预加载到内存

4. RRF 融合优化：
   - 融合逻辑纯内存操作，目标 <10ms
   - 预计算 top_k 融合结果缓存

5. Cross-Encoder 重排优化：
   - 批量推理：一次处理 top_k * 2 条
   - GPU 加速（如果可用）
   - 重排超时 = 100ms，超时跳过

6. 创建性能基准测试 tests/stress/test_rag_latency.py：
   - 对 6 个 Collection 各执行 100 次查询
   - P50 < 300ms, P95 < 500ms, P99 < 800ms
   - 验证缓存命中率

7. 遵循 PRD §6.1 V2.0：RAG 检索延迟 <500ms
8. 遵循 AGENTS.md ADR-003：禁止仅使用纯向量检索
```

**验证检查点**：

```bash
pytest tests/stress/test_rag_latency.py -v --timeout=120
# 验证 P95 < 500ms
```

***

### Step 20.3：CLI 冷启动 <1.5s 与内存优化

**目标**：优化 CLI 冷启动时间 <1.5s 和空闲内存 <256MB。

**依赖**：V1.0 Step 6.3（CLI 命令层）

**Prompt 模板**：

```
请优化 TestAgent CLI 冷启动时间和内存占用。

基于 V1.0 已有的 testagent/cli/（参照 PRD §6.1 V2.0 性能目标：CLI 启动 <1.5s，空闲内存 <256MB）：

1. CLI 冷启动优化（目标 <1.5s）：
   - 懒加载：所有子命令模块延迟 import
   - 仅在命令执行时 import 重依赖（FastAPI/Celery/ChromaDB/Milvus）
   - 入口点优化：testagent/__init__.py 不做任何初始化
   - 使用 importlib 按需加载

   修改 testagent/cli/main.py：
   app = typer.Typer(
       name="testagent",
       lazy_loading=True,  # 子命令延迟加载
   )
   # 每个子命令使用 callback 延迟 import
   @app.callback()
   def main(): pass

   @app.command()
   def run():
       from testagent.cli.run_cmd import _run  # 延迟 import
       _run()

2. 内存优化（空闲 <256MB）：
   - RAG Pipeline：不预加载 Embedding 模型，首次使用时加载
   - Milvus/Meilisearch：连接池按需创建
   - MCP Server：不预启动，按需 spawn 子进程
   - 配置缓存：使用 __slots__ 减少内存

3. 创建性能基准测试：
   tests/stress/test_cli_startup.py：
   - 测量 testagent --help 启动时间 <1.5s
   - 测量 testagent skill list 启动时间 <2s

   tests/stress/test_memory_usage.py：
   - 测量空闲进程内存 <256MB
   - 测量 20 并行时内存 <12GB

4. 遵循 PRD §6.1 V2.0：CLI 启动 <1.5s，空闲内存 <256MB
```

**验证检查点**：

```bash
pytest tests/stress/test_cli_startup.py tests/stress/test_memory_usage.py -v --timeout=120
# 验证 CLI 启动 <1.5s
# 验证空闲内存 <256MB
```

***

### Step 20.4：全链路 E2E 压力测试与 V2.0 Release

**目标**：完成全链路 E2E 压力测试，打包 V2.0 Release。

**依赖**：Step 20.1 ~ Step 20.3

**Prompt 模板**：

```
请完成 TestAgent V2.0 全链路 E2E 压力测试和 Release 打包。

1. 创建 tests/e2e/test_v2_full_chain.py：

   async def test_multi_llm_routing():
   验证 LLM Router 在 20 路并行下正确路由

   async def test_exploratory_testing_end_to_end():
   验证 ExplorationAgent 自主探索并发现缺陷

   async def test_performance_regression_detection():
   验证性能基准检测到回归并创建缺陷

   async def test_accessibility_compliance():
   验证 WCAG 2.1 AA 级合规检测

   async def test_device_matrix_compatibility():
   验证多设备兼容性测试

   async def test_plugin_market_install():
   验证插件安装和卸载

   async def test_multi_tenant_isolation():
   验证两个租户数据完全隔离

   async def test_collaboration_notification():
   验证缺陷归档后发送 Slack 通知

2. 创建 tests/stress/test_v2_all_targets.py：
   验证所有 PRD §6.1 V2.0 性能目标：
   - 测试计划生成 <10s
   - 单条 API 测试 <2s
   - 单条 Web 测试 <15s
   - 20 路并行
   - 失败分类 <3s
   - CLI 启动 <1.5s
   - RAG 检索 <500ms
   - 空闲内存 <256MB
   - 20 并行内存 <12GB

3. V2.0 Release 打包：
   - 更新 pyproject.toml 版本到 2.0.0
   - 更新 README.md V2.0 功能列表
   - 更新 CHANGELOG.md
   - 打包 Docker 镜像：testagent:2.0.0
   - 打包 Dashboard：dashboard/dist/
   - 生成 API 文档：OpenAPI spec
```

**验证检查点**：

```bash
# E2E 全链路
pytest tests/e2e/test_v2_full_chain.py -v --timeout=600

# 性能目标
pytest tests/stress/test_v2_all_targets.py -v --timeout=600

# 代码质量
ruff check . && mypy testagent/ --strict
pytest --cov=testagent --cov-report=term-missing

# 打包验证
pip install -e ".[dev]"
testagent --version  # 2.0.0
docker build -t testagent:2.0.0 .
```

***

## 附录

### A. V2.0 Phase/Step 总览与依赖图

```
Phase 15: 多 LLM 适配层 (5 Steps)
  15.1 ILLMProvider 接口扩展 + Claude Provider ── 依赖 V1.0 Step 2.2
  15.2 Gemini Provider + LLM Router ──────────── 依赖 15.1
  15.3 Ollama 多模型管理 + Provider 注册 ──────── 依赖 V1.0 Step 2.2
  15.4 令牌桶优化 + 成本实时看板 ─────────────── 依赖 15.2
  15.5 Phase 15 集成验证 ────────────────────── 依赖 15.1~15.4

Phase 16: 自主探索性测试引擎 (3 Steps)
  16.1 ExplorationAgent 角色定义 ─────────────── 依赖 V1.0 Step 2.5
  16.2 好奇心驱动策略 + 状态空间图 ────────────── 依赖 16.1
  16.3 探索执行集成 + exploratory_test Skill ─── 依赖 16.2

Phase 17: 性能基准验证 (3 Steps)
  17.1 PerformanceRunner + k6/Locust MCP Server ── 依赖 V1.0 Step 5.1
  17.2 性能基线管理 + 回归检测 ──────────────── 依赖 17.1
  17.3 performance_sanity_test Skill + Dashboard ── 依赖 17.2

Phase 18: 无障碍与兼容性 (3 Steps)
  18.1 AccessibilityRunner + axe-core ────────── 依赖 V1.0 Step 5.1
  18.2 WCAG 2.1 AA 规则引擎 + accessibility_test ── 依赖 18.1
  18.3 设备矩阵 + 云真机 Provider ────────────── 依赖 V1.0 Step 10.2

Phase 19: 插件市场与团队协作 (4 Steps)
  19.1 Plugin Registry + 签名验证 ────────────── 依赖 V1.0 Step 14.1
  19.2 多租户隔离 + RBAC 权限 ──────────────── 依赖 V1.0 Step 8.2
  19.3 共享知识库 + 协作通知 ────────────────── 依赖 19.2
  19.4 Dashboard 团队视图 ───────────────────── 依赖 19.2, 19.3

Phase 20: 性能优化与发布 (4 Steps)
  20.1 20 路并行 + 瓶颈优化 ─────────────────── 依赖 V1.0 Step 11.1
  20.2 RAG 检索 <500ms 优化 ─────────────────── 依赖 V1.0 Step 9.5
  20.3 CLI <1.5s + 内存 <256MB 优化 ─────────── 依赖 V1.0 Step 6.3
  20.4 全链路 E2E + V2.0 Release ────────────── 依赖 20.1~20.3

Phase 间并行关系：
  Phase 15 (LLM) ← 独立（可先启动）
  Phase 16 (探索) ← 独立，但 16.3 可用 15.2 的 LLM Router
  Phase 17 (性能) ← 独立
  Phase 18 (无障碍/兼容) ← 独立
  Phase 19 (插件/协作) ← 独立
  Phase 20 (性能优化) ← 依赖 15-19 全部完成
  推荐：Phase 15 先行，16/17/18/19 可并行
```

### B. V2.0 创新风险速查表

| Step | 创新技术 | 风险等级 | 降级方案 |
|------|---------|---------|---------|
| 16.1 | ExplorationAgent 接入 ReAct Loop | 🔴 高 | 脚本化探索模式 |
| 16.2 | 好奇心驱动策略 | 🔴 高 | 随机探索 + 人工引导 |
| 16.3 | 自主探索发现率 | 🟡 中 | 基准被测应用验证 |
| 15.2 | LLM Router 多 Provider 路由 | 🟡 中 | 回退默认 Provider |
| 18.3 | 云真机 API 稳定性 | 🟡 中 | 本地设备/模拟器优先 |
| 19.1 | 插件恶意扫描 | 🟡 中 | 人工审核 + 白名单 |
| 20.2 | RAG <500ms | 🟡 中 | 缓存 + 预计算 + 跳过重排 |

### C. V2.0 PRD 功能编号映射

| PRD 功能方向 | PRD 功能描述 | 对应 Step | 状态 |
|------------|-------------|----------|------|
| 自主探索性测试 | Agent 无脚本探索 | Phase 16 | V2.0 |
| 性能基准验证 | API P95/P99 + 吞吐量 | Phase 17 | V2.0 |
| 无障碍合规测试 | WCAG 2.1 AA | Step 18.1-18.2 | V2.0 |
| App 多设备兼容性 | 云真机矩阵 | Step 18.3 | V2.0 |
| 多 LLM 适配 | Claude/Gemini/本地切换 | Phase 15 | V2.0 |
| 插件市场 | 第三方插件分发 | Step 19.1 | V2.0 |
| 团队协作 | 多租户+RBAC+共享知识库 | Step 19.2-19.4 | V2.0 |

### D. 每步 Commit 规范

每完成一个 Step，执行以下 Git 操作：

```bash
git add -A
git commit -m "feat(scope): Step XX.Y — <简要描述>"
# 示例：git commit -m "feat(llm): Step 15.2 — Gemini Provider + LLM Router 智能路由"
# 遵循 AGENTS.md Git 工作流：type(scope): description
# type 限于 feat/fix/refactor/test/docs/chore
# 分支命名：feat/F-XXX-description
```

### E. V2.0 性能目标验收标准

| 指标 | V2.0 目标 | 验收方法 | 验收 Step |
|------|---------|---------|----------|
| 测试计划生成 | <10s | 20 次取 P95 | 20.4 |
| 单条 API 测试 | <2s | 100 次取 P95 | 20.4 |
| 单条 Web 测试 | <15s | 20 次取 P95 | 20.4 |
| 并发执行路数 | 20 | 压测验证 | 20.1 |
| 失败分类 | <3s | 50 次取 P95 | 20.4 |
| CLI 启动 | <1.5s | 20 次取 P95 | 20.3 |
| RAG 检索 | <500ms | 100 次取 P95 | 20.2 |
| 空闲内存 | <256MB | 进程 RSS | 20.3 |
| 20 并行内存 | <12GB | 峰值 RSS | 20.1 |
           