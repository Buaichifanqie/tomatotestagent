import React, { useState } from 'react';
import {
  Table, Card, Tag, Typography, Space, Drawer, Button, Switch,
  Descriptions, message, Alert, Tooltip,
} from 'antd';
import {
  PlusOutlined, PlayCircleOutlined, CodeOutlined,
  CheckCircleOutlined, CloseCircleOutlined, ApiOutlined,
} from '@ant-design/icons';
import type { SkillDefinition, MCPServer } from '@/types';

const { Title, Text } = Typography;

const statusColorMap: Record<string, string> = {
  normal: 'green',
  active: 'green',
  degraded: 'orange',
  inactive: 'default',
};

const mockMCPServers: MCPServer[] = [
  { name: 'api_server', status: 'healthy', version: '1.0.0', endpoint: 'http://localhost:8001', tools_count: 12 },
  { name: 'playwright_server', status: 'healthy', version: '1.2.0', endpoint: 'http://localhost:8002', tools_count: 8 },
  { name: 'jira_server', status: 'healthy', version: '0.9.0', endpoint: 'http://localhost:8003', tools_count: 5 },
  { name: 'database_server', status: 'degraded', version: '1.1.0', endpoint: 'http://localhost:8004', tools_count: 6 },
  { name: 'appium_server', status: 'unhealthy', version: '0.8.0', endpoint: 'http://localhost:8005', tools_count: 3 },
] as unknown as MCPServer[];

type ExtendedSkill = SkillDefinition & {
  trigger_words?: string[];
  detail_content?: string;
  mcp_dependencies: string[];
  rag_collections?: string[];
};

const mockSkills: ExtendedSkill[] = [
  {
    name: 'api_smoke_test',
    version: '1.0.0',
    description: 'API 冒烟测试 - 快速验证核心 API 端点可用性',
    status: 'active',
    trigger: 'keyword_match',
    trigger_words: ['smoke', '冒烟', 'api', 'quick'],
    required_mcp_servers: ['api_server', 'database_server'],
    required_rag_collections: ['api_docs', 'defect_history'],
    mcp_dependencies: ['api_server', 'database_server'],
    rag_collections: ['api_docs', 'defect_history'],
    detail_content: `## API 冒烟测试

### 目标
快速验证核心 API 端点的可用性和基础响应正确性。

### 操作流程
1. 加载 API 文档（api_docs collection）
2. 提取核心端点列表（GET/POST/DELETE）
3. 对每个端点执行基础请求验证
4. 验证 HTTP 状态码和响应结构

### 断言策略
- 响应时间 < 1000ms
- HTTP 状态码 2xx
- 响应体包含必要字段

### 失败处理
- 重试 1 次（网络抖动）
- 连续失败标记为缺陷
`,
  },
  {
    name: 'api_regression_test',
    version: '1.1.0',
    description: 'API 回归测试 - 含边界值、异常值、参数组合测试',
    status: 'active',
    trigger: 'keyword_match',
    trigger_words: ['regression', '回归', 'api', 'boundary'],
    required_mcp_servers: ['api_server', 'database_server'],
    required_rag_collections: ['api_docs', 'defect_history'],
    mcp_dependencies: ['api_server', 'database_server'],
    rag_collections: ['api_docs', 'defect_history'],
    detail_content: `## API 回归测试

### 目标
全面验证 API 在变更后仍保持正确行为。

### 操作流程
1. 加载 API 文档和缺陷历史
2. 生成边界值测试用例
3. 生成异常值测试用例
4. 按参数组合全量执行

### 断言策略
- 正向用例：状态码 2xx + 响应结构校验
- 异常用例：状态码 4xx + 错误信息匹配
- 边界值：数值上下限 + 字符串长度极限

### 失败处理
- 错误回归自动创建缺陷
- 已知缺陷标记为 expected
`,
  },
  {
    name: 'web_smoke_test',
    version: '1.0.0',
    description: 'Web 页面冒烟测试 - 核心页面加载与基础交互验证',
    status: 'active',
    trigger: 'keyword_match',
    trigger_words: ['smoke', '冒烟', 'web', 'page'],
    required_mcp_servers: ['playwright_server'],
    required_rag_collections: ['req_docs', 'locator_library'],
    mcp_dependencies: ['playwright_server'],
    rag_collections: ['req_docs', 'locator_library'],
    detail_content: `## Web 页面冒烟测试

### 目标
验证核心页面可正常加载和基础交互。

### 操作流程
1. 加载需求文档获取页面列表
2. 逐页打开验证加载完成
3. 验证关键元素可见性
4. 执行基础交互（点击、输入）

### 断言策略
- 页面加载时间 < 3000ms
- 无 JS 控制台错误
- 关键元素存在且可见

### 失败处理
- 自动重试 2 次
- 失败后截图保存
`,
  },
  {
    name: 'app_smoke_test',
    version: '0.9.0',
    description: 'App 核心流程冒烟测试（Beta）',
    status: 'degraded',
    trigger: 'keyword_match',
    trigger_words: ['smoke', '冒烟', 'app', 'mobile'],
    required_mcp_servers: ['appium_server'],
    required_rag_collections: ['req_docs', 'locator_library'],
    mcp_dependencies: ['appium_server'],
    rag_collections: ['req_docs', 'locator_library'],
    detail_content: `## App 核心流程冒烟测试

### 目标
验证 App 核心用户流程可正常完成。

### 操作流程
1. 加载需求文档获取核心流程
2. 启动 App 模拟器
3. 按流程步骤执行操作
4. 验证各步骤结果

### 断言策略
- 页面切换时间 < 2000ms
- 关键元素存在
- 流程正常完成

### 失败处理
- 截图保存现场
- 标记为环境问题
`,
  },
  {
    name: 'full_regression_test',
    version: '0.5.0',
    description: '全量回归测试编排（开发中）',
    status: 'inactive',
    trigger: 'keyword_match',
    trigger_words: ['regression', 'full', '全量回归'],
    required_mcp_servers: ['api_server', 'playwright_server'],
    required_rag_collections: ['api_docs', 'defect_history', 'test_reports'],
    mcp_dependencies: ['api_server', 'playwright_server'],
    rag_collections: ['api_docs', 'defect_history', 'test_reports'],
    detail_content: `## 全量回归测试编排

### 目标
编排全量回归测试执行，覆盖 API + Web + App。

### 操作流程
1. 加载所有需求文档和缺陷历史
2. 按优先级编排测试顺序
3. 并行执行 API 和 Web 测试
4. 汇总分析执行结果

### 断言策略
- API: 100% 通过
- Web: 核心流程 100%
- Flaky 率 < 5%

### 失败处理
- 失败自动重试
- 缺陷自动归档
`,
  },
];

