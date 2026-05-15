# my_api_test

## 概述

基于 `api_test` 模板生成的测试 Skill，my_api_test: API 测试技能，覆盖核心 Endpoint 的正向验证。

## 前置条件

### 必要的 MCP Servers

- `api_server`
- `database_server`

### 必要的 RAG Collections

- `api_docs`
- `defect_history`

## 使用方式

```bash
testagent run --skill my_api_test --env staging
```

## 文件结构

```
my_api_test/
├── SKILL.md      # Skill 定义文件（YAML Front Matter + Markdown Body）
└── README.md     # 本文件（使用说明）
```

## 自定义指南

1. 编辑 `SKILL.md` 中的 `trigger` 字段，调整触发模式匹配
2. 修改 `操作流程` 章节，补充具体的测试步骤
3. 调整 `断言策略` 和 `失败处理` 章节，匹配实际业务场景
4. 如有需要，更新 `required_mcp_servers` 和 `required_rag_collections`
