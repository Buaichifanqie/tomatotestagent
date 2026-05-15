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
5. 对依赖数据库的端点，通过 `database_server` MCP Server 验证数据写操作的正确性
6. 汇总所有端点的响应时间、状态码、Schema 校验结果

## 断言策略

- 所有端点 HTTP 状态码必须在 2xx 范围内（允许 200/201/204）
- 响应体 JSON Schema 必须严格匹配 OpenAPI 规范定义
- 单个端点响应时间不超过 5s
- 总通过率必须达到 100%
- 数据库写操作的结果必须与 API 响应一致

## 失败处理

- 单个端点失败：记录失败详情，继续执行后续端点
- 连续 3 个端点失败：标记为环境/网络问题，暂停执行，通知 Planner Agent 决策
- 超时失败（>5s）：记录为 `environment` 类别缺陷
- Schema 校验失败：记录字段差异详情，标记为 `bug` 类别缺陷
- 数据库一致性校验失败：标记为 `bug` 类别缺陷，优先级 `critical`