const Skills: React.FC = () => {
  const [detailDrawerOpen, setDetailDrawerOpen] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<ExtendedSkill | null>(null);
  const [skills, setSkills] = useState<ExtendedSkill[]>(mockSkills);

  const openDetail = (skill: ExtendedSkill) => {
    setSelectedSkill(skill);
    setDetailDrawerOpen(true);
  };

  const handleToggleStatus = (skillName: string, checked: boolean) => {
    setSkills((prev) =>
      prev.map((s) =>
        s.name === skillName
          ? { ...s, status: checked ? 'active' : 'inactive' }
          : s,
      ),
    );
    message.success(`Skill ${skillName} 已${checked ? '启用' : '禁用'}`);
  };

  const handleTestTrigger = (skillName: string) => {
    message.success(`触发测试: ${skillName}（功能待集成）`);
  };

  const handleAddSkill = () => {
    message.info('Skills SDK 创建流程即将开放（Phase 14 集成）');
  };

  const checkMCPStatus = (serverName: string): 'healthy' | 'degraded' | 'unhealthy' | 'unknown' => {
    const server = mockMCPServers.find((s) => s.name === serverName);
    return server?.status as 'healthy' | 'degraded' | 'unhealthy' | 'unknown' || 'unknown';
  };

  const renderMCPStatus = (serverName: string) => {
    const status = checkMCPStatus(serverName);
    const colorMap: Record<string, string> = {
      healthy: 'green',
      degraded: 'orange',
      unhealthy: 'red',
      unknown: 'default',
    };
    const IconComponent = status === 'healthy' ? CheckCircleOutlined : CloseCircleOutlined;
    return (
      <Tag
        key={serverName}
        color={colorMap[status] || 'default'}
        icon={<IconComponent />}
        style={{ marginBottom: 4 }}
      >
        {serverName}: {status}
      </Tag>
    );
  };

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (name: string) => (
        <Text strong style={{ fontFamily: 'monospace', cursor: 'pointer' }} onClick={() => {
          const skill = skills.find((s) => s.name === name);
          if (skill) openDetail(skill);
        }}>
          {name}
        </Text>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: string) => {
        const displayStatus = status === 'active' ? 'normal' : status;
        return (
          <Tag color={statusColorMap[displayStatus] || 'default'}>
            {displayStatus}
          </Tag>
        );
      },
    },
    {
      title: '触发词',
      key: 'trigger_words',
      width: 200,
      render: (_: unknown, record: ExtendedSkill) => (
        <Space size={2} wrap>
          {(record.trigger_words || []).map((w) => (
            <Tag key={w} style={{ fontSize: 11, marginBottom: 2 }}>{w}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: 'MCP 依赖',
      key: 'mcp_deps',
      width: 160,
      render: (_: unknown, record: ExtendedSkill) => (
        <Space size={2} wrap>
          {record.mcp_dependencies.map((dep) => {
            const status = checkMCPStatus(dep);
            return (
              <Tooltip key={dep} title={`${dep}: ${status}`}>
                <Tag
                  color={status === 'healthy' ? 'green' : status === 'degraded' ? 'orange' : status === 'unhealthy' ? 'red' : 'default'}
                  style={{ fontSize: 11, marginBottom: 2 }}
                >
                  {dep}
                </Tag>
              </Tooltip>
            );
          })}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 180,
      render: (_: unknown, record: ExtendedSkill) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => openDetail(record)}>
            详情
          </Button>
          <Button
            type="link"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={(e) => { e.stopPropagation(); handleTestTrigger(record.name); }}
          >
            触发
          </Button>
          <Switch
            size="small"
            checked={record.status === 'active'}
            onChange={(checked) => handleToggleStatus(record.name, checked)}
            checkedChildren="开"
            unCheckedChildren="关"
          />
        </Space>
      ),
    },
  ];

  const detailDrawer = (
    <Drawer
      title={
        <Space>
          <CodeOutlined />
          <span>Skill 详情: {selectedSkill?.name}</span>
          <Tag>{selectedSkill?.version}</Tag>
          <Tag
            color={statusColorMap[selectedSkill?.status === 'active' ? 'normal' : selectedSkill?.status || ''] || 'default'}
          >
            {selectedSkill?.status === 'active' ? 'normal' : selectedSkill?.status}
          </Tag>
        </Space>
      }
      open={detailDrawerOpen}
      onClose={() => setDetailDrawerOpen(false)}
      width={640}
    >
      {selectedSkill && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card size="small" title="基本信息">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="名称" span={2}>
                <Text strong style={{ fontFamily: 'monospace' }}>{selectedSkill.name}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="版本">
                <Text code>{selectedSkill.version}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColorMap[selectedSkill.status === 'active' ? 'normal' : selectedSkill.status] || 'default'}>
                  {selectedSkill.status === 'active' ? 'normal' : selectedSkill.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="描述" span={2}>
                <Text>{selectedSkill.description}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="触发词" span={2}>
                <Space size={2} wrap>
                  {(selectedSkill.trigger_words || []).map((w) => (
                    <Tag key={w}>{w}</Tag>
                  ))}
                </Space>
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card
            size="small"
            title={
              <Space>
                <ApiOutlined />
                <span>MCP Server 依赖状态</span>
              </Space>
            }
          >
            {selectedSkill.mcp_dependencies.length > 0 ? (
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                {selectedSkill.mcp_dependencies.map((dep) => (
                  <div
                    key={dep}
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '4px 0',
                    }}
                  >
                    <Space>
                      <Text code>{dep}</Text>
                    </Space>
                    {renderMCPStatus(dep)}
                  </div>
                ))}
              </Space>
            ) : (
              <Text type="secondary">无 MCP 依赖</Text>
            )}
          </Card>

          <Card size="small" title="RAG Collections">
            <Space size={4} wrap>
              {(selectedSkill.rag_collections || []).map((col) => (
                <Tag key={col} color="blue">{col}</Tag>
              ))}
            </Space>
          </Card>

          <Card
            size="small"
            title="Skill 定义内容"
            styles={{ body: { padding: 0 } }}
          >
            <div style={{ padding: 16 }}>
              <pre
                style={{
                  background: '#f6f8fa',
                  border: '1px solid #e8e8e8',
                  borderRadius: 6,
                  padding: 16,
                  fontSize: 13,
                  lineHeight: 1.7,
                  overflow: 'auto',
                  maxHeight: 400,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  fontFamily: "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
                }}
              >
                {selectedSkill.detail_content || '暂无详细内容'}
              </pre>
            </div>
          </Card>

          <Card size="small" title="操作">
            <Space>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                onClick={() => handleTestTrigger(selectedSkill.name)}
              >
                测试触发
              </Button>
              <Switch
                checked={selectedSkill.status === 'active'}
                onChange={(checked) => {
                  handleToggleStatus(selectedSkill.name, checked);
                  setSelectedSkill((prev) =>
                    prev ? { ...prev, status: checked ? 'active' : 'inactive' } : prev,
                  );
                }}
                checkedChildren="已启用"
                unCheckedChildren="已禁用"
              />
            </Space>
          </Card>
        </Space>
      )}
    </Drawer>
  );

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>Skills 管理</Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAddSkill}>
          新增 Skill
        </Button>
      </div>

      <Card>
        {skills.some((s) => s.status === 'degraded') && (
          <Alert
            message="部分 Skill 处于降级状态"
            description="以下 Skill 依赖的 MCP Server 不可用：app_smoke_test → appium_server (unhealthy)"
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            closable
          />
        )}

        <Table
          columns={columns}
          dataSource={skills}
          rowKey="name"
          pagination={{
            pageSize: 10,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 个 Skill`,
          }}
          size="small"
          locale={{ emptyText: '暂无 Skill' }}
        />
      </Card>

      {detailDrawer}
    </div>
  );
};

export default Skills;
