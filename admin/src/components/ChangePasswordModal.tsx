import { useState } from 'react';
import { Alert, Button, Form, Input, Modal, Space, Typography, message } from 'antd';
import { api, errorText, setToken } from '../api';
import { contextApi, CURRENT_ENTRY } from '../entry';
import type { Identity, LoginResult } from '../identity';
export function ChangePasswordModal({ open, onClose, onChanged, onLogout, force = false }: { open: boolean; onClose: () => void; onChanged?: (identity: Identity) => void; onLogout?: () => void; force?: boolean }) {
  const [form] = Form.useForm(); const [saving, setSaving] = useState(false); const [error, setError] = useState('');
  const submit = async () => {
    if (CURRENT_ENTRY.kind === 'support') return;
    const values = await form.validateFields().catch(() => null); if (!values) return;
    setSaving(true); setError('');
    try {
      const result = await api<LoginResult>(contextApi() + '/auth/password', { method: 'POST', body: JSON.stringify({ old_password: values.old_password, new_password: values.new_password }) });
      setToken(result.token); onChanged?.(result.context); form.resetFields(); onClose(); message.success('密码已更新，其他登录会话已失效');
    } catch (e) { setError(errorText(e)); } finally { setSaving(false); }
  };
  return <Modal open={open} title={force ? '首次登录请修改临时密码' : '修改密码'} okText="保存新密码" cancelText="取消" onOk={() => void submit()} confirmLoading={saving}
    footer={force ? <Space><Button onClick={onLogout}>退出登录</Button><Button type="primary" loading={saving} onClick={() => void submit()}>保存新密码</Button></Space> : undefined}
    closable={!force} keyboard={!force} maskClosable={!force} cancelButtonProps={{ style: force ? { display: 'none' } : undefined }}
    onCancel={() => { if (!force) { form.resetFields(); setError(''); onClose(); } }} destroyOnHidden>
    {force && <Alert showIcon type="info" message="设置自己的密码后，即可进入后台。" style={{ marginBottom: 16 }} />}
    {error && <Alert showIcon type="error" message={error} style={{ marginBottom: 16 }} />}
    <Form form={form} layout="vertical" preserve={false}>
      <Form.Item name="old_password" label={force ? '临时密码' : '原密码'} rules={[{ required: true, message: '请输入当前密码' }]}><Input.Password autoComplete="current-password" maxLength={128} /></Form.Item>
      <Form.Item name="new_password" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 8, max: 128, message: '新密码需为 8–128 位' }]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
      <Form.Item name="confirm" label="确认新密码" dependencies={['new_password']} rules={[{ required: true, message: '请再次输入新密码' }, ({ getFieldValue }) => ({ validator: (_, value) => !value || value === getFieldValue('new_password') ? Promise.resolve() : Promise.reject(new Error('两次密码不一致')) })]}><Input.Password autoComplete="new-password" maxLength={128} /></Form.Item>
    </Form>
    <Typography.Text type="secondary">更新后将保留当前新会话，其他已登录会话失效。</Typography.Text>
  </Modal>;
}
