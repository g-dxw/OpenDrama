import { useEffect, useState } from 'react';
import { Alert, Button, Checkbox, Input, Modal, Space, Typography, message } from 'antd';
import { tenantEntryUrl } from '../entry';
export type Credential = { username: string; temporary_password: string; tenant_code?: string };
export default function TemporaryCredential({ value, onClose }: { value: Credential | null; onClose: () => void }) {
  const [saved, setSaved] = useState(false); useEffect(() => { setSaved(false); }, [value]);
  const copy = async () => { try { await navigator.clipboard.writeText(value?.temporary_password || ''); message.success('临时密码已复制'); } catch { message.error('请手动选择并复制临时密码'); } };
  return <Modal open={!!value} title="保存临时登录信息" closable={false} maskClosable={false} keyboard={false} cancelButtonProps={{ style: { display: 'none' } }} okText="已保存，关闭" okButtonProps={{ disabled: !saved }} onOk={onClose} destroyOnHidden>
    <Space direction="vertical" style={{ width: '100%' }} size="middle">
      <Alert type="info" showIcon message="临时密码仅展示这一次，首次登录后需要修改。" />
      {value?.tenant_code && <Typography.Text>所属租户：<Typography.Text strong>{value.tenant_code}</Typography.Text></Typography.Text>}
      <Typography.Text>账号：<Typography.Text code copyable>{value?.username}</Typography.Text></Typography.Text>
      <Space.Compact style={{ width: '100%' }}><Input aria-label="临时密码" readOnly value={value?.temporary_password || ''} onFocus={event => event.target.select()} /><Button onClick={() => void copy()}>复制密码</Button></Space.Compact>
      {value?.tenant_code && <Typography.Link href={tenantEntryUrl(value.tenant_code) + '/login'} target="_blank" rel="noreferrer">打开该租户登录入口</Typography.Link>}
      <Checkbox checked={saved} onChange={event => setSaved(event.target.checked)}>我已保存临时登录信息</Checkbox>
    </Space>
  </Modal>;
}
