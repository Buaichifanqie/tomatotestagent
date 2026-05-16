# TestAgent Skill 开发规范

> 本文档是 Skill 开发者参考手册，描述如何定义、创建、调试和发布 TestAgent Skill。

---

## 1. Skill 是什么

Skill 是 TestAgent 的可复用测试知识单元，采用 **YAML Front Matter（元数据）+ Markdown Body（操作知识）** 格式定义。

```
┌─────────────────────────────────────┐
│  SKILL.md                           │
│                                     │
│  ---                                │
│  name: api_smoke_test              │  ← YAML Front Matter（元数据）
│  version: "1.0.0"                  │     告诉 TestAgent 这个 Skill 是谁、
│  trigger: "api.*smoke|..."         │     什么时候用、依赖什么资源
│  required_mcp_servers:             │
│    - api_server                    │
│  ---                                │
│                                     │
│  ## 目标                            │  ← Markdown Body（操作知识）
│  ...                                │     告诉 Agent 具体做什么、
│  ## 操作流程                        │     怎么判断对错、
│  ...                                │     出错了怎么办
│  ## 断言策略                        │
│  ...                                │
│  ## 失败处理                        │
│  ...                                │
└─────────────────────────────────────┘
```

### 两层注入机制

Skill 不是一次性全部塞进 LLM 上下文，而是通过**两层注入**分阶段加载，避免系统提示膨胀：

| 层级 | 时机 | 内容 | 大小 |
|------|------|------|------|
| **Layer 1** | 系统提示构建时 | 名称 + 短描述（`description` 字段） | ~100 tokens / Skill |
| **Layer 2** | LLM 调用 `load_skill()` 时 | 完整 Markdown Body | ~2000 tokens / Skill |

Layer 1 让 LLM 知道有哪些 Skill 可用；Layer 2 在 LLM 决定使用某个 Skill 时按需注入完整操作知识。这确保了即使注册了数十个 Skill，系统提示也不会超出 token 预算。

### Skill 与代码的分离

```
项目根目录/
├── skills/                      ← Skill 定义文件（Markdown，Git 管理）
│   ├── api_smoke_test/
│   │   └── SKILL.md
│   ├── web_smoke_test/
│   │   └── SKILL.md
│   └── my_custom_skill/
│       └── SKILL.md
│
├── testagent/skills/            ← Skill Engine 代码（Python）
│   ├── parser.py                ← MarkdownParser
│   ├── loader.py                ← SkillLoader
│   ├── validator.py             ← SkillValidator
│   ├── registry.py              ← SkillRegistry
│   ├── matcher.py               ← SkillMatcher
│   ├── executor.py              ← SkillExecutor
│   ├── scaffold.py              ← SkillScaffold（脚手架）
│   └── templates.py             ← 模板定义
```

**`skills/`** 存放 Skill Markdown 定义，**`testagent/skills/`** 存放 Skill Engine 代码，两者必须分离。开发者只需编辑 `skills/` 目录下的 Markdown 文件，无需修改 Python 代码。

---

## 2. 创建你的第一个 Skill

### 使用脚手架命令

```bash
testagent skill create --name my_api_test --template api_test
```

该命令在 `skills/my_api_test/` 目录下生成两个文件：

```
skills/my_api_test/
├── SKILL.md          ← Skill 定义文件（YAML Front Matter + Markdown Body）
└── README.md         ← 使用说明（人类阅读）
```

### 可用模板

| 模板名称 | 适用场景 | 预置 MCP Server | 预置 RAG Collection |
|---------|---------|-----------------|-------------------|
| `api_test` | API 接口测试 | `api_server`, `database_server` | `api_docs`, `defect_history` |
| `web_test` | Web 页面测试 | `playwright_server` | `req_docs`, `locator_library` |
| `app_test` | App 移动端测试 | `appium_server` | `req_docs`, `locator_library` |
| `empty` | 从零开始自定义 | 无 | 无 |

### 生成的 SKILL.md 示例（api_test 模板）

