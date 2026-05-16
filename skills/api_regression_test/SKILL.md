---
name: api_regression_test
version: "1.1.0"
description: API 回归测试（含边界值/异常值）
trigger: "回归测试|regression|API regression|api.*regression|regression.*api|API 回归|接口回归"
required_mcp_servers:
  - api_server
  - database_server
required_rag_collections:
  - api_docs
  - defect_history
---

## 目标

对被测 API 执行全面的回归测试，覆盖正向流程、边界值、异常输入、服务端错误和权限场景。基于 RAG `defect_history` 中的高频缺陷模式动态调整测试重心，从 RAG `api_docs` 提取所有 Endpoint 参数实现数据驱动测试，确保代码变更未引入新的缺陷。

## 操作流程

### 1. 数据准备：从 RAG 提取测试数据源

1. 从 `api_docs` RAG Collection 检索目标 API 的完整 OpenAPI 规范（Swagger/OpenAPI 3.x）
2. 解析所有 Endpoint 的请求参数（path/query/header/cookie/body），提取每个参数的：
   - 数据类型、格式约束（format: int64 / email / uuid 等）
   - 取值范围（minimum/maximum/enum/maxLength/minLength/pattern）
   - 必填/可选标记、默认值
3. 从 `defect_history` RAG Collection 检索与本次变更相关的历史缺陷，确定高风险回归范围：
   - 统计高频缺陷模式（如"必填字段未校验"、"整数溢出"、"分页越界"）
   - 将高频模式映射到对应 Endpoint 参数，提升相关用例的优先级

### 2. 边界值测试策略（参照 defect_history 高频缺陷模式）

对每个 Endpoint 的数值/字符串/集合类参数执行边界值覆盖：

a. **数值字段**（integer/number）：
   - 最小值（minimum）、最大值（maximum）
   - 略小于最小值（minimum - 1）、略大于最大值（maximum + 1）
   - 零值（如业务允许）、负值（如不期望负数）
   - 整数溢出边界（INT32_MAX + 1 / INT64_MAX + 1）
   - 浮点精度边界（0.1 + 0.2 ≠ 0.3 场景）

b. **字符串字段**（string）：
   - 空字符串 ""、仅空白字符 "   "
   - 最小长度（minLength）、最大长度（maxLength）
   - 超长字符串（maxLength + 1 / maxLength + 100）
   - 特殊字符注入（SQL/NoSQL/XSS payload）
   - Unicode / Emoji 字符（如业务涉及多语言）

c. **枚举字段**（enum）：
   - 每个合法枚举值逐一测试
   - 不在枚举列表中的非法值

d. **分页参数**：
   - page=0、page=1、page=最大有效页、page=超大值
   - limit=0、limit=1、limit=最大允许值、limit=最大允许值+1
   - 排序字段：合法排序字段、非法排序字段、SQL 注入排序字段

e. **数组字段**（array）：
   - 空数组 []、单元素数组、最大长度数组、超长数组
   - 数组内元素包含重复项（如业务要求唯一性）

### 3. 异常值测试策略（4xx/5xx/超时/空响应）

a. **客户端错误（4xx）**：
   - 必填字段缺失：逐一移除每个必填参数，验证返回 400 + 明确缺失字段名称
   - 字段类型错误：字符串传数字、数字传布尔、对象传数组，验证返回 400 + 类型不匹配描述
   - 无效枚举值、无效 UUID 格式、无效日期格式
   - 非法的 JSON 请求体（如缺少闭合括号、非法转义字符）
   - Content-Type 不匹配（如 Expect JSON 但发送 form-data）

b. **服务端错误（5xx）**：
   - 触发内部异常的极端输入（超长字符串导致 Buffer Overflow / 超大数组导致 OOM）
   - 并发冲突场景（同一资源同时 PUT，验证是否返回 409 而非 500）
   - 依赖服务不可用时的降级行为（如数据库连接失败时返回 503 而非 500）

c. **超时场景**：
   - 大数据量请求（如批量创建 1000 条记录），验证是否在合理时间内响应或返回 408/504
   - 复杂查询请求（如多表联合查询 + 全量导出），验证超时后是否有友好错误响应

d. **空响应场景**：
   - 查询不存在资源的列表（如 status=nonexistent_status），验证返回 200 + 空数组 而非 404
   - 删除已删除的资源，验证返回 404 或 204（幂等性验证）
   - 空请求体（Content-Length: 0），验证返回 400 而非 500

### 4. HTTP 方法与鉴权测试

