# TestAgent 使用文档

> **版本**：v0.1.0 (MVP)  
> **最后更新**：2026-05-06

---

## 1. 项目简介

TestAgent 是一款面向 App / Web / API 全平台的 **AI 测试智能体平台**，通过 Planner→Executor→Analyzer 三层 Agent 协作、MCP 工具调用、RAG 知识检索与 Harness 沙箱执行，实现从测试规划到缺陷归档的全生命周期自主化——让 AI 替你写脚本、跑测试、提缺陷。

---

## 2. 环境要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| **操作系统** | macOS 12+ / Ubuntu 22.04+ / Windows 11 (WSL2) | 同左 |
| **Python** | 3.12+ | 3.12 或 3.13 |
| **Docker** | Docker Desktop 4.x（用于沙箱隔离） | Docker Desktop 4.x |
| **Redis** | 7.x（任务队列 Broker） | 7.x |
| **CPU** | 4 核 | 8 核 |
| **内存** | 8 GB | 16 GB |
| **磁盘** | 20 GB SSD | 50 GB SSD |
| **网络** | 可访问 OpenAI API（或配置本地模型） | 同左 |

> **提示**：MVP 最低 8GB 内存分配——Gateway + Redis 1GB、RAG (ChromaDB) 1GB、LLM API 调用 512MB、Docker 沙箱 4GB、系统保留 1.5GB。使用本地模型需额外 4-8GB 显存 (GPU) 或 8GB 内存 (CPU 推理)。

---

## 3. 安装步骤

### 3.1 克隆项目

```bash
git clone <repository-url> vibe-ai-agent
cd vibe-ai-agent
```

### 3.2 创建并激活虚拟环境

```bash
# macOS / Linux
python -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3.3 安装依赖

```bash
# 安装运行时依赖
pip install -e .

# 安装开发依赖（含测试、lint、类型检查）
pip install -e ".[dev]"
```

### 3.4 启动基础设施服务

使用 Docker Compose 一键启动 Redis、ChromaDB、Meilisearch：

```bash
docker compose -f docker/docker-compose.dev.yml up -d
```

验证服务状态：

```bash
docker compose -f docker/docker-compose.dev.yml ps
```

预期输出中 `redis`、`chromadb`、`meilisearch` 三个服务状态均为 `healthy`。

### 3.5 初始化数据库

```bash
alembic upgrade head
```

### 3.6 配置环境变量

在项目根目录创建 `.env` 文件：

```bash
# LLM 配置（必填）
TESTAGENT_OPENAI_API_KEY=sk-your-openai-api-key

# 或使用本地模型
# TESTAGENT_LLM_PROVIDER=local
# TESTAGENT_LOCAL_MODEL_URL=http://localhost:11434

# 数据库（默认 SQLite，无需修改）
# TESTAGENT_DATABASE_URL=sqlite+aiosqlite:///./testagent.db

# Redis（默认 localhost:6379）
# TESTAGENT_REDIS_URL=redis://localhost:6379/0

# Meilisearch（默认 localhost:7700）
# TESTAGENT_MEILISEARCH_URL=http://localhost:7700
# TESTAGENT_MEILISEARCH_API_KEY=your-meilisearch-key

# Embedding 模式（local 或 openai）
# TESTAGENT_EMBEDDING_MODE=local
# TESTAGENT_EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
```

> **安全提示**：API Key 通过环境变量注入，配置文件中 Key 以 `***` 脱敏显示。也可通过操作系统 Keyring 加密存储。

### 3.7 验证安装

```bash
testagent --help
```

看到帮助信息即表示安装成功。

---

## 4. 快速开始

以下示例帮助你在 5 分钟内跑通一次 API 冒烟测试。

### 4.1 初始化项目

```bash
testagent init --project my-app --type api
```

输出示例：

```
Initialized test project 'my-app' at /path/to/my-app
  Type: api
  Config: /path/to/my-app/testagent.json
  Plans:  /path/to/my-app/test-plans/
  Config: /path/to/my-app/config/
