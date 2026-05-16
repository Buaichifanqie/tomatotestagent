---
name: full_regression_test
version: "1.0.0"
description: 全量回归测试编排
trigger: "全量回归|full regression|完整测试|全量测试|full.*regression|regression.*full"
required_mcp_servers:
  - api_server
  - playwright_server
required_rag_collections:
  - api_docs
  - defect_history
---

## 目标

协调 API 层与 Web 层的全量回归测试，按照"先 API 后 Web"的执行顺序和依赖编排策略，在保证测试覆盖完整性的前提下最大化并行执行效率，生成综合 API + Web 的全量回归报告。

## 操作流程

### 1. 测试规划与依赖分析

1. 从 `api_docs` RAG Collection 检索全部 API Endpoint 的 OpenAPI 规范，提取：
   - 所有 Endpoint 列表、请求/响应 Schema、鉴权要求
   - Endpoint 间的数据依赖关系（如创建资源 → 查询资源 → 更新资源 → 删除资源）
2. 从 `defect_history` RAG Collection 检索历史缺陷，提取：
   - 高频缺陷模式及其影响的 Endpoint 和页面
   - 近期修复的缺陷列表（作为回归重点）
3. 分析 API 与 Web 测试的依赖关系：
   - Web 测试依赖 API 数据准备（如登录获取 Token、创建测试数据）
   - 标识可并行执行的 API 测试组（无数据依赖的 Endpoint）
   - 标识必须串行执行的 API 测试链（有数据依赖的 Endpoint 序列）
4. 生成全量回归测试执行计划：
   - Phase 1：API 回归测试（含数据准备）
   - Phase 2：Web 回归测试（依赖 Phase 1 数据）
   - 标注每个 Phase 内可并行执行的测试组

### 2. Phase 1：API 全量回归测试

1. 通过 `api_server` MCP Server 执行 API 回归测试：

   a. **数据准备**：
      - 创建测试所需的初始数据（用户、配置项、基础资源）
      - 获取鉴权 Token（不同角色：管理员/普通用户/只读用户）
      - 记录创建的测试数据 ID，供后续清理

   b. **并行执行**（无数据依赖的 Endpoint 组）：
      - 对独立的 GET/POST Endpoint 组并行发送请求
      - 并行路数不超过系统配置的并发上限（MVP: 5路, V1.0: 10路）
      - 每条并行路径独立记录请求/响应日志

   c. **串行执行**（有数据依赖的 Endpoint 链）：
      - 创建资源 → 获取资源 → 更新资源 → 删除资源，严格按顺序执行
      - 每步验证前一步的操作结果，链中任一步失败则跳过后续步骤并记录

   d. **API 测试维度**：
      - 正向流程：最小正向请求，验证 2xx + Schema
      - 边界值：数值/字符串/枚举参数的边界覆盖
      - 异常值：4xx/5xx/超时/空响应场景
      - 鉴权：无 Token / 过期 Token / 无权限 Token
      - 数据一致性：API 响应与数据库记录一致

2. 汇总 API 测试结果：
   - 按 Endpoint 统计通过/失败/跳过数量
   - 记录失败用例详情，供 Phase 2 判断是否继续

### 3. Phase 2：Web 全量回归测试

1. 根据 Phase 1 结果决策：
   - Phase 1 关键 API 全部通过 → Phase 2 正常执行
   - Phase 1 关键 API 存在失败 → 评估影响范围：
     - 仅非关键 API 失败 → Phase 2 执行，报告中标注 API 异常
     - 关键 API 失败（登录/核心业务）→ Phase 2 仅执行不依赖失败 API 的页面，其余标记为 `skipped`

2. 通过 `playwright_server` MCP Server 执行 Web 回归测试：

   a. **页面加载验证**：
      - 对每个目标页面执行加载测试（桌面端 1920x1080 + 移动端 375x812）
      - 等待 `networkidle` 状态（超时 30s），验证 HTTP 200
      - 验证关键元素可见（导航栏、主内容区、页脚）

   b. **核心流程验证**：
      - 登录流程：输入凭据 → 提交 → 验证登录成功
      - 数据展示流程：列表加载 → 详情查看 → 返回列表
      - 表单提交流程：填写表单 → 提交 → 验证成功提示
      - 搜索流程：输入关键词 → 提交搜索 → 验证结果列表

   c. **交互功能验证**：
      - 导航切换：Tab/菜单切换后页面内容正确
      - 弹窗/对话框：打开/关闭正常，内容正确
      - 下拉选择：选择后值更新正确
      - 分页/排序：分页切换和排序功能正常

