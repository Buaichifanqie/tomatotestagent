import React, { useState, useMemo } from 'react';
import {
  Table, Card, Tag, Typography, Space, Tabs, Drawer, Button,
  Descriptions, Timeline, Select, message, Empty, List, Badge,
} from 'antd';
import {
  BugOutlined, WarningOutlined, LinkOutlined,
  HistoryOutlined, BranchesOutlined,
} from '@ant-design/icons';
import type { Defect } from '@/types';

const { Title, Text } = Typography;

const severityColorMap: Record<string, string> = {
  critical: 'red',
  major: 'orange',
  minor: 'blue',
  trivial: 'gray',
};

const categoryColorMap: Record<string, string> = {
  bug: 'red',
  flaky: 'yellow',
  environment: 'blue',
  configuration: 'purple',
};

const statusColorMap: Record<string, string> = {
  open: 'red',
  investigating: 'orange',
  fixed: 'green',
  closed: 'default',
};

const statusDisplayMap: Record<string, string> = {
  open: 'Open',
  investigating: 'Confirmed',
  fixed: 'Resolved',
  closed: 'Closed',
};

type ExtendedDefect = Defect & {
  jira_key?: string;
  duplicate_of?: string;
  tags?: string[];
  timeline?: { time: string; action: string; user: string }[];
};

