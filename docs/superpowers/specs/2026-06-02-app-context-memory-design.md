# App Context Memory — 设计文档

> 版本：v1.0
> 状态：决策已定，待实施
> 核心原则：越用越聪明，但每一步学习都经人类把关

---

## 1. 概述

### 1.1 解决什么问题

当前 Agent 生成的测试用例执行完即丢弃，每次为新 App 或同一 App 的新需求生成 case 时都从零开始。App Context Memory 让系统具备「记忆」能力——记住历史用例、App 文档、从用户修改中学到的经验，并在下次生成时作为上下文注入，提升生成质量。

### 1.2 核心设计决策

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 命名 | **App Context Memory**（应用上下文记忆） |
| 2 | 架构 | 三层：DB 结构化 + 向量语义检索 + 全文关键词检索，复用现有基础设施 |
| 3 | 隔离 | Per-App namespace，通过 metadata `app_id` 过滤 |
| 4 | 学习模式 | 半自动：LLM 提取 + 人类把关 |
| 5 | 信任演进 | 渐进式，积累标注数据后逐步走向全自动 |

---

## 2. 数据模型

### 2.0 App 标识

`app_id` 是 Per-App 隔离的核心维度，格式为 Android 逆域名（如 `com.bilibili.app`）或 iOS Bundle ID。

**输入途径**（优先级从高到低）：

| 途径 | 示例 | 说明 |
|------|------|------|
| CLI 参数 | `testagent app plan "测试搜索" --app-id com.bilibili.app` | 最高优先级 |
| 配置文件 | 项目根目录 `testagent.yaml` 中 `default_app_id` | 项目级默认值 |
| 自动检测 | `adb shell pm list packages -3` + LLM 匹配 | 已有实现，复用 |
| 交互确认 | 首次为某 App 生成 case 时，CLI 提示输入 | fallback |

`app_id` 一旦确定，在同一 plan 流程中贯穿所有存储和检索操作。

### 2.1 TestCaseRecord — 测试用例记录

```python
@dataclass
class TestCaseRecord:
    id: str
    app_id: str                       # "com.bilibili.app"
    app_version: str                  # "7.45.0"
    case_content: str                 # 完整用例内容（自然语言）
    case_steps: list[dict]            # 结构化步骤列表
    source: str                       # "ai_generated" | "user_modified" | "manual"
    original_case_id: str | None      # 修改来源，user_modified 时指向原始 case
    modification_delta: str | None    # LLM 总结的修改摘要
    confidence: float                 # 0.0~1.0，初始值由 source 决定
    tags: list[str]                   # ["搜索", "视频播放", "弹幕"]
    scope: str                        # "app_local" | "global"，预留跨 App
    created_at: datetime
    updated_at: datetime              # 最后修改时间
    last_validated_version: str | None  # 上次验证通过时的 App 版本，None 表示未验证
    execution_count: int = 0          # 执行次数
    pass_count: int = 0               # 通过次数
```

**初始置信度**：

| source | 初始 confidence |
|--------|----------------|
| manual | 0.95 |
| user_modified | 0.85 |
| ai_generated | 0.60 |

**动态置信度**（执行后更新）：

```python
def update_confidence(record: TestCaseRecord) -> float:
    if record.execution_count == 0:
        return record.confidence
    pass_rate = record.pass_count / record.execution_count
    n = record.execution_count
    return record.confidence * (1 / (1 + 0.1 * n)) + pass_rate * (0.1 * n / (1 + 0.1 * n))
```

### 2.2 LearnedPattern — 学习经验

```python
@dataclass
class LearnedPattern:
    id: str
    app_id: str
    app_version: str                  # 经验来源版本
    pattern: str                      # 经验描述（自然语言）
    pattern_type: str                 # "behavior" | "workaround" | "anti_pattern" | "failure_mode"
    source_case_id: str               # 来源 case ID
    source_type: str                  # "modification_delta" | "failure_analysis" | "manual_entry"
    confidence: float                 # 0.0~1.0
    scope: str                        # "app_local" | "global"
    created_at: datetime
    review_status: str                # "pending" | "approved" | "rejected"
    review_reason: str | None         # 忽略原因（负样本训练数据）
    occurrence_count: int = 1         # 同一模式被观察到的次数
```

**置信度初始值**：

| source_type | 初始 confidence |
|-------------|----------------|
| manual_entry | 0.95 |
| modification_delta（用户确认） | 0.80 |
| failure_analysis | 0.70 |

**重复模式叠加**：如果提取到的新经验与已有经验语义相似度 > 0.9，不新增，而是将已有经验的 `occurrence_count += 1` 并提升置信度：