```

### 4.2 运行 API 冒烟测试

```bash
testagent run --skill api_smoke_test --env staging
```

### 4.3 运行 Web 冒烟测试

```bash
testagent run --skill web_smoke_test --url https://staging.myapp.com
```

### 4.4 使用测试计划文件

```bash
testagent run --plan ./test-plans/my-plan.json
```

### 4.5 交互式对话

```bash
testagent chat
```

进入交互模式后，直接输入自然语言：

```
You> 帮我对登录模块做一次冒烟测试
You> 上次支付接口的缺陷修复了吗？帮我回归验证
You> exit
```

---

## 5. 核心功能说明

### 5.1 命令总览

| 命令 | 说明 |
|------|------|
| `testagent init` | 初始化测试项目 |
| `testagent run` | 执行测试 Skill 或测试计划 |
| `testagent chat` | 启动交互式对话模式 |
| `testagent ci` | CI/CD 非交互模式执行 |
| `testagent serve` | 启动 Gateway API 服务 |
| `testagent skill list` | 列出已注册的 Skill |
| `testagent skill create` | 从模板创建新 Skill |
| `testagent mcp add` | 注册 MCP Server |
| `testagent mcp list` | 列出已配置的 MCP Server |
| `testagent mcp health` | 检查 MCP Server 健康状态 |
| `testagent rag-index` | 索引文档到 RAG 知识库 |
| `testagent rag-query` | 查询 RAG 知识库 |

### 5.2 测试执行 (`testagent run`)

**核心命令**，通过指定 Skill 或测试计划文件来触发完整的 Planner→Executor→Analyzer 三层 Agent 流水线。

```bash
# 通过 Skill 名称执行
testagent run --skill api_smoke_test --env staging

# 通过测试计划文件执行
testagent run --plan ./test-plan.json

# 指定目标 URL
testagent run --skill web_smoke_test --url https://demo.example.com

# 组合使用
testagent run --skill api_regression_test --env production
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--skill`, `-s` | 要执行的 Skill 名称 | — |
| `--plan`, `-p` | 测试计划 JSON 文件路径 | — |
| `--env`, `-e` | 目标环境 | `dev` |
| `--url`, `-u` | 目标 URL（覆盖环境配置） | — |

> `--skill` 和 `--plan` 必须提供其中之一。

### 5.3 交互式对话 (`testagent chat`)

启动一个基于 Agent Loop 的交互式测试会话，支持自然语言输入。

```bash
testagent chat
```

内置命令：

| 命令 | 说明 |
|------|------|
| `exit` / `quit` | 退出对话 |
| `help` | 显示帮助 |
| `clear` | 清空对话历史 |

### 5.4 CI/CD 模式 (`testagent ci`)

专为 CI/CD 管道设计的非交互模式，支持 JUnit XML 报告输出和退出码控制。

```bash
# 基本用法
testagent ci api_smoke_test --env ci

# 失败时返回非零退出码
testagent ci api_smoke_test --exit-code --env ci

# 输出 JUnit XML 报告
testagent ci api_smoke_test --exit-code --junit report.xml

# 设置全局超时
testagent ci api_smoke_test --timeout 300 --env ci
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `SKILL` | 要执行的 Skill 名称（位置参数） | — |
| `--exit-code` | 失败时返回非零退出码 | `False` |
| `--junit`, `-j` | JUnit XML 报告输出路径 | — |
| `--timeout`, `-t` | 全局超时（秒） | `300` |
| `--env`, `-e` | 目标环境 | `ci` |
| `--url`, `-u` | 目标 URL | — |

**GitHub Actions 集成示例**：

```yaml
name: TestAgent Smoke Test
on: [push, pull_request]
jobs:
  testagent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install TestAgent
        run: pip install -e .
      - name: Run Smoke Test
        run: testagent ci api_smoke_test --exit-code --junit report.xml --env staging
      - name: Upload Report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: testagent-report
          path: report.xml
```

### 5.5 Gateway 服务 (`testagent serve`)

启动 TestAgent 的 FastAPI Gateway 服务，提供 RESTful API 和 WebSocket 接口。