const mockDefects: ExtendedDefect[] = [
  {
    id: 'def-001', session_id: 'ses-003', task_id: 'task-012',
    title: '登录接口返回 500 错误', category: 'bug', severity: 'critical',
    status: 'open', error_message: 'Internal Server Error: NullPointerException',
    root_cause: '用户服务 NPE：未处理 userId 为空场景', created_at: '2026-05-15T09:30:00',
    jira_key: 'BUG-101', tags: ['高频', 'P0'],
    timeline: [
      { time: '2026-05-15 09:30', action: '缺陷创建', user: 'System' },
      { time: '2026-05-15 10:00', action: '标记为 Investigating', user: 'QA-Team' },
    ],
  },
  {
    id: 'def-002', session_id: 'ses-003', task_id: 'task-023',
    title: '用户列表分页参数校验失败', category: 'bug', severity: 'major',
    status: 'investigating', error_message: 'ValidationError: page must be positive',
    root_cause: '前端传参类型不匹配：number 传为 string', created_at: '2026-05-15T09:35:00',
    jira_key: 'BUG-102',
    timeline: [
      { time: '2026-05-15 09:35', action: '缺陷创建', user: 'System' },
    ],
  },
  {
    id: 'def-003', session_id: 'ses-004', task_id: 'task-008',
    title: '首页加载超时（偶发）', category: 'flaky', severity: 'minor',
    status: 'investigating', error_message: 'TimeoutError: navigation timed out after 30000ms',
    root_cause: 'CDN 资源加载偶发超时，建议增加重试机制', created_at: '2026-05-14T16:50:00',
    jira_key: 'BUG-103',
    timeline: [
      { time: '2026-05-14 16:50', action: '缺陷创建', user: 'System' },
      { time: '2026-05-15 09:00', action: '归类为 Flaky', user: 'Analyzer' },
    ],
  },
  {
    id: 'def-004', session_id: 'ses-003', task_id: 'task-045',
    title: '数据库连接池耗尽导致服务不可用', category: 'environment', severity: 'critical',
    status: 'open', error_message: 'ConnectionPoolExhausted: no available connections',
    root_cause: '连接池 max_connections=10 过小，并发请求超限', created_at: '2026-05-15T10:00:00',
    jira_key: 'BUG-104', tags: ['P0', '紧急'],
    timeline: [
      { time: '2026-05-15 10:00', action: '缺陷创建', user: 'System' },
    ],
  },
  {
    id: 'def-005', session_id: 'ses-001', task_id: 'task-005',
    title: '搜索结果排序与预期不符', category: 'bug', severity: 'major',
    status: 'fixed', error_message: 'AssertionError: expected [1,2,3] but got [3,2,1]',
    root_cause: '排序参数拼写错误：order_by → orderBy', created_at: '2026-05-15T09:00:00',
    jira_key: 'BUG-105',
    timeline: [
      { time: '2026-05-15 09:00', action: '缺陷创建', user: 'System' },
      { time: '2026-05-15 11:00', action: '根因分析完成', user: 'Analyzer' },
      { time: '2026-05-15 14:00', action: '已修复', user: 'Dev-Team' },
    ],
  },
  {
    id: 'def-006', session_id: 'ses-002', task_id: 'task-010',
    title: 'CI 环境 Node 版本过低导致构建失败', category: 'configuration', severity: 'minor',
    status: 'closed', error_message: 'BuildError: requires Node >= 18',
    root_cause: 'CI 镜像未更新，Node 版本锁定在 16', created_at: '2026-05-15T08:20:00',
    jira_key: 'BUG-106',
    timeline: [
      { time: '2026-05-15 08:20', action: '缺陷创建', user: 'System' },
      { time: '2026-05-15 08:30', action: '环境配置已更新', user: 'Ops-Team' },
      { time: '2026-05-15 09:00', action: '已关闭', user: 'QA-Team' },
    ],
  },
  {
    id: 'def-007', session_id: 'ses-004', task_id: 'task-006',
    title: '注册流程验证码发送延迟', category: 'flaky', severity: 'major',
    status: 'open', error_message: 'TimeoutError: captcha not received within 60s',
    created_at: '2026-05-14T17:00:00',
    timeline: [
      { time: '2026-05-14 17:00', action: '缺陷创建', user: 'System' },
    ],
  },
  {
    id: 'def-008', session_id: 'ses-005', task_id: 'task-015',
    title: '支付回调签名验证失败', category: 'bug', severity: 'critical',
    status: 'open', error_message: 'SignatureError: invalid signature',
    root_cause: '回调签名算法与文档不一致：SHA256 vs SHA1', created_at: '2026-05-16T08:00:00',
    jira_key: 'BUG-107', tags: ['P0'],
    duplicate_of: 'def-001',
    timeline: [
      { time: '2026-05-16 08:00', action: '缺陷创建', user: 'System' },
      { time: '2026-05-16 08:05', action: '标记为重复: def-001', user: 'Analyzer' },
    ],
  },
  {
    id: 'def-009', session_id: 'ses-005', task_id: 'task-020',
    title: '数据导出 CSV 编码问题', category: 'bug', severity: 'minor',
    status: 'fixed', error_message: 'EncodingError: UTF-8 BOM missing',
    root_cause: 'CSV 输出未添加 BOM 头，Excel 打开乱码', created_at: '2026-05-16T09:00:00',
    jira_key: 'BUG-108',
    timeline: [
      { time: '2026-05-16 09:00', action: '缺陷创建', user: 'System' },
      { time: '2026-05-16 10:30', action: '已修复', user: 'Dev-Team' },
    ],
  },
];

const similarDefectsMap: Record<string, ExtendedDefect[]> = {
  'def-001': [
    { id: 'def-008', session_id: 'ses-005', task_id: 'task-015', title: '支付回调签名验证失败', category: 'bug', severity: 'critical', status: 'open', error_message: 'SignatureError', duplicate_of: 'def-001', created_at: '2026-05-16T08:00:00' },
    { id: 'def-010', session_id: 'ses-003', task_id: 'task-030', title: '订单查询接口偶发 500', category: 'bug', severity: 'major', status: 'open', error_message: 'Internal Server Error', created_at: '2026-05-15T11:00:00' },
  ],
};