```python
updated_confidence = min(1.0, existing.confidence + 0.05)
```

**写入流程**（每次写入新经验前必须执行）：

```
1. 新经验 embedding
2. 在 app_learned_patterns 中检索相似经验（similarity > 0.9）
3. 如果命中 → 叠加 occurrence_count，提升 confidence（不新增）
4. 如果未命中 → 新增记录
```

这是写入流程的必要步骤，不是可选优化。成本：一次向量检索 + 一次写入。

### 2.3 RetrievalTrace — 检索追踪

```python
@dataclass
class RetrievalTrace:
    id: str
    app_id: str
    query: str                        # 检索 query
    query_stage: str                  # "stage1_app_context" | "stage2_case_context" | "single_batch"
    retrieved_items: list[dict]       # [{id, type, score, version, confidence}]
    generated_case_ids: list[str]     # 最终生成的 case ID 列表
    adoption_score: float | None      # 生成内容与检索结果的重叠度（见下方公式）
    created_at: datetime
```

**adoption_score 计算方式**：

```python
def compute_adoption_score(generated_cases: list[str], retrieved_items: list[dict]) -> float:
    """
    计算生成内容对检索结果的采纳度。
    方法：对每条检索结果，检查生成内容是否包含其关键信息（语义相似度 > 阈值）。
    返回被采纳的检索结果占比。
    """
    if not retrieved_items:
        return 0.0

    adopted = 0
    generated_text = " ".join(generated_cases)
    for item in retrieved_items:
        sim = cosine_similarity(embed(generated_text), embed(item["content"]))
        if sim > 0.85:  # 阈值可调
            adopted += 1

    return adopted / len(retrieved_items)
```

Phase 1 先记 `adoption_score=None`（不计算），Phase 3 补计算逻辑。需要额外 embedding 调用，成本可控。

### 2.4 AppVersion — 版本注册

```python
@dataclass
class AppVersion:
    app_id: str                       # PK
    current_version: str              # "7.46.0"
    updated_at: datetime
    updated_by: str                   # "cli" | "doc_upload" | "execution_infer"
```

---

## 3. 三层架构分工

```
┌─────────────────────────────────────────────────────────────┐
│                    Query / Search Layer                      │
│           (用户搜索、Agent检索、Case管理界面)                  │
├──────────────────┬──────────────────┬───────────────────────┤
│  结构化存储       │   向量检索(RAG)   │   全文检索             │
│  PostgreSQL      │   向量数据库      │   BM25/ES             │
│                  │                  │                       │
│  ● Case 元数据   │  ● Case 语义检索 │  ● 关键词精确搜索      │
│  ● 版本追踪      │  ● 相似 Case 推荐│  ● App 文档搜索       │
│  ● 执行结果      │  ● 跨 App 经验   │  ● 变更日志检索       │
│  ● 高频统计      │    迁移检索      │                       │
│  ● 失败模式统计  │                  │                       │
├──────────────────┴──────────────────┴───────────────────────┤
│              统一数据层 (Case + 文档 + 经验)                   │
└─────────────────────────────────────────────────────────────┘
```

**查询路由规则**：

| 查询类型 | 走哪层 | 示例 |
|---------|--------|------|
| 语义相似检索 | 向量 + 全文（混合） | "B站搜索相关的历史用例" |
| 精确关键词搜索 | 全文 | "弹幕" |
| 聚合统计 | DB (SQL) | "B站 Top5 高频测试场景" |
| 版本过滤 | DB (SQL) | "v7.45 的所有用例" |
| 失败模式分析 | DB (SQL) | "近 7 天执行失败率 > 50% 的用例" |

---

## 4. 检索策略

### 4.1 Phase 1：单次批量检索

```
app_plan 启动
    │
    ▼
  一次性检索
    ├── RAG: 历史案例 (top_k=5) + App文档 (top_k=3) + 学习经验 (top_k=3)
    │   query = 用户需求全文
    │   filters = {"app_id": current_app_id}
    └── DB:  高频场景统计 (SQL)
    │
    ▼
  后处理：版本衰减 × 时间衰减 × 置信度加权
    │
    ▼
  注入 prompt → 一次性生成所有用例
    │
    ▼
  记录 RetrievalTrace
```

**Prompt Token 预算**（32K 窗口）：

| 内容 | Token 预算 | 说明 |
|------|-----------|------|
| 历史 case | 3K (~3-5条) | 最核心的参考 |
| App 文档 | 2K (~2-3段) | 版本更新、UI 变更 |
| 学习经验 | 1.5K (~5-8条) | 按置信度排序 |
| 高频统计 | 0.5K | 纯文本摘要 |
| 保留缓冲 | 25K | prompt 模板 + 输出 |

