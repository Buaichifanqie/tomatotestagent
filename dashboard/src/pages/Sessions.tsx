import React, { useEffect, useMemo, useState } from 'react';
import {
  Table, Card, Tag, Select, Typography, Space, Button, Progress,
  Descriptions, DatePicker, message, Modal, Collapse,
} from 'antd';
import {
  StopOutlined, FileTextOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { TestSession, TestPlan, TestTask, TaskProgress } from '@/types';
import { api } from '@/api/client';
import { useSessionStore } from '@/store/sessionStore';
import { useEventStore } from '@/store/eventStore';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const sessionStatusColorMap: Record<string, string> = {
  pending: 'blue',
  planning: 'orange',
  executing: 'green',
  analyzing: 'purple',
  completed: 'green',
  failed: 'red',
};

const taskStatusColorMap: Record<string, string> = {
  queued: 'default',
  running: 'blue',
  passed: 'green',
  failed: 'red',
  flaky: 'orange',
  skipped: 'default',
  retrying: 'purple',
};

const mockSessions: (TestSession & { trigger_type: string; target_environment: string })[] = Array.from({ length: 25 }, (_, i) => {
  const statuses: TestSession['status'][] = ['pending', 'planning', 'executing', 'analyzing', 'completed', 'completed', 'failed'];
  const triggerTypes = ['manual', 'schedule', 'ci', 'webhook'];
  const environments = ['staging', 'production', 'testing'];
  const skills = ['api_smoke_test', 'web_smoke_test', 'api_regression_test', 'full_regression_test'];
  const hours = 8 + (i % 12);
  const day = Math.min(15 - Math.floor(i / 4), 15);
  return {
    id: `ses-${String(i + 1).padStart(3, '0')}`,
    status: statuses[i % statuses.length],
    test_type: (['api', 'web', 'app'] as const)[i % 3],
    skill_name: skills[i % skills.length],
    environment: environments[i % environments.length],
    trigger_type: triggerTypes[i % triggerTypes.length],
    target_environment: environments[(i + 1) % environments.length],
    total_tasks: Math.floor(Math.random() * 50) + 5,
    passed_tasks: Math.floor(Math.random() * 40) + 1,
    failed_tasks: Math.floor(Math.random() * 5),
    flaky_tasks: Math.floor(Math.random() * 3),
    skipped_tasks: Math.floor(Math.random() * 2),
    created_at: `2026-05-${String(day).padStart(2, '0')}T${String(hours).padStart(2, '0')}:${String(i * 3 % 60).padStart(2, '0')}:00`,
    completed_at: `2026-05-${String(day).padStart(2, '0')}T${String(hours + 1).padStart(2, '0')}:${String(i * 3 % 60).padStart(2, '0')}:00`,
  };
});

const mockPlan: TestPlan = {
  id: 'plan-001',
  session_id: 'ses-001',
  tasks: [],
  strategy: 'parallel',
  created_at: '2026-05-15T08:00:00',
};

const mockTasks: TestTask[] = Array.from({ length: 12 }, (_, i) => {
  const taskStatuses: TestTask['status'][] = ['passed', 'passed', 'passed', 'failed', 'passed', 'flaky', 'passed', 'passed', 'skipped', 'passed', 'running', 'queued'];
  return {
    id: `task-${String(i + 1).padStart(3, '0')}`,
    session_id: 'ses-001',
    name: ['登录验证', '用户列表查询', '创建订单', '支付流程', '搜索功能', '注册流程', '数据导出', '权限校验', '消息通知', '文件上传', '性能基准测试', '安全扫描'][i],
    status: taskStatuses[i % taskStatuses.length],
    category: ['api', 'api', 'api', 'web', 'api', 'web', 'api', 'api', 'web', 'api', 'performance', 'security'][i],
    duration_ms: Math.floor(Math.random() * 5000) + 200,
    retry_count: [0, 0, 0, 2, 0, 1, 0, 0, 0, 0, 0, 0][i],
    error_message: taskStatuses[i % taskStatuses.length] === 'failed' ? 'AssertionError: expected 200 got 500' : undefined,
    created_at: '2026-05-15T08:00:00',
  };
});

const TaskProgressBar: React.FC<{ session: TestSession }> = ({ session }) => {
  const progress = session.total_tasks > 0
    ? Math.round(((session.passed_tasks + session.failed_tasks + session.flaky_tasks + session.skipped_tasks) / session.total_tasks) * 100)
    : 0;

  const statusColorMap: Record<string, string> = {
    pending: '#d9d9d9',
    planning: '#fa8c16',
    executing: '#1677ff',
    analyzing: '#722ed1',
    completed: '#52c41a',
    failed: '#ff4d4f',
  };

  if (session.status === 'completed' || session.status === 'failed' || session.status === 'analyzing') {
    return (
      <Progress
        percent={session.status === 'completed' ? 100 : progress}
        size="small"
        status={session.status === 'failed' ? 'exception' : 'success'}
        format={(pct) => `${pct}%`}
      />
    );
  }

  return (
    <Progress
      percent={progress}
      size="small"
      strokeColor={statusColorMap[session.status] || '#1677ff'}
      format={(pct) => `${pct}%`}
    />
  );
};

const Sessions: React.FC = () => {
  const { cancelSession } = useSessionStore();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [triggerFilter, setTriggerFilter] = useState<string | undefined>();
  const [dateRange, setDateRange] = useState<[string, string] | null>(null);
  const [expandedRowKeys, setExpandedRowKeys] = useState<string[]>([]);
  const [expandedSessionTasks, setExpandedSessionTasks] = useState<Record<string, TestTask[]>>({});
  const [expandedSessionPlans, setExpandedSessionPlans] = useState<Record<string, TestPlan | null>>({});
  const [loadingExpand, setLoadingExpand] = useState<Record<string, boolean>>({});
  const [dataSource, setDataSource] = useState(mockSessions);

  useEffect(() => {
    const unsub = useEventStore.subscribe((state, prevState) => {
      if (state.latestEvent !== prevState.latestEvent && state.latestEvent?.event_type === 'task.progress') {
        const progressData = state.latestEvent.data as unknown as TaskProgress;
        setDataSource((prev) =>
          prev.map((s) =>
            s.id === progressData.session_id
              ? { ...s, status: 'executing' as const }
              : s,
          ),
        );
      }
    });
    return unsub;
  }, []);

  const filteredSessions = useMemo(() => {
    let result = dataSource;
    if (statusFilter) {
      result = result.filter((s) => s.status === statusFilter);
    }
    if (triggerFilter) {
      result = result.filter((s) => s.trigger_type === triggerFilter);
    }
    if (dateRange) {
      const [start, end] = dateRange;
      result = result.filter((s) => {
        const created = s.created_at.substring(0, 10);
        return created >= start && created <= end;
      });
    }
    return result;
  }, [dataSource, statusFilter, triggerFilter, dateRange]);

  const handleExpandRow = async (expanded: boolean, record: TestSession) => {
    if (!expanded) {
      setExpandedRowKeys([]);
      return;
    }
    setExpandedRowKeys([record.id]);
    setLoadingExpand((prev) => ({ ...prev, [record.id]: true }));

    try {
      const plan = await api.plans.get(record.id).catch(() => mockPlan);
      const tasksRes = await api.results.list(record.id, { page: 1, page_size: 50 }).catch(() => null);

      setExpandedSessionPlans((prev) => ({ ...prev, [record.id]: plan }));
      setExpandedSessionTasks((prev) => ({
        ...prev,
        [record.id]: tasksRes?.items || mockTasks,
      }));
    } catch {
      setExpandedSessionPlans((prev) => ({ ...prev, [record.id]: mockPlan }));
      setExpandedSessionTasks((prev) => ({ ...prev, [record.id]: mockTasks }));
    } finally {
      setLoadingExpand((prev) => ({ ...prev, [record.id]: false }));
    }
  };

  const handleCancel = async (sessionId: string) => {
    Modal.confirm({
      title: '确认取消',
      content: `确定要取消 Session ${sessionId} 吗？`,
      okText: '确认取消',
      cancelText: '返回',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await cancelSession(sessionId);
          message.success(`Session ${sessionId} 已取消`);
        } catch {
          message.error('取消失败，请稍后重试');
        }
      },
    });
  };

  const handleViewReport = (sessionId: string) => {
    message.info(`查看报告: ${sessionId}（功能待集成）`);
  };

  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 100,
      render: (id: string) => <Text copyable={{ text: id }}>{id}</Text>,
    },
    {
      title: '名称',
      key: 'name',
      width: 200,
      render: (_: unknown, record: TestSession) => (
        <Space direction="vertical" size={0}>
          <Text strong>{record.skill_name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            [{(record as typeof mockSessions[0]).trigger_type}] {record.test_type.toUpperCase()}
          </Text>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={sessionStatusColorMap[status] || 'default'}>{status}</Tag>
      ),
    },
    {
      title: '进度',
      key: 'progress',
      width: 180,
      render: (_: unknown, record: TestSession) => <TaskProgressBar session={record} />,
    },
    {
      title: '触发类型',
      dataIndex: 'trigger_type',
      key: 'trigger_type',
      width: 100,
      render: (type: string) => <Tag>{type}</Tag>,
    },
    {
      title: '通过率',
      key: 'pass_rate',
      width: 80,
      render: (_: unknown, record: TestSession) =>
        record.total_tasks > 0
          ? `${((record.passed_tasks / record.total_tasks) * 100).toFixed(1)}%`
          : '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: '操作',
      key: 'actions',
      width: 130,
      render: (_: unknown, record: TestSession) => (
        <Space size="small">
          {(record.status === 'executing' || record.status === 'analyzing' || record.status === 'planning') && (
            <Button
              type="link"
              size="small"
              danger
              icon={<StopOutlined />}
              onClick={(e) => { e.stopPropagation(); handleCancel(record.id); }}
            >
              取消
            </Button>
          )}
          {record.status === 'completed' && (
            <Button
              type="link"
              size="small"
              icon={<FileTextOutlined />}
              onClick={(e) => { e.stopPropagation(); handleViewReport(record.id); }}
            >
              报告
            </Button>
          )}
          {record.status === 'failed' && (
            <Button
              type="link"
              size="small"
              icon={<FileTextOutlined />}
              onClick={(e) => { e.stopPropagation(); handleViewReport(record.id); }}
            >
              报告
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const taskColumns = [
    { title: 'Task ID', dataIndex: 'id', key: 'id', width: 90 },
    { title: '名称', dataIndex: 'name', key: 'name', ellipsis: true },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (status: string) => (
        <Tag color={taskStatusColorMap[status] || 'default'}>{status}</Tag>
      ),
    },
    { title: '类别', dataIndex: 'category', key: 'category', width: 80 },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      key: 'duration_ms',
      width: 80,
      render: (ms: number) => `${(ms / 1000).toFixed(1)}s`,
    },
    {
      title: '重试',
      dataIndex: 'retry_count',
      key: 'retry_count',
      width: 60,
    },
    {
      title: '错误信息',
      dataIndex: 'error_message',
      key: 'error_message',
      ellipsis: true,
      render: (msg: string) =>
        msg ? <Text type="danger" style={{ fontSize: 12 }}>{msg}</Text> : '-',
    },
  ];

  const expandedRowRender = (record: TestSession) => {
    const plan = expandedSessionPlans[record.id];
    const tasks = expandedSessionTasks[record.id];
    const loading = loadingExpand[record.id];

    if (loading) {
      return (
        <div style={{ padding: '24px 0', textAlign: 'center' }}>
          <Text type="secondary">加载中...</Text>
        </div>
      );
    }

    const planItems = [
      { label: 'Plan ID', children: plan?.id || '-' },
      { label: '策略', children: plan?.strategy || '-' },
      { label: '创建时间', children: plan?.created_at ? new Date(plan.created_at).toLocaleString() : '-' },
    ];

    return (
      <div style={{ padding: 16 }}>
        <Collapse
          size="small"
          defaultActiveKey={['tasks']}
          items={[
            {
              key: 'plan',
              label: 'Plan 详情',
              children: (
                <Descriptions size="small" column={3} bordered>
                  {planItems.map((item) => (
                    <Descriptions.Item key={item.label} label={item.label}>
                      {item.children}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              ),
            },
            {
              key: 'tasks',
              label: `Task 列表 (${tasks?.length || 0})`,
              children: (
                <Table
                  columns={taskColumns}
                  dataSource={tasks || []}
                  rowKey="id"
                  pagination={false}
                  size="small"
                  locale={{ emptyText: '暂无任务数据' }}
                />
              ),
            },
            {
              key: 'result',
              label: '执行结果摘要',
              children: (
                <Descriptions size="small" column={4} bordered>
                  <Descriptions.Item label="总计">{record.total_tasks}</Descriptions.Item>
                  <Descriptions.Item label="通过">
                    <Text style={{ color: '#52c41a' }}>{record.passed_tasks}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="失败">
                    <Text style={{ color: '#ff4d4f' }}>{record.failed_tasks}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="Flaky">
                    <Text style={{ color: '#fa8c16' }}>{record.flaky_tasks}</Text>
                  </Descriptions.Item>
                  <Descriptions.Item label="跳过">{record.skipped_tasks}</Descriptions.Item>
                  <Descriptions.Item label="通过率">
                    {record.total_tasks > 0
                      ? `${((record.passed_tasks / record.total_tasks) * 100).toFixed(1)}%`
                      : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="创建时间">{new Date(record.created_at).toLocaleString()}</Descriptions.Item>
                  <Descriptions.Item label="完成时间">
                    {record.completed_at ? new Date(record.completed_at).toLocaleString() : '-'}
                  </Descriptions.Item>
                </Descriptions>
              ),
            },
          ]}
        />
      </div>
    );
  };

  return (
    <div>
      <Title level={4} style={{ marginBottom: 24 }}>执行历史</Title>
      <Card>
        <Space style={{ marginBottom: 16 }} wrap>
          <Select
            placeholder="筛选状态"
            allowClear
            style={{ width: 140 }}
            value={statusFilter}
            onChange={(val) => setStatusFilter(val)}
            options={[
              { label: '全部状态', value: undefined },
              ...Object.entries(sessionStatusColorMap).map(([key]) => ({
                label: key.charAt(0).toUpperCase() + key.slice(1),
                value: key,
              })),
            ]}
          />
          <Select
            placeholder="触发类型"
            allowClear
            style={{ width: 140 }}
            value={triggerFilter}
            onChange={(val) => setTriggerFilter(val)}
            options={[
              { label: '全部类型', value: undefined },
              { label: 'Manual', value: 'manual' },
              { label: 'Schedule', value: 'schedule' },
              { label: 'CI', value: 'ci' },
              { label: 'Webhook', value: 'webhook' },
            ]}
          />
          <RangePicker
            onChange={(_, dateStrings) => {
              if (dateStrings[0] && dateStrings[1]) {
                setDateRange([dateStrings[0], dateStrings[1]]);
              } else {
                setDateRange(null);
              }
            }}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              setDataSource([...mockSessions]);
              message.success('已刷新');
            }}
          >
            刷新
          </Button>
          <Text type="secondary">
            共 {filteredSessions.length} 条
            {statusFilter ? ` · 状态: ${statusFilter}` : ''}
            {triggerFilter ? ` · 类型: ${triggerFilter}` : ''}
          </Text>
        </Space>

        <Table
          columns={columns}
          dataSource={filteredSessions}
          rowKey="id"
          pagination={{
            pageSize: 10,
            showSizeChanger: true,
            showQuickJumper: true,
            showTotal: (total) => `共 ${total} 条`,
          }}
          size="small"
          expandable={{
            expandedRowRender,
            expandedRowKeys,
            onExpand: handleExpandRow,
            rowExpandable: () => true,
          }}
          locale={{ emptyText: '暂无执行记录' }}
        />
      </Card>
    </div>
  );
};

export default Sessions;
