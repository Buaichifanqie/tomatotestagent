import React, { useState } from 'react';
import {
  Card, Table, Tag, Typography, Space, Button, Input, Select,
  Form, Modal, message, Tabs, Radio, Switch,
  InputNumber, Alert, Row, Col,
} from 'antd';
import {
  SettingOutlined, ApiOutlined, KeyOutlined, EnvironmentOutlined,
  PlusOutlined, ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined,
  SyncOutlined, SafetyOutlined, EditOutlined,
  CloudServerOutlined, DatabaseOutlined, RobotOutlined,
} from '@ant-design/icons';
import type { MCPServer, MCPRegisterRequest, APIKeyItem } from '@/types';
import { api } from '@/api/client';

const { Title, Text } = Typography;

interface MCPServerExtended extends MCPServer {
  command: string;
  args: string[];
}

const mockMCPServers: MCPServerExtended[] = [
  { name: 'api_server', status: 'healthy', version: '1.0.0', command: 'python -m mcp_servers.api_server', args: ['--port', '8001'], last_heartbeat: '2026-05-16 10:30:00', tools_count: 12 },
  { name: 'playwright_server', status: 'healthy', version: '1.2.0', command: 'python -m mcp_servers.playwright_server', args: ['--headless'], last_heartbeat: '2026-05-16 10:30:00', tools_count: 8 },
  { name: 'jira_server', status: 'healthy', version: '0.9.0', command: 'python -m mcp_servers.jira_server', args: ['--config', './jira.json'], last_heartbeat: '2026-05-16 10:29:00', tools_count: 5 },
  { name: 'database_server', status: 'healthy', version: '1.1.0', command: 'python -m mcp_servers.database_server', args: ['--db', 'sqlite:///testagent.db'], last_heartbeat: '2026-05-16 10:28:00', tools_count: 6 },
  { name: 'appium_server', status: 'unhealthy', version: '0.8.0', command: 'python -m mcp_servers.appium_server', args: ['--port', '4723'], last_heartbeat: '2026-05-15 18:00:00', tools_count: 3 },
];

const mockAPIKeys: APIKeyItem[] = [
  { name: 'OpenAI API Key', key_preview: 'sk-********************abcd', updated_at: '2026-05-10' },
  { name: 'Jira API Token', key_preview: 'jira-********************xyz', updated_at: '2026-05-01' },
  { name: 'Database Password', key_preview: '****', updated_at: '2026-04-20' },
  { name: 'GitHub Token', key_preview: 'ghp_********************1234', updated_at: '2026-05-15' },
];

const statusColorMap: Record<string, string> = {
  healthy: 'green',
  unhealthy: 'red',
  starting: 'orange',
};

const statusIconMap: Record<string, React.ReactNode> = {
  healthy: <CheckCircleOutlined />,
  unhealthy: <CloseCircleOutlined />,
  starting: <SyncOutlined spin />,
};