### 4.2 Phase 2：两阶段检索 + 批量精炼

```
app_plan 启动
    │
    ▼
  Stage 1: App 级上下文（广谱）
    ├── RAG: App 文档/更新日志 (top_k=3)
    │   query = 用户需求全文
    └── DB:  高频场景 + 近期失败模式 (SQL)
    │
    ▼
  LLM 生成初始用例列表
    │
    ▼
  Stage 2: 用例级上下文（精准，批量）
    ├── RAG: 所有初始用例拼接为 query
    │   → 检索历史相似案例 (top_k=5) + 经验 (top_k=3)
    │
    ▼
  LLM 批量精炼（去重、补充步骤、参考经验调整）
    │
    ▼
  记录 RetrievalTrace（对比两个 stage 的 adoption_score）
```

**A → B 升级路径**：Stage 1 逻辑不变，只是把原来混在一起的「历史案例检索」挪到 Stage 2，用更精准的 query 重新检索。拆分 + 加一次调用，不需要重写。

### 4.3 冲突消解

不在代码里硬编码优先级，在 prompt 中标注来源和版本，让 LLM 自行判断：

```
以下是从历史经验中检索到的内容，请注意核对版本时效性：

[历史用例 - v7.45] TC-SEARCH-007: 搜索前先清除历史...
[App 文档 - v7.46] 修复了搜索框历史残留问题...
[学习经验] B站搜索页会保留历史搜索词...（来源版本: v7.45, 置信度: ★★★★）

请优先参考最新版本的文档信息，历史用例和经验仅作为参考。
```

---

## 5. 生命周期管理

### 5.1 衰减公式

```
最终检索分数 = 原始相似度 × version_weight × time_weight × confidence_weight
```

### 5.2 版本衰减

```python
def get_effective_version(record) -> str:
    """返回有效版本：验证通过时的版本优先，否则用创建时版本。"""
    return record.last_validated_version or record.app_version

def version_weight(current_version: str, record, base: float) -> float:
    effective = get_effective_version(record)
    gap = parse_version_gap(current_version, effective)
    if gap == 0:
        return 1.0
    return base ** gap

def parse_version_gap(current: str, item: str) -> int:
    """返回次版本号差距，如 7.46 vs 7.43 = 3"""
    cur = [int(x) for x in current.split(".")]
    itm = [int(x) for x in item.split(".")]
    return abs(cur[1] - itm[1])  # 只看次版本号
```

各类型 `base` 值：

| 数据类型 | version_base | 说明 |
|---------|-------------|------|
| App 文档 | 0.0（硬切） | 旧版文档直接误导，跨版本不召回 |
| 用户修改 case | 0.8 | 核心逻辑通常跨版本通用 |
| AI 生成 case | 0.6 | 本身置信度低，过期更快 |
| 学习经验 | 0.7 | 中等衰减 |
| 失败模式（抽象） | 0.95 | 模式跨版本复现，几乎不衰减 |
| 具体失败记录 | 0.6 | 跟版本走 |

### 5.3 时间衰减

仅对学习经验和具体失败记录生效，case 依靠版本衰减足够：

```python
def time_weight(created_at: datetime, now: datetime,
                monthly_decay: float, floor: float) -> float:
    months = (now - created_at).days / 30
    return max(floor, 1.0 - monthly_decay * months)
```

| 数据类型 | monthly_decay | floor |
|---------|-------------|-------|
| 学习经验 | 0.05 | 0.3 |
| 失败模式 | 0.01 | 0.7 |
| 具体失败记录 | 0.05 | 0.3 |

### 5.4 人工续命

当用户在 v7.46 重新跑通了基于 v7.45 的旧 case 时：

```python
record.last_validated_version = current_version  # e.g. "7.46.0"
# version_weight 将用 last_validated_version="7.46.0" 计算差距，而非 app_version="7.45.0"
```

### 5.5 版本更新入口

```bash
testagent memory set-version <app_id> <version>
```

上传新文档时也可顺带推进版本，但 CLI 主动声明为主途径。

---

## 6. Delta 提取边界

### 6.1 触发流程

```
用户修改用例并保存
    │
    ▼
步骤有变更？ ─── 否 ──→ 静默保存
    │ 是
    ▼
变更是否有学习价值？
    ├── 仅 value/wait 调整 → 静默保存
    ├── 仅 typo/格式 → 静默保存
    └── action/target/流程变更 → 触发提取确认框
```

