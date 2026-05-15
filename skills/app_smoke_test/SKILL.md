---
name: app_smoke_test
version: "1.0.0"
description: App 核心流程冒烟测试技能，验证移动端关键功能可用性
trigger: "app.*smoke|smoke.*app|App 冒烟|移动端测试|mobile.*smoke"
required_mcp_servers:
  - appium_server
required_rag_collections:
  - req_docs
  - locator_library
---

## 目标

对被测移动应用（iOS/Android）的核心功能和关键用户流程执行快速冒烟测试，验证应用启动正常、核心页面可渲染、关键交互可触达，确保基本可用性。

## 操作流程

1. 从 `req_docs` RAG Collection 检索目标 App 的需求描述，提取核心功能模块和关键用户流程
2. 从 `locator_library` RAG Collection 检索 App 页面元素的定位器信息（Accessibility ID / XPath / 资源 ID）
3. 通过 `appium_server` MCP Server 启动 App Session，指定平台（iOS/Android）、设备（模拟器/真机）、App 包名
4. 执行核心功能冒烟验证：

   a. **应用启动验证**：
      - 启动 App，等待首屏加载完成
      - 验证 Splash 页面正常显示并自动消失
      - 验证首页关键元素（导航栏、Tab 栏、主内容区）可见
      - 截图记录首页加载状态

   b. **导航流程验证**：
      - 验证底部 Tab 导航可正常切换各一级页面
      - 验证返回手势/按钮行为正常
      - 对每个导航目标页面，截图并验证关键元素可见

   c. **核心功能验证**（根据 `req_docs` 动态识别）：
      - 如包含登录功能：验证登录页面元素可见、输入框可交互
      - 如包含列表功能：验证列表可滑动加载、列表项可点击
      - 如包含搜索功能：验证搜索框可聚焦、搜索可触发结果展示

5. 收集 App 运行性能指标：启动耗时、页面切换耗时、内存占用

## 断言策略

- App Session 创建成功，无 `SessionNotCreatedException`
- 应用首屏在 15s 内加载完成（以首个关键元素可见为标志）
- 首页所有关键元素必须可见（`isDisplayed()` 返回 true）
- 底部 Tab 导航切换后，目标页面的标志性元素在 5s 内可见
- 核心功能的关键交互元素（输入框、按钮、列表项）可点击/可交互
- 应用运行过程中无 ANR（Application Not Responding）或无预期外的 Crash
- 启动耗时不超过 10s（从点击图标到首帧渲染完成）

## 失败处理

- App Session 创建失败：自动重试 1 次（更换设备或重置 App 状态），仍失败则标记为 `environment` 类别缺陷，检查设备连接状态和 App 包完整性
- 首屏加载超时（>15s）：截图记录当前页面状态，标记为 `bug` 类别缺陷，优先级 `major`，说明可能的内存泄漏或启动性能退化
- 元素不可见或不可交互：
  - 尝试隐式等待 + 主动轮询（最长 10s，间隔 500ms）
  - 若仍超时，截图 + 获取页面源码（Page Source）保存至测试报告
  - 标记为 `bug` 类别缺陷，记录缺失元素和期望定位器
  - 如定位器匹配到多个元素，记录 ambiguity 详情，标记为 `bug`
- 导航切换失败（目标页面标志性元素不可见）：截图记录当前页面，标记为 `bug` 类别缺陷，优先级 `critical`
- App Crash（Session 丢失）：收集 Crash log（logcat / syslog）保存至测试报告，标记为 `bug` 类别缺陷，优先级 `critical`
- 定位器失效：自动尝试备用定位策略（Accessibility ID → 资源 ID → XPath → 文本匹配），全部失败则记录定位器失效事件，标记为 `bug` 类别缺陷