```bash
# 默认启动
testagent serve

# 自定义主机和端口
testagent serve --host 127.0.0.1 --port 9000
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--host` | 绑定地址 | `0.0.0.0` |
| `--port`, `-p` | 绑定端口 | `8000` |

启动后可访问：
- API 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`

### 5.6 Skill 管理

#### 列出已注册 Skill

```bash
testagent skill list
```

输出示例：

```
Name                           Version      Description
--------------------------------------------------------------------------------
api_smoke_test                 1.0.0        API 冒烟测试技能，覆盖核心 Endpoint 的正向验证
api_regression_test            1.0.0        API 回归测试技能，覆盖边界值和异常值场景
web_smoke_test                 1.0.0        Web 页面冒烟测试，验证核心流程可用性
app_smoke_test                 1.0.0        App 核心流程冒烟测试
```

#### 创建新 Skill

```bash
# 从 API 测试模板创建
testagent skill create --template api_test --output ./skills/

# 从 Web 测试模板创建
testagent skill create --template web_test --output ./skills/
```

可用模板：`api_test`、`web_test`

### 5.7 MCP Server 管理

MCP (Model Context Protocol) Server 是 Agent 调用外部工具的标准化接口。

#### 注册 MCP Server

```bash
# 基本注册
testagent mcp add api-server --command python

# 通过配置文件注册
testagent mcp add jira-server --config ./configs/jira.json
```

#### 列出已配置的 MCP Server

```bash
testagent mcp list
```

#### 健康检查

```bash
# 检查所有 Server
testagent mcp health

# 检查指定 Server
testagent mcp health api_server
```

#### 预置 MCP Server

| Server | 说明 | 关键工具 |
|--------|------|---------|
| `api_server` | API 测试执行 | `api_request`、`api_validate_schema`、`api_compare_response` |
| `playwright_server` | Web UI 测试 | `browser_navigate`、`browser_click`、`browser_screenshot`、`browser_assert` |
| `jira_server` | 缺陷管理 | `jira_create_issue`、`jira_search_issues`、`jira_update_issue` |
| `git_server` | 代码分析 | `git_diff`、`git_blame`、`git_log` |
| `database_server` | 数据库验证 | `db_query`、`db_seed`、`db_cleanup` |

### 5.8 RAG 知识库

RAG (Retrieval-Augmented Generation) 知识库为 Agent 提供历史知识检索能力，让测试"越用越准"。

#### 索引文档

```bash
# 索引目录下的文档
testagent rag-index ./docs/api --collection api_docs

# 索引需求文档
testagent rag-index ./docs/requirements --collection req_docs
```

#### 查询知识库

```bash
# 查询 API 文档
testagent rag-query "登录接口的请求参数" --collection api_docs

# 指定返回结果数
testagent rag-query "订单模块历史缺陷" --collection defect_history --top-k 10
```

#### 预置 RAG Collection

| Collection | 说明 | 数据源 | 访问角色 |
|-----------|------|--------|---------|
| `req_docs` | 产品需求文档 | PRD、User Story | Planner |
| `api_docs` | OpenAPI/Swagger 规范 | API 文档 | Planner, Executor |
| `defect_history` | 历史缺陷 | Jira 缺陷记录 | Planner, Analyzer |
| `test_reports` | 历史测试报告 | 测试执行结果 | Analyzer |
| `locator_library` | UI 定位器库 | 元素定位信息 | Executor |
| `failure_patterns` | 失败模式库 | 已知失败模式 | Analyzer |

### 5.9 三层 Agent 协作流程

TestAgent 的核心架构是 Planner→Executor→Analyzer 三层 Agent 协作：

```
用户输入 → TestGateway → Planner Agent（生成测试计划）
                         ↓
                    Executor Agent（在沙箱中执行测试）
                         ↓
                    Analyzer Agent（失败分类 + 缺陷归档）
                         ↓
                    测试报告 → 用户
