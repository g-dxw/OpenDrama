import { Alert, Card, Space, Typography } from 'antd';
import { useTenant } from '../tenant';
import { useCanAdministerTenant } from '../me';
import TenantRoles from './TenantRoles';
export default function TenantRolesPage() {
  const { current } = useTenant(); const canManage = useCanAdministerTenant();
  if (!current || !canManage) return <Alert type="warning" message="仅租户管理员可管理本租户角色" showIcon />;
  return <Space direction="vertical" size="middle" style={{ width: '100%' }}><Card><Typography.Title level={4} style={{ margin: '0 0 6px' }}>租户角色</Typography.Title><Typography.Text type="secondary">为本租户员工配置菜单和操作权限。受保护的租户管理员角色用于自主管理员工和权限，不可修改或删除。</Typography.Text></Card><Card><TenantRoles tenant={current} inline onClose={() => {}} /></Card></Space>;
}