### 6.2 判定规则

```python
def should_trigger_extraction(original: TestCase, modified: TestCase) -> bool:
    # 元数据变更（标题、优先级）不触发
    if _steps_equal(original.steps, modified.steps):
        return False
    return _has_meaningful_step_change(original.steps, modified.steps)

def _has_meaningful_step_change(old_steps, new_steps) -> bool:
    # 1. 步骤数量变了
    if len(old_steps) != len(new_steps):
        return True
    # 2. action 类型变了
    for old, new in zip(old_steps, new_steps):
        if old.action != new.action:
            return True
    # 3. target 实质性变化
    for old, new in zip(old_steps, new_steps):
        if _is_meaningful_target_change(old.target, new.target):
            return True
    # 4. 步骤顺序变了
    if [s.action for s in old_steps] != [s.action for s in new_steps]:
        return True
    return False

def _is_meaningful_target_change(old_target: str, new_target: str) -> bool:
    if old_target == new_target:
        return False
    # 编辑距离 <= 2 且长度差 <= 2 -> 大概率 typo
    if edit_distance(old_target, new_target) <= 2 and \
       abs(len(old_target) - len(new_target)) <= 2:
        return False
    # 一个包含另一个 -> 描述细化，有学习价值
    if old_target in new_target or new_target in old_target:
        return True
    # 其他变化 -> 有学习价值
    return True
```

### 6.3 半自动确认交互

```
+-----------------------------------------------------+
|  检测到用例修改                                       |
|                                                      |
|  原始步骤 3: 点击搜索按钮                              |
|  修改为:    先点击清除历史按钮，再点击搜索按钮           |
|                                                      |
|  提取的经验:                                          |
|  +-------------------------------------------------+ |
|  | B站搜索页会保留历史搜索词，测试时需先清除搜索      | |
|  | 历史再输入新关键词，否则搜索结果会被历史影响       | |
|  +-------------------------------------------------+ |
|                                                      |
|  [保存经验]  [修改后保存]  [忽略]                       |
|                                                      |
|  忽略原因（可选）:                                     |
|  ( ) 本次修改无通用价值  ( ) 仅适配特定环境  ( ) 其他   |
+-----------------------------------------------------+
```

| 操作 | 效果 | 数据价值 |
|------|------|---------|
| 保存 | 写入经验池，review_status="approved" | 正样本 |
| 修改后保存 | 用户修正后写入，review_status="approved" | 正样本 + 修正信号 |
| 忽略 | 不写入，但记录忽略原因到 source_case 的元数据 | **负样本** |

**UI 载体**：Phase 2 使用 CLI 交互（`questionary` 库，支持选择列表和文本输入），与现有 `testagent/cli/plan.py` 的编辑器交互风格一致。后续有 Web UI 时再迁移渲染层，逻辑层不变。

---

## 7. Per-App 知识空间分区

```
App Context Memory
├── com.bilibili.app
│   ├── App 文档层 (collection: app_documentation, filter: app_id)
│   │   ├── 使用说明
│   │   ├── 版本更新日志 (v7.46, v7.45, ...)
│   │   └── UI 结构描述
│   ├── Case 经验层 (collection: app_test_cases, filter: app_id)
│   │   ├── AI 生成的用例
│   │   ├── 用户修改的用例 (含 delta)
│   │   └── 执行结果 & 通过率
│   ├── 学习经验层 (collection: app_learned_patterns, filter: app_id)
│   │   ├── 行为模式 (behavior)
│   │   ├── 绕行方案 (workaround)
│   │   ├── 反面模式 (anti_pattern)
│   │   └── 失败模式 (failure_mode)
│   └── 统计层 (DB 聚合查询)
│       ├── 高频测试场景 Top-N
│       ├── 常见失败模式
│       └── 用户修改模式
│
├── com.tencent.qq
│   └── ... (同构)
│
└── Cross-App 共享层
    ├── scope="global" 的学习经验
    └── 通用测试模式 (登录、支付、分享...)
```

**Collection 规划**（新建 3 个，与现有 6 个独立）：

| Collection | 状态 | chunk_size | metadata 关键字段 | 说明 |
|-----------|------|-----------|-----------------|------|
| req_docs | 现有 | 512 | project_id | 需求文档 |
| api_docs | 现有 | 512 | project_id | API 文档 |
| defect_history | 现有 | 512 | project_id | 缺陷历史 |
| test_reports | 现有 | 768 | project_id | 测试报告 |
| locator_library | 现有 | 256 | project_id | 定位器库 |
| failure_patterns | 现有 | 256 | project_id | 失败模式 |
| app_documentation | **新建** | 768 | app_id, app_version, doc_type | App 文档/更新日志 |
| app_test_cases | **新建** | 512 | app_id, app_version, case_type, confidence | 历史测试用例 |
| app_learned_patterns | **新建** | 256 | app_id, app_version, pattern_type, review_status | 学习经验 |

