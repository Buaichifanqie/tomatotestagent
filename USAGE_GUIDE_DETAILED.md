# TestAgent 完整使用操作手册

> **适用版本**: v0.1.0 (MVP)  
> **适用对象**: 第一次接触 TestAgent 的开发者/测试工程师  
> **目标**: 从零开始，完整掌握 TestAgent 的安装、配置、使用、排错

---

## 目录

1. [项目概述](#1-项目概述)
2. [环境准备与安装](#2-环境准备与安装)
3. [基础配置](#3-基础配置)
4. [启动基础设施服务](#4-启动基础设施服务)
5. [数据库初始化](#5-数据库初始化)
6. [CLI 命令详解](#6-cli-命令详解)
7. [运行你的第一个测试](#7-运行你的第一个测试)
8. [交互式聊天模式](#8-交互式聊天模式)
9. [CI/CD 集成](#9-cicd-集成)
10. [Gateway API 服务](#10-gateway-api-服务)
11. [Skill 管理](#11-skill-管理)
12. [MCP Server 管理](#12-mcp-server-管理)
13. [RAG 知识库管理](#13-rag-知识库管理)
14. [三层 Agent 架构说明](#14-三层-agent-架构说明)
15. [Harness 沙箱执行引擎](#15-harness-沙箱执行引擎)
16. [项目目录结构](#16-项目目录结构)
17. [常见问题与排错](#17-常见问题与排错)
18. [开发相关命令](#18-开发相关命令)

---

## 1. 项目概述

TestAgent 是一个 AI 驱动的测试智能体平台，支持 **App（iOS/Android）/ Web / API** 全平台的自动化测试。它的核心是一个三层的 AI Agent 协作系统：

```
用户输入 → Planner Agent（制定测试计划）
                ↓
          Executor Agent（在沙箱中执行测试）
                ↓
          Analyzer Agent（分析结果、归档缺陷）
                ↓
            测试报告
```

### 核心能力

- **AI 驱动**: 利用 LLM（GPT-4o 或本地模型）理解需求、生成测试用例、分析结果
- **全平台**: API 测试、Web UI 测试、App 测试全覆盖
- **知识积累**: RAG 知识库让测试越用越准
- **沙箱隔离**: 测试在 Docker 容器中安全执行
- **Skills 技能系统**: 可插拔的测试技能，支持自定义

---

## 2. 环境准备与安装

### 2.1 硬件要求

| 配置项 | 最低要求 | 推荐配置 |
|-------|---------|---------|
| 操作系统 | macOS 12+ / Ubuntu 22.04+ / Windows 11 (WSL2) | 同左 |
| CPU | 4 核 | 8 核 |
| 内存 | 8 GB | 16 GB |
| 磁盘 | 20 GB SSD | 50 GB SSD |
| Docker | Docker Desktop 4.x | Docker Desktop 4.x |
| 网络 | 可访问 OpenAI API（或配置本地模型） | 同左 |

### 2.2 安装步骤

#### 步骤 1：克隆项目

```bash
cd D:\test-ai-agent
# 项目已在此目录的 vibe-ai-agent 文件夹中
```

#### 步骤 2：创建并激活虚拟环境

```bash
# Windows PowerShell
cd D:\test-ai-agent\vibe-ai-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# macOS / Linux
cd vibe-ai-agent
python -m venv .venv
source .venv/bin/activate
```

#### 步骤 3：安装依赖

```bash
# 安装运行时依赖
pip install -e .

# 安装开发依赖（推荐）
pip install -e ".[dev]"
```

#### 步骤 4：验证安装

```bash
testagent --help
```

如果看到帮助信息，说明安装成功。

---

## 3. 基础配置

### 3.1 环境变量配置

在项目根目录创建 `.env` 文件（可从 `.env.example` 复制）：

```bash
# === LLM 配置（至少配置一种）===

# 选项 A：使用 OpenAI（推荐，需要 API Key）
TESTAGENT_OPENAI_API_KEY=sk-your-openai-api-key

# 选项 B：使用本地模型（免费，需要安装 Ollama）
# TESTAGENT_LLM_PROVIDER=local
# TESTAGENT_LOCAL_MODEL_URL=http://localhost:11434

# === 数据库（MVP 使用 SQLite，无需修改）===
# TESTAGENT_DATABASE_URL=sqlite+aiosqlite:///./testagent.db

# === Redis（默认 localhost:6379）===
# TESTAGENT_REDIS_URL=redis://localhost:6379/0

# === Meilisearch（默认 localhost:7700）===
# TESTAGENT_MEILISEARCH_URL=http://localhost:7700
# TESTAGENT_MEILISEARCH_API_KEY=testagent-dev-master-key

# === Embedding 配置 ===
# local: 使用本地模型（BAAI/bge-large-zh-v1.5，首次需下载约 1.3GB）
# openai: 使用 OpenAI API
TESTAGENT_EMBEDDING_MODE=local

# === 调试模式 ===
TESTAGENT_DEBUG=True
```

### 3.2 环境变量说明

所有配置项以 `TESTAGENT_` 为前缀，完整列表：

| 环境变量 | 说明 | 默认值 | 必填 |
|---------|------|--------|------|
| `TESTAGENT_OPENAI_API_KEY` | OpenAI API Key | — | 使用 OpenAI 时必填 |
| `TESTAGENT_LLM_PROVIDER` | LLM 类型 (`openai`/`local`) | `openai` | 否 |
| `TESTAGENT_OPENAI_MODEL` | OpenAI 模型名 | `gpt-4o` | 否 |
| `TESTAGENT_LOCAL_MODEL_URL` | 本地模型地址（Ollama） | `http://localhost:11434` | 本地模式时 |
| `TESTAGENT_DATABASE_URL` | 数据库连接串 | `sqlite+aiosqlite:///./testagent.db` | 否 |
| `TESTAGENT_REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` | 否 |
| `TESTAGENT_CHROMA_PERSIST_DIR` | ChromaDB 持久化目录 | `./chroma_data` | 否 |
| `TESTAGENT_MEILISEARCH_URL` | Meilisearch 地址 | `http://localhost:7700` | 否 |
| `TESTAGENT_MEILISEARCH_API_KEY` | Meilisearch API Key | `testagent-dev-master-key` | 否 |
| `TESTAGENT_EMBEDDING_MODE` | Embedding 模式 (`local`/`openai`) | `local` | 否 |
| `TESTAGENT_EMBEDDING_MODEL` | 本地 Embedding 模型 | `BAAI/bge-large-zh-v1.5` | 否 |
| `TESTAGENT_DEBUG` | 是否开启调试日志 | `False` | 否 |

---

## 4. 启动基础设施服务

TestAgent 依赖多个外部服务：**Redis**（任务队列）、**ChromaDB**（向量数据库）、**Meilisearch**（全文搜索引擎）。

### 4.1 使用 Docker Compose 一键启动

```bash
# 在项目根目录执行
cd D:\test-ai-agent\vibe-ai-agent
docker compose -f docker/docker-compose.dev.yml up -d
```

这将启动以下服务：

| 服务 | 端口 | 用途 |
|------|------|------|
| Redis | 6379 | 任务队列 Broker |
| ChromaDB | 8001 | 向量数据库（RAG 存储） |
| Meilisearch | 7700 | 全文搜索引擎 |
| etcd | 2379 | Milvus 元数据存储（V1.0） |
| MinIO | 9000 | Milvus 对象存储（V1.0） |
| Milvus | 19530 | 分布式向量数据库（V1.0） |

### 4.2 验证服务状态

```bash
docker compose -f docker/docker-compose.dev.yml ps
```

预期输出中所有服务状态均为 `healthy`（首次启动可能需要等待 30-60 秒）。

### 4.3 仅启动必需服务（节省资源）

如果内存有限，可以只启动 MVP 必需的服务：

```bash
# 只启动 Redis + ChromaDB + Meilisearch
docker run -d --name testagent-redis -p 6379:6379 redis:7-alpine
docker run -d --name testagent-chroma -p 8001:8000 chromadb/chroma:latest
docker run -d --name testagent-meili -p 7700:7700 getmeili/meilisearch:v1.9
```

---

## 5. 数据库初始化

### 5.1 运行数据库迁移

```bash
# 确保虚拟环境已激活
alembic upgrade head
```

### 5.2 查看迁移状态

```bash
# 查看当前数据库版本
alembic current

# 查看迁移历史
alembic history
```

### 5.3 数据库说明

- MVP 阶段使用 **SQLite**（零配置，文件存储在 `./testagent.db`）
- V1.0 将迁移到 **PostgreSQL**
- 迁移脚本位于 `testagent/db/alembic/versions/`

---

## 6. CLI 命令详解

### 6.1 命令总览

```bash
testagent --help
```

| 命令 | 说明 | 使用场景 |
|------|------|---------|
| `init` | 初始化测试项目 | 首次使用，创建项目结构 |
| `run` | 执行测试 Skill 或测试计划 | 日常测试执行 |
| `chat` | 启动交互式对话模式 | AI 对话式测试 |
| `ci` | CI/CD 非交互模式 | CI 管道集成 |
| `serve` | 启动 Gateway API 服务 | 启动 REST API + WebSocket |
| `skill list` | 列出已注册的 Skill | 查看可用测试技能 |
| `skill create` | 从模板创建新 Skill | 自定义测试技能 |
| `mcp add` | 注册 MCP Server | 接入新工具 |
| `mcp list` | 列出已配置的 MCP Server | 查看工具注册状态 |
| `mcp health` | 检查 MCP Server 健康状态 | 排错 |
| `rag-index` | 索引文档到 RAG 知识库 | 建立知识库 |
| `rag-query` | 查询 RAG 知识库 | 检索知识 |

### 6.2 命令详细用法

#### `testagent init` — 初始化测试项目

```bash
# 在当前目录下创建测试项目
testagent init my-test-project --type api

# 支持的项目类型
# --type api     : API 测试项目
# --type web     : Web 测试项目
# --type app     : App 测试项目
# --type web+api  : 组合类型

# 输出示例：
# Initialized test project 'my-test-project' at /path/to/my-test-project
#   Type: api
#   Config: /path/to/my-test-project/testagent.json
#   Plans:  /path/to/my-test-project/test-plans/
#   Config: /path/to/my-test-project/config/
```

#### `testagent run` — 执行测试（核心命令）

```bash
# 通过 Skill 名称执行
testagent run --skill api_smoke_test --env staging

# 通过测试计划文件执行
testagent run --plan ./test-plans/my-plan.json

# 指定目标 URL
testagent run --skill web_smoke_test --url https://myapp.com

# 指定环境和 URL 组合
testagent run --skill api_smoke_test --env production --url https://api.myapp.com
```

**参数说明**:

| 参数 | 缩写 | 说明 | 默认值 |
|------|------|------|--------|
| `--skill` | `-s` | Skill 名称 | — |
| `--plan` | `-p` | 测试计划 JSON 文件路径 | — |
| `--env` | `-e` | 目标环境 | `dev` |
| `--url` | `-u` | 目标 URL（覆盖环境配置） | — |

> `--skill` 和 `--plan` 必须提供至少一个。

#### `testagent chat` — 交互式对话

```bash
# 启动交互模式
testagent chat
```

进入后：

```
TestAgent Chat — type 'exit' to quit, 'help' for commands.
----------------------------------------
You> 帮我对登录模块做一次冒烟测试
... (AI 回复)

You> 查看上次支付接口的测试结果
... (AI 回复)

You> exit
Goodbye!
```

内置命令：

| 命令 | 说明 |
|------|------|
| `exit` / `quit` | 退出 |
| `help` | 显示帮助 |
| `clear` | 清空对话历史 |

#### `testagent ci` — CI/CD 模式

```bash
# 基本运行
testagent ci api_smoke_test --env ci

# 失败时返回非零退出码（用于 CI 门禁）
testagent ci api_smoke_test --exit-code

# 输出 JUnit XML 报告
testagent ci api_smoke_test --exit-code --junit report.xml

# 设置全局超时（秒）
testagent ci api_smoke_test --timeout 300
```

#### `testagent serve` — 启动 Gateway 服务

```bash
# 默认启动（0.0.0.0:8000）
testagent serve

# 自定义主机和端口
testagent serve --host 127.0.0.1 --port 9000
```

启动后访问：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

---

## 7. 运行你的第一个测试

### 7.1 前置检查清单

在运行测试之前，请确保：

- [ ] 虚拟环境已激活（`.venv`）
- [ ] `.env` 文件已配置（至少配置了 LLM）
- [ ] 基础设施服务已启动（Redis + ChromaDB + Meilisearch）
- [ ] 数据库迁移已完成（`alembic upgrade head`）

### 7.2 运行 API 冒烟测试

这是最简单的测试，用来验证 API 核心端点是否可用：

```bash
# 执行 API 冒烟测试
testagent run --skill api_smoke_test --env staging
```

期望输出：

```
╭─ TestAgent Run ─╮
│ Skill: api_smoke_test  │
│ Target: staging        │
│ Timeout: 60s           │
╰───────────────────────╯

✓ [1/1] api_smoke_test  (completed)  2.3s

╭─ Summary ─╮
│ Passed    │        1 │
│ Total     │        1 │
│ Duration  │   2.3s  │
╰───────────╯
```

### 7.3 运行 Web 冒烟测试

```bash
testagent run --skill web_smoke_test --url https://example.com
```

### 7.4 使用测试计划文件

创建 `test-plans/my-plan.json`：

```json
{
  "name": "我的测试计划",
  "tasks": [
    {
      "name": "登录API测试",
      "type": "api_test",
      "endpoint": "/api/login",
      "method": "POST"
    },
    {
      "name": "首页加载测试",
      "type": "web_test",
      "url": "https://myapp.com"
    }
  ]
}
```

执行：

```bash
testagent run --plan ./test-plans/my-plan.json
```

---

## 8. 交互式聊天模式

### 8.1 启动聊天

```bash
testagent chat
```

### 8.2 使用示例

```
You> 帮我对支付模块做一次回归测试
Agent: 好的，我来分析支付模块的测试需求...

You> 上次发现的登录缺陷修复了吗？帮我验证
Agent: 让我查一下历史缺陷记录...

You> 当前有哪些可用的测试技能？
Agent: 让我列出已注册的 Skill...
```

### 8.3 技术说明

聊天模式使用 `OpenAIProvider`（或 `LocalProvider`）作为 LLM 后端，通过 `agent_loop` 实现 ReAct（Reasoning + Acting）循环。由于当前版本未注册 MCP 工具，聊天模式主要用于 AI 对话和测试咨询。

---

## 9. CI/CD 集成

### 9.1 GitHub Actions 集成

创建 `.github/workflows/testagent.yml`：

```yaml
name: TestAgent Smoke Test
on: [push, pull_request]

jobs:
  testagent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install TestAgent
        run: |
          pip install -e .
          pip install -e ".[dev]"

      - name: Start services
        run: |
          docker compose -f docker/docker-compose.dev.yml up -d redis chromadb meilisearch

      - name: Initialize database
        run: alembic upgrade head

      - name: Run Smoke Test
        env:
          TESTAGENT_OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: testagent ci api_smoke_test --exit-code --junit report.xml --env staging

      - name: Upload Report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: testagent-report
          path: report.xml
```

### 9.2 Jenkins 集成

在 Jenkins Pipeline 中：

```groovy
stage('TestAgent') {
    steps {
        sh '''
            pip install -e .
            docker compose -f docker/docker-compose.dev.yml up -d
            alembic upgrade head
            testagent ci api_smoke_test --exit-code --junit report.xml --env staging
        '''
    }
    post {
        always {
            junit 'report.xml'
        }
    }
}
```

---

## 10. Gateway API 服务

### 10.1 启动服务

```bash
testagent serve
```

### 10.2 API 端点一览

启动后可访问 `http://localhost:8000/docs` 查看 Swagger 文档。

#### Sessions（测试会话）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/sessions` | 创建测试会话 |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{id}` | 获取会话详情 |
| POST | `/api/v1/sessions/{id}/cancel` | 取消会话 |
| GET | `/api/v1/sessions/{id}/plan` | 获取会话的测试计划 |
| GET | `/api/v1/sessions/{id}/results` | 获取会话的测试结果 |

#### Skills

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/skills` | 列出所有 Skill |
| GET | `/api/v1/skills/{name}` | 获取 Skill 详情 |

#### MCP

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/mcp/servers` | 列出 MCP Server |
| POST | `/api/v1/mcp/servers` | 注册 MCP Server |
| GET | `/api/v1/mcp/servers/{name}/health` | 检查 MCP Server 健康 |

#### RAG

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/rag/index` | 触发文档索引 |
| POST | `/api/v1/rag/query` | 查询知识库 |
| GET | `/api/v1/rag` | 列出 RAG Collections |

#### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/api/v1/dashboard/stats` | 仪表盘统计 |
| GET | `/api/v1/resources` | 系统资源监控 |
| GET | `/api/v1/quality/trends` | 质量趋势 |
| GET | `/api/v1/quality/summary` | 质量概览 |

### 10.3 WebSocket 接口

```bash
# 全局事件推送
ws://localhost:8000/api/v1/ws

# 指定会话事件推送
ws://localhost:8000/api/v1/ws/sessions/{session_id}
```

### 10.4 认证（可选）

设置环境变量启用 API Token 认证：

```bash
TESTAGENT_API_TOKEN=your-secret-token
```

然后在请求头中添加：

```
Authorization: Bearer your-secret-token
```

---

## 11. Skill 管理

### 11.1 Skill 是什么

Skill 是 TestAgent 的"测试技能"——一个 Markdown 文件，定义了测试的目标、步骤、断言策略和失败处理。Agent 读取 Skill 后按照指示执行测试。

### 11.2 查看已有 Skill

```bash
# 列出所有 Skill
testagent skill list
```

预期输出：

```
Name                           Version      Description
--------------------------------------------------------------------------------
api_smoke_test                 1.0.0        API 冒烟测试技能
api_regression_test            1.1.0        API 回归测试技能
web_smoke_test                 1.0.0        Web 页面冒烟测试
app_smoke_test                 1.0.0        App 核心流程冒烟测试
```

### 11.3 预置 Skill 清单

| Skill 名称 | 描述 | 适用类型 | 前置 MCP |
|-----------|------|---------|---------|
| `api_smoke_test` | API 冒烟测试 | API | api_server, database_server |
| `api_regression_test` | API 回归测试 | API | api_server, database_server |
| `web_smoke_test` | Web 页面冒烟 | Web | playwright_server |
| `app_smoke_test` | App 冒烟测试 | App | appium_server |

### 11.4 创建自定义 Skill

```bash
# 从 API 测试模板创建
testagent skill create --template api_test --output ./skills/my_custom_test/

# 从 Web 测试模板创建
testagent skill create --template web_test --output ./skills/my_web_test/
```

### 11.5 Skill 文件格式

Skill 文件是 `skills/<skill_name>/SKILL.md`，格式如下：

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

1. 第一步：...
2. 第二步：...
3. 第三步：...

## 断言策略

- 断言规则 1
- 断言规则 2

## 失败处理

- 失败处理方式
```

**YAML Front Matter 字段说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Skill 名称，唯一标识 |
| `version` | 是 | 版本号 |
| `description` | 是 | 简短描述 |
| `trigger` | 是 | 触发关键词（正则表达式） |
| `required_mcp_servers` | 否 | 需要哪些 MCP Server |
| `required_rag_collections` | 否 | 需要哪些 RAG Collection |

---

## 12. MCP Server 管理

### 12.1 MCP Server 是什么

MCP (Model Context Protocol) Server 是 Agent 调用外部工具的标准接口。每个 MCP Server 提供一组工具（Tools）供 Agent 调用。

### 12.2 预置 MCP Server

| Server | 工具 | 说明 |
|--------|------|------|
| `api_server` | `api_request`, `api_validate_schema`, `api_compare_response` | HTTP API 测试 |
| `playwright_server` | `browser_navigate`, `browser_click`, `browser_screenshot` | Web 浏览器自动化 |
| `jira_server` | `jira_create_issue`, `jira_search_issues`, `jira_update_issue` | 缺陷管理 |
| `git_server` | `git_diff`, `git_blame`, `git_log` | 代码分析 |
| `database_server` | `db_query`, `db_seed`, `db_cleanup` | 数据库验证 |

### 12.3 配置 MCP Server

复制模板文件：

```bash
cp configs/mcp.json.template configs/mcp.json
```

编辑 `configs/mcp.json`，根据环境修改配置（如 API 地址、认证信息）。

### 12.4 注册 MCP Server

```bash
# 通过配置文件注册
testagent mcp add api-server --config ./configs/mcp.json

# 直接注册（基本方式）
testagent mcp add api-server --command python --args '{"-m": "testagent.mcp_servers.api_server"}'
```

### 12.5 管理 MCP Server

```bash
# 列出所有已注册的 Server
testagent mcp list

# 检查健康状态
testagent mcp health

# 检查特定 Server
testagent mcp health api_server
```

---

## 13. RAG 知识库管理

### 13.1 RAG 是什么

RAG (Retrieval-Augmented Generation) 是 TestAgent 的知识库系统。它让 Agent 可以检索历史文档、缺陷记录和测试报告，"站在历史的肩膀上"做决策。

### 13.2 预置 RAG Collections

| Collection | 内容 | 使用方 |
|-----------|------|--------|
| `req_docs` | 产品需求文档 | Planner |
| `api_docs` | OpenAPI/Swagger 规范 | Planner, Executor |
| `defect_history` | 历史缺陷记录 | Planner, Analyzer |
| `test_reports` | 测试报告 | Analyzer |
| `locator_library` | UI 元素定位器 | Executor |
| `failure_patterns` | 失败模式库 | Analyzer |

### 13.3 索引文档到知识库

```bash
# 索引 API 文档
testagent rag-index ./docs/api --collection api_docs

# 索引需求文档
testagent rag-index ./docs/requirements --collection req_docs

# 索引全部文档（对应目录需事先创建）
testagent rag-index ./docs --collection req_docs
```

### 13.4 查询知识库

```bash
# 查询 API 文档
testagent rag-query "登录接口的请求参数" --collection api_docs

# 查询历史缺陷
testagent rag-query "支付模块的 Bug" --collection defect_history --top-k 10
```

### 13.5 配置 RAG

复制模板文件：

```bash
cp configs/rag_config.yaml.template configs/rag_config.yaml
```

编辑 `configs/rag_config.yaml` 配置 Embedding 模型、分块策略和 Collection 设置。

---

## 14. 三层 Agent 架构说明

### 14.1 Agent 角色

TestAgent 的核心是三个 AI Agent 的协作：

```
                    ┌─────────────────┐
                    │  Planner Agent   │   128K 上下文窗口
                    │  (测试策略生成)   │   负责需求解析、策略制定
                    └────────┬────────┘
                             │ 测试计划
                             ▼
                    ┌─────────────────┐
                    │  Executor Agent  │   32K 上下文窗口
                    │  (测试执行)       │   负责执行测试、收集结果
                    └────────┬────────┘
                             │ 测试结果
                             ▼
                    ┌─────────────────┐
                    │  Analyzer Agent  │   64K 上下文窗口
                    │  (结果分析)       │   负责失败分类、缺陷归档
                    └─────────────────┘
```

| Agent | 职责 | 上下文窗口 | 工具集 |
|-------|------|-----------|--------|
| **Planner** | 需求解析、策略生成、任务编排 | 128K | Jira, Git, Skills |
| **Executor** | 测试执行、自愈修复、结果收集 | 32K | Playwright, API, Harness |
| **Analyzer** | 失败分类、根因分析、缺陷归档 | 64K | Jira, Git, RAG |

### 14.2 执行流程

```
用户输入
    │
    ▼
Planner Agent
    ├── 检索 RAG 知识库（需求文档、历史缺陷）
    ├── 生成测试计划（任务分解 + 排序）
    └── 提交测试计划给 Gateway
    │
    ▼
Gateway 调度
    ├── 分发任务给 Executor Agent
    └── 管理 Session 状态
    │
    ▼
Executor Agent
    ├── 在 Harness 沙箱中执行测试
    ├── 调用 MCP 工具（API 请求/浏览器操作）
    ├── 失败时自愈修复
    └── 收集测试结果
    │
    ▼
Analyzer Agent
    ├── 检索 RAG 知识库（失败模式）
    ├── 智能失败分类（Bug/Flaky/环境/配置）
    ├── 根因分析（Git blame + 代码关联）
    └── 缺陷归档（Jira/GitHub Issues）
    │
    ▼
测试报告 → 用户
```

---

## 15. Harness 沙箱执行引擎

### 15.1 沙箱隔离级别

Harness 提供三级沙箱隔离：

| 级别 | 技术 | 资源配额 | 适用场景 |
|------|------|---------|---------|
| **Docker** | Docker Container | API: 1CPU/512MB, Web: 2CPU/2GB | API 和 Web 测试（MVP） |
| **MicroVM** | Firecracker | 4CPU/4GB | App 测试（V1.0） |
| **本地进程** | 直接子进程 | 无限制 | 本地开发调试 |

### 15.2 沙箱安全特性

- Docker 容器使用 `--security-opt=no-new-privileges` 防止提权
- 默认只读文件系统（Read-only rootfs）
- 网络隔离（默认无网络，仅白名单地址可访问）
- 资源限制（CPU、内存硬限制）
- 超时强制终止（API: 60s, Web: 120s）
- 数据用后即焚（沙箱销毁时自动清理）

### 15.3 隔离级别切换

```bash
# 使用 Docker 隔离（默认，生产推荐）
export TESTAGENT_DEFAULT_ISOLATION_LEVEL=docker

# 使用本地进程隔离（仅开发调试！）
export TESTAGENT_DEFAULT_ISOLATION_LEVEL=local
export TESTAGENT_ALLOW_LOCAL=1    # 本地模式需要额外授权
```

> **安全警告**：本地进程模式无任何隔离，仅用于开发调试，禁止用于生产或 CI 环境。

### 15.4 构建 Docker 沙箱镜像

```bash
# Harness 基础镜像
docker build -f docker/Dockerfile.harness -t testagent/harness:latest .

# API Runner 镜像
docker build -f docker/Dockerfile.api_runner -t testagent/api-runner:latest .

# Web Runner 镜像（含 Chromium 浏览器）
docker build -f docker/Dockerfile.web_runner -t testagent/web-runner:latest .
```

---

## 16. 项目目录结构

```
vibe-ai-agent/
├── testagent/                    # 主 Python 包
│   ├── __init__.py               # 版本号
│   ├── __main__.py               # 入口点
│   ├── cli/                      # CLI 交互层
│   │   ├── main.py               # Typer 命令定义（init/run/chat/ci/serve）
│   │   ├── skill_cmd.py          # testagent skill 命令组
│   │   ├── mcp_cmd.py            # testagent mcp 命令组
│   │   ├── rag_cmd.py            # testagent rag-index / rag-query
│   │   ├── output.py             # Rich 格式化输出
│   │   └── junit.py              # JUnit XML 报告生成
│   ├── agent/                    # Agent Runtime
│   │   ├── loop.py               # 核心 ReAct Loop
│   │   ├── context.py            # 上下文组装
│   │   ├── planner.py            # Planner Agent
│   │   ├── executor.py           # Executor Agent
│   │   ├── analyzer.py           # Analyzer Agent
│   │   ├── protocol.py           # Agent 通信协议
│   │   ├── tools.py              # 工具注册
│   │   └── todo.py               # 任务追踪
│   ├── gateway/                  # Gateway 调度层
│   │   ├── app.py                # FastAPI 应用
│   │   ├── router.py             # RESTful 路由
│   │   ├── session.py            # Session 管理
│   │   ├── mcp_registry.py       # MCP 注册发现
│   │   ├── mcp_router.py         # MCP 工具路由
│   │   └── websocket.py          # WebSocket 管理
│   ├── rag/                      # RAG 知识库
│   │   ├── pipeline.py           # RAG 主流水线
│   │   ├── ingestion.py          # 文档摄入
│   │   ├── embedding.py          # Embedding 服务
│   │   ├── vector_store.py       # 向量索引（ChromaDB）
│   │   ├── fulltext.py           # 全文索引（Meilisearch）
│   │   ├── fusion.py             # RRF 融合排序
│   │   ├── collections.py        # Collection 配置
│   │   └── factories.py          # Pipeline 工厂
│   ├── harness/                  # 执行引擎
│   │   ├── orchestrator.py       # 任务编排
│   │   ├── sandbox.py            # 沙箱协议
│   │   ├── sandbox_factory.py    # 沙箱工厂
│   │   ├── docker_sandbox.py     # Docker 沙箱
│   │   ├── local_runner.py       # 本地进程
│   │   ├── microvm_sandbox.py    # MicroVM（V1.0）
│   │   └── runners/              # Runner 插件
│   ├── skills/                   # Skill 引擎
│   │   ├── loader.py             # Skill 加载
│   │   ├── parser.py             # 解析
│   │   ├── registry.py           # 注册表
│   │   └── executor.py           # 执行器
│   ├── llm/                      # LLM 抽象层
│   │   ├── base.py               # 接口 + 限流
│   │   ├── openai_provider.py    # OpenAI Provider
│   │   └── local_provider.py     # 本地模型 Provider
│   ├── models/                   # 数据模型
│   │   ├── base.py               # 基类
│   │   ├── session.py            # TestSession
│   │   ├── plan.py               # TestPlan / TestTask
│   │   ├── result.py             # TestResult
│   │   ├── defect.py             # Defect
│   │   └── mcp_config.py         # MCPConfig
│   ├── db/                       # 数据库
│   │   ├── engine.py             # Engine 管理
│   │   ├── repository.py         # Repository 模式
│   │   └── alembic/              # 迁移脚本
│   ├── config/                   # 配置
│   │   └── settings.py           # Pydantic Settings
│   └── common/                   # 公共工具
│       ├── logging.py            # 结构化日志
│       ├── errors.py             # 异常体系
│       └── security.py           # 安全工具
├── skills/                       # Skill 定义文件
│   ├── api_smoke_test/SKILL.md
│   ├── api_regression_test/SKILL.md
│   ├── web_smoke_test/SKILL.md
│   └── app_smoke_test/SKILL.md
├── configs/                      # 配置模板
│   ├── mcp.json.template
│   └── rag_config.yaml.template
├── docker/                       # Docker 配置
│   ├── docker-compose.dev.yml
│   └── Dockerfile.*
├── tests/                        # 测试
│   ├── unit/                     # 单元测试
│   ├── integration/              # 集成测试
│   └── e2e/                      # 端到端测试
├── dashboard/                    # Web 仪表盘（V1.0）
├── pyproject.toml                # 项目配置
└── alembic.ini                   # 迁移配置
```

---

## 17. 常见问题与排错

### Q1: `testagent` 命令找不到

**原因**：虚拟环境未激活或未安装。

**解决**：
```bash
# 激活虚拟环境
# Windows:
.\.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

# 重新安装
pip install -e .
```

### Q2: OpenAI API key not found

**原因**：未配置 API Key。

**解决**：
```bash
# 方法 1：在 .env 文件中添加
echo "TESTAGENT_OPENAI_API_KEY=sk-your-key" >> .env

# 方法 2：使用本地模型
export TESTAGENT_LLM_PROVIDER=local
# 先安装 Ollama: https://ollama.com
ollama pull qwen2.5
```

### Q3: Docker Compose 启动失败

**原因**：端口被占用或 Docker 未运行。

**解决**：
```bash
# 检查 Docker 是否运行
docker ps

# 检查端口占用
netstat -ano | findstr :6379  # Windows
lsof -i :6379                 # macOS/Linux

# 修改端口映射（编辑 docker-compose.dev.yml）
# 将 "6379:6379" 改为 "6380:6379"
# 同时更新 .env 中 TESTAGENT_REDIS_URL=redis://localhost:6380/0
```

### Q4: 数据库迁移失败

**原因**：数据库文件被锁定或权限问题。

**解决**：
```bash
# 检查是否有写权限
ls -la testagent.db

# 重置数据库（会丢失数据！）
rm testagent.db
alembic upgrade head
```

### Q5: Embedding 模型下载慢

**原因**：首次使用 `BAAI/bge-large-zh-v1.5` 需从 HuggingFace 下载约 1.3GB。

**解决**：
```bash
# 选项 A：切换到 OpenAI Embedding（需 API Key）
export TESTAGENT_EMBEDDING_MODE=openai

# 选项 B：设置 HuggingFace 镜像（国内用户）
export HF_ENDPOINT=https://hf-mirror.com
```

### Q6: `testagent run` 报错 "Session execution module not available"

**原因**：模块导入失败，通常是依赖未安装完整。

**解决**：
```bash
pip install -e .
```

### Q7: Celery Worker 如何工作

**解决**：在另一个终端中启动：
```bash
celery -A testagent.gateway.celery_app worker --loglevel=info --concurrency=4
```

需要先确保 Redis 服务已运行。

### Q8: 如何查看详细日志

```bash
export TESTAGENT_DEBUG=True
testagent run --skill api_smoke_test --env dev
```

日志会输出到 stderr，包含 Agent 每轮的详细信息、工具调用记录等。

### Q9: MCP Server 连接失败

**解决**：
```bash
# 检查 MCP Server 配置
testagent mcp list

# 检查健康状态
testagent mcp health <server_name>

# 确保对应的 MCP Server 代码已实现且可导入
python -c "from testagent.mcp_servers.api_server import ..."
```

### Q10: Windows WSL2 下 Docker 沙箱不可用

**解决**：
1. 确保 Docker Desktop 已启用 WSL2 集成：
   Docker Desktop → Settings → Resources → WSL Integration → 勾选你的发行版
2. 或使用本地进程模式（仅开发）：
   ```bash
   export TESTAGENT_DEFAULT_ISOLATION_LEVEL=local
   export TESTAGENT_ALLOW_LOCAL=1
   ```

---

## 18. 开发相关命令

### 18.1 运行测试

```bash
# 运行所有单元测试
pytest tests/unit/ -v

# 运行特定测试文件
pytest tests/unit/test_cli.py -v

# 运行集成测试（需要 Docker 服务运行）
pytest tests/integration/ -v

# 运行 E2E 测试
pytest tests/e2e/ -v

# 带覆盖率报告
pytest --cov=testagent --cov-report=term-missing

# 运行特定标记的测试
pytest -m unit
pytest -m integration
pytest -m e2e
```

### 18.2 代码质量检查

```bash
# Lint 检查
ruff check .

# 自动修复
ruff check . --fix

# 格式化
ruff format .

# 类型检查
mypy testagent/ --strict
```

### 18.3 数据库迁移管理

```bash
# 生成新的迁移脚本
alembic revision --autogenerate -m "描述变更内容"

# 升级到最新版本
alembic upgrade head

# 回滚一个版本
alembic downgrade -1

# 查看当前版本
alembic current

# 查看历史
alembic history
```

### 18.4 构建 Docker 镜像

```bash
# Harness 沙箱基础镜像
docker build -f docker/Dockerfile.harness -t testagent/harness:latest .

# API Runner 镜像
docker build -f docker/Dockerfile.api_runner -t testagent/api-runner:latest .

# Web Runner 镜像（含 Chromium）
docker build -f docker/Dockerfile.web_runner -t testagent/web-runner:latest .
```

---

## 附录：快速启动检查表

当你在新环境部署时，按以下顺序操作：

- [ ] 1. `cd vibe-ai-agent`
- [ ] 2. `python -m venv .venv && source .venv/bin/activate`
- [ ] 3. `pip install -e ".[dev]"`
- [ ] 4. 配置 `.env` 文件（至少 LLM 配置）
- [ ] 5. `docker compose -f docker/docker-compose.dev.yml up -d`
- [ ] 6. `alembic upgrade head`
- [ ] 7. `testagent --help`（验证安装）
- [ ] 8. `testagent skill list`（查看可用技能）
- [ ] 9. `testagent run --skill api_smoke_test --env dev`（运行测试）