```yaml
---
name: my_api_test
version: "1.0.0"
description: "my_api_test: API 测试技能，覆盖核心 Endpoint 的正向验证"
trigger: "my_api_test.*test|test.*my_api_test|my_api_test"
required_mcp_servers:
  - api_server
  - database_server
required_rag_collections:
  - api_docs
  - defect_history
---

## 目标

对被测 API 的核心端点执行快速冒烟测试，验证基本可用性和响应格式符合 OpenAPI 规范...

## 操作流程

1. 从 `api_docs` RAG Collection 检索目标 API 的 OpenAPI 规范
2. ...

## 断言策略

- 所有端点 HTTP 状态码必须在 2xx 范围内
- ...

## 失败处理

- 单个端点失败：记录失败详情，继续执行后续端点
- ...
```

生成后，根据实际业务场景修改 `SKILL.md` 内容即可。

---

## 3. YAML Front Matter 规范

YAML Front Matter 位于 `---` 分隔符之间，定义 Skill 的元数据。

### 必填字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `name` | string | Skill 唯一标识（与 `version` 组成复合唯一键） | `api_smoke_test` |
| `version` | string | 语义化版本号，必须用引号包裹 | `"1.0.0"` |
| `description` | string | 短描述，用于 Layer 1 注入（~100 tokens 以内） | `API 冒烟测试技能，覆盖核心 Endpoint 的正向验证` |
| `trigger` | string | 正则表达式，用于 Skill 匹配 | `"api.*smoke\|smoke.*api\|API 冒烟\|接口冒烟"` |
| `required_mcp_servers` | list[string] | 依赖的 MCP Server 列表（可为空列表 `[]`） | `["api_server", "database_server"]` |
| `required_rag_collections` | list[string] | 依赖的 RAG Collection 列表（可为空列表 `[]`） | `["api_docs", "defect_history"]` |

### 可选字段

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `tags` | list[string] | 标签索引，辅助分类与检索 | `["api", "smoke", "regression"]` |
| `author` | string | 作者信息 | `"test-team"` |

### trigger 模式语法

`trigger` 字段是一个**正则表达式**，用于在用户输入或任务描述中匹配到该 Skill。匹配在加载时校验合法性，不合法则校验失败。

**语法规则：**

- 使用 Python `re` 模块正则语法，匹配时自动启用 `re.IGNORECASE`（不区分大小写）
- 多个模式用 `|` 分隔，表示「或」关系
- 建议同时包含英文和中文关键词，覆盖不同输入习惯

**示例：**

```yaml
trigger: "api.*smoke|smoke.*api|API 冒烟|接口冒烟"
```

当用户输入「帮我跑一次 API 冒烟测试」时，`api.*smoke` 和 `API 冒烟` 两个模式均可命中。

**匹配评分：**

- trigger 模式匹配：权重 **10.0**
- 描述关键词重叠：权重 **1.0 × 重叠词数**
- 最终按总分降序排列，返回最高分 Skill

> 注意：trigger 正则不合法会导致 Skill 校验失败，该 Skill 不会被注册。请确保正则语法正确。

### version 语义化版本规则

采用 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/) 规范：`MAJOR.MINOR.PATCH`

| 版本号 | 何时递增 | 示例 |
|-------|---------|------|
| **MAJOR** | 不兼容的操作流程变更（如断言策略大幅改变） | `1.0.0` → `2.0.0` |
| **MINOR** | 向后兼容的功能新增（如新增操作步骤） | `1.0.0` → `1.1.0` |
| **PATCH** | 向后兼容的问题修复（如修正描述文字） | `1.0.0` → `1.0.1` |

- Skill 以 `name + version` 为唯一键，同一 `name` 可注册多个版本
- 查询时不指定版本则返回最高版本
- YAML 中 `version` 必须用引号包裹（避免 YAML 将 `1.0` 解析为浮点数）

```yaml
version: "1.0.0"    # ✅ 正确：引号包裹
version: 1.0.0      # ❌ 错误：YAML 解析为浮点数 1.0
```

---

## 4. Markdown Body 规范

Markdown Body 位于 `---` 分隔符之后，定义 Skill 的操作知识，供 Agent 在执行时参考。

### 必填章节

| 章节 | 标题格式 | 说明 |
|------|---------|------|
| **目标** | `## 目标` | 清晰描述该 Skill 要达成的测试目标 |
| **操作流程** | `## 操作流程` | 有序列表描述每个步骤，每个步骤可独立重试 |
| **断言策略** | `## 断言策略` | 无序列表描述通过/失败的判断条件 |
| **失败处理** | `## 失败处理` | 无序列表描述各类失败场景的应对策略 |

