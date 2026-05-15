import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Avatar, Dropdown, Badge, Typography, theme } from 'antd';
import {
  DashboardOutlined,
  HistoryOutlined,
  BugOutlined,
  ThunderboltOutlined,
  DatabaseOutlined,
  SettingOutlined,
  BellOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  WifiOutlined,
} from '@ant-design/icons';
import { useAppStore } from '@/store';

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

const menuItems = [
  { key: '/', icon: <DashboardOutlined />, label: '质量大盘' },
  { key: '/sessions', icon: <HistoryOutlined />, label: '执行历史' },
  { key: '/defects', icon: <BugOutlined />, label: '缺陷追踪' },
  { key: '/skills', icon: <ThunderboltOutlined />, label: 'Skills 管理' },
  { key: '/knowledge', icon: <DatabaseOutlined />, label: '知识库管理' },
  { key: '/settings', icon: <SettingOutlined />, label: '设置' },
];

const MainLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { sidebarCollapsed, setSidebarCollapsed, wsStatus } = useAppStore();
  const { token } = theme.useToken();

  const userMenuItems = [
    { key: 'profile', label: '个人信息' },
    { key: 'logout', label: '退出登录' },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={sidebarCollapsed}
        style={{
          overflow: 'auto',
          height: '100vh',
          position: 'fixed',
          left: 0,
          top: 0,
          bottom: 0,
          borderRight: `1px solid ${token.colorBorderSecondary}`,
        }}
        theme="light"
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
            cursor: 'pointer',
          }}
          onClick={() => navigate('/')}
        >
          <ThunderboltOutlined style={{ fontSize: 24, color: token.colorPrimary }} />
          {!sidebarCollapsed && (
            <Text strong style={{ marginLeft: 8, fontSize: 16 }}>
              TestAgent
            </Text>
          )}
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderInlineEnd: 'none' }}
        />
      </Sider>
      <Layout style={{ marginLeft: sidebarCollapsed ? 80 : 200, transition: 'margin-left 0.2s' }}>
        <Header
          style={{
            padding: '0 24px',
            background: token.colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: `1px solid ${token.colorBorderSecondary}`,
            height: 64,
            position: 'sticky',
            top: 0,
            zIndex: 10,
          }}
        >
          <div
            style={{ cursor: 'pointer', fontSize: 18 }}
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          >
            {sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <Badge status={wsStatus.connected ? 'success' : 'error'}>
              <Text type="secondary" style={{ fontSize: 13 }}>
                {wsStatus.connected ? (
                  <><WifiOutlined style={{ marginRight: 4 }} />已连接</>
                ) : (
                  <>未连接</>
                )}
              </Text>
            </Badge>
            <Badge count={3} size="small">
              <BellOutlined style={{ fontSize: 18, cursor: 'pointer' }} />
            </Badge>
            <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
              <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Avatar size="small" icon={<UserOutlined />} />
                <Text>Admin</Text>
              </div>
            </Dropdown>
          </div>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default MainLayout;
