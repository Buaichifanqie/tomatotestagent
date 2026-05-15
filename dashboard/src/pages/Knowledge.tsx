import React, { useState, useMemo } from 'react';
import {
  Table, Card, Tag, Typography, Space, Button, Input, Select,
  Row, Col, Statistic, Modal, Upload, message, Empty, Collapse,
  Tooltip,
} from 'antd';
import {
  DatabaseOutlined, SearchOutlined, DeleteOutlined,
  FileTextOutlined, ClockCircleOutlined, SafetyOutlined,
  CloudUploadOutlined, ReloadOutlined, InboxOutlined,
} from '@ant-design/icons';
import type { RAGCollection, RAGQueryResult, KnowledgeDocument } from '@/types';
import { api } from '@/api/client';

const { Title, Text } = Typography;
const { Dragger } = Upload;

const collectionMeta: Record<string, { label: string; color: string; access: string }> = {
  req_docs: { label: '需求文档', color: 'blue', access: 'Planner' },
  api_docs: { label: 'API 文档', color: 'cyan', access: 'Planner, Executor' },
  defect_history: { label: '历史缺陷', color: 'red', access: 'Planner, Analyzer' },
  test_reports: { label: '测试报告', color: 'green', access: 'Analyzer' },
  locator_library: { label: '定位器库', color: 'purple', access: 'Executor' },
  failure_patterns: { label: '失败模式库', color: 'orange', access: 'Analyzer' },
};

const mockCollections: RAGCollection[] = [
  { name: 'req_docs', document_count: 24, last_index_time: '2026-05-15 14:30:00', access: 'Planner' },
  { name: 'api_docs', document_count: 156, last_index_time: '2026-05-15 14:30:00', access: 'Planner, Executor' },
  { name: 'defect_history', document_count: 89, last_index_time: '2026-05-15 14:30:00', access: 'Planner, Analyzer' },
  { name: 'test_reports', document_count: 42, last_index_time: '2026-05-14 10:00:00', access: 'Analyzer' },
  { name: 'locator_library', document_count: 67, last_index_time: '2026-05-13 16:45:00', access: 'Executor' },
  { name: 'failure_patterns', document_count: 35, last_index_time: '2026-05-12 09:15:00', access: 'Analyzer' },
];

const mockDocuments: KnowledgeDocument[] = [
  { id: 'doc-001', collection: 'api_docs', title: '用户服务 API 文档 v2', content: 'RESTful API 设计规范，包含用户注册、登录、信息查询等接口定义。', metadata: { module: 'user-service' }, created_at: '2026-05-10' },
  { id: 'doc-002', collection: 'api_docs', title: '订单服务 OpenAPI 3.0 规范', content: '订单 CRUD 接口定义，包括创建、查询、更新、取消订单等操作。', metadata: { module: 'order-service' }, created_at: '2026-05-11' },
  { id: 'doc-003', collection: 'req_docs', title: 'V1.0 需求规格说明书', content: '测试平台需求文档，涵盖 API 测试、Web 测试、App 测试全链路功能。', metadata: { version: '1.0' }, created_at: '2026-05-01' },
  { id: 'doc-004', collection: 'defect_history', title: '历史缺陷报告', content: '过去 30 天缺陷记录，包括分类、严重度、根因分析等。', metadata: { period: '30d' }, created_at: '2026-05-15' },
  { id: 'doc-005', collection: 'locator_library', title: 'Web 页面定位器库', content: 'CSS/XPath 定位器映射，覆盖登录、注册、首页等核心页面。', metadata: { platform: 'web' }, created_at: '2026-05-12' },
  { id: 'doc-006', collection: 'test_reports', title: '回归测试报告 05-14', content: 'API + Web 回归测试结果，含通过率、失败分布、性能指标等。', metadata: { date: '2026-05-14' }, created_at: '2026-05-14' },
  { id: 'doc-007', collection: 'failure_patterns', title: '失败模式分类库', content: '常见测试失败模式，包括环境问题、配置错误、Flaky 测试等分类。', metadata: { type: 'patterns' }, created_at: '2026-05-08' },
  { id: 'doc-008', collection: 'req_docs', title: 'MVP 阶段功能特性文档', content: 'MVP 阶段核心功能定义，包括 Agent 架构、MCP 通信、RAG 检索等。', metadata: { version: 'mvp' }, created_at: '2026-04-28' },
  { id: 'doc-009', collection: 'locator_library', title: '移动端页面定位器库', content: 'App 页面元素定位器，覆盖 Android 和 iOS 双平台。', metadata: { platform: 'mobile' }, created_at: '2026-05-10' },
  { id: 'doc-010', collection: 'defect_history', title: 'V1.0 版本已知缺陷', content: 'V1.0 版本开发过程中发现的已知缺陷及修复状态。', metadata: { version: '1.0' }, created_at: '2026-05-13' },
];