a. **HTTP 方法测试**：
   - 对 GET 端点使用 POST/PUT/DELETE，验证返回 405
   - 对 POST 端点使用 GET，验证返回 405
   - 对只读端点使用 PATCH/DELETE，验证返回 405

b. **鉴权测试**：
   - 无 Token 请求，验证返回 401
   - 过期 Token 请求，验证返回 401
   - 无效签名 Token 请求，验证返回 401
   - 无权限角色的 Token 请求，验证返回 403
   - Token 中缺少必要 scope/permission，验证返回 403

### 5. 依赖数据状态测试

- 操作不存在的资源（如 ID 不存在），验证返回 404
- 重复创建唯一约束资源，验证返回 409
- 已删除资源的后续操作（GET 返回 404、PUT 返回 404 或重新创建）
- 跨租户/跨用户资源访问，验证返回 403

### 6. 执行与结果收集

1. 对每个测试用例：
   a. 通过 `api_server` MCP Server 发送请求
   b. 记录 HTTP 状态码、响应体、响应时间
   c. 验证 HTTP 状态码符合预期
   d. 验证响应体格式与错误规范一致（如 `{ "error": { "code": "...", "message": "..." } }`）
2. 涉及数据库写操作的用例，通过 `database_server` MCP Server 验证数据一致性
3. 汇总所有用例结果，按 Endpoint 分组统计通过率

## 断言策略

### Schema 校验

- 正向用例：响应 JSON Schema 严格匹配 OpenAPI 规范定义（字段名、类型、必填约束、嵌套结构）
- 异常用例：错误响应体必须包含结构化的 `error` 对象，含 `code`（字符串）和 `message`（可读描述）字段
- 枚举字段返回值必须在 enum 定义范围内

### 业务逻辑校验

- 正向用例：创建资源后 GET 返回完整数据，字段值与请求一致
- 更新用例：PUT/PATCH 后 GET 返回更新后的值，未更新字段保持不变
- 删除用例：DELETE 后 GET 返回 404，关联资源处理符合业务规则
- 列表用例：分页参数生效，返回数据总量与 total 字段一致
- 幂等性：相同 PUT/DELETE 请求执行两次，结果一致（无副作用）

### 性能基线

- 单个 API 请求响应时间不超过 5s（正向用例）
- 边界值/异常值用例响应时间不超过 10s
- 批量操作（如 POST /batch）响应时间不超过 30s
- 正向用例 P95 响应时间不超过历史基线的 1.5 倍（性能退化检测）

### 综合通过率

- 正向用例通过率必须 100%
- 边界值/异常值用例允许预知的合理失败（如特定格式校验未实现），但必须逐一记录并说明原因
- 整体通过率低于 95% 视为回归测试未通过

## 失败处理

### 自动重试

- 网络超时/连接重置（非业务逻辑错误）：自动重试（指数退避 2s→4s→8s，最多 3 次），记录重试过程
- 5xx 错误（非输入引起）：自动重试 1 次，区分偶发与稳定复现

### 根因分析触发

- 正向用例失败：标记为 `bug` 类别缺陷，优先级 `critical`，触发 Analyzer Agent 根因分析，阻断后续依赖该端点的用例
- 边界/异常用例与预期状态码不符：
  - 预期 4xx 但得到 2xx：标记为 `bug` 类别缺陷，优先级 `major`，说明输入校验缺失
  - 预期 2xx 但得到 4xx/5xx：标记为 `bug` 类别缺陷，优先级 `critical`，说明功能退化，触发根因分析
- 鉴权测试失败（匿名可访问需鉴权的端点）：标记为 `bug` 类别缺陷，优先级 `critical`，安全漏洞，触发根因分析
- 历史缺陷对应的回归用例失败：标记为 `bug` 类别缺陷，优先级 `critical`，关联原始缺陷 ID，说明回归
- 性能基线退化（P95 超过历史 1.5 倍）：标记为 `bug` 类别缺陷，优先级 `major`，记录性能对比数据
- 5xx 错误（稳定复现）：标记为 `bug` 类别缺陷，优先级 `critical`，触发根因分析
- 环境类问题（不限于单个端点）：标记为 `environment` 类别缺陷，暂停执行，通知 Planner Agent 决策

### 知识闭环

- 测试执行完成后，将新发现的缺陷模式记录回 `defect_history` RAG Collection，形成知识闭环（越用越准）
- 新增 Endpoint 参数组合的测试结果记录到 `api_docs` Collection 补充测试覆盖信息
