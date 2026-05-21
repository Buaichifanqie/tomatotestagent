# App 测试视觉理解改造与智能导航设计

## 1. 背景

当前 vibe-ai-agent 的 App 测试完全依赖 Appium XML 页面源码来理解手机界面（`app_get_source` 获取 UI 树，解析 `resource-id`、`content-desc`、`text` 等属性来定位元素）。这种方式的局限性：

- **无法理解图像内容**：截图仅用于错误记录，不作为理解界面的依据
- **无法处理视觉信息**：图标、图片按钮、无 text 属性的元素难以定位
- **无法智能导航**：目标在当前屏幕不存在时（如快手在第二屏），agent 不知道滑动寻找

## 2. 目标

1. **引入视觉理解能力**：通过截图 + 多模态大模型（GLM-4.6V-Flash）让 agent 能"看懂"手机界面
2. **实现坐标级交互**：多模态模型返回元素坐标，agent 通过 Appium/ADB 点击对应位置
3. **智能滑动导航**：目标不在当前屏幕时，AI 分析方向 + 规则遍历的混合策略
4. **保留审计轨迹**：测试过程的截图和录屏自动保存，便于问题排查

## 3. 架构

### 3.1 整体架构

```
Agent (LLM) ── MCP Tool Calls (经 Gateway 审计) ──→ Gateway (MCP Registry)
                                                       │
                                          ┌────────────┼────────────┐
                                          ▼            ▼            ▼
                                   vision_server  appium_server  其他 Server
                                    (NEW)        (增强)         (playwright..)
                                          │            │
                                          ▼            ▼
                                   GLM-4.6V     Appium REST
                                   -Flash API   :4723 → ADB → Android
```

### 3.2 Vision MCP Server（新增）

**位置**：`testagent/mcp_servers/vision_server/`

**文件结构**：

```
vision_server/
├── __init__.py      导出 VisionMCPServer
├── __main__.py      MCP stdio 入口点
├── server.py        VisionMCPServer 类（继承 BaseMCPServer，工具注册与路由）
└── tools.py         工具函数实现（调用 GLM API）
```

**工具 1：`vision_find_element`**

```
输入:
  image: str       (base64 编码的 PNG 截图)
  target: str      (自然语言描述要寻找的目标，如"美团 app 图标")
  context: str     (可选，之前的屏幕描述，辅助导航决策)

输出:
  found: bool                      (是否找到)
  center: {x, y} | null           (元素中心坐标，用于点击)
  bounds: {x1,y1,x2,y2} | null    (元素边界框)
  suggestion: str | null           (未找到时的导航建议，如 "swipe_left")
  description: str                 (模型对该区域的描述)
```

**工具 2：`vision_describe_screen`**

```
输入:
  image: str       (base64 编码的 PNG 截图)

输出:
  elements: list    (可交互元素列表，每项含描述和大致位置)
  layout: str       (屏幕整体布局描述)
  suggestions: list (可能的导航操作建议)
```

### 3.3 GLM API 集成

- **端点**：`https://open.bigmodel.cn/api/paas/v4/chat/completions`
- **模型**：`glm-4.6v-flash`
- **认证**：Bearer Token（API Key 从配置文件读取）
- **图片输入**：OpenAI 兼容格式，`image_url` 以 `data:image/png;base64,{base64}` 传入
- **参数**：`temperature=0.1`（精确任务使用低温度）

### 3.4 Appium MCP Server 增强（现有）

在 `appium_server` 中新增两个工具：

**工具：`app_start_recording`**
```
输入: session_id: str
输出: { status: "recording" }
```

**工具：`app_stop_recording`**
```
输入: session_id: str
输出: { video_base64: str, file_path: str }
```

底层使用 Appium 的 `startRecordingScreen` / `stopRecordingScreen` API。

### 3.5 AppiumRunner 增强（现有）

在 `AppiumRunner.execute()` 中，每次 action 执行后自动截取一张截图，保存至 `artifacts/screenshots/{timestamp}_{action_index}.png`。无论测试通过还是失败，截图都保留。

