<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://img.shields.io/badge/🍅_TestAgent-v0.1.0-ff6b35?style=for-the-badge&logo=python&logoColor=white&labelColor=2d333b">
    <img alt="TestAgent" src="https://img.shields.io/badge/🍅_TestAgent-v0.1.0-ff6b35?style=for-the-badge&logo=python&logoColor=white&labelColor=ffffff">
  </picture>
</p>

<p align="center">
  <strong>AI 测试智能体平台 · AI-Powered Testing Agent Platform</strong>
</p>

<p align="center">
  让 AI 替你写脚本 · 跑测试 · 提缺陷
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow" alt="License: MIT"></a>
  <a href="https://github.com/features/actions"><img src="https://img.shields.io/badge/CI-GitHub_Actions-2088FF?logo=githubactions&logoColor=white" alt="CI"></a>
  <a href="https://fastapi.tiangolo.com/"><img src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white" alt="FastAPI"></a>
  <a href="https://reactjs.org/"><img src="https://img.shields.io/badge/Dashboard-React-61DAFB?logo=react&logoColor=white" alt="Dashboard"></a>
  <br>
  <a href="#"><img src="https://img.shields.io/badge/Android-34A853?logo=android&logoColor=white" alt="Android"></a>
  <a href="#"><img src="https://img.shields.io/badge/iOS-000000?logo=apple&logoColor=white" alt="iOS"></a>
  <a href="#"><img src="https://img.shields.io/badge/Web-4285F4?logo=googlechrome&logoColor=white" alt="Web"></a>
  <a href="#"><img src="https://img.shields.io/badge/API-FF6B6B?logo=swagger&logoColor=white" alt="API"></a>
  <br>
  <a href="https://img.shields.io/badge/coverage-70%25-2ea44f?style=flat"><img src="https://img.shields.io/badge/coverage-70%25-2ea44f" alt="coverage"></a>
  <a href="https://img.shields.io/badge/code_style-ruff-000000"><img src="https://img.shields.io/badge/code_style-ruff-000000" alt="Ruff"></a>
  <a href="https://img.shields.io/badge/types-mypy_strict-1B6AC6"><img src="https://img.shields.io/badge/types-mypy_strict-1B6AC6" alt="mypy strict"></a>
</p>

---

## 📋 目录

