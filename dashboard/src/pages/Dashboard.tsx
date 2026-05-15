import React, { useEffect, useMemo, useRef } from 'react';
import { Row, Col, Card, Statistic, Table, Tag, Typography, Spin, Empty, message } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  BugOutlined,
  ExperimentOutlined,
  RiseOutlined,
  HddOutlined,
} from '@ant-design/icons';
import * as echarts from 'echarts/core';
import { LineChart, PieChart } from 'echarts/charts';
import {
  TooltipComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';
import type { LineSeriesOption, PieSeriesOption } from 'echarts/charts';
import type {
  TooltipComponentOption,
  GridComponentOption,
  LegendComponentOption,
  TitleComponentOption,
} from 'echarts/components';
import type { Defect, TestSession } from '@/types';
import { api } from '@/api/client';
import { useResourceStore } from '@/store/resourceStore';

echarts.use([LineChart, PieChart, TooltipComponent, GridComponent, LegendComponent, TitleComponent, CanvasRenderer]);

type EChartsOption = {
  tooltip?: TooltipComponentOption;
  grid?: GridComponentOption;
  xAxis?: Record<string, unknown>;
  yAxis?: Record<string, unknown>;
  series?: (LineSeriesOption | PieSeriesOption)[];
  legend?: LegendComponentOption;
  title?: TitleComponentOption;
};

interface EChartsWrapperProps {
  option: EChartsOption;
  style?: React.CSSProperties;
}

const EChartsWrapper: React.FC<EChartsWrapperProps> = ({ option, style }) => {
  const chartRef = useRef<HTMLDivElement>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!chartRef.current) return;
    if (!instanceRef.current) {
      instanceRef.current = echarts.init(chartRef.current);
    }
    instanceRef.current.setOption(option, { notMerge: true });

    const handleResize = () => instanceRef.current?.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      instanceRef.current?.dispose();
      instanceRef.current = null;
    };
  }, [option]);

  return <div ref={chartRef} style={{ height: 300, ...style }} />;
};

const { Title, Text } = Typography;

const sessionStatusColorMap: Record<string, string> = {
  completed: 'green',
  failed: 'red',
  executing: 'blue',
  analyzing: 'orange',
  pending: 'default',
  planning: 'purple',
};

const defectCategoryColorMap: Record<string, string> = {
  bug: 'red',
  flaky: 'orange',
  environment: 'blue',
  configuration: 'purple',
};

const defectSeverityColorMap: Record<string, string> = {
  critical: 'red',
  major: 'orange',
  minor: 'blue',
  trivial: 'default',
};