### 推荐章节

| 章节 | 标题格式 | 说明 |
|------|---------|------|
| **前置条件** | `## 前置条件` | 执行前必须满足的环境和数据条件 |
| **环境要求** | `## 环境要求` | 运行时需要的服务、工具、配置 |
| **自愈策略** | `## 自愈策略` | 执行失败时的自动修复尝试（如定位器降级） |

### 章节编写要求

**目标**：一句话说明要验证什么，不要写操作步骤。

```markdown
## 目标

对被测 API 的核心端点执行快速冒烟测试，验证基本可用性和响应格式符合 OpenAPI 规范，确保核心链路通畅。
```

**操作流程**：有序列表，每步描述具体操作和验证行为。子步骤用 `a. b. c.` 缩进。

```markdown
## 操作流程

1. 从 `api_docs` RAG Collection 检索目标 API 的 OpenAPI 规范
2. 对每个端点：
   a. 构造最小正向请求（必需参数填有效值，可选参数忽略）
   b. 通过 `api_server` MCP Server 发送 HTTP 请求
   c. 验证 HTTP 状态码为 2xx
3. 汇总所有端点的响应时间、状态码、Schema 校验结果
```

**断言策略**：无序列表，每条是一个可判定的条件。

```markdown
## 断言策略

- 所有端点 HTTP 状态码必须在 2xx 范围内（允许 200/201/204）
- 单个端点响应时间不超过 5s
- 总通过率必须达到 100%
```

**失败处理**：按场景分条描述，包含缺陷分类（`bug` / `flaky` / `environment` / `configuration`）。

```markdown
## 失败处理

- 单个端点失败：记录失败详情，继续执行后续端点
- 连续 3 个端点失败：标记为 `environment` 类别，暂停执行，通知 Planner Agent
- 超时失败（>5s）：记录为 `environment` 类别缺陷
- Schema 校验失败：标记为 `bug` 类别缺陷
```

### Markdown 模板变量

Body 中可使用模板变量，在执行时由上下文动态替换：

| 变量 | 说明 | 示例值 |
|------|------|-------|
| `{{base_url}}` | 被测服务的基础 URL | `https://staging.myapp.com` |
| `{{env}}` | 当前执行环境 | `staging` / `production` |
| `{{timeout}}` | 请求超时时间 | `30` |

```markdown
## 操作流程

1. 通过 `api_server` 向 `{{base_url}}/api/v1/health` 发送 GET 请求，超时 `{{timeout}}`s
2. ...
```

---

## 5. MCP Server 依赖

Skill 通过 `required_mcp_servers` 声明所需的 MCP Server，Agent 执行时会通过 Gateway 路由调用对应的工具。

### 可用 Server 列表

| MCP Server | 提供的能力 | 适用测试类型 |
|-----------|----------|------------|
| `api_server` | HTTP 请求发送、响应验证、Schema 校验 | API 测试 |
| `playwright_server` | 浏览器操作、页面截图、元素交互、控制台日志捕获 | Web 测试 |
| `appium_server` | App 启动/操作、元素定位、手势模拟 | App 测试 [V1.0] |
| `jira_server` | 缺陷创建/查询、项目信息获取 | 缺陷归档 |
| `git_server` | 代码变更查询、分支信息 | 变更关联分析 |
| `database_server` | 数据库查询、数据一致性校验 | 数据验证 |

### degraded 机制

当 `required_mcp_servers` 中声明的 Server 在 MCPRegistry 中未注册时：

1. Skill 校验阶段将该 Skill 标记为 **degraded**（降级）
2. 注册仍然成功，Skill 可被匹配和加载
3. 运行时该 MCP 工具不可用，Agent 需要降级处理（跳过依赖该 MCP 的步骤或改用替代方案）
4. 系统日志会输出告警信息

```
⚠ MCP Server 'appium_server' not registered, Skill 'app_smoke_test' marked as degraded
```

**开发者应：**
- 在 `失败处理` 章节中为依赖 MCP 的步骤提供降级方案
- 确保生产环境已注册所有 `required_mcp_servers`
- 开发/调试阶段可容忍 degraded 状态

---

## 6. RAG Collection 依赖

Skill 通过 `required_rag_collections` 声明所需的 RAG 知识库，Agent 执行时会从对应 Collection 检索上下文知识。

