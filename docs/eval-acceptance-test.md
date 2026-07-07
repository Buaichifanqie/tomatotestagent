# 评测系统验收步骤

## 前提条件

- [ ] 模拟器/真机已连接（`adb devices` 看到 `emulator-5554  device`）
- [ ] Appium 已启动且带 `ANDROID_HOME` 环境变量
- [ ] Bilibili APK 已安装（`adb shell pm list packages | grep bili`）
- [ ] 项目 venv 已激活，`python -m testagent eval list` 能运行

---

## 1. 基础：CLI 命令可用性

```bash
# 列出可用套件
python -m testagent eval list
```
**预期：** 显示 "bilibili" 套件，5-7 个任务

```bash
# 查看帮助
python -m testagent eval --help
```
**预期：** 显示 run / generate / list / history 四个命令

```bash
# 查看 run 子命令帮助
python -m testagent eval run --help
```
**预期：** 显示 --device、--appium-url、--filter、--trials 参数

---

## 2. 自动生成评测任务

```bash
# 清理旧数据
rm -rf evals/tasks/bilibili/

# 自动生成
python -m testagent eval generate bilibili
```

**验收：**
- [ ] 输出 "Detecting app package..." → 匹配到 `tv.danmaku.bili`
- [ ] 输出 "Found SKILL.md" → 加载了 Bilibili 技能知识
- [ ] 输出 "Explored 2 pages" → 探索了首页和搜索页
- [ ] 输出 "Generated 6-7 tasks" → 生成了评测任务
- [ ] 检查 `evals/tasks/bilibili/` 下有子目录和 YAML 文件

```bash
ls -R evals/tasks/bilibili/
```

- [ ] 每个 YAML 文件包含 `instruction`、`graders`、`scoring`、`setup` 字段
- [ ] `timeout` 值都在 90-300 之间

---

## 3. 运行评测

### 3.1 单任务快速验证

```bash
python -m testagent eval run bilibili --filter "bilibili_app_launch" --trials 1
```

**验收：**
- [ ] Appium session 创建成功（看到 "Appium session created"）
- [ ] App 启动成功（看到 "App launched"）
- [ ] 评测在 60s 内完成
- [ ] 在 `reports/eval/bilibili/` 下生成报告

```bash
# 查看报告
cat reports/eval/bilibili/eval_bilibili_*/report.md | head -30
```

- [ ] 报告包含：总体概览、逐任务详情、稳定性分析、性能指标

### 3.2 全量运行

```bash
python -m testagent eval run bilibili --trials 1
```

**验收：**
- [ ] 所有 6-7 个任务依次执行
- [ ] 没有 `Grader error` 报错
- [ ] 至少 2-3 个任务通过（pass@k ≥ 30%）
- [ ] 总耗时在 5-10 分钟
- [ ] 报告中的 llm_rubric 评分给出有意义的语义评判

---

## 4. 多设备支持

```bash
# 指定设备运行
python -m testagent eval run bilibili --device emulator-5554 --trials 1 --filter "*launch*"
```

**验收：**
- [ ] 使用 `--device` 参数后，adb 操作在指定设备上执行
- [ ] 不指定 `--device` 时默认使用 `emulator-5554`

---

## 5. 回归测试（单元测试）

```bash
pytest tests/eval/ -v
```

**验收：**
- [ ] 全部 110+ 个测试通过
- [ ] 没有 FAILED 或 ERROR

---

## 6. 边界场景

### 6.1 不存在的 App

```bash
python -m testagent eval generate nonexistentapp
```

**预期：** 报错 "Could not match" 或 "No third-party packages found"

### 6.2 空的 filter

```bash
python -m testagent eval run bilibili --filter "xxxxxx"
```

**预期：** 输出 "No tasks match the filter. Nothing to run."

### 6.3 没有历史记录

```bash
python -m testagent eval history
```

**预期：** 显示空列表或 "未发现历史评测报告"

---

## 7. 通过标准

| 检查项 | 最低要求 |
|--------|---------|
| 单元测试 | 110 passed |
| `eval generate` | 成功生成 6+ 任务 |
| 单任务运行 | 不报 Grader error |
| 全量运行 | pass@k ≥ 30% |
| 报告生成 | 包含完整 7 章节 |
| CLI 命令 | 全部 4 个子命令可用 |
| 设备参数 | --device 正常工作 |