```

| Agent | 职责 | 上下文窗口 | 工具集 |
|-------|------|-----------|--------|
| **Planner** | 需求解析、策略生成、任务编排 | 128K | MCP: Jira, Git; Skills: 策略类 |
| **Executor** | 测试执行、自愈修复、结果收集 | 32K | MCP: Playwright, API; Harness Runner |
| **Analyzer** | 失败分类、根因分析、缺陷归档 | 64K | MCP: Jira, Git; Skills: 分析类 |

### 5.10 Harness 沙箱执行

TestAgent 通过 Harness 引擎在隔离沙箱中执行测试：

| 隔离级别 | 适用场景 | 资源配额 | 超时 |
|---------|---------|---------|------|
| **Docker Container** | API 测试、Web 测试 | API: 1CPU/512MB; Web: 2CPU/2GB | API: 60s; Web: 120s |
| **MicroVM** | App 测试（V1.0） | 4CPU/4GB | 180s |
| **本地进程** | 开发调试（仅限本地） | 无限制 | 自定义 |

> 生产和 CI 环境禁止使用本地进程隔离模式。

---

## 6. 配置说明

所有配置通过环境变量注入，统一前缀为 `TESTAGENT_`，支持 `.env` 文件。

### 6.1 应用配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_APP_NAME` | 应用名称 | `TestAgent` | — |
| `TESTAGENT_APP_VERSION` | 应用版本 | `0.1.0` | — |
| `TESTAGENT_DEBUG` | 调试模式 | `False` | 开发时设为 `True` |

### 6.2 数据库配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_DATABASE_URL` | 数据库连接串 | `sqlite+aiosqlite:///./testagent.db` | MVP 用默认值；V1.0 迁移 PostgreSQL |
| `TESTAGENT_DATABASE_ECHO` | SQL 日志输出 | `False` | 调试时设为 `True` |

### 6.3 Redis / Celery 配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` | — |
| `TESTAGENT_CELERY_BROKER_URL` | Celery Broker | `redis://localhost:6379/0` | 与 Redis 同 |
| `TESTAGENT_CELERY_RESULT_BACKEND` | Celery Result Backend | `redis://localhost:6379/1` | 使用不同 DB 编号 |

### 6.4 LLM 配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_LLM_PROVIDER` | LLM 提供者 | `openai` | `openai` 或 `local` |
| `TESTAGENT_OPENAI_API_KEY` | OpenAI API Key | — | **必填**（使用 OpenAI 时） |
| `TESTAGENT_OPENAI_MODEL` | OpenAI 模型名 | `gpt-4o` | `gpt-4o` |
| `TESTAGENT_LOCAL_MODEL_URL` | 本地模型 URL | `http://localhost:11434` | Ollama 默认地址 |

### 6.5 RAG 配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_CHROMA_PERSIST_DIR` | ChromaDB 持久化目录 | `./chroma_data` | — |
| `TESTAGENT_MEILISEARCH_URL` | Meilisearch 地址 | `http://localhost:7700` | — |
| `TESTAGENT_MEILISEARCH_API_KEY` | Meilisearch API Key | — | Docker Compose 默认: `testagent-dev-master-key` |
| `TESTAGENT_EMBEDDING_MODE` | Embedding 模式 | `local` | `local`(免费) 或 `openai`(需 API) |
| `TESTAGENT_EMBEDDING_MODEL` | 本地 Embedding 模型 | `BAAI/bge-large-zh-v1.5` | 中文场景推荐 |
| `TESTAGENT_OPENAI_EMBEDDING_MODEL` | OpenAI Embedding 模型 | `text-embedding-3-small` | API 模式时使用 |

### 6.6 Agent 配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_AGENT_MAX_ROUNDS` | Agent Loop 最大轮次 | `50` | — |
| `TESTAGENT_AGENT_TOKEN_THRESHOLD` | 上下文压缩阈值 (tokens) | `100000` | — |