### 3.6 录屏工具实现细节

Android 录屏通过 Appium 的 `mobile: startRecordingScreen` 和 `mobile: stopRecordingScreen` 实现：

```python
# 开始录屏
driver.execute_script("mobile: startRecordingScreen", {
    "timeLimit": 180,        # 最大录制时长（秒）
    "videoType": "h264",     # 视频编码
    "videoQuality": "medium", # 画质（low/medium/high）
    "bitRate": 4000000       # 比特率
})

# 停止录屏，返回 base64 编码的视频
video_base64 = driver.execute_script("mobile: stopRecordingScreen")
```

录屏文件保存到 `artifacts/recordings/{session_id}_{timestamp}.mp4`。

## 4. 智能导航策略（混合模式）

当 `vision_find_element` 返回 `found: false` 时：

```
Step 1 - AI 分析方向
  Agent 调用 vision_find_element 时通过 context 参数提供上文
  → GLM 分析截图并返回 suggestion
  → suggestion 取值: "swipe_left" / "swipe_right" / "swipe_up" / "swipe_down" / null

Step 2 - 执行滑动
  → Agent 调用 appium_server 的 app_swipe 执行滑动
  → 等待屏幕稳定（~1s）
  → 再次截图 → 再次调用 vision_find_element
  → 找到 → 返回坐标，继续流程

Step 3 - AI 不确定时按规则遍历
  默认顺序：向左划 → 向右划 → 向上划 → 向下划
  每个方向最多执行 1 次滑动后截图重试

Step 4 - 全部方向耗尽 → 报告 "目标未在当前设备找到"
```

**约束**：
- 每个方向最多尝试 1 次
- 每次滑动后自动截图保存到 artifacts
- 导航历史记录在测试会话的 artifacts 元数据中

## 5. 配置文件

新增 `configs/vision_config.json`：

```json
{
  "api_key": "",
  "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
  "model": "glm-4.6v-flash",
  "timeout": 30,
  "max_retries": 3
}
```

API Key 通过配置文件注入，禁止硬编码在代码中。

## 6. Agent 系统提示增强

在系统提示中增加以下能力说明：

- Agent 拥有视觉理解能力，可通过截图理解手机界面
- 当需要定位元素时，优先使用 `vision_find_element` 获取坐标
- 当需要了解当前屏幕全貌时，使用 `vision_describe_screen`
- 元素在当前屏幕找不到时，通过智能导航（AI 分析 + 规则滑动）继续寻找
- 视觉分析结果与 XML 页面源码可互为补充

## 7. 现有文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `testagent/mcp_servers/vision_server/__init__.py` | 新建 | 包入口 |
| `testagent/mcp_servers/vision_server/__main__.py` | 新建 | MCP stdio 入口 |
| `testagent/mcp_servers/vision_server/server.py` | 新建 | Server 主类 |
| `testagent/mcp_servers/vision_server/tools.py` | 新建 | 工具函数实现 |
| `testagent/mcp_servers/appium_server/tools.py` | 修改 | 新增录屏工具 |
| `testagent/mcp_servers/appium_server/server.py` | 修改 | 注册录屏工具 |
| `testagent/harness/runners/appium_runner.py` | 修改 | 每次 action 后自动截图 |
| `testagent/gateway/mcp_registry.py` | 修改 | 注册 vision_server |
| `testagent/config/settings.py` | 修改 | vision 配置项 |
| `configs/vision_config.json` | 新建 | 视觉模型配置 |
| `testagent/cli/ask.py` | 修改 | 系统提示增强 |
| `AGENTS.md` | 修改 | 更新架构文档 |

## 8. 测试计划

1. **单元测试**：
   - Vision MCP Server 工具函数（mock GLM API）
   - 坐标解析逻辑
   - 导航策略逻辑

2. **集成测试**：
   - Vision Server + Appium Server 联合调用
   - 真实截图 + GLM API 调用

3. **E2E 测试**：
   - 完整流程：截图 → 视觉分析 → 坐标点击 → 操作验证
   - 智能导航：元素不在第一屏时的滑动寻找
