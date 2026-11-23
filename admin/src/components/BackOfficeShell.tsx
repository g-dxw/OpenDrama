import { useEffect, useState, type ReactNode } from 'react';
import { Avatar, Breadcrumb, Button, Drawer, Dropdown, Grid, Layout, Space, Tag, Typography } from 'antd';
import { DownOutlined, KeyOutlined, LogoutOutlined, MenuFoldOutlined, MenuOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import TenantNavigation from './TenantNavigation';
import { selectedNavigation, type NavigationNode } from '../navigation';
const { Sider, Header, Content } = Layout;
export default function BackOfficeShell({ brand, items, route, scope, onNavigate, accountName, accountLabel, onLogout, onPassword, loadingLogout, headerExtra, banner, children, contentKey }: {
  brand: string; items: NavigationNode[]; route: string; scope: string; onNavigate: (key: string) => void;
  accountName: string; accountLabel: string; onLogout: () => void; onPassword?: () => void; loadingLogout?: boolean;
  headerExtra?: ReactNode; banner?: ReactNode; children: ReactNode; contentKey?: string;
}) {
  const screens = Grid.useBreakpoint(); const narrow = screens.md === false;
  const desktop = screens.lg ?? window.matchMedia('(min-width: 992px)').matches;
  const [open, setOpen] = useState(false); const [collapsed, setCollapsed] = useState(false);
  useEffect(() => { setOpen(false); }, [route]);
  const selected = selectedNavigation(route, items, /^\/dramas\/[^/]+$/.test(route));
  const crumbs = selected ? [...(selected.parent ? [{ title: selected.parent.label }] : []), { title: selected.leaf.label }] : [];
  const navigation = <><div className="backoffice-brand">{brand}</div><TenantNavigation items={items} selected={selected?.leaf.key} route={route} scope={scope} onNavigate={key => { onNavigate(key); setOpen(false); }} /></>;
  return <Layout style={{ minHeight: '100vh', background: '#f5f6f8' }}>
    {desktop ? <Sider width={208} collapsedWidth={0} collapsed={collapsed} trigger={null} theme="light" style={{ overflow: 'hidden' }}>{navigation}</Sider>
      : <Drawer open={open} placement="left" width={208} closable={false} onClose={() => setOpen(false)} styles={{ body: { padding: 0 } }}>{navigation}</Drawer>}
    <Layout style={{ minWidth: 0 }}>
      <Header className="backoffice-header">
        <Space size={4} className="app-header-location">
          <Button type="text" aria-label={desktop ? collapsed ? '展开导航' : '收起导航' : '打开导航菜单'} icon={desktop ? collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined /> : <MenuOutlined />} onClick={() => desktop ? setCollapsed(!collapsed) : setOpen(true)} />
          {narrow ? <Typography.Text strong ellipsis style={{ maxWidth: 170 }}>{brand}</Typography.Text> : <Breadcrumb className="app-breadcrumb" items={crumbs} />}
        </Space>
        <Space size={8} style={{ flexShrink: 0 }}>
          {headerExtra}
          <Dropdown trigger={['click']} placement="bottomRight" menu={{ items: [{ key: 'identity', label: accountName + ' · ' + accountLabel, disabled: true }, { type: 'divider' },
            ...(onPassword ? [{ key: 'password', label: '修改密码', icon: <KeyOutlined /> }] : []),
            { key: 'logout', label: '退出登录', danger: true, icon: <LogoutOutlined /> },
          ], onClick: ({ key }) => key === 'password' ? onPassword?.() : onLogout() }}>
            <Button type="text" aria-label="账号菜单" loading={loadingLogout} style={{ paddingInline: 8, height: 40 }}><Space size={8}>
              <Avatar size={26} style={{ background: '#E53B3E', fontSize: 13 }}>{accountName.slice(0, 1)}</Avatar>
              {!narrow && <><Typography.Text ellipsis style={{ maxWidth: 144 }}>{accountName}</Typography.Text><Tag color="red" style={{ margin: 0 }}>{accountLabel}</Tag></>}
              <DownOutlined style={{ fontSize: 10 }} />
            </Space></Button>
          </Dropdown>
        </Space>
      </Header>
      <Content key={contentKey} style={{ padding: narrow ? 12 : 24, width: '100%', minWidth: 0 }}>
        {narrow && <Breadcrumb className="app-breadcrumb app-breadcrumb-mobile" items={crumbs} />}
        {banner}{children}
      </Content>
    </Layout>
  </Layout>;
}
