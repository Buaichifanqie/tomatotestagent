from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, cast

from testagent.common import get_logger

_logger = get_logger(__name__)


@dataclass
class ScaffoldResult:
    skill_dir: Path
    skill_md_path: Path
    readme_path: Path
    generated_files: list[Path] = field(default_factory=list)


class SkillScaffold:
    TEMPLATES: ClassVar[dict[str, dict[str, object]]] = {
        "api_test": {
            "required_mcp_servers": ["api_server", "database_server"],
            "required_rag_collections": ["api_docs", "defect_history"],
            "body_template": """## 目标

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
""",
        },
        "web_test": {
            "required_mcp_servers": ["playwright_server"],
            "required_rag_collections": ["req_docs", "locator_library"],
            "body_template": """## 目标

对被测 Web 应用的核心页面执行快速冒烟测试，验证页面加载正常、关键交互元素可见、
无 JavaScript 运行时错误，确保核心用户流程通畅。

## 操作流程

1. 从 `req_docs` RAG Collection 检索目标页面的需求描述，提取核心页面列表
2. 从 `locator_library` RAG Collection 检索页面关键元素的定位器信息
3. 对每个目标页面：
   a. 通过 `playwright_server` MCP Server 打开页面
   b. 等待页面加载完成（`networkidle` 状态），设置超时 30s
   c. 截图保存初始加载状态至测试报告
   d. 验证页面 HTTP 状态码为 200
   e. 验证关键元素（导航栏、搜索框、主内容区、页脚）可见
   f. 检查浏览器控制台日志，捕获 error 和 warning 级别消息
4. 对核心用户流程（如登录、搜索、表单提交）：
   a. 按定位器库中的交互步骤依次操作
   b. 每步操作后截图记录中间状态
   c. 验证每步操作后的页面状态符合预期
5. 收集所有页面的加载性能指标（FCP、LCP、DOMContentLoaded 时间）

## 断言策略

- 页面 HTTP 状态码必须为 200
- 所有关键元素必须可见（`isVisible()` 返回 true）
- 页面加载时间不超过 30s
- 浏览器控制台无 error 级别日志
- 核心用户流程的每步操作后页面状态与预期一致
- FCP 不超过 3s，LCP 不超过 5s

## 失败处理

- 页面加载超时（>30s）：自动重试 1 次，仍失败则标记为 `environment` 类别缺陷
- 页面 HTTP 状态码非 200：标记为 `bug` 或 `environment` 类别缺陷
- 关键元素不可见：标记为 `bug` 类别缺陷
- 控制台 JavaScript error：标记为 `bug` 类别缺陷
- 核心流程中断：标记为 `bug` 类别缺陷，优先级 `critical`
- 定位器失效：自动尝试备用定位策略（CSS→XPath→文本匹配）
""",
        },
        "app_test": {
            "required_mcp_servers": ["appium_server"],
            "required_rag_collections": ["req_docs", "locator_library"],
            "body_template": """## 目标

对被测移动应用（iOS/Android）的核心功能和关键用户流程执行快速冒烟测试，验证应用启动正常、核心页面可渲染、关键交互可触达。

## 操作流程

1. 从 `req_docs` RAG Collection 检索目标 App 的需求描述，提取核心功能模块
2. 从 `locator_library` RAG Collection 检索 App 页面元素的定位器信息
3. 通过 `appium_server` MCP Server 启动 App Session，指定平台和设备
4. 执行核心功能冒烟验证：

   a. **应用启动验证**：
      - 启动 App，等待首屏加载完成
      - 验证 Splash 页面正常显示并自动消失
      - 验证首页关键元素可见
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
- 应用首屏在 15s 内加载完成
- 首页所有关键元素必须可见（`isDisplayed()` 返回 true）
- 底部 Tab 导航切换后，目标页面的标志性元素在 5s 内可见
- 核心功能的关键交互元素可点击/可交互
- 应用运行过程中无 ANR 或无预期外的 Crash
- 启动耗时不超过 10s

## 失败处理

- App Session 创建失败：自动重试 1 次，仍失败则标记为 `environment` 类别缺陷
- 首屏加载超时（>15s）：标记为 `bug` 类别缺陷，优先级 `major`
- 元素不可见或不可交互：尝试隐式等待 + 主动轮询，仍失败则标记为 `bug` 类别缺陷
- 导航切换失败：标记为 `bug` 类别缺陷，优先级 `critical`
- App Crash（Session 丢失）：收集 Crash log，标记为 `bug` 类别缺陷，优先级 `critical`
- 定位器失效：自动尝试备用定位策略（Accessibility ID → 资源 ID → XPath → 文本匹配）
""",
        },
        "empty": {
            "required_mcp_servers": [],
            "required_rag_collections": [],
            "body_template": """## 目标

在此填写 Skill 的测试目标。

## 操作流程

1. 在此填写操作步骤
2. 每步应描述具体的测试操作和验证行为

## 断言策略

- 在此填写断言条件和预期结果

## 失败处理

- 在此填写失败场景的处理策略
""",
        },
    }

    VALID_TEMPLATES: ClassVar[frozenset[str]] = frozenset(TEMPLATES.keys())

    def generate(self, name: str, template: str, output_dir: str | Path) -> ScaffoldResult:
        resolved_output = Path(output_dir).resolve()
        template_name = template or "empty"

        if template_name not in self.VALID_TEMPLATES:
            msg = f"Unknown template: {template_name}. Available: {', '.join(sorted(self.VALID_TEMPLATES))}"
            raise ValueError(msg)

        tmpl = self.TEMPLATES[template_name]
        skill_dir = resolved_output / name
        skill_dir.mkdir(parents=True, exist_ok=True)

        description = self._default_description(name, template_name)
        trigger = self._default_trigger(name)

        front_matter_lines = [
            "---",
            f"name: {name}",
            """version: "1.0.0\"""",
            f'description: "{description}"',
            f'trigger: "{trigger}"',
        ]

        mcp_servers: list[str] = cast("list[str]", tmpl["required_mcp_servers"])
        rag_collections: list[str] = cast("list[str]", tmpl["required_rag_collections"])

        if mcp_servers:
            front_matter_lines.append("required_mcp_servers:")
            for s in mcp_servers:
                front_matter_lines.append(f"  - {s}")
        else:
            front_matter_lines.append("required_mcp_servers: []")

        if rag_collections:
            front_matter_lines.append("required_rag_collections:")
            for c in rag_collections:
                front_matter_lines.append(f"  - {c}")
        else:
            front_matter_lines.append("required_rag_collections: []")

        front_matter_lines.append("---")
        front_matter_lines.append("")

        body = str(tmpl["body_template"])
        skill_md_content = "\n".join(front_matter_lines) + body

        skill_md_path = skill_dir / "SKILL.md"
        skill_md_path.write_text(skill_md_content, encoding="utf-8")

        readme_content = self._generate_readme(name, template_name, mcp_servers, rag_collections)
        readme_path = skill_dir / "README.md"
        readme_path.write_text(readme_content, encoding="utf-8")

        generated_files = [skill_md_path, readme_path]

        _logger.info(
            "Skill scaffold created",
            extra={
                "extra_data": {
                    "name": name,
                    "template": template_name,
                    "dir": str(skill_dir),
                    "files": [str(p) for p in generated_files],
                }
            },
        )

        return ScaffoldResult(
            skill_dir=skill_dir,
            skill_md_path=skill_md_path,
            readme_path=readme_path,
            generated_files=generated_files,
        )

    def _default_description(self, name: str, template_name: str) -> str:
        descriptions = {
            "api_test": f"{name}: API 测试技能，覆盖核心 Endpoint 的正向验证",
            "web_test": f"{name}: Web 页面测试技能，验证核心流程可用性",
            "app_test": f"{name}: App 核心流程测试技能，验证移动端关键功能可用性",
            "empty": f"{name}: 自定义测试技能",
        }
        return descriptions.get(template_name, f"{name}: 自定义测试技能")

    def _default_trigger(self, name: str) -> str:
        return f"{name}.*test|test.*{name}|{name}"

    def _generate_readme(
        self,
        name: str,
        template_name: str,
        mcp_servers: list[str],
        rag_collections: list[str],
    ) -> str:
        lines = [
            f"# {name}",
            "",
            "## 概述",
            "",
            f"基于 `{template_name}` 模板生成的测试 Skill，{self._default_description(name, template_name)}。",
            "",
            "## 前置条件",
            "",
            "### 必要的 MCP Servers",
            "",
        ]
        if mcp_servers:
            for s in mcp_servers:
                lines.append(f"- `{s}`")
        else:
            lines.append("*无*")

        lines += [
            "",
            "### 必要的 RAG Collections",
            "",
        ]
        if rag_collections:
            for c in rag_collections:
                lines.append(f"- `{c}`")
        else:
            lines.append("*无*")

        lines += [
            "",
            "## 使用方式",
            "",
            "```bash",
            f"testagent run --skill {name} --env staging",
            "```",
            "",
            "## 文件结构",
            "",
            f"""```
{name}/
├── SKILL.md      # Skill 定义文件（YAML Front Matter + Markdown Body）
└── README.md     # 本文件（使用说明）
```""",
            "",
            "## 自定义指南",
            "",
            "1. 编辑 `SKILL.md` 中的 `trigger` 字段，调整触发模式匹配",
            "2. 修改 `操作流程` 章节，补充具体的测试步骤",
            "3. 调整 `断言策略` 和 `失败处理` 章节，匹配实际业务场景",
            "4. 如有需要，更新 `required_mcp_servers` 和 `required_rag_collections`",
            "",
        ]

        return "\n".join(lines)