const DefectKanbanCard: React.FC<{ defect: ExtendedDefect; onClick: (d: ExtendedDefect) => void }> = ({ defect, onClick }) => (
  <Card
    size="small"
    hoverable
    style={{ marginBottom: 12, borderLeft: `4px solid ${severityColorMap[defect.severity] === 'gray' ? '#d9d9d9' : severityColorMap[defect.severity]}` }}
    onClick={() => onClick(defect)}
  >
    <Space direction="vertical" size={4} style={{ width: '100%' }}>
      <Text strong ellipsis style={{ maxWidth: 260 }}>{defect.title}</Text>
      <Space size={4} wrap>
        <Tag color={severityColorMap[defect.severity] || 'default'} style={{ fontSize: 11, margin: 0 }}>
          {defect.severity}
        </Tag>
        <Tag color={categoryColorMap[defect.category] || 'default'} style={{ fontSize: 11, margin: 0 }}>
          {defect.category}
        </Tag>
        {defect.duplicate_of && (
          <Tag color="default" style={{ fontSize: 11, margin: 0 }}>重复</Tag>
        )}
      </Space>
      <Space size={4}>
        {defect.jira_key && <Text type="secondary" style={{ fontSize: 11 }}>{defect.jira_key}</Text>}
        <Text type="secondary" style={{ fontSize: 11 }}>{new Date(defect.created_at).toLocaleDateString()}</Text>
      </Space>
    </Space>
  </Card>
);