### 可用 Collection 列表

| Collection | 数据源 | 访问权限 |
|-----------|-------|---------|
| `req_docs` | 需求文档 PRD | Planner, Executor |
| `api_docs` | OpenAPI/Swagger 规范 | Planner, Executor |
| `defect_history` | 历史缺陷 Jira | Planner, Analyzer |
| `test_reports` | 历史测试报告 | Analyzer |
| `locator_library` | UI 定位器库 | Executor |
| `failure_patterns` | 失败模式库 | Analyzer |

### 访问权限说明

不同 Agent 角色对 RAG Collection 的访问权限不同：

| Agent | 可访问的 Collection |
|-------|-------------------|
| **Planner Agent** | `req_docs`, `api_docs`, `defect_history` |
| **Executor Agent** | `api_docs`, `req_docs`, `locator_library` |
| **Analyzer Agent** | `defect_history`, `test_reports`, `failure_patterns` |

声明了 Agent 无权访问的 Collection 不会导致 Skill 校验失败，但在运行时该 Agent 无法从中检索数据。开发者应确保 `required_rag_collections` 与 Skill 运行的 Agent 角色权限匹配。

---

## 7. 调试与测试

### 查看注册状态

```bash
testagent skill list
```

输出所有已注册 Skill 的名称、版本、描述、触发词、MCP 依赖和 RAG Collections。degraded 状态的 Skill 会有特殊标记。

### 执行 Skill

```bash
# 指定 Skill 名称执行
testagent run --skill api_smoke_test --env staging

# 指定 base_url
testagent run --skill web_smoke_test --url https://staging.myapp.com

# 通过测试计划文件执行
testagent run --plan ./test-plan.json
```

### 查看日志定位问题

Skill 加载、校验、匹配、执行的每个环节都会输出结构化日志：

| 阶段 | 日志内容 | 排查方向 |
|------|---------|---------|
| 加载 | 解析成功/失败、文件路径 | SKILL.md 格式是否正确 |
| 校验 | 必填字段缺失、trigger 非法、MCP 未注册 | YAML Front Matter 是否完整 |
| 注册 | 重复注册告警、版本覆盖 | 是否存在同名 Skill |
| 匹配 | 输入文本、匹配分数、命中原因 | trigger 模式是否覆盖目标输入 |
| 执行 | 步骤进度、MCP 调用结果、断言结果 | 操作流程是否合理 |

常见问题排查：

| 问题 | 可能原因 | 解决方法 |
|------|---------|---------|
| Skill 未出现在 `skill list` | YAML 格式错误或必填字段缺失 | 检查 Front Matter `---` 分隔符和必填字段 |
| trigger 未匹配 | 正则语法错误或模式不覆盖 | 在 `trigger` 中添加更多关键词模式 |
| Skill 状态为 degraded | MCP Server 未注册 | 在 `configs/mcp.json` 中配置对应 Server |
| 执行步骤失败 | MCP Server 不可用或操作流程有误 | 检查 MCP Server 健康状态，调整操作流程 |

---

## 8. 最佳实践

### 单一职责

一个 Skill 只做一件事。不要把 API 冒烟测试和 Web 冒烟测试写进同一个 Skill。

```yaml
# ✅ 正确：职责单一
name: api_smoke_test
description: API 冒烟测试技能，覆盖核心 Endpoint 的正向验证

# ❌ 错误：职责混杂
name: smoke_test
description: API 和 Web 冒烟测试技能
```

如果需要编排多个 Skill，使用 Test Plan 而非合并 Skill。

### 原子步骤

操作流程中的每个步骤应可独立重试，避免步骤间强耦合。

```markdown
# ✅ 正确：每步可独立执行和重试
3. 对每个端点：
   a. 构造最小正向请求
   b. 发送 HTTP 请求
   c. 验证状态码为 2xx

# ❌ 错误：步骤耦合，前一步失败后一步无法执行
3. 构造请求并发送并验证响应
```

### 断言完整

断言策略应覆盖多个维度，不要只检查状态码。

```markdown
# ✅ 正确：多维度断言
- HTTP 状态码必须在 2xx 范围内
- 响应体 JSON Schema 必须严格匹配 OpenAPI 规范定义
- 单个端点响应时间不超过 5s
- 数据库写操作的结果必须与 API 响应一致

# ❌ 错误：断言不足
- 请求返回 200 即可
```

