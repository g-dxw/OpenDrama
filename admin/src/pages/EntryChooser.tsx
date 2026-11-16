import { Button, Form } from 'antd';
import { ApartmentOutlined, ArrowRightOutlined } from '@ant-design/icons';
import { tenantEntryUrl } from '../entry';
import TenantLoginSelector, { useTenantLoginOptions } from '../components/TenantLoginSelector';
import AuthLayout from '../components/AuthLayout';

export default function EntryChooser() {
  const directory = useTenantLoginOptions();
  return <AuthLayout chooser>
    <div data-testid="auth-entry-chooser">
      <span className="auth-eyebrow">OPENDRAMA WORKSPACE</span>
      <h1 className="auth-title">开启你的运营空间</h1>
      <p className="auth-description">选择对应的管理入口，与团队一起创造更多可能。</p>
      <Button className="auth-company-entry" aria-label="平台管理登录" onClick={() => window.location.assign('/admin/platform/login')}>
        <span className="auth-entry-icon"><ApartmentOutlined aria-hidden /></span>
        <span className="auth-entry-copy"><strong>公司总控</strong><span>统筹站点、团队与共享内容</span></span>
        <ArrowRightOutlined className="auth-entry-arrow" aria-hidden />
      </Button>
      <div className="auth-divider"><span>或进入所属站点</span></div>
      <Form layout="vertical" onFinish={({ code }) => window.location.assign(tenantEntryUrl(code) + '/login')}>
        <Form.Item label="选择租户" name="code" rules={[{ required: true, message: '请选择所属租户' }]}>
          <TenantLoginSelector directory={directory} />
        </Form.Item>
        <Button className="auth-submit" type="primary" htmlType="submit" block size="large" disabled={directory.loading || !!directory.error || !directory.items.length}>进入租户登录</Button>
      </Form>
      <p className="auth-help">账号由管理员分配，请选择你所属的工作空间。</p>
    </div>
  </AuthLayout>;
}
