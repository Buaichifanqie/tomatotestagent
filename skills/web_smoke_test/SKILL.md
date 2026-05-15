---
name: web_smoke_test
version: "1.0.0"
description: Web 页面冒烟测试技能，验证核心流程可用性
trigger: "web.*smoke|smoke.*web|Web 冒烟|页面测试|page.*test"
required_mcp_servers:
  - playwright_server
required_rag_collections:
  - req_docs
  - locator_library
---

## 目标

对被测 Web 应用的核心页面执行快速冒烟测试，验证页面加载正常、关键交互元素可见、无 JavaScript 运行时错误，确保核心用户流程通畅。

## 操作流程

1. 从 `req_docs` RAG Collection 检索目标页面的需求描述，提取核心页面列表和关键用户流程
2. 从 `locator_library` RAG Collection 检索页面关键元素的定位器信息（CSS Selector / XPath）
3. 对每个目标页面：
   a. 通过 `playwright_server` MCP Server 打开页面
   b. 等待页面加载完成（`networkidle` 状态），设置超时 30s
   c. 截图保存初始加载状态至测试报告
   d. 验证页面 HTTP 状态码为 200（非 4xx/5xx）
   e. 验证关键元素（导航栏、搜索框、主内容区、页脚）可见（`isVisible()` 返回 true）
   f. 检查浏览器控制台日志，捕获 error 和 warning 级别消息
4. 对核心用户流程（如登录、搜索、表单提交）：
   a. 按定位器库中的交互步骤依次操作
   b. 每步操作后截图记录中间状态
   c. 验证每步操作后的页面状态符合预期（如跳转后的 URL、弹窗可见性）
5. 收集所有页面的加载性能指标（FCP、LCP、DOMContentLoaded 时间）

## 断言策略

- 页面 HTTP 状态码必须为 200（非重定向状态码 3xx 也视为通过，但需记录）
- 所有关键元素必须可见（`isVisible()` 返回 true）
- 页面加载时间不超过 30s（以 `networkidle` 为完成标志）
- 浏览器控制台无 error 级别日志（warning 级别仅记录，不阻断）
- 核心用户流程的每步操作后页面状态与预期一致
- FCP（First Contentful Paint）不超过 3s，LCP（Largest Contentful Paint）不超过 5s

## 失败处理

- 页面加载超时（>30s）：自动重试 1 次，仍失败则标记为 `environment` 类别缺陷，记录网络/服务端耗时详情
- 页面 HTTP 状态码非 200：记录实际状态码和响应头，标记为 `bug` 或 `environment` 类别缺陷（取决于 4xx 或 5xx）
- 关键元素不可见：记录缺失元素的名称和预期定位器，截图标记不可见区域，标记为 `bug` 类别缺陷
- 控制台 JavaScript error：捕获并记录错误消息、堆栈轨迹、发生页面 URL，标记为 `bug` 类别缺陷
- 核心流程中断（某步操作后页面状态不符合预期）：记录中断步骤和当前页面状态截图，标记为 `bug` 类别缺陷，优先级 `critical`
- 定位器失效（元素未找到）：自动尝试备用定位策略（CSS→XPath→文本匹配），全部失败则记录定位器失效事件，标记为 `bug` 类别缺陷