### 失败优雅

为每种失败场景提供明确的处理策略和降级方案，包含缺陷分类。

```markdown
# ✅ 正确：分类处理 + 降级方案
- 单个端点失败：记录详情，继续执行后续端点
- 连续 3 个端点失败：标记为 `environment` 类别，暂停执行
- 定位器失效：自动尝试 CSS → XPath → 文本匹配降级

# ❌ 错误：无降级方案
- 测试失败则终止
```

### trigger 覆盖全面

trigger 应覆盖中英文、缩写、口语化表达。

```yaml
# ✅ 正确：多模式覆盖
trigger: "api.*smoke|smoke.*api|API 冒烟|接口冒烟|接口冒烟测试"

# ❌ 错误：单一模式
trigger: "api_smoke_test"
```

### description 精炼

description 用于 Layer 1 注入，控制在 ~100 tokens 以内，只写核心信息。

```yaml
# ✅ 正确：精炼
description: API 冒烟测试技能，覆盖核心 Endpoint 的正向验证

# ❌ 错误：冗长
description: 本技能用于对被测 API 的所有核心端点执行快速冒烟测试，
  验证基本可用性和响应格式符合 OpenAPI 规范，确保核心链路通畅，
  同时检查数据库一致性...
```

### 版本管理

- 修改操作流程或断言策略时递增 MINOR 版本
- 仅修正文字或格式时递增 PATCH 版本
- 不兼容变更（如删除断言条件）时递增 MAJOR 版本
- 避免频繁发布 `0.x.x` 版本，应尽快稳定到 `1.0.0`

---

## 附录：完整 SKILL.md 示例

```yaml
---
name: api_smoke_test
version: "1.0.0"
description: API 冒烟测试技能，覆盖核心 Endpoint 的正向验证
trigger: "api.*smoke|smoke.*api|API 冒烟|接口冒烟"
required_mcp_servers:
  - api_server
  - database_server
required_rag_collections:
  - api_docs
  - defect_history
tags:
  - api
  - smoke
author: test-team
---

## 目标

对被测 API 的核心端点执行快速冒烟测试，验证基本可用性和响应格式符合 OpenAPI 规范，确保核心链路通畅。

## 操作流程

1. 从 `api_docs` RAG Collection 检索目标 API 的 OpenAPI 规范（Swagger/OpenAPI 3.x）
2. 提取所有标记为 `critical` 或 `core` 的端点及其请求/响应 Schema
3. 从 `defect_history` 检索最近同类 API 的已知缺陷模式，作为测试关注点参考
4. 对每个端点：
   a. 构造最小正向请求（必需参数填有效值，可选参数忽略）
   b. 通过 `api_server` MCP Server 发送 HTTP 请求
   c. 验证 HTTP 状态码为 2xx
   d. 验证响应 JSON Schema 与 OpenAPI 规范一致
   e. 记录端点响应时间
5. 对依赖数据库的端点，通过 `database_server` MCP Server 验证数据写操作的正确性（如创建资源后确认数据库记录存在）
6. 汇总所有端点的响应时间、状态码、Schema 校验结果

## 断言策略

- 所有端点 HTTP 状态码必须在 2xx 范围内（允许 200/201/204）
- 响应体 JSON Schema 必须严格匹配 OpenAPI 规范定义（字段名、类型、必填约束）
- 单个端点响应时间不超过 5s（从请求发出到收到完整响应）
- 总通过率必须达到 100%（任一端点失败即视为冒烟未通过）
- 数据库写操作的结果必须与 API 响应一致（如 POST 返回的 id 可在数据库中找到对应记录）

## 失败处理

- 单个端点失败：记录失败详情（端点路径、HTTP 状态码、响应体、耗时、Schema 校验错误），继续执行后续端点
- 连续 3 个端点失败：标记为环境/网络问题（`environment` 类别），暂停执行，通知 Planner Agent 决策是否继续
- 超时失败（>5s）：记录为 `environment` 类别缺陷，建议检查网络连接或服务负载
- Schema 校验失败：记录字段差异详情，标记为 `bug` 类别缺陷，关联到对应 API 端点
- 数据库一致性校验失败：记录 API 响应与数据库记录差异，标记为 `bug` 类别缺陷，优先级 `critical`
```
