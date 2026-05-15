---
name: api_regression_test
version: "1.0.0"
description: API 回归测试技能（含边界值/异常值），验证 API 修改后未引入回归缺陷
trigger: "api.*regression|regression.*api|API 回归|接口回归|regression"
required_mcp_servers:
  - api_server
  - database_server
required_rag_collections:
  - api_docs
  - defect_history
---

## 目标

对被测 API 执行全面的回归测试，覆盖正向流程、边界值、异常输入和权限场景，确保代码变更未引入新的缺陷。

## 操作流程

1. 从 `api_docs` RAG Collection 检索目标 API 的完整 OpenAPI 规范
2. 从 `defect_history` 检索与本次变更相关的历史缺陷，确定高风险的回归范围
3. 对每个端点按以下策略生成测试用例：

   a. **正向流程**：构造最小正向请求，验证 2xx 状态码和响应 Schema
   b. **边界值测试**（数值字段）：
      - 最小值、最大值、略小于最小值、略大于最大值
      - 空字符串、超长字符串（字段最大长度 +1）
      - 分页参数：page=0、page=1、page=max、limit=0、limit=max+1
   c. **异常值测试**：
      - 必填字段缺失
      - 字段类型错误（字符串传数字、数字传布尔）
      - 无效枚举值、无效 UUID 格式
      - 非法的 JSON 请求体
   d. **HTTP 方法测试**：
      - 对 GET 端点使用 POST/PUT/DELETE，验证返回 405
      - 对 POST 端点使用 GET，验证返回 405
   e. **鉴权测试**：
      - 无 Token 请求，验证返回 401
      - 过期/无效 Token 请求，验证返回 401
      - 无权限角色的 Token 请求，验证返回 403
   f. **依赖数据状态测试**：
      - 操作不存在的资源（如 ID 不存在），验证返回 404
      - 重复创建唯一约束资源，验证返回 409

4. 对每个测试用例：
   a. 通过 `api_server` MCP Server 发送请求
   b. 验证 HTTP 状态码符合预期
   c. 验证响应体符合预期的错误格式（如 `{ "error": { "code": "...", "message": "..." } }`）
   d. 记录测试结果和响应详情

5. 涉及数据库写操作的用例，通过 `database_server` MCP Server 验证数据一致性

## 断言策略

- 正向用例：HTTP 状态码 2xx，响应 Schema 与 OpenAPI 规范一致
- 边界值用例：HTTP 状态码 2xx（有效边界）或 400（无效边界），响应体包含清晰的错误描述
- 异常值用例：HTTP 状态码 4xx，错误消息明确指示具体的校验失败原因（而非笼统的 "Bad Request"）
- 鉴权用例：无 Token 返回 401，无效 Token 返回 401，无权限返回 403，错误体符合 RFC 7235 规范
- 资源状态用例：不存在返回 404，唯一约束冲突返回 409，错误体包含冲突资源标识
- 所有用例的响应时间不超过 10s（含边界和异常用例）
- 正向用例通过率必须 100%；边界/异常用例允许预知的合理失败，但必须记录

## 失败处理

- 正向用例失败：标记为 `bug` 类别缺陷，优先级 `critical`，立即通知 Planner Agent 阻断后续流程
- 边界/异常用例与预期状态码不符：
  - 预期 4xx 但得到 2xx：标记为 `bug` 类别缺陷，优先级 `major`，说明校验缺失
  - 预期 2xx 但得到 4xx/5xx：标记为 `bug` 类别缺陷，优先级 `critical`，说明功能退化
- 鉴权测试失败（匿名可访问需鉴权的端点）：标记为 `bug` 类别缺陷，优先级 `critical`，安全漏洞
- 历史缺陷对应的回归用例失败：标记为 `bug` 类别缺陷，优先级 `critical`，关联原始缺陷 ID，说明回归
- 超时或网络错误（不限于单个端点）：标记为 `environment` 类别缺陷，触发自动重试（指数退避 2s→4s→8s，最多 3 次）
- 测试执行完成后，将新发现的缺陷模式记录回 `defect_history` RAG Collection，形成知识闭环