**为什么新建而不复用**：现有 collection 与新建 collection 语义领域不同（项目级通用知识 vs App 级历史记忆），混在一起会导致向量空间污染。且三种数据的最佳 chunk_size 差异大（768/512/256），合并 collection 无法统一 chunking 策略。

两组 collection 唯一的交叉点是 `failure_patterns`（现有）和 `app_learned_patterns` 中 `pattern_type=failure_mode` 的条目。如需统一检索，在检索层做跨 collection 并行查询即可，不在存储层合并。

---

## 8. 渐进式信任模型

```
阶段 1（当前）: 全部半自动
    每次提取都过人工 -> 积累正/负样本
    |
    v  积累 200+ 条人工标注后
阶段 2: 置信度分轨
    LLM 对自己提取的经验打置信度分
    - 高置信度 (>0.9): 静默写入，review_status="auto_approved"
    - 低置信度: 弹确认框，review_status="pending"
    |
    v  auto_approved 的精确率 > 95% 后
阶段 3: 默认自动 + 可选审查
    经验自动写入，提供「审查本周学习」入口
    |
    v
阶段 4: 全自动
    系统高度可信，人类只在异常时介入
```

---

## 9. CLI 命令设计

```bash
# 版本管理
testagent memory set-version <app_id> <version>

# 知识查询
testagent memory search <app_id> <query> [--type case|doc|pattern] [--top-k 5]
testagent memory stats <app_id>                          # 高频场景、失败模式统计

# 经验管理
testagent memory list-patterns <app_id> [--status pending|approved|rejected]
testagent memory approve <pattern_id>
testagent memory reject <pattern_id> [--reason "..."]
testagent memory add-pattern <app_id> <pattern_text>     # 手动录入

# 文档管理
testagent memory upload-doc <app_id> <file_path> [--doc-type release_note|user_manual|ui_structure]

# 评估
testagent memory trace <app_id> [--days 7]               # 查看检索追踪和 adoption_score
```

---

## 10. 实施计划

### Phase 1：先把 Case 存下来（1-2 天）

**目标**：case 生成后存 DB + 向量化，下次生成同 App 时能检索到历史。

**改动范围**：
- `test_case_generator.py`：生成后新增写入逻辑
- `repository.py`：新增 TestCaseRecord 的 CRUD
- `rag_service.py`：新增写入 app_test_cases collection

**验收标准**：为 B站生成一次 case -> 再为 B站生成同需求 -> prompt 中出现历史 case 参考。

### Phase 2：检索策略 + 学习循环（3-5 天）

**目标**：两阶段检索上线 + Delta 提取 + 半自动确认。

**改动范围**：
- `app_plan_flow.py`：检索逻辑从单次改为两阶段
- 新增 `delta_extractor.py`：修改检测 + LLM 提取
- 新增 `learned_pattern_repository.py`：经验的 CRUD
- CLI：`memory` 子命令组

**验收标准**：用户修改 case -> 弹确认框 -> 保存经验 -> 下次生成时注入。

### Phase 3：生命周期 + 评估（3-5 天）

**目标**：版本衰减、时间衰减、RetrievalTrace 评估面板。

**改动范围**：
- 新增 `retrieval_post_processor.py`：衰减逻辑
- `rag_service.py`：检索后加后处理
- CLI：`memory trace`、`memory stats`

**验收标准**：B站版本从 7.45 升到 7.46 -> 旧版 case 检索分数自动下降 -> 文档硬切到新版。

---

## 11. 预留设计

### 11.1 跨 App 经验迁移

- `scope="global"` 字段已预留
- 迁移时置信度 x 0.5，需人工确认
- Phase 3 后视需求启用

### 11.2 评估指标

| 指标 | 计算方式 | 目标 |
|------|---------|------|
| case 采纳率 | 未修改直接执行的 case / 总生成 case | > 60% |
| case 修改率 | 被用户修改的 case / 总生成 case | < 30% |
| 经验命中率 | 生成时实际采纳的经验 / 注入的经验 | > 40% |
| 检索 adoption_score | 生成内容与检索结果重叠度 | 持续上升 |

Phase 1 起埋点，Phase 3 起出面板。