### 6.7 Harness 配置

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_DEFAULT_ISOLATION_LEVEL` | 默认隔离级别 | `docker` | 生产环境保持 `docker` |
| `TESTAGENT_DOCKER_TIMEOUT_API` | API 测试超时 (秒) | `60` | — |
| `TESTAGENT_DOCKER_TIMEOUT_WEB` | Web 测试超时 (秒) | `120` | — |

### 6.8 数据保留

| 环境变量 | 说明 | 默认值 | 推荐设置 |
|---------|------|--------|---------|
| `TESTAGENT_DATA_RETENTION_DAYS` | 数据保留天数 | `90` | 合规要求可调整 |

### 6.9 MCP Server 配置

复制模板文件并修改：

```bash
cp configs/mcp.json.template configs/mcp.json
```

编辑 `configs/mcp.json`，配置各 MCP Server 的启动命令和环境变量。模板中已预置 5 个 Server 的配置示例。

### 6.10 RAG Collection 配置

复制模板文件并修改：

```bash
cp configs/rag_config.yaml.template configs/rag_config.yaml
```

配置各 Collection 的数据源路径、索引策略和访问权限。

---

## 7. 常见问题（FAQ）

### Q1: 启动时提示 `OpenAI API key not found` 怎么办？

**A**: 需要设置 OpenAI API Key 环境变量：

```bash
# 方法 1：在 .env 文件中添加
echo "TESTAGENT_OPENAI_API_KEY=sk-your-key" >> .env

# 方法 2：通过环境变量
export TESTAGENT_OPENAI_API_KEY=sk-your-key

# 方法 3：使用本地模型（无需 API Key）
export TESTAGENT_LLM_PROVIDER=local
# 需要先启动 Ollama 并下载模型
ollama pull qwen2.5
```

### Q2: Docker Compose 启动失败，端口被占用怎么办？

**A**: 修改 `docker/docker-compose.dev.yml` 中的端口映射：

```yaml
# 例如 Redis 端口冲突，修改为：
ports:
  - "6380:6379"  # 将宿主机端口改为 6380
```

同时更新 `.env` 中对应的连接配置：

```
TESTAGENT_REDIS_URL=redis://localhost:6380/0
```

### Q3: `testagent run` 报错 "Session execution module not available" 怎么办？

**A**: 确保已完整安装所有依赖：

```bash
pip install -e .
```

如果问题仍然存在，尝试先启动 Gateway 服务：

```bash
testagent serve &
testagent run --skill api_smoke_test
```

### Q4: 本地 Embedding 模型首次运行很慢？

**A**: `BAAI/bge-large-zh-v1.5` 模型首次运行时需从 HuggingFace 下载（约 1.3GB）。如网络受限，可切换到 OpenAI Embedding：

```bash
# .env 中设置
TESTAGENT_EMBEDDING_MODE=openai
```

### Q5: Celery Worker 如何启动？

**A**: 在另一个终端中启动 Celery Worker：

```bash
celery -A testagent.gateway.celery_app worker --loglevel=info --concurrency=4
```

需确保 Redis 服务已启动。

### Q6: Windows WSL2 下 Docker 沙箱无法正常工作？

**A**: 确保 Docker Desktop 已启用 WSL2 集成：Docker Desktop → Settings → Resources → WSL Integration → 勾选你的发行版。如果仍有问题，可在开发模式下使用本地进程隔离：

```bash
export TESTAGENT_DEFAULT_ISOLATION_LEVEL=local
```

> ⚠️ 本地进程模式仅限开发调试，禁止用于生产或 CI 环境。

### Q7: 数据库迁移报错怎么办？

**A**: 检查 `alembic.ini` 中的数据库连接配置，确保 `testagent.db` 文件所在目录有写权限。如需重置：

```bash
rm testagent.db
alembic upgrade head
```

### Q8: 如何查看 Agent 的详细执行日志？

**A**: 开启调试模式：

```bash
export TESTAGENT_DEBUG=True
testagent run --skill api_smoke_test --env dev
```

日志中会包含 Agent Loop 每轮的详细信息、工具调用记录和 RAG 检索结果。

### Q9: 如何自定义 Skill？

**A**: Skill 以 YAML Front Matter + Markdown Body 格式定义，存放在 `skills/` 目录：

```bash
# 从模板创建
testagent skill create --template api_test --output ./skills/my_skill/

