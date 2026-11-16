import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Form, Input } from 'antd';
import { LockOutlined, UserOutlined } from '@ant-design/icons';
import AuthLayout from '../components/AuthLayout';
import { api, setToken, takeLogoutReason, errorText } from '../api';
import { CURRENT_ENTRY, contextApi, tenantEntryUrl } from '../entry';
import TenantLoginSelector, { useTenantLoginOptions } from '../components/TenantLoginSelector';
import type { Identity, LoginResult } from '../identity';
const REASON = takeLogoutReason();
export default function Login({ onLogin }: { onLogin: (context: Identity) => void }) {
  const navigate = useNavigate();
  const platform = CURRENT_ENTRY.kind === 'platform';
  const directory = useTenantLoginOptions(!platform);
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
  return <AuthLayout systemLogin={platform}>
    <span className="auth-eyebrow">{platform ? '管理系统' : '平台工作台'}</span>
    <h1 className="auth-title">欢迎回来</h1>
    <p className="auth-description">{platform ? '统一管理内容、平台与团队，开启高效协作。' : '登录你的运营空间，让每一个好故事被看见。'}</p>
    <div className="auth-notices">
      {REASON && <Alert showIcon type="info" message="登录状态已失效，请重新登录当前入口。" />}
      {error && <Alert showIcon type="error" message={error} />}
    </div>
    <Form data-testid="auth-login-form" layout="vertical" onFinish={onFinish} initialValues={platform && import.meta.env.DEV ? { username: 'admin', password: 'admin-1234' } : undefined}>
      {!platform && <Form.Item label="选择平台"><TenantLoginSelector directory={directory} value={CURRENT_ENTRY.code} onChange={code => { if (code !== CURRENT_ENTRY.code) window.location.assign(tenantEntryUrl(code) + '/login'); }} /></Form.Item>}
      <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
        <Input size="large" autoComplete="username" prefix={<UserOutlined aria-hidden />} placeholder={platform ? '请输入管理员账号' : '请输入该平台的员工账号'} />
      </Form.Item>
      <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
        <Input.Password size="large" prefix={<LockOutlined aria-hidden />} placeholder="请输入密码" autoComplete="current-password" maxLength={128} />
      </Form.Item>
      <Button className="auth-submit" type="primary" size="large" htmlType="submit" block loading={loading} aria-label={platform ? '登录管理系统' : '登录所选平台'}>{loading ? '正在登录…' : '登录'}</Button>
    </Form>
    <p className="auth-help">{platform ? '管理系统仅供管理员使用，其他成员请选择所属平台。' : '如需开通账号或找回密码，请联系你的管理员。'}</p>
  </AuthLayout>;
}
