import { Button, Form } from 'antd';
import { tenantEntryUrl } from '../entry';
import TenantLoginSelector, { useTenantLoginOptions } from '../components/TenantLoginSelector';
import AuthLayout from '../components/AuthLayout';

export default function EntryChooser() {
  const directory = useTenantLoginOptions();
  return <AuthLayout>
    <div data-testid="auth-entry-chooser">
      <span className="auth-eyebrow">OPENDRAMA WORKSPACE</span>
      <h1 className="auth-title">开启你的运营空间</h1>
      <p className="auth-description">选择所属平台，进入你的运营空间。</p>
      <Form layout="vertical" onFinish={({ code }) => window.location.assign(tenantEntryUrl(code) + '/login')}>
        <Form.Item label="选择平台" name="code" rules={[{ required: true, message: '请选择所属平台' }]}>
          <TenantLoginSelector directory={directory} />
        </Form.Item>
        <Button className="auth-submit" type="primary" htmlType="submit" block size="large" disabled={directory.loading || !!directory.error || !directory.items.length}>进入平台登录</Button>
      </Form>
      <p className="auth-help">账号由管理员分配，请选择你所属的工作空间。</p>
    </div>
  </AuthLayout>;
}