# 或手动创建 skills/my_skill/SKILL.md
```

Skill 文件格式：

```markdown
---
name: my_custom_test
version: "1.0.0"
description: 我的自定义测试技能
trigger: "自定义测试|custom test"
required_mcp_servers:
  - api_server
required_rag_collections:
  - api_docs
---

## 目标
描述这个 Skill 的测试目标。

## 操作流程
1. 步骤一
2. 步骤二

## 断言策略
- 断言规则

## 失败处理
- 失败时的处理方式
```

### Q10: RAG 检索无结果怎么办？

**A**: 确保已索引文档到对应的 Collection：

```bash
# 先索引文档
testagent rag-index ./docs/api --collection api_docs

# 再查询
testagent rag-query "登录接口" --collection api_docs
```

如果 Embedding 服务不可用，系统会自动降级到纯 BM25 关键词检索。

---

## 8. 目录结构

```
vibe-ai-agent/
├── testagent/                    # 主包
│   ├── __init__.py
│   ├── __main__.py               # 入口点
│   │
│   ├── agent/                    # Agent Runtime 模块
│   │   ├── loop.py               # 核心 ReAct Loop（while stop_reason != "tool_use"）
│   │   ├── context.py            # 上下文组装器（AGENTS/SOUL/TOOLS/MEMORY 四层注入）
│   │   ├── planner.py            # Planner Agent 实现
│   │   ├── executor.py           # Executor Agent 实现
│   │   ├── analyzer.py           # Analyzer Agent 实现
│   │   ├── protocol.py           # Agent 间消息协议
│   │   ├── tools.py              # 工具注册与分发
│   │   └── todo.py               # 任务追踪
│   │
│   ├── gateway/                  # TestGateway 调度层
│   │   ├── app.py                # FastAPI 应用 + 生命周期管理
│   │   ├── router.py             # RESTful API 路由（Sessions/Skills/MCP/RAG）
│   │   ├── websocket.py          # WebSocket 会话管理
│   │   ├── session.py            # Session 状态机（pending→planning→executing→analyzing→completed）
│   │   ├── mcp_registry.py       # MCP Server 注册发现 + 健康检查
│   │   ├── mcp_router.py         # MCP 工具调用路由 + 审计日志
│   │   ├── middleware.py         # 认证/限流/错误处理中间件
│   │   ├── celery_app.py         # Celery 任务队列配置
│   │   └── tasks.py              # Celery 异步任务定义
│   │
│   ├── mcp_servers/              # MCP Server 实现
│   │   └── base.py               # MCP Server 基类 + 工具注册装饰器
│   │
│   ├── rag/                      # RAG Pipeline 模块
│   │   ├── pipeline.py           # RAG 主流水线（摄入→双路召回→RRF 融合）
│   │   ├── ingestion.py          # 文档摄入 + 分块（512 tokens + 64 overlap）
│   │   ├── embedding.py          # Embedding 服务（bge/OpenAI 双模式）
│   │   ├── vector_store.py       # 向量索引（ChromaDB）
│   │   ├── fulltext.py           # BM25 全文检索（Meilisearch）
│   │   ├── fusion.py             # RRF 融合排序（k=60）
│   │   ├── collections.py        # Collection 配置管理
│   │   └── factories.py          # Pipeline 工厂
│   │
│   ├── harness/                  # Harness 执行引擎
│   │   ├── orchestrator.py       # 任务调度 + 隔离决策 + 指数退避重试
│   │   ├── sandbox_factory.py    # 根据 isolation_level 创建 Sandbox
│   │   ├── sandbox.py            # ISandbox 协议定义
│   │   ├── docker_sandbox.py     # Docker Container 隔离
│   │   ├── local_runner.py       # 本地进程执行（开发模式）
│   │   ├── microvm_sandbox.py    # MicroVM 隔离（V1.0）
│   │   ├── runners/              # Runner 插件
│   │   │   ├── base.py           # IRunner 抽象类
│   │   ├── resource.py           # 资源配额管理
│   │   └── snapshot.py           # 执行快照 + 断点续跑
│   │
│   ├── skills/                   # Skill Engine 模块
│   │   ├── loader.py             # Skill 扫描 + 加载
│   │   ├── parser.py             # YAML Front Matter + Markdown Body 解析
│   │   ├── validator.py          # Schema 校验 + 依赖检查
│   │   ├── registry.py           # Skill 注册表（name/trigger/tags 索引）
│   │   ├── matcher.py            # Skill 匹配引擎
│   │   ├── executor.py           # Skill 执行器
│   │   └── templates.py          # Skill 模板（api_test / web_test）
│   │
│   ├── models/                   # 数据模型（SQLAlchemy 2.x）
│   │   ├── base.py               # BaseModel + 通用 Mixin
│   │   ├── session.py            # TestSession
│   │   ├── plan.py               # TestPlan / TestTask
│   │   ├── result.py             # TestResult
│   │   ├── defect.py             # Defect
│   │   ├── skill.py              # SkillDefinition
│   │   └── mcp_config.py         # MCPConfig
│   │
│   ├── db/                       # 数据库访问层
│   │   ├── engine.py             # Engine 创建 + 连接池
│   │   ├── repository.py         # 通用 Repository 模式
│   │   ├── migrations.py         # 迁移辅助
│   │   └── alembic/              # Alembic 迁移脚本
│   │
│   ├── llm/                      # LLM Provider 抽象层
│   │   ├── base.py               # ILLMProvider 接口 + RateLimiter + BudgetManager
│   │   ├── openai_provider.py    # OpenAI GPT-4o 实现（含 429 重试）
│   │   └── local_provider.py     # Ollama/Qwen 本地模型
│   │
│   ├── cli/                      # CLI 交互层
│   │   ├── main.py               # Typer App 入口（init/run/chat/ci/serve）
│   │   ├── skill_cmd.py          # testagent skill 命令组
│   │   ├── mcp_cmd.py            # testagent mcp 命令组
│   │   ├── rag_cmd.py            # testagent rag-index / rag-query
│   │   ├── output.py             # Rich 格式化输出
│   │   └── junit.py              # JUnit XML 报告生成
│   │
│   ├── config/                   # 配置管理
│   │   ├── settings.py           # Pydantic Settings（环境变量 + .env）
│   │   └── defaults.py           # 默认配置常量
│   │
│   └── common/                   # 公共工具
│       ├── logging.py            # 结构化日志
│       ├── errors.py             # 统一异常体系（TestAgentError）
│       └── security.py           # API Key 加密存储 + 数据脱敏
│
├── skills/                       # Skill Markdown 定义文件（与代码分离）
│   ├── api_smoke_test/SKILL.md
│   ├── api_regression_test/SKILL.md
│   ├── web_smoke_test/SKILL.md
│   └── app_smoke_test/SKILL.md
│
├── configs/                      # 配置模板
│   ├── mcp.json.template         # MCP Server 注册模板
│   └── rag_config.yaml.template  # RAG Collection 配置模板
│
├── docker/                       # Docker 镜像定义
│   ├── Dockerfile.harness        # Harness 沙箱镜像
│   ├── Dockerfile.api_runner     # API Runner 镜像
│   ├── Dockerfile.web_runner     # Web Runner 镜像（含 Chromium）
│   └── docker-compose.dev.yml    # 开发环境（Redis + ChromaDB + Meilisearch）
│
├── tests/                        # 测试
│   ├── unit/                     # 单元测试
│   ├── integration/              # 集成测试
│   └── e2e/                      # 端到端测试
│
├── pyproject.toml                # 项目元数据 + 依赖 + 工具配置
├── alembic.ini                   # Alembic 迁移配置
└── .env                          # 环境变量（不提交到 Git）
```

**关键设计原则**：
- `skills/` 目录存放 Skill Markdown 定义，`testagent/skills/` 存放 Skill Engine 代码，两者分离
- 模块间通过 `protocol.py` / `base.py` 定义的接口通信，禁止直接引用其他模块内部实现
- 所有 I/O 操作使用 `async/await`，使用 `httpx` 替代 `requests`
