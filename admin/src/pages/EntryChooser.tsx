import { Button, Card, Form, Input, Space, Typography } from 'antd';
import { ApartmentOutlined, UserOutlined } from '@ant-design/icons';
import { tenantEntryUrl } from '../entry';
export default function EntryChooser() {
  return <div className="entry-page"><Card className="entry-card" title="JJTV 管理入口">
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <Typography.Text type="secondary">平台管理与租户业务分别登录。租户员工请使用所属租户的入口。</Typography.Text>
      <Button block size="large" icon={<ApartmentOutlined aria-hidden />} onClick={() => window.location.assign('/admin/platform/login')}>平台管理登录</Button>
      <Form layout="vertical" onFinish={({ code }) => window.location.assign(tenantEntryUrl(code.trim().toLowerCase()) + '/login')}>
        <Form.Item label="租户标识" name="code" rules={[{ required: true, message: '请输入租户标识' }, { pattern: /^[a-z0-9][a-z0-9-]{1,63}$/i, message: '使用 2–64 位字母、数字或短横线' }]}>
          <Input size="large" autoComplete="organization" placeholder="例如 jjtv" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block size="large" icon={<UserOutlined aria-hidden />}>进入租户登录</Button>
      </Form>
    </Space>
  </Card></div>;
}
