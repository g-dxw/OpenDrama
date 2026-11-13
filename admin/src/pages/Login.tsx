import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { api, setToken, takeLogoutReason, errorText } from '../api';
import { CURRENT_ENTRY, contextApi } from '../entry';
import type { Identity, LoginResult } from '../identity';
const REASON = takeLogoutReason();
export default function Login({ onLogin }: { onLogin: (context: Identity) => void }) {
  const navigate = useNavigate();
  const platform = CURRENT_ENTRY.kind === 'platform';
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true); setError('');
    try {
      const result = await api<LoginResult>(contextApi() + '/auth/login', { method: 'POST', body: JSON.stringify({ ...values, ...(platform ? {} : { tenant_code: CURRENT_ENTRY.code }) }) });
      setToken(result.token); onLogin(result.context); navigate('/', { replace: true });
    } catch (e: any) { setError(e?.status === 401 ? '账号或密码不正确' : errorText(e)); }
    finally { setLoading(false); }
  };
  return <div className="entry-page"><Card className="entry-card" title={platform ? 'JJTV 平台管理登录' : '租户业务后台登录'}>
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {!platform && <div><Typography.Text type="secondary">当前租户</Typography.Text><Typography.Title level={4} style={{ margin: '4px 0' }}>{CURRENT_ENTRY.code}</Typography.Title></div>}
      {REASON && <Alert showIcon type="info" message="登录状态已失效，请重新登录当前入口。" />}
      {error && <Alert showIcon type="error" message={error} />}
      <Form layout="vertical" onFinish={onFinish} initialValues={platform && import.meta.env.DEV ? { username: 'admin', password: 'admin-1234' } : undefined}>
        <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}><Input autoComplete="username" placeholder={platform ? '平台管理员账号' : '该租户的员工账号'} /></Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password autoComplete="current-password" maxLength={128} /></Form.Item>
        <Button type="primary" htmlType="submit" block loading={loading} aria-label={platform ? '登录平台' : '登录租户'}>登录</Button>
      </Form>
      <Button type="link" onClick={() => window.location.assign('/admin/login')}>返回入口选择</Button>
    </Space>
  </Card></div>;
}