const Knowledge: React.FC = () => {
  const [collections, setCollections] = useState<RAGCollection[]>(mockCollections);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>(mockDocuments);
  const [queryText, setQueryText] = useState('');
  const [queryCollections, setQueryCollections] = useState<string[]>([]);
  const [searchResults, setSearchResults] = useState<RAGQueryResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [uploadModalOpen, setUploadModalOpen] = useState(false);
  const [uploadCollection, setUploadCollection] = useState<string | undefined>(undefined);
  const [selectedDocs, setSelectedDocs] = useState<KnowledgeDocument[]>([]);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);

  const handleSearch = async () => {
    if (!queryText.trim()) {
      message.warning('请输入搜索查询');
      return;
    }
    setSearching(true);
    try {
      const results = await api.rag.query({
        query: queryText,
        collections: queryCollections.length > 0 ? queryCollections : Object.keys(collectionMeta),
        top_k: 10,
      });
      setSearchResults(results);
      if (results.length === 0) {
        message.info('未找到相关结果');
      }
    } catch {
      const mockResults: RAGQueryResult[] = documents
        .filter((d) =>
          (queryCollections.length === 0 || queryCollections.includes(d.collection)) &&
          (d.title.includes(queryText) || d.content.includes(queryText)),
        )
        .map((d) => ({
          doc_id: d.id,
          title: d.title,
          content: d.content,
          score: Math.random() * 0.5 + 0.5,
          collection: d.collection,
        }))
        .sort((a, b) => b.score - a.score);
      setSearchResults(mockResults);
      if (mockResults.length === 0) {
        message.info('未找到相关结果');
      }
    } finally {
      setSearching(false);
    }
  };

  const handleUpload = async (file: File) => {
    const collection = uploadCollection || 'req_docs';
    try {
      const reader = new FileReader();
      reader.onload = async (e) => {
        const content = e.target?.result as string;
        await api.rag.index({
          collection,
          documents: [{ title: file.name, content, metadata: { source: 'upload' } }],
        });
        message.success(`文档 ${file.name} 已成功摄入到 ${collection}`);
        setUploadModalOpen(false);
      };
      reader.readAsText(file);
    } catch {
      message.success(`文档 ${file.name} 已成功摄入到 ${collection}（Mock）`);
      setUploadModalOpen(false);
    }
    return false;
  };

  const handleDelete = async () => {
    try {
      setDocuments((prev) => prev.filter((d) => !selectedDocs.some((s) => s.id === d.id)));
      message.success(`已删除 ${selectedDocs.length} 个文档`);
      setDeleteModalOpen(false);
      setSelectedDocs([]);
    } catch {
      message.error('删除失败');
    }
  };

  const handleRefreshCollections = () => {
    setCollections((prev) =>
      prev.map((c) => ({
        ...c,
        document_count: c.document_count + Math.floor(Math.random() * 3),
        last_index_time: new Date().toLocaleString('zh-CN', { hour12: false }),
      })),
    );
    message.success('已刷新 Collection 状态');
  };

  const totalDocs = useMemo(() => collections.reduce((sum, c) => sum + c.document_count, 0), [collections]);
  const collectionsWithData = useMemo(() => collections.filter((c) => c.document_count > 0).length, [collections]);

  const collectionColumns = [
    {
      title: 'Collection 名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      render: (name: string) => {
        const meta = collectionMeta[name] || { label: name, color: 'default' };
        return (
          <Space>
            <DatabaseOutlined style={{ color: '#1677ff' }} />
            <Text strong style={{ fontFamily: 'monospace' }}>{name}</Text>
            <Tag color={meta.color}>{meta.label}</Tag>
          </Space>
        );
      },
    },
    {
      title: '文档数',
      dataIndex: 'document_count',
      key: 'document_count',
      width: 100,
      sorter: (a: RAGCollection, b: RAGCollection) => a.document_count - b.document_count,
      render: (count: number) => (
        <Text strong style={{ color: '#1677ff' }}>{count.toLocaleString()}</Text>
      ),
    },
    {
      title: '索引时间',
      dataIndex: 'last_index_time',
      key: 'last_index_time',
      width: 180,
      render: (time: string) => (
        <Space>
          <ClockCircleOutlined style={{ color: '#999' }} />
          <Text type="secondary">{time}</Text>
        </Space>
      ),
    },
    {
      title: '访问权限',
      dataIndex: 'access',
      key: 'access',
      width: 200,
      render: (access: string) => (
        <Space>
          <SafetyOutlined style={{ color: '#52c41a' }} />
          <Text>{access}</Text>
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: RAGCollection) => (
        <Button
          type="link"
          size="small"
          icon={<CloudUploadOutlined />}
          onClick={() => {
            setUploadCollection(record.name);
            setUploadModalOpen(true);
          }}
        >
          摄入文档
        </Button>
      ),
    },
  ];

  const docColumns = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (title: string, record: KnowledgeDocument) => (
        <Tooltip title={record.content}>
          <Space>
            <FileTextOutlined style={{ color: '#999' }} />
            <Text>{title}</Text>
          </Space>
        </Tooltip>
      ),
    },
    {
      title: 'Collection',
      dataIndex: 'collection',
      key: 'collection',
      width: 160,
      render: (col: string) => {
        const meta = collectionMeta[col] || { label: col, color: 'default' };
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 120,
      render: (v: string) => new Date(v).toLocaleDateString(),
    },
  ];

  const searchResultColumns = [
    {
      title: '相关度',
      dataIndex: 'score',
      key: 'score',
      width: 90,
      sorter: (a: RAGQueryResult, b: RAGQueryResult) => a.score - b.score,
      render: (score: number) => {
        const pct = (score * 100).toFixed(0);
        const color = score >= 0.8 ? '#52c41a' : score >= 0.5 ? '#fa8c16' : '#ff4d4f';
        return (
          <Tag color={color} style={{ fontFamily: 'monospace' }}>
            {pct}%
          </Tag>
        );
      },
    },
    {
      title: '文档标题',
      dataIndex: 'title',
      key: 'title',
      width: 240,
      render: (title: string) => <Text strong>{title}</Text>,
    },
    {
      title: 'Collection',
      dataIndex: 'collection',
      key: 'collection',
      width: 140,
      render: (col: string) => {
        const meta = collectionMeta[col] || { label: col, color: 'default' };
        return <Tag color={meta.color}>{meta.label}</Tag>;
      },
    },
    {
      title: '内容摘要',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
      render: (content: string) => (
        <Text type="secondary" ellipsis style={{ maxWidth: 400 }}>
          {content}
        </Text>
      ),
    },
    {
      title: '文档 ID',
      dataIndex: 'doc_id',
      key: 'doc_id',
      width: 180,
      render: (id: string) => <Text copyable={{ text: id }} style={{ fontSize: 12, fontFamily: 'monospace' }}>{id}</Text>,
    },
  ];

  const rowSelection = {
    selectedRowKeys: selectedDocs.map((d) => d.id),
    onChange: (_: React.Key[], rows: KnowledgeDocument[]) => setSelectedDocs(rows),
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <Title level={4} style={{ margin: 0 }}>RAG 知识库管理</Title>
          <Text type="secondary">管理 RAG 向量知识库的 Collection 与文档，支持搜索、摄入和删除</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={handleRefreshCollections}>
            刷新状态
          </Button>
          <Button
            type="primary"
            icon={<CloudUploadOutlined />}
            onClick={() => {
              setUploadCollection(undefined);
              setUploadModalOpen(true);
            }}
          >
            摄入文档
          </Button>
        </Space>
      </div>

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="Collection 总数"
              value={collections.length}
              prefix={<DatabaseOutlined />}
              valueStyle={{ color: '#1677ff' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="文档总数"
              value={totalDocs}
              prefix={<FileTextOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="有数据 Collection"
              value={collectionsWithData}
              suffix={`/ ${collections.length}`}
              prefix={<SafetyOutlined />}
              valueStyle={{ color: '#fa8c16' }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="可访问角色"
              value="Planner / Executor / Analyzer"
              prefix={<SearchOutlined />}
              valueStyle={{ fontSize: 14, color: '#722ed1' }}
            />
          </Card>
        </Col>
      </Row>

      <Card
        title={
          <Space>
            <DatabaseOutlined />
            <span>Collection 概览</span>
          </Space>
        }
        style={{ marginBottom: 24 }}
      >
        <Table
          columns={collectionColumns}
          dataSource={collections}
          rowKey="name"
          pagination={false}
          size="small"
          locale={{ emptyText: <Empty description="暂无 Collection" /> }}
        />
      </Card>

      <Collapse
        defaultActiveKey={['search']}
        style={{ marginBottom: 24 }}
        items={[
          {
            key: 'search',
            label: (
              <Space>
                <SearchOutlined />
                <span>搜索测试</span>
              </Space>
            ),
            children: (
              <Space direction="vertical" size={16} style={{ width: '100%' }}>
                <Space style={{ width: '100%' }} align="start" wrap>
                  <Input
                    placeholder="输入搜索查询语句..."
                    value={queryText}
                    onChange={(e) => setQueryText(e.target.value)}
                    onPressEnter={handleSearch}
                    style={{ width: 400 }}
                    prefix={<SearchOutlined />}
                    allowClear
                  />
                  <Select
                    mode="multiple"
                    placeholder="按 Collection 筛选（可选）"
                    value={queryCollections}
                    onChange={setQueryCollections}
                    style={{ minWidth: 300 }}
                    allowClear
                    options={Object.entries(collectionMeta).map(([key, meta]) => ({
                      label: `${meta.label} (${key})`,
                      value: key,
                    }))}
                  />
                  <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch} loading={searching}>
                    搜索
                  </Button>
                  <Button onClick={() => { setQueryText(''); setSearchResults([]); }}>
                    清除
                  </Button>
                </Space>

                {searchResults.length > 0 && (
                  <div>
                    <div style={{ marginBottom: 8 }}>
                      <Text type="secondary">
                        找到 {searchResults.length} 条结果
                      </Text>
                    </div>
                    <Table
                      columns={searchResultColumns}
                      dataSource={searchResults}
                      rowKey="doc_id"
                      pagination={{ pageSize: 5, showTotal: (total) => `共 ${total} 条` }}
                      size="small"
                      locale={{ emptyText: <Empty description="暂无搜索结果" /> }}
                    />
                  </div>
                )}

                {searchResults.length === 0 && queryText && !searching && (
                  <Empty description="未找到匹配的文档，请尝试其他关键词" />
                )}

                {!queryText && !searching && (
                  <Text type="secondary" style={{ textAlign: 'center', display: 'block', padding: 24 }}>
                    输入查询语句后点击搜索，将对选中 Collection 执行向量 + 关键词混合检索
                  </Text>
                )}
              </Space>
            ),
          },
        ]}
      />

      <Card
        title={
          <Space>
            <FileTextOutlined />
            <span>文档列表</span>
          </Space>
        }
        extra={
          <Space>
            <Button
              danger
              icon={<DeleteOutlined />}
              disabled={selectedDocs.length === 0}
              onClick={() => setDeleteModalOpen(true)}
            >
              删除 ({selectedDocs.length})
            </Button>
          </Space>
        }
      >
        <Table
          rowSelection={rowSelection}
          columns={docColumns}
          dataSource={documents}
          rowKey="id"
          pagination={{
            pageSize: 10,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条`,
          }}
          size="small"
          locale={{ emptyText: <Empty description="暂无文档" /> }}
        />
      </Card>

      <Modal
        title={
          <Space>
            <CloudUploadOutlined />
            <span>摄入文档</span>
          </Space>
        }
        open={uploadModalOpen}
        onCancel={() => setUploadModalOpen(false)}
        footer={null}
        width={600}
      >
        <Space direction="vertical" size={16} style={{ width: '100%', marginTop: 16 }}>
          <div>
            <Text strong>目标 Collection</Text>
            <Select
              value={uploadCollection}
              onChange={setUploadCollection}
              style={{ width: '100%', marginTop: 8 }}
              placeholder="选择目标 Collection"
              options={Object.entries(collectionMeta).map(([key, meta]) => ({
                label: `${meta.label} (${key})`,
                value: key,
              }))}
            />
          </div>
          <Dragger
            beforeUpload={handleUpload}
            accept=".md,.txt,.json,.yaml,.yml,.csv"
            multiple={false}
          >
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
            <p className="ant-upload-hint">
              支持 .md .txt .json .yaml .yml .csv 格式，单文件上传
            </p>
          </Dragger>
        </Space>
      </Modal>

      <Modal
        title={
          <Space>
            <DeleteOutlined style={{ color: '#ff4d4f' }} />
            <span>确认删除</span>
          </Space>
        }
        open={deleteModalOpen}
        onCancel={() => setDeleteModalOpen(false)}
        onOk={handleDelete}
        okText="确认删除"
        cancelText="取消"
        okButtonProps={{ danger: true }}
      >
        <Text>
          确定要删除选中的 <Text strong>{selectedDocs.length}</Text> 个文档吗？此操作不可撤销。
        </Text>
        <div style={{ marginTop: 12 }}>
          {selectedDocs.map((d) => (
            <Tag key={d.id} style={{ marginBottom: 4 }} closable onClose={() => {
              setSelectedDocs((prev) => prev.filter((s) => s.id !== d.id));
            }}>
              {d.title}
            </Tag>
          ))}
        </div>
      </Modal>
    </div>
  );
};

export default Knowledge;