3. 收集 Web 性能指标：FCP、LCP、CLS、页面加载时间

### 4. 综合结果汇总与报告生成

1. 合并 Phase 1 (API) 和 Phase 2 (Web) 测试结果：
   - API 测试结果：按 Endpoint 分组，统计通过/失败/跳过/错误
   - Web 测试结果：按页面和流程分组，统计通过/失败/跳过
   - 交叉分析：API 失败导致的 Web 测试跳过/失败链路追踪
2. 生成全量回归测试报告：
   - 整体通过率（API + Web 综合）
   - API 层通过率、Web 层通过率
   - 缺陷清单（按严重度和类别分类）
   - 性能基线对比（API 响应时间、Web 加载时间）
   - 测试覆盖范围（覆盖的 Endpoint 和页面占比）
3. 将报告存入 `test_reports` RAG Collection

## 断言策略

### API 层断言

- 正向用例：HTTP 2xx + 响应 Schema 匹配 OpenAPI 规范
- 边界值/异常值：HTTP 状态码符合预期 + 错误响应结构符合规范
- 数据一致性：API 写操作后数据库记录与响应一致
- 性能基线：单个 API 请求 < 5s，P95 不超过历史基线 1.5 倍
- API 层正向用例通过率 100%

### Web 层断言

- 页面加载：HTTP 200 + 关键元素可见 + 无控制台 error
- 核心流程：每步操作后页面状态与预期一致
- 交互功能：操作响应正确，无异常跳转或空白页
- 性能基线：FCP < 3s、LCP < 5s、CLS < 0.1
- Web 层核心流程通过率 100%

### 综合断言

- 全量回归整体通过率 ≥ 95%（API + Web 综合）
- 无 `critical` 级别缺陷 → 回归测试通过
- 存在 `critical` 级别缺陷 → 回归测试失败，阻断发布
- API 和 Web 层均无 `critical` 缺陷且整体通过率 ≥ 95% → 回归通过

## 失败处理

### 部分失败继续执行

- API 单个 Endpoint 失败：记录失败详情，继续执行其他 Endpoint 测试
- API 某串行链中断：跳过该链后续步骤，记录跳过原因，继续执行其他链
- Web 单个页面失败：记录失败详情，继续执行其他页面测试
- Web 某流程中断：跳过该流程后续步骤，继续执行其他流程
- Phase 1 非关键 API 失败：Phase 2 正常执行，报告中标注 API 异常影响

### 全量报告与阻断

- Phase 1 关键 API 失败（登录/鉴权/核心业务）：
  - 标记为 `bug` 类别缺陷，优先级 `critical`
  - 评估受影响的 Web 测试范围，将依赖项标记为 `skipped`
  - 触发 Analyzer Agent 根因分析
- Phase 1 整体通过率 < 80%：
  - 标记为 `bug` 类别缺陷，优先级 `critical`
  - 暂停 Phase 2 执行，通知 Planner Agent 决策是否继续
  - 生成 API 层阶段性报告
- Phase 2 关键页面/流程失败：
  - 标记为 `bug` 类别缺陷，优先级 `critical`
  - 触发 Analyzer Agent 根因分析

### 自动重试

- 网络超时/连接错误（非业务逻辑）：自动重试（指数退避 2s→4s→8s，最多 3 次）
- 5xx 错误（非输入引起）：自动重试 1 次，区分偶发与稳定复现
- Web 页面加载超时：自动重试 1 次，仍失败则标记为 `environment` 类别缺陷

### 缺陷归档

- API 缺陷：按缺陷分类规则归档（`bug` / `flaky` / `environment` / `configuration`）
- Web 缺陷：按缺陷分类规则归档
- 交叉缺陷：API 失败导致 Web 失败的链路，同时归档 API 根因和 Web 影响，关联原始缺陷 ID

### 知识闭环

- 测试执行完成后，将缺陷模式记录回 `defect_history` RAG Collection
- API 测试覆盖信息更新到 `api_docs` Collection
- 全量回归报告存入 `test_reports` RAG Collection，供后续测试基线参考