- [项目简介](#-项目简介)
- [核心特性](#-核心特性)
- [系统架构](#-系统架构)
- [快速开始](#-快速开始)
- [使用指南](#-使用指南)
- [预置 Skill](#-预置-skill)
- [三层 Agent 协作](#-三层-agent-协作)
- [CI/CD 集成](#-cicd-集成)
- [Dashboard](#-dashboard)
- [配置参考](#-配置参考)
- [项目结构](#-项目结构)
- [参与贡献](#-参与贡献)
- [项目负责人](#-项目负责人)
- [开源协议](#-开源协议)

---

## 🎯 项目简介

**TestAgent** 是一款面向 **App（Android/iOS）**、**Web** 和 **API** 全平台的 **AI 测试智能体平台**。

它采用 **Planner → Executor → Analyzer** 三层 Agent 协作架构，结合 **MCP 工具调用**、**RAG 知识检索** 与 **Harness 沙箱执行**，实现从测试规划到缺陷归档的 **全生命周期自动化**。

> 🍅 项目代号 **TomatoPilot（番茄领航）**—— 像自动驾驶一样，让测试自动巡航。

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| 🤖 **AI 驱动测试** | 自然语言描述需求，AI 自动生成测试计划、执行用例、分析结果 |
| 📱 **全平台覆盖** | App (Android / iOS) · Web · API — 一套平台覆盖所有端 |
| 🧠 **三层 Agent 架构** | Planner（规划）→ Executor（执行）→ Analyzer（分析）流水线协作 |
| 📚 **RAG 知识库** | 双路召回（向量 + BM25）+ RRF 融合，测试越用越聪明 |
| 🔒 **沙箱隔离** | Docker / MicroVM 隔离执行，安全可靠 |
| 🛠️ **MCP 协议** | 标准化工具调用接口，轻松扩展第三方工具 |
| 📊 **可视化仪表盘** | React + Ant Design Web Dashboard，实时监控测试进度 |
| 🔄 **CI/CD 集成** | 原生支持 GitHub Actions，JUnit XML 报告输出 |
| 🧩 **可编排 Skill** | Markdown 定义测试技能，组合、编排、复用 |
| 🗂️ **数据持久化** | SQLAlchemy + Alembic，支持 SQLite / PostgreSQL |

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                           🧑‍💻 用户入口                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  CLI (testagent)          │      Dashboard (React)      │   │
│  └───────────────────────────┴─────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     🌐  TestGateway (FastAPI)                    │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────┐   │
│  │ REST API     │  │  WebSocket   │  │  Celery Task Queue   │   │
│  └─────────────┘  └──────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    🧠 Agent 三层协作                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Planner (128K ctx)                                      │   │
│  │  需求解析 → 策略生成 → 任务编排                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                    │
│                              ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Executor (32K ctx)                                      │   │
│  │  沙箱执行 → 自愈修复 → 结果收集                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                    │
│                              ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Analyzer (64K ctx)                                      │   │
│  │  失败分类 → 根因分析 → 缺陷归档                            │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  🛡️ Harness 沙箱  │ │  📚 RAG 知识库   │ │  🔧 MCP Servers │
│                  │ │                  │ │                  │
│ · Docker         │ │ · ChromaDB      │ │ · API Server    │
│ · MicroVM (V1.0) │ │ · Meilisearch   │ │ · Playwright    │
│ · Local (Dev)    │ │ · Embedding     │ │ · Jira          │
│                  │ │ · BM25 Fulltext │ │ · Git           │
│                  │ │                  │ │ · Database      │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

### Agent 职责

| Agent | 上下文窗口 | 核心职责 | 工具集 |
|-------|-----------|---------|--------|
| **Planner** | 128K | 需求解析、策略生成、任务编排 | Jira, Git, Skill 策略类 |
| **Executor** | 32K | 沙箱执行、自愈修复、结果收集 | Playwright, API, Harness Runner |
| **Analyzer** | 64K | 失败分类、根因分析、缺陷归档 | Jira, Git, Skill 分析类 |

---

## ⚡ 快速开始

### 环境要求

| 项目 | 最低要求 | 推荐配置 |
|------|---------|---------|
| 🖥️ **操作系统** | macOS 12+ / Ubuntu 22.04+ / Windows 11 (WSL2) | 同左 |
| 🐍 **Python** | 3.12+ | 3.12 或 3.13 |
| 🐳 **Docker** | Docker Desktop 4.x（沙箱隔离） | Docker Desktop 4.x |
| 📦 **Redis** | 7.x | 7.x |
| 💾 **内存** | 8 GB | 16 GB |
| 💿 **磁盘** | 20 GB SSD | 50 GB SSD |

### 安装步骤

#### 1️⃣ 克隆仓库

```bash
git clone https://github.com/<your-org>/vibe-ai-agent.git
cd vibe-ai-agent
```

#### 2️⃣ 创建虚拟环境

```bash
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\Activate.ps1     # Windows PowerShell
```

#### 3️⃣ 安装依赖

```bash
# 运行时依赖
pip install -e .

# 开发依赖（含测试、Lint、类型检查）
pip install -e ".[dev]"
```

#### 4️⃣ 启动基础设施

```bash
docker compose -f docker/docker-compose.dev.yml up -d
```

一键启动：**Redis**（任务队列） + **ChromaDB**（向量数据库） + **Meilisearch**（全文搜索引擎）

验证服务状态：

```bash
docker compose -f docker/docker-compose.dev.yml ps
```

#### 5️⃣ 初始化数据库

```bash
alembic upgrade head
```

#### 6️⃣ 配置环境变量

创建 `.env` 文件：

```bash
# LLM 配置（必填）
TESTAGENT_OPENAI_API_KEY=sk-your-openai-api-key

# 或使用本地模型（无需 API Key）
# TESTAGENT_LLM_PROVIDER=local
# TESTAGENT_LOCAL_MODEL_URL=http://localhost:11434

# 数据库（默认 SQLite，无需修改）
# TESTAGENT_DATABASE_URL=sqlite+aiosqlite:///./testagent.db
```

#### 7️⃣ 验证安装

```bash
testagent --help
```

看到帮助信息即表示安装成功 ✅

---

## 🚀 使用指南

### CLI 命令概览

| 命令 | 功能 |
|------|------|
| `testagent init` | 🆕 初始化新的测试项目 |
| `testagent run` | ▶️ 执行测试 Skill 或测试计划 |
| `testagent chat` | 💬 交互式自然语言测试模式 |
| `testagent ci` | 🔄 CI/CD 非交互模式执行 |
| `testagent serve` | 🌐 启动 FastAPI Gateway 服务 |
| `testagent skill list` | 📋 列出已注册的 Skill |
| `testagent skill create` | ✏️ 从模板创建新 Skill |
| `testagent mcp add` | 🔌 注册 MCP Server |
| `testagent mcp list` | 📋 列出已配置的 MCP Server |
| `testagent mcp health` | ❤️ 检查 MCP Server 健康状态 |
| `testagent rag-index` | 📥 索引文档到 RAG 知识库 |
| `testagent rag-query` | 🔍 查询 RAG 知识库 |
| `testagent config` | ⚙️ 查看/配置 API 设置 |

### 快速示例

#### 🔹 运行 API 冒烟测试

```bash
testagent run --skill api_smoke_test --env staging
```

#### 🔹 运行 Web 冒烟测试

```bash
testagent run --skill web_smoke_test --url https://staging.myapp.com
```

#### 🔹 运行 App 测试

```bash
testagent run --skill app_smoke_test --platform android
```

#### 🔹 自然语言交互模式

```bash
testagent chat
```

进入交互模式后，直接输入自然语言：

```
You> 帮我对登录模块做一次冒烟测试
You> 上次支付接口的缺陷修复了吗？帮我回归验证
```

#### 🔹 使用测试计划文件

```bash
testagent run --plan ./test-plans/my-plan.json
```

---

## 🧩 预置 Skill

TestAgent 提供开箱即用的测试 Skill，采用 **YAML Front Matter + Markdown Body** 格式定义，存放在 `skills/` 目录：

| Skill | 说明 | 适用平台 |
|-------|------|---------|
| `api_smoke_test` | 🧪 API 冒烟测试 — 覆盖核心 Endpoint 正向验证 | API |
| `api_regression_test` | 🔄 API 回归测试 — 覆盖边界值和异常值场景 | API |
| `web_smoke_test` | 🌐 Web 冒烟测试 — 验证核心流程可用性 | Web |
| `app_smoke_test` | 📱 App 冒烟测试 — 覆盖 App 核心流程 | App |
| `web_visual_test` | 👁️ Web 视觉对比测试 | Web |
| `full_regression_test` | 📊 全量回归测试 | All |

> 你可以通过 `testagent skill create --template <name>` 从模板创建自定义 Skill。

---

## 🗂️ 三层 Agent 协作

```
用户输入 ──▶ TestGateway ──▶ Planner ──▶ Executor ──▶ Analyzer ──▶ 测试报告
                │              │             │              │
                │          📋 生成计划    🛡️ 执行测试    🧐 分析失败
                │          📖 查询 RAG   🔧 自愈修复    🐛 归档缺陷
                ▼              ▼             ▼              ▼
           WebSocket      Jira/Git      Playwright/API    Jira/Git
```

---

## 🔄 CI/CD 集成

TestAgent 原生支持 GitHub Actions，通过 `testagent ci` 命令在 CI 管道中执行测试：

```yaml
name: TestAgent Smoke Test
on: [push, pull_request]
jobs:
  testagent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install -e ".[dev]"
      - name: Run Smoke Test
        run: testagent ci api_smoke_test --exit-code --junit report.xml --env staging
      - name: Upload Report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: testagent-report
          path: report.xml
```

> 项目内置的 CI 工作流参见 [`.github/workflows/testagent.yml`](.github/workflows/testagent.yml)，覆盖单元测试、集成测试、类型检查和 Lint。

---

## 🖥️ Dashboard

TestAgent 提供基于 **React 19 + TypeScript + Ant Design 5 + Vite** 的可视化仪表盘：

```bash
cd dashboard
npm install
npm run dev
```

主要功能：
- 📊 测试执行实时监控（WebSocket）
- 📋 测试计划管理与查看
- 🐛 缺陷追踪看板
- 📈 测试覆盖率统计
- 🎯 Skill 触发与编排管理

---

## ⚙️ 配置参考

所有配置通过 **环境变量** 注入，统一前缀为 `TESTAGENT_`，支持 `.env` 文件。

### 核心配置

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `TESTAGENT_LLM_PROVIDER` | LLM 提供者 | `openai` |
| `TESTAGENT_OPENAI_API_KEY` | OpenAI API Key | — |
| `TESTAGENT_OPENAI_MODEL` | OpenAI 模型名 | `gpt-4o` |
| `TESTAGENT_LOCAL_MODEL_URL` | 本地模型 URL | `http://localhost:11434` |
| `TESTAGENT_DATABASE_URL` | 数据库连接串 | `sqlite+aiosqlite:///./testagent.db` |
| `TESTAGENT_REDIS_URL` | Redis 连接串 | `redis://localhost:6379/0` |
| `TESTAGENT_EMBEDDING_MODE` | Embedding 模式 (`local` / `openai`) | `local` |
| `TESTAGENT_DEBUG` | 调试模式 | `False` |
| `TESTAGENT_AGENT_MAX_ROUNDS` | Agent Loop 最大轮次 | `50` |
| `TESTAGENT_DATA_RETENTION_DAYS` | 数据保留天数 | `90` |

> 完整配置项参见 [USAGE_GUIDE.md](USAGE_GUIDE.md)。

### 预置 MCP Server

| Server | 说明 | 关键工具 |
|--------|------|---------|
| `api_server` | 🧪 API 测试执行 | `api_request`, `api_validate_schema` |
| `playwright_server` | 🌐 Web UI 测试 | `browser_navigate`, `browser_click`, `browser_screenshot` |
| `jira_server` | 🐛 缺陷管理 | `jira_create_issue`, `jira_search_issues` |
| `git_server` | 📄 代码分析 | `git_diff`, `git_blame`, `git_log` |
| `database_server` | 🗄️ 数据库验证 | `db_query`, `db_seed`, `db_cleanup` |

### 预置 RAG Collection

| Collection | 说明 | 访问角色 |
|-----------|------|---------|
| `req_docs` | 📋 产品需求文档 | Planner |
| `api_docs` | 📖 OpenAPI/Swagger 规范 | Planner, Executor |
| `defect_history` | 🐞 历史缺陷 | Planner, Analyzer |
| `test_reports` | 📊 历史测试报告 | Analyzer |
| `locator_library` | 🔍 UI 定位器库 | Executor |
| `failure_patterns` | ❌ 失败模式库 | Analyzer |

---

## 📁 项目结构

```
vibe-ai-agent/
├── testagent/                          # 🐍 主 Python 包
│   ├── agent/                          #   Agent Runtime（Planner/Executor/Analyzer）
│   ├── cli/                            #   CLI 交互层（Typer + Rich）
│   ├── gateway/                        #   FastAPI Gateway + WebSocket + Celery
│   ├── harness/                        #   沙箱执行引擎（Docker / MicroVM / Local）
│   ├── skills/                         #   Skill 引擎（加载/解析/校验/编排）
│   ├── rag/                            #   RAG Pipeline（摄入/向量/全文/融合）
│   ├── mcp_servers/                    #   MCP Server 基类与实现
│   ├── llm/                            #   LLM Provider 抽象层（OpenAI / Local）
│   ├── models/                         #   SQLAlchemy 数据模型
│   ├── db/                             #   数据库访问层 + Alembic 迁移
│   ├── config/                         #   Pydantic Settings 配置管理
│   ├── plan/                           #   测试计划生成
│   ├── exploration/                    #   探索性测试引擎
│   ├── platform/                       #   平台抽象层（Android / iOS）
│   ├── memory/                         #   Agent 记忆/模式存储
│   ├── rule_engine/                    #   规则引擎
│   ├── eval/                           #   评估模块
│   └── judge/                          #   Judge 智能体
├── dashboard/                          # ⚛️ React + TypeScript Web 仪表盘
├── skills/                             # 📝 Skill Markdown 定义文件
├── docker/                             # 🐳 Dockerfile + Docker Compose
├── configs/                            # ⚙️ 配置模板
├── tests/                              # 🧪 测试套件（unit / integration / e2e）
├── docs/                               # 📚 文档
├── scripts/                            # 🔧 实用脚本
├── .github/workflows/                  # 🔄 GitHub Actions CI/CD
├── pyproject.toml                      # 项目元数据 + 依赖 + 工具配置
└── USAGE_GUIDE.md                      # 详细使用指南
```

---

## 🤝 参与贡献

我们欢迎所有形式的贡献！无论是新功能、Bug 修复、文档改进还是想法建议。

### 贡献方式

- 🐛 **提交 Bug** — 通过 [Issues](https://github.com/<your-org>/vibe-ai-agent/issues) 提交
- 💡 **功能建议** — 通过 [Discussions](https://github.com/<your-org>/vibe-ai-agent/discussions) 讨论
- 🛠️ **提交 PR** — Fork 仓库，创建特性分支，提交 PR

### 开发指引

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行 Lint
ruff check .
ruff format --check .

# 运行类型检查
mypy testagent/ --strict

# 运行测试
pytest tests/ -v --cov=testagent

# 运行单元测试
pytest tests/unit/ -v

# 运行集成测试
pytest tests/integration/ -v
```

---

## 👥 项目负责人

- **@kongwenshuo** — 项目发起人 & 核心开发者

感谢所有 [贡献者](https://github.com/<your-org>/vibe-ai-agent/graphs/contributors) 的支持！🎉

---

## 📄 开源协议

本项目基于 **MIT 许可证** 开源。详见 [LICENSE](LICENSE) 文件。

```
MIT License

Copyright (c) 2026 TestAgent Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files...
```

---

<p align="center">
  <strong>🍅 TomatoPilot — 番茄领航，测试自动驾驶</strong>
  <br>
  <sub>Built with ❤️ for the testing community</sub>
</p>