const Defects: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>('table');
  const [detailDrawerOpen, setDetailDrawerOpen] = useState(false);
  const [selectedDefect, setSelectedDefect] = useState<ExtendedDefect | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string | undefined>();
  const [severityFilter, setSeverityFilter] = useState<string | undefined>();
  const [kanbanStatus, setKanbanStatus] = useState<string>('open');

  const filteredDefects = useMemo(() => {
    let result = mockDefects;
    if (categoryFilter) {
      result = result.filter((d) => d.category === categoryFilter);
    }
    if (severityFilter) {
      result = result.filter((d) => d.severity === severityFilter);
    }
    return result;
  }, [categoryFilter, severityFilter]);

  const kanbanGroups = useMemo(() => {
    const groups: Record<string, ExtendedDefect[]> = {
      open: [],
      investigating: [],
      fixed: [],
      closed: [],
    };
    mockDefects.forEach((d) => {
      if (groups[d.status]) {
        groups[d.status].push(d);
      }
    });
    return groups;
  }, []);

  const openDetailDrawer = (defect: ExtendedDefect) => {
    setSelectedDefect(defect);
    setDetailDrawerOpen(true);
  };

  const handleStatusUpdate = (defectId: string, newStatus: Defect['status']) => {
    message.success(`缺陷 ${defectId} 状态已更新为 ${statusDisplayMap[newStatus] || newStatus}`);
  };

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 80,
      render: (id: string) => <Text copyable={{ text: id }} style={{ fontSize: 12 }}>{id}</Text>,
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (title: string, record: ExtendedDefect) => (
        <Space>
          {record.duplicate_of && <Tag color="default" style={{ fontSize: 10 }}>重复</Tag>}
          <Text
            strong={record.severity === 'critical' || record.severity === 'major'}
            style={{ cursor: 'pointer' }}
            onClick={() => openDetailDrawer(record)}
          >
            {title}
          </Text>
        </Space>
      ),
    },
    {
      title: '严重度',
      dataIndex: 'severity',
      key: 'severity',
      width: 80,
      render: (severity: string) => (
        <Tag color={severityColorMap[severity] || 'default'}>{severity}</Tag>
      ),
    },
    {
      title: '分类',
      dataIndex: 'category',
      key: 'category',
      width: 100,
      render: (category: string) => (
        <Tag color={categoryColorMap[category] || 'default'}>{category}</Tag>
      ),
    },
    {
      title: 'Jira Key',
      dataIndex: 'jira_key',
      key: 'jira_key',
      width: 90,
      render: (key: string) =>
        key ? (
          <Text
            style={{ cursor: 'pointer' }}
            onClick={() => message.info(`跳转到 Jira: ${key}`)}
          >
            {key}
          </Text>
        ) : '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 110,
      render: (status: string) => (
        <Tag color={statusColorMap[status] || 'default'}>
          {statusDisplayMap[status] || status}
        </Tag>
      ),
    },
    {
      title: '根因',
      dataIndex: 'root_cause',
      key: 'root_cause',
      ellipsis: true,
      render: (cause: string) => cause || <Text type="secondary">分析中</Text>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 150,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: ExtendedDefect) => (
        <Space size="small">
          <Button type="link" size="small" onClick={() => openDetailDrawer(record)}>
            详情
          </Button>
          {record.status === 'open' && (
            <Button
              type="link"
              size="small"
              onClick={() => handleStatusUpdate(record.id, 'investigating')}
            >
              确认
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const detailDrawer = (
    <Drawer
      title={
        <Space>
          <BugOutlined />
          <span>缺陷详情: {selectedDefect?.id}</span>
          {selectedDefect?.jira_key && (
            <Tag style={{ marginLeft: 8 }}>{selectedDefect.jira_key}</Tag>
          )}
        </Space>
      }
      open={detailDrawerOpen}
      onClose={() => setDetailDrawerOpen(false)}
      width={560}
    >
      {selectedDefect && (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Card size="small" title="基本信息">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="标题" span={2}>
                <Text strong>{selectedDefect.title}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="严重度">
                <Tag color={severityColorMap[selectedDefect.severity]}>
                  {selectedDefect.severity}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="分类">
                <Tag color={categoryColorMap[selectedDefect.category]}>
                  {selectedDefect.category}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusColorMap[selectedDefect.status]}>
                  {statusDisplayMap[selectedDefect.status] || selectedDefect.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Session">
                <Text copyable={{ text: selectedDefect.session_id }}>{selectedDefect.session_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Task" span={2}>
                <Text copyable={{ text: selectedDefect.task_id }}>{selectedDefect.task_id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="错误信息" span={2}>
                <Text code style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {selectedDefect.error_message}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="根因" span={2}>
                {selectedDefect.root_cause ? (
                  <Text>{selectedDefect.root_cause}</Text>
                ) : (
                  <Text type="secondary">根因分析尚未完成</Text>
                )}
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card
            size="small"
            title={
              <Space><BranchesOutlined />根因链</Space>
            }
          >
            {selectedDefect.root_cause ? (
              <Timeline
                items={[
                  { children: <Text>缺陷: {selectedDefect.title}</Text>, color: 'red' },
                  { children: <Text>错误: {selectedDefect.error_message.substring(0, 60)}</Text>, color: 'orange' },
                  { children: <Text>根因: {selectedDefect.root_cause}</Text>, color: 'blue' },
                ]}
              />
            ) : (
              <Empty description="暂无根因链数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          {selectedDefect.duplicate_of && (
            <Card size="small" title={<Space><WarningOutlined />重复缺陷关联</Space>}>
              <Tag color="red">重复</Tag>
              <Text>该缺陷被标记为 <Text code>{selectedDefect.duplicate_of}</Text> 的重复</Text>
              <br />
              <Button
                type="link"
                size="small"
                icon={<LinkOutlined />}
                onClick={() => {
                  const original = mockDefects.find((d) => d.id === selectedDefect.duplicate_of);
                  if (original) openDetailDrawer(original);
                }}
              >
                查看原始缺陷
              </Button>
            </Card>
          )}

          <Card
            size="small"
            title={
              <Space>
                <HistoryOutlined />
                <span>相似缺陷 ({similarDefectsMap[selectedDefect.id]?.length || 0})</span>
              </Space>
            }
          >
            {similarDefectsMap[selectedDefect.id]?.length ? (
              <List
                size="small"
                dataSource={similarDefectsMap[selectedDefect.id]}
                renderItem={(item) => (
                  <List.Item
                    style={{ cursor: 'pointer' }}
                    onClick={() => openDetailDrawer(item)}
                    actions={[
                      <Tag color={severityColorMap[item.severity]} key="sev">
                        {item.severity}
                      </Tag>,
                    ]}
                  >
                    <List.Item.Meta
                      title={
                        <Space>
                          {item.duplicate_of && <Tag color="default" style={{ fontSize: 10 }}>重复</Tag>}
                          <Text>{item.title}</Text>
                        </Space>
                      }
                      description={<Text type="secondary" style={{ fontSize: 12 }}>{item.id}</Text>}
                    />
                  </List.Item>
                )}
              />
            ) : (
              <Empty description="暂无相似缺陷" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          <Card size="small" title={<Space><HistoryOutlined />时间线</Space>}>
            {selectedDefect.timeline && selectedDefect.timeline.length > 0 ? (
              <Timeline
                items={selectedDefect.timeline.map((entry) => ({
                  children: (
                    <Space direction="vertical" size={0}>
                      <Text style={{ fontSize: 13 }}>{entry.action}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {entry.time} · {entry.user}
                      </Text>
                    </Space>
                  ),
                }))}
              />
            ) : (
              <Empty description="暂无时间线数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Space>
      )}
    </Drawer>
  );

  const kanbanView = (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Text type="secondary">按状态分组:</Text>
        {Object.entries(kanbanGroups).map(([status, defects]) => (
          <Badge
            key={status}
            count={defects.length}
            size="small"
            offset={[4, -4]}
          >
            <Button
              type={kanbanStatus === status ? 'primary' : 'default'}
              size="small"
              onClick={() => setKanbanStatus(status)}
            >
              {statusDisplayMap[status] || status}
            </Button>
          </Badge>
        ))}
      </Space>

      <div style={{ display: 'flex', gap: 16, overflow: 'auto', minHeight: 400 }}>
        {Object.entries(kanbanGroups).map(([status, defects]) => (
          <div key={status} style={{ minWidth: 280, flex: 1 }}>
            <Card
              title={
                <Space>
                  <Badge
                    count={defects.length}
                    size="small"
                    color={statusColorMap[status] !== 'default' ? statusColorMap[status] : undefined}
                  />
                  <span>{statusDisplayMap[status] || status}</span>
                </Space>
              }
              size="small"
              style={{
                background: '#fafafa',
                borderTop: `3px solid ${statusColorMap[status] === 'default' ? '#d9d9d9' : statusColorMap[status]}`,
              }}
            >
              {defects.length === 0 ? (
                <Empty description="无" image={Empty.PRESENTED_IMAGE_SIMPLE} />
              ) : (
                defects.map((defect) => (
                  <DefectKanbanCard
                    key={defect.id}
                    defect={defect}
                    onClick={openDetailDrawer}
                  />
                ))
              )}
            </Card>
          </div>
        ))}
      </div>
    </div>
  );

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>缺陷追踪</Title>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'table',
            label: <Space><BugOutlined />表格视图</Space>,
            children: (
              <Card>
                <Space style={{ marginBottom: 16 }} wrap>
                  <Select
                    placeholder="筛选分类"
                    allowClear
                    style={{ width: 140 }}
                    value={categoryFilter}
                    onChange={(val) => setCategoryFilter(val)}
                    options={[
                      { label: '全部分类', value: undefined },
                      ...Object.keys(categoryColorMap).map((key) => ({
                        label: key.charAt(0).toUpperCase() + key.slice(1),
                        value: key,
                      })),
                    ]}
                  />
                  <Select
                    placeholder="筛选严重度"
                    allowClear
                    style={{ width: 140 }}
                    value={severityFilter}
                    onChange={(val) => setSeverityFilter(val)}
                    options={[
                      { label: '全部严重度', value: undefined },
                      ...Object.keys(severityColorMap).map((key) => ({
                        label: key.charAt(0).toUpperCase() + key.slice(1),
                        value: key,
                      })),
                    ]}
                  />
                  <Text type="secondary">
                    共 {filteredDefects.length} 条
                    {categoryFilter ? ` · 分类: ${categoryFilter}` : ''}
                    {severityFilter ? ` · 严重度: ${severityFilter}` : ''}
                  </Text>
                </Space>
                <Table
                  columns={columns}
                  dataSource={filteredDefects}
                  rowKey="id"
                  pagination={{
                    pageSize: 10,
                    showSizeChanger: true,
                    showTotal: (total) => `共 ${total} 条`,
                  }}
                  size="small"
                  locale={{ emptyText: <Empty description="暂无缺陷记录" /> }}
                />
              </Card>
            ),
          },
          {
            key: 'kanban',
            label: <Space><BranchesOutlined />看板视图</Space>,
            children: <Card>{kanbanView}</Card>,
          },
        ]}
      />

      {detailDrawer}
    </div>
  );
};

export default Defects;
