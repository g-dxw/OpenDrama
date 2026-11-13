import { Button, Card, Form, Space, Typography } from 'antd';
import { ApartmentOutlined, UserOutlined } from '@ant-design/icons';
import { tenantEntryUrl } from '../entry';
import TenantLoginSelector, { useTenantLoginOptions } from '../components/TenantLoginSelector';
export default function EntryChooser() {
  const directory = useTenantLoginOptions();
  return <div className="entry-page"><Card className="entry-card" title="JJTV 管理入口">
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Text type="secondary">平台管理与租户业务分别登录。请选择所属租户后，使用该租户的员工账号登录。</Typography.Text>
      <Button block size="large" icon={<ApartmentOutlined aria-hidden />} onClick={() => window.location.assign('/admin/platform/login')}>平台管理登录</Button>
      <Form layout="vertical" onFinish={({ code }) => window.location.assign(tenantEntryUrl(code) + '/login')}>
        <Form.Item label="选择租户" name="code" rules={[{ required: true, message: '请选择所属租户' }]}>
          <TenantLoginSelector directory={directory} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block size="large" disabled={directory.loading || !!directory.error || !directory.items.length} icon={<UserOutlined aria-hidden />}>进入租户登录</Button>
      </Form>
    </Space>
  </Card></div>;
}