const Settings: React.FC = () => {
  const [activeTab, setActiveTab] = useState('mcp');
  const [mcpServers, setMcpServers] = useState<MCPServerExtended[]>(mockMCPServers);
  const [registerModalOpen, setRegisterModalOpen] = useState(false);
  const [registerForm] = Form.useForm();
  const [healthChecking, setHealthChecking] = useState<string | null>(null);

  const [llmProvider, setLlmProvider] = useState<'openai' | 'local'>('openai');
  const [llmModel, setLlmModel] = useState('gpt-4o');
  const [dbType, setDbType] = useState<'sqlite' | 'postgresql'>('sqlite');
  const [vectorStore, setVectorStore] = useState<'chromadb' | 'milvus'>('chromadb');
  const [embeddingMode, setEmbeddingMode] = useState<'local' | 'api'>('local');
  const [embeddingModel, setEmbeddingModel] = useState('bge-large-zh-v1.5');

  const [apiKeys, setApiKeys] = useState<APIKeyItem[]>(mockAPIKeys);
  const [updateKeyModalOpen, setUpdateKeyModalOpen] = useState(false);
  const [selectedKeyName, setSelectedKeyName] = useState<string | null>(null);
  const [updateKeyForm] = Form.useForm();

  const [saveEnvLoading, setSaveEnvLoading] = useState(false);

  const handleHealthCheck = async (name: string) => {
    setHealthChecking(name);
    try {
      const result = await api.mcp.health(name);
      setMcpServers((prev) =>
        prev.map((s) =>
          s.name === name
            ? { ...s, status: result.status as 'healthy' | 'unhealthy', last_heartbeat: new Date().toLocaleString('zh-CN', { hour12: false }) }
            : s,
        ),
      );
      message.success(`${name} 健康检查通过`);
    } catch {
      setMcpServers((prev) =>
        prev.map((s) =>
          s.name === name ? { ...s, status: 'unhealthy' as const } : s,
        ),
      );
      message.error(`${name} 健康检查失败`);
    } finally {
      setHealthChecking(null);
    }
  };

  const handleRegisterMCP = async () => {
    try {
      const values = await registerForm.validateFields();
      const registerData: MCPRegisterRequest = {
        name: values.name,
        command: values.command,
        args: values.args ? values.args.split(' ').filter((s: string) => s) : [],
      };
      await api.mcp.register(registerData);
      const newServer: MCPServerExtended = {
        name: values.name,
        status: 'starting',
        version: '0.0.1',
        command: values.command,
        args: values.args ? values.args.split(' ').filter((s: string) => s) : [],
        last_heartbeat: new Date().toLocaleString('zh-CN', { hour12: false }),
        tools_count: 0,
      };
      setMcpServers((prev) => [...prev, newServer]);
      message.success(`MCP Server ${values.name} 注册成功`);
      setRegisterModalOpen(false);
      registerForm.resetFields();
    } catch {
      message.error('注册失败，请检查表单');
    }
  };

  const handleSaveEnvConfig = () => {
    setSaveEnvLoading(true);
    setTimeout(() => {
      message.success('环境变量配置已保存');
      setSaveEnvLoading(false);
    }, 500);
  };

  const handleUpdateKey = async () => {
    try {
      const values = await updateKeyForm.validateFields();
      setApiKeys((prev) =>
        prev.map((k) =>
          k.name === selectedKeyName
            ? { ...k, key_preview: values.new_key.substring(0, 4) + '*'.repeat(20) + values.new_key.substring(values.new_key.length - 4), updated_at: new Date().toISOString().split('T')[0] }
            : k,
        ),
      );
      message.success(`${selectedKeyName} 已更新`);
      setUpdateKeyModalOpen(false);
      updateKeyForm.resetFields();
    } catch {
      message.error('更新失败，请检查表单');
    }
  };

  const mcpColumns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 180,
      render: (name: string) => (
        <Space>
          <CloudServerOutlined style={{ color: '#1677ff' }} />
          <Text strong style={{ fontFamily: 'monospace' }}>{name}</Text>
        </Space>
      ),
    },
    {
      title: '启动命令',
      key: 'command',
      width: 300,
      render: (_: unknown, record: MCPServerExtended) => (
        <Text code style={{ fontSize: 12 }}>
          {record.command} {record.args.join(' ')}
        </Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: string) => (
        <Tag color={statusColorMap[status] || 'default'} icon={statusIconMap[status]}>
          {status}
        </Tag>
      ),
    },
    {
      title: '工具数',
      dataIndex: 'tools_count',
      key: 'tools_count',
      width: 80,
      render: (count: number) => <Text style={{ fontFamily: 'monospace' }}>{count}</Text>,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 80,
      render: (v: string) => <Text code>{v}</Text>,
    },
    {
      title: '最后心跳',
      dataIndex: 'last_heartbeat',
      key: 'last_heartbeat',
      width: 170,
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: unknown, record: MCPServerExtended) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<ReloadOutlined />}
            loading={healthChecking === record.name}
            onClick={() => handleHealthCheck(record.name)}
          >
            健康检查
          </Button>
        </Space>
      ),
    },
  ];

  const apiKeyColumns = [
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      render: (name: string) => (
        <Space>
          <KeyOutlined style={{ color: '#fa8c16' }} />
          <Text strong>{name}</Text>
        </Space>
      ),
    },
    {
      title: '密钥',
      dataIndex: 'key_preview',
      key: 'key_preview',
      width: 300,
      render: (preview: string) => (
        <Space>
          <Text code style={{ fontSize: 13, color: '#999', fontFamily: 'monospace', letterSpacing: 1 }}>
            {preview}
          </Text>
          <SafetyOutlined style={{ color: '#52c41a' }} />
        </Space>
      ),
    },
    {
      title: '最后更新',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 120,
      render: (v: string) => <Text type="secondary">{v}</Text>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_: unknown, record: APIKeyItem) => (
        <Button
          type="link"
          size="small"
          icon={<EditOutlined />}
          onClick={() => {
            setSelectedKeyName(record.name);
            setUpdateKeyModalOpen(true);
          }}
        >
          更新
        </Button>
      ),
    },
  ];

  const tabItems = [
    {
      key: 'mcp',
      label: (
        <Space>
          <ApiOutlined />
          <span>MCP 配置管理</span>
        </Space>
      ),
      children: (
        <div>
          <Alert
            message="MCP Server 管理"
            description="管理 MCP Server 的注册、启动和健康检查。MCP Server 以 stdio 子进程方式启动，通过 Gateway MCP Registry 统一管理生命周期。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Card
            title={
              <Space>
                <CloudServerOutlined />
                <span>MCP Server 列表</span>
              </Space>
            }
            extra={
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setRegisterModalOpen(true)}
              >
                注册新 MCP Server
              </Button>
            }
          >
            <Table
              columns={mcpColumns}
              dataSource={mcpServers}
              rowKey="name"
              pagination={false}
              size="small"
            />
          </Card>

          <Modal
            title={
              <Space>
                <PlusOutlined />
                <span>注册新 MCP Server</span>
              </Space>
            }
            open={registerModalOpen}
            onCancel={() => setRegisterModalOpen(false)}
            onOk={handleRegisterMCP}
            okText="注册"
            cancelText="取消"
            width={560}
          >
            <Form
              form={registerForm}
              layout="vertical"
              style={{ marginTop: 16 }}
            >
              <Form.Item
                label="Server 名称"
                name="name"
                rules={[{ required: true, message: '请输入 Server 名称' }]}
              >
                <Input placeholder="例如: git_server" />
              </Form.Item>
              <Form.Item
                label="启动命令"
                name="command"
                rules={[{ required: true, message: '请输入启动命令' }]}
              >
                <Input placeholder="例如: python -m mcp_servers.git_server" />
              </Form.Item>
              <Form.Item
                label="启动参数"
                name="args"
                rules={[{ required: true, message: '请输入启动参数' }]}
              >
                <Input placeholder="例如: --config ./git.json --port 8006" />
              </Form.Item>
            </Form>
          </Modal>
        </div>
      ),
    },
    {
      key: 'env',
      label: (
        <Space>
          <EnvironmentOutlined />
          <span>环境变量配置</span>
        </Space>
      ),
      children: (
        <div>
          <Alert
            message="环境变量配置"
            description="配置 LLM Provider、数据库和 RAG 引擎的参数。所有配置通过 Pydantic Settings 管理，变更后需重启服务生效。"
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <Card
                title={
                  <Space>
                    <RobotOutlined />
                    <span>LLM Provider 设置</span>
                  </Space>
                }
              >
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <div>
                    <Text strong>Provider</Text>
                    <Radio.Group
                      value={llmProvider}
                      onChange={(e) => setLlmProvider(e.target.value)}
                      style={{ width: '100%', marginTop: 8 }}
                    >
                      <Radio.Button value="openai" style={{ width: '50%', textAlign: 'center' }}>
                        OpenAI
                      </Radio.Button>
                      <Radio.Button value="local" style={{ width: '50%', textAlign: 'center' }}>
                        Local (Ollama/Qwen2.5)
                      </Radio.Button>
                    </Radio.Group>
                  </div>
                  <div>
                    <Text strong>模型</Text>
                    <Select
                      value={llmModel}
                      onChange={setLlmModel}
                      style={{ width: '100%', marginTop: 8 }}
                      options={
                        llmProvider === 'openai'
                          ? [
                            { label: 'GPT-4o', value: 'gpt-4o' },
                            { label: 'GPT-4o-mini', value: 'gpt-4o-mini' },
                            { label: 'GPT-4-turbo', value: 'gpt-4-turbo' },
                          ]
                          : [
                            { label: 'Qwen2.5-72B', value: 'qwen2.5-72b' },
                            { label: 'Qwen2.5-32B', value: 'qwen2.5-32b' },
                            { label: 'Qwen2.5-7B', value: 'qwen2.5-7b' },
                          ]
                      }
                    />
                  </div>
                  {llmProvider === 'openai' && (
                    <div>
                      <Text strong>API Base URL</Text>
                      <Input
                        defaultValue="https://api.openai.com/v1"
                        placeholder="https://api.openai.com/v1"
                        style={{ marginTop: 8 }}
                      />
                    </div>
                  )}
                </Space>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card
                title={
                  <Space>
                    <DatabaseOutlined />
                    <span>数据库配置</span>
                  </Space>
                }
              >
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <div>
                    <Text strong>数据库类型</Text>
                    <Radio.Group
                      value={dbType}
                      onChange={(e) => setDbType(e.target.value)}
                      style={{ width: '100%', marginTop: 8 }}
                    >
                      <Radio.Button value="sqlite" style={{ width: '50%', textAlign: 'center' }}>
                        SQLite (MVP)
                      </Radio.Button>
                      <Radio.Button value="postgresql" style={{ width: '50%', textAlign: 'center' }}>
                        PostgreSQL (V1.0)
                      </Radio.Button>
                    </Radio.Group>
                  </div>
                  {dbType === 'sqlite' && (
                    <div>
                      <Text strong>数据库路径</Text>
                      <Input
                        defaultValue="sqlite:///testagent.db"
                        placeholder="sqlite:///testagent.db"
                        style={{ marginTop: 8 }}
                      />
                    </div>
                  )}
                  {dbType === 'postgresql' && (
                    <>
                      <div>
                        <Text strong>主机</Text>
                        <Input defaultValue="localhost" style={{ marginTop: 8 }} />
                      </div>
                      <Row gutter={16}>
                        <Col span={12}>
                          <div>
                            <Text strong>端口</Text>
                            <InputNumber defaultValue={5432} style={{ width: '100%', marginTop: 8 }} />
                          </div>
                        </Col>
                        <Col span={12}>
                          <div>
                            <Text strong>数据库名</Text>
                            <Input defaultValue="testagent" style={{ marginTop: 8 }} />
                          </div>
                        </Col>
                      </Row>
                    </>
                  )}
                  <div>
                    <Text strong>WAL 模式</Text>
                    <div style={{ marginTop: 8 }}>
                      <Switch defaultChecked disabled={dbType === 'postgresql'} />
                      <Text type="secondary" style={{ marginLeft: 8 }}>SQLite WAL 模式启用并发读</Text>
                    </div>
                  </div>
                </Space>
              </Card>
            </Col>
          </Row>
          <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
            <Col xs={24} lg={12}>
              <Card
                title={
                  <Space>
                    <SettingOutlined />
                    <span>RAG 配置</span>
                  </Space>
                }
              >
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <div>
                    <Text strong>向量存储</Text>
                    <Radio.Group
                      value={vectorStore}
                      onChange={(e) => setVectorStore(e.target.value)}
                      style={{ width: '100%', marginTop: 8 }}
                    >
                      <Radio.Button value="chromadb" style={{ width: '50%', textAlign: 'center' }}>
                        ChromaDB (MVP)
                      </Radio.Button>
                      <Radio.Button value="milvus" style={{ width: '50%', textAlign: 'center' }}>
                        Milvus (V1.0)
                      </Radio.Button>
                    </Radio.Group>
                  </div>
                  <div>
                    <Text strong>Embedding 模式</Text>
                    <Radio.Group
                      value={embeddingMode}
                      onChange={(e) => setEmbeddingMode(e.target.value)}
                      style={{ width: '100%', marginTop: 8 }}
                    >
                      <Radio.Button value="local" style={{ width: '50%', textAlign: 'center' }}>
                        本地模型
                      </Radio.Button>
                      <Radio.Button value="api" style={{ width: '50%', textAlign: 'center' }}>
                        API 服务
                      </Radio.Button>
                    </Radio.Group>
                  </div>
                  <div>
                    <Text strong>Embedding 模型</Text>
                    <Select
                      value={embeddingModel}
                      onChange={setEmbeddingModel}
                      style={{ width: '100%', marginTop: 8 }}
                      options={
                        embeddingMode === 'local'
                          ? [
                            { label: 'bge-large-zh-v1.5（本地）', value: 'bge-large-zh-v1.5' },
                            { label: 'bge-base-zh-v1.5', value: 'bge-base-zh-v1.5' },
                          ]
                          : [
                            { label: 'text-embedding-3-small', value: 'text-embedding-3-small' },
                            { label: 'text-embedding-3-large', value: 'text-embedding-3-large' },
                          ]
                      }
                    />
                  </div>
                  <div>
                    <Text strong>全文检索</Text>
                    <div style={{ marginTop: 8 }}>
                      <Switch defaultChecked />
                      <Text type="secondary" style={{ marginLeft: 8 }}>Meilisearch 全文索引</Text>
                    </div>
                  </div>
                </Space>
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card
                title={
                  <Space>
                    <SafetyOutlined />
                    <span>Agent 配置</span>
                  </Space>
                }
              >
                <Space direction="vertical" size={16} style={{ width: '100%' }}>
                  <div>
                    <Text strong>Planner Agent 模型</Text>
                    <Select
                      defaultValue="gpt-4o"
                      style={{ width: '100%', marginTop: 8 }}
                      options={[
                        { label: 'GPT-4o (128K 上下文)', value: 'gpt-4o' },
                        { label: 'GPT-4o-mini (128K 上下文)', value: 'gpt-4o-mini' },
                        { label: 'Qwen2.5-72B', value: 'qwen2.5-72b' },
                      ]}
                    />
                  </div>
                  <div>
                    <Text strong>最大循环轮次</Text>
                    <InputNumber defaultValue={50} min={10} max={200} style={{ width: '100%', marginTop: 8 }} />
                  </div>
                  <div>
                    <Text strong>Token 阈值</Text>
                    <InputNumber defaultValue={100000} min={10000} max={500000} step={10000} style={{ width: '100%', marginTop: 8 }} />
                  </div>
                  <div>
                    <Text strong>MCP 健康检查间隔</Text>
                    <InputNumber defaultValue={30} min={5} max={300} addonAfter="秒" style={{ width: '100%', marginTop: 8 }} />
                  </div>
                </Space>
              </Card>
            </Col>
          </Row>
          <div style={{ marginTop: 24, textAlign: 'right' }}>
            <Button type="primary" icon={<SettingOutlined />} loading={saveEnvLoading} onClick={handleSaveEnvConfig}>
              保存环境变量配置
            </Button>
          </div>
        </div>
      ),
    },
    {
      key: 'apikey',
      label: (
        <Space>
          <KeyOutlined />
          <span>API Key 管理</span>
        </Space>
      ),
      children: (
        <div>
          <Alert
            message="API Key 安全管理"
            description="所有密钥以脱敏形式显示（AGENTS.md 安全红线），明文密钥不会在界面中展示。更新密钥通过加密通道提交。"
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
          />
          <Card
            title={
              <Space>
                <KeyOutlined />
                <span>已配置的 API Keys</span>
              </Space>
            }
          >
            <Table
              columns={apiKeyColumns}
              dataSource={apiKeys}
              rowKey="name"
              pagination={false}
              size="small"
            />
          </Card>

          <Modal
            title={
              <Space>
                <EditOutlined />
                <span>更新密钥: {selectedKeyName}</span>
              </Space>
            }
            open={updateKeyModalOpen}
            onCancel={() => setUpdateKeyModalOpen(false)}
            onOk={handleUpdateKey}
            okText="更新"
            cancelText="取消"
            width={520}
          >
            <Form
              form={updateKeyForm}
              layout="vertical"
              style={{ marginTop: 16 }}
            >
              <Form.Item label="密钥名称">
                <Input value={selectedKeyName || ''} disabled />
              </Form.Item>
              <Form.Item
                label="当前密钥（脱敏）"
              >
                <Input
                  value={apiKeys.find((k) => k.name === selectedKeyName)?.key_preview || ''}
                  disabled
                  prefix={<SafetyOutlined style={{ color: '#52c41a' }} />}
                />
              </Form.Item>
              <Form.Item
                label="新密钥"
                name="new_key"
                rules={[
                  { required: true, message: '请输入新密钥' },
                  { min: 8, message: '密钥长度至少 8 位' },
                ]}
              >
                <Input.Password
                  placeholder="输入新的密钥"
                  autoComplete="new-password"
                />
              </Form.Item>
              <Form.Item
                label="确认新密钥"
                name="confirm_key"
                dependencies={['new_key']}
                rules={[
                  { required: true, message: '请确认新密钥' },
                  ({ getFieldValue }) => ({
                    validator(_, value) {
                      if (!value || getFieldValue('new_key') === value) {
                        return Promise.resolve();
                      }
                      return Promise.reject(new Error('两次输入的密钥不一致'));
                    },
                  }),
                ]}
              >
                <Input.Password
                  placeholder="再次输入新密钥"
                  autoComplete="new-password"
                />
              </Form.Item>
            </Form>
          </Modal>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <Title level={4} style={{ margin: 0 }}>设置</Title>
        <Text type="secondary">系统配置管理，包括 MCP Server、环境变量和 API Key</Text>
      </div>
      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          items={tabItems}
          size="large"
        />
      </Card>
    </div>
  );
};

export default Settings;