const Dashboard: React.FC = () => {
  const {
    qualitySummary,
    passRateTrends,
    defectDensityTrends,
    coverageTrends,
    resourceUsage,
    setQualitySummary,
    setPassRateTrends,
    setDefectDensityTrends,
    setCoverageTrends,
    setLoading,
    setError,
  } = useResourceStore();

  const [recentSessions, setRecentSessions] = React.useState<TestSession[]>([]);
  const [recentDefects, setRecentDefects] = React.useState<Defect[]>([]);
  const [loadingData, setLoadingData] = React.useState(true);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.quality.trends('pass_rate', 30),
      api.quality.trends('defect_density', 30),
      api.quality.trends('coverage', 30),
      api.quality.summary(),
      api.sessions.list({ page: 1, page_size: 10 }),
      api.defects.list({ page: 1, page_size: 10 }),
    ])
      .then(([passRateRes, defectDensityRes, coverageRes, summaryData, sessionsRes, defectsRes]) => {
        if (cancelled) return;
        setPassRateTrends(passRateRes.trends as typeof passRateTrends);
        setDefectDensityTrends(defectDensityRes.trends as typeof defectDensityTrends);
        setCoverageTrends(coverageRes.trends as typeof coverageTrends);
        setQualitySummary(summaryData);
        setRecentSessions(sessionsRes.items);
        setRecentDefects(defectsRes.items);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : '加载大盘数据失败';
        message.error(msg);
        setError(msg);
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingData(false);
        }
      });
    return () => { cancelled = true; };
  }, [setPassRateTrends, setDefectDensityTrends, setCoverageTrends, setQualitySummary, setLoading, setError]);

  const activeSessions = useMemo(
    () => recentSessions.filter((s) => s.status === 'executing' || s.status === 'analyzing').length,
    [recentSessions],
  );

  const passRateOption: EChartsOption = useMemo(() => {
    const data = passRateTrends;
    return {
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: unknown) => {
          const items = params as { name: string; value: number }[];
          const item = items[0];
          const trend = data.find((d) => d.date === item.name);
          if (!trend) return '';
          return `<strong>${item.name}</strong><br/>
            通过率: ${(trend.pass_rate * 100).toFixed(1)}%<br/>
            总任务: ${trend.total}<br/>
            通过: ${trend.passed} | 失败: ${trend.failed} | Flaky: ${trend.flaky}`;
        },
      },
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: data.map((d) => d.date), boundaryGap: false },
      yAxis: {
        type: 'value',
        min: 0,
        max: 1,
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      series: [
        {
          name: '通过率',
          type: 'line',
          smooth: true,
          data: data.map((d) => d.pass_rate),
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { width: 2 },
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: 'rgba(82, 196, 26, 0.3)' },
                { offset: 1, color: 'rgba(82, 196, 26, 0.02)' },
              ],
            },
          },
          itemStyle: { color: '#52c41a' },
        },
        {
          name: '失败率',
          type: 'line',
          smooth: true,
          data: data.map((d) => d.total > 0 ? d.failed / d.total : 0),
          symbol: 'diamond',
          symbolSize: 5,
          lineStyle: { width: 1, type: 'dashed' },
          itemStyle: { color: '#ff4d4f' },
        },
      ],
      legend: { bottom: 0, icon: 'roundRect' },
    };
  }, [passRateTrends]);

  const defectDensityOption: EChartsOption = useMemo(() => {
    const data = defectDensityTrends;
    return {
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: unknown) => {
          const items = params as { name: string; value: number }[];
          const date = items[0].name;
          const trend = data.find((d) => d.date === date);
          if (!trend) return '';
          return `<strong>${date}</strong><br/>
            缺陷总数: ${trend.total}<br/>
            Critical: ${trend.critical} | Major: ${trend.major}<br/>
            Minor: ${trend.minor} | Trivial: ${trend.trivial}`;
        },
      },
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: data.map((d) => d.date), boundaryGap: false },
      yAxis: { type: 'value', min: 0 },
      series: [
        {
          name: 'Critical',
          type: 'line',
          smooth: true,
          stack: 'defects',
          data: data.map((d) => d.critical),
          itemStyle: { color: '#ff4d4f' },
        },
        {
          name: 'Major',
          type: 'line',
          smooth: true,
          stack: 'defects',
          data: data.map((d) => d.major),
          itemStyle: { color: '#fa8c16' },
        },
        {
          name: 'Minor',
          type: 'line',
          smooth: true,
          stack: 'defects',
          data: data.map((d) => d.minor),
          itemStyle: { color: '#1677ff' },
        },
        {
          name: 'Trivial',
          type: 'line',
          smooth: true,
          stack: 'defects',
          data: data.map((d) => d.trivial),
          itemStyle: { color: '#b0b0b0' },
        },
      ],
      legend: { bottom: 0, icon: 'roundRect' },
    };
  }, [defectDensityTrends]);

  const coverageOption: EChartsOption = useMemo(() => {
    const data = coverageTrends;
    return {
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: unknown) => {
          const items = params as { name: string; value: number; seriesName: string }[];
          const date = items[0].name;
          const trend = data.find((d) => d.date === date);
          if (!trend) return '';
          return `<strong>${date}</strong><br/>
            ${items.map((p) => `${p.seriesName}: ${(p.value * 100).toFixed(1)}%`).join('<br/>')}`;
        },
      },
      grid: { left: 50, right: 20, top: 30, bottom: 30 },
      xAxis: { type: 'category', data: data.map((d) => d.date), boundaryGap: false },
      yAxis: {
        type: 'value',
        min: 0,
        max: 1,
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      series: [
        {
          name: 'API 覆盖率',
          type: 'line',
          smooth: true,
          data: data.map((d) => d.api_coverage),
          symbol: 'circle',
          symbolSize: 5,
          itemStyle: { color: '#1677ff' },
        },
        {
          name: 'Web 覆盖率',
          type: 'line',
          smooth: true,
          data: data.map((d) => d.web_coverage),
          symbol: 'diamond',
          symbolSize: 5,
          itemStyle: { color: '#52c41a' },
        },
        {
          name: '总覆盖率',
          type: 'line',
          smooth: true,
          data: data.map((d) => d.total_coverage),
          symbol: 'triangle',
          symbolSize: 5,
          lineStyle: { width: 2 },
          itemStyle: { color: '#fa8c16' },
        },
      ],
      legend: { bottom: 0, icon: 'roundRect' },
    };
  }, [coverageTrends]);

  const defectCategoryOption: EChartsOption = useMemo(() => {
    const distribution: Record<string, number> = { bug: 0, flaky: 0, environment: 0, configuration: 0 };
    recentDefects.forEach((d) => {
      distribution[d.category] = (distribution[d.category] || 0) + 1;
    });

    const hasData = Object.values(distribution).some((v) => v > 0);
    if (!hasData) {
      return {
        title: { text: '暂无数据', left: 'center', top: 'center', textStyle: { fontSize: 14, color: '#999' } },
        series: [],
      };
    }

    return {
      tooltip: {
        trigger: 'item' as const,
        formatter: (params: unknown) => {
          const item = params as { name: string; value: number; percent: number };
          return `<strong>${item.name}</strong><br/>数量: ${item.value}<br/>占比: ${item.percent}%`;
        },
      },
      series: [
        {
          type: 'pie',
          radius: ['35%', '60%'],
          center: ['50%', '50%'],
          roseType: 'radius',
          itemStyle: { borderRadius: 6 },
          label: {
            formatter: (params: unknown) => {
              const item = params as { name: string; percent: number };
              return `${item.name}: ${item.percent}%`;
            },
          },
          data: [
            { value: distribution.bug, name: 'Bug', itemStyle: { color: '#ff4d4f' } },
            { value: distribution.flaky, name: 'Flaky', itemStyle: { color: '#fa8c16' } },
            { value: distribution.environment, name: 'Environment', itemStyle: { color: '#1677ff' } },
            { value: distribution.configuration, name: 'Configuration', itemStyle: { color: '#722ed1' } },
          ],
        },
      ],
    };
  }, [recentDefects]);

  const defectSeverityOption: EChartsOption = useMemo(() => {
    const distribution: Record<string, number> = { critical: 0, major: 0, minor: 0, trivial: 0 };
    recentDefects.forEach((d) => {
      distribution[d.severity] = (distribution[d.severity] || 0) + 1;
    });

    const hasData = Object.values(distribution).some((v) => v > 0);
    if (!hasData) {
      return {
        title: { text: '暂无数据', left: 'center', top: 'center', textStyle: { fontSize: 14, color: '#999' } },
        series: [],
      };
    }

    return {
      tooltip: {
        trigger: 'item' as const,
        formatter: (params: unknown) => {
          const item = params as { name: string; value: number; percent: number };
          return `<strong>${item.name}</strong><br/>数量: ${item.value}<br/>占比: ${item.percent}%`;
        },
      },
      series: [
        {
          type: 'pie',
          radius: ['35%', '60%'],
          center: ['50%', '50%'],
          roseType: 'radius',
          itemStyle: { borderRadius: 6 },
          label: {
            formatter: (params: unknown) => {
              const item = params as { name: string; percent: number };
              return `${item.name}: ${item.percent}%`;
            },
          },
          data: [
            { value: distribution.critical, name: 'Critical', itemStyle: { color: '#ff4d4f' } },
            { value: distribution.major, name: 'Major', itemStyle: { color: '#fa8c16' } },
            { value: distribution.minor, name: 'Minor', itemStyle: { color: '#1677ff' } },
            { value: distribution.trivial, name: 'Trivial', itemStyle: { color: '#b0b0b0' } },
          ],
        },
      ],
    };
  }, [recentDefects]);

  const sessionColumns = [
    {
      title: 'Session ID',
      dataIndex: 'id',
      key: 'id',
      width: 180,
      render: (id: string) => <Text copyable={{ text: id }} ellipsis style={{ maxWidth: 160 }}>{id}</Text>,
    },
    { title: 'Skill', dataIndex: 'skill_name', key: 'skill_name', width: 160 },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={sessionStatusColorMap[status] || 'default'}>{status}</Tag>
      ),
    },
    {
      title: 'Pass Rate',
      key: 'pass_rate',
      width: 100,
      render: (_: unknown, record: { passed_tasks: number; total_tasks: number }) =>
        record.total_tasks > 0
          ? `${((record.passed_tasks / record.total_tasks) * 100).toFixed(1)}%`
          : '-',
    },
    {
      title: 'Type',
      dataIndex: 'test_type',
      key: 'test_type',
      width: 80,
      render: (type: string) => <Tag>{type}</Tag>,
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString(),
    },
  ];

  const defectColumns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 180,
      render: (id: string) => <Text copyable={{ text: id }} ellipsis style={{ maxWidth: 160 }}>{id}</Text>,
    },
    { title: 'Title', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: 'Category',
      dataIndex: 'category',
      key: 'category',
      width: 120,
      render: (category: string) => <Tag color={defectCategoryColorMap[category] || 'default'}>{category}</Tag>,
    },
    {
      title: 'Severity',
      dataIndex: 'severity',
      key: 'severity',
      width: 100,
      render: (severity: string) => <Tag color={defectSeverityColorMap[severity] || 'default'}>{severity}</Tag>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => <Tag>{status}</Tag>,
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 170,
      render: (v: string) => new Date(v).toLocaleString(),
    },
  ];

  if (loadingData && !qualitySummary) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 24 }}>
        <Col>
          <Title level={4} style={{ margin: 0 }}>质量大盘</Title>
          <Text type="secondary">实时监控质量指标与趋势</Text>
        </Col>
        <Col>
          <Text type="secondary">
            {resourceUsage && `资源: CPU ${(resourceUsage.cpu_percent).toFixed(1)}% · 内存 ${(resourceUsage.memory_percent).toFixed(1)}%`}
          </Text>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="今日通过率"
              value={qualitySummary ? qualitySummary.overall_pass_rate * 100 : 0}
              precision={1}
              suffix="%"
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
            {qualitySummary && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  较上周 <Text style={{ color: qualitySummary.pass_rate_change_7d >= 0 ? '#52c41a' : '#ff4d4f' }}>
                    {(qualitySummary.pass_rate_change_7d * 100).toFixed(1)}%
                  </Text>
                </Text>
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="今日缺陷数"
              value={qualitySummary ? qualitySummary.total_defects_30d : 0}
              prefix={<BugOutlined />}
              valueStyle={{ color: qualitySummary && qualitySummary.total_defects_30d > 0 ? '#ff4d4f' : '#52c41a' }}
            />
            {qualitySummary && (
              <div style={{ marginTop: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  较30天前 <Text style={{ color: qualitySummary.defect_change_30d <= 0 ? '#52c41a' : '#ff4d4f' }}>
                    {qualitySummary.defect_change_30d > 0 ? '+' : ''}{qualitySummary.defect_change_30d}
                  </Text>
                </Text>
              </div>
            )}
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="活跃 Session"
              value={activeSessions}
              prefix={<SyncOutlined spin={activeSessions > 0} />}
              valueStyle={{ color: '#1677ff' }}
            />
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                总执行 {qualitySummary ? qualitySummary.total_tests_30d : 0} 次
              </Text>
            </div>
          </Card>
        </Col>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="覆盖率"
              value={qualitySummary ? qualitySummary.latest_coverage * 100 : 0}
              precision={1}
              suffix="%"
              prefix={<HddOutlined />}
              valueStyle={{ color: '#fa8c16' }}
            />
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Flaky率: {qualitySummary ? (qualitySummary.latest_flaky_rate * 100).toFixed(1) : '0.0'}%
              </Text>
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={12}>
          <Card
            title={
              <span>
                <RiseOutlined style={{ marginRight: 8 }} />
                通过率趋势（30天）
              </span>
            }
          >
            {passRateTrends.length > 0 ? (
              <EChartsWrapper option={passRateOption} />
            ) : (
              <Empty description="暂无通过率趋势数据" />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card
            title={
              <span>
                <CloseCircleOutlined style={{ marginRight: 8 }} />
                缺陷密度趋势（30天）
              </span>
            }
          >
            {defectDensityTrends.length > 0 ? (
              <EChartsWrapper option={defectDensityOption} />
            ) : (
              <Empty description="暂无缺陷密度趋势数据" />
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <Card
            title={
              <span>
                <ExperimentOutlined style={{ marginRight: 8 }} />
                覆盖率趋势（30天）
              </span>
            }
          >
            {coverageTrends.length > 0 ? (
              <EChartsWrapper option={coverageOption} />
            ) : (
              <Empty description="暂无覆盖率趋势数据" />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={6}>
          <Card title="缺陷分类分布">
            <EChartsWrapper option={defectCategoryOption} />
          </Card>
        </Col>
        <Col xs={24} lg={6}>
          <Card title="缺陷严重度分布">
            <EChartsWrapper option={defectSeverityOption} />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={12}>
          <Card title="最近执行列表">
            <Table
              columns={sessionColumns}
              dataSource={recentSessions}
              rowKey="id"
              pagination={false}
              size="small"
              locale={{ emptyText: <Empty description="暂无执行记录" /> }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="最近缺陷列表">
            <Table
              columns={defectColumns}
              dataSource={recentDefects}
              rowKey="id"
              pagination={false}
              size="small"
              locale={{ emptyText: <Empty description="暂无缺陷记录" /> }}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
