import { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Select, Space, Typography } from 'antd';
import { API_BASE, ApiError, errorText } from '../api';

export type TenantLoginOption = { code: string; name: string };
type Directory = { items: TenantLoginOption[]; loading: boolean; error: string; retry: () => void };
export function useTenantLoginOptions(enabled = true): Directory {
  const [items, setItems] = useState<TenantLoginOption[]>([]);
  const [loading, setLoading] = useState(enabled); const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError('');
    void (async () => {
      try {
        // This is public entry metadata; account sessions are never sent here.
        const response = await fetch(API_BASE + '/tenant/v1/login-options', { signal: controller.signal, credentials: 'omit', headers: { Accept: 'application/json' } });
        const data = await response.json();
        if (!response.ok) throw new ApiError(response.status, data?.detail);
        if (!Array.isArray(data?.items) || !data.items.every((item: TenantLoginOption) => typeof item?.name === 'string' && typeof item?.code === 'string' && /^[a-z0-9][a-z0-9-]{1,63}$/.test(item.code))) throw new Error('Invalid tenant directory');
        if (!controller.signal.aborted) setItems(data.items);
      } catch (cause) {
        if (!controller.signal.aborted) { setItems([]); setError(cause instanceof ApiError ? errorText(cause) : '租户列表暂时无法加载，请重试。'); }
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [enabled, attempt]);
  return { items, loading, error, retry };
}
export default function TenantLoginSelector({ directory, value, onChange, id }: { directory: Directory; value?: string; onChange?: (code: string) => void; id?: string }) {
  return <Space direction="vertical" size="small" style={{ width: '100%' }}>
    <Select id={id} value={value} onChange={onChange} aria-label="选择租户" size="large" style={{ width: '100%', height: 44 }}
      showSearch optionFilterProp="label" loading={directory.loading} disabled={directory.loading || !!directory.error || !directory.items.length}
      placeholder={directory.loading ? '正在加载租户…' : '请选择所属租户'}
      notFoundContent="没有匹配的租户" options={directory.items.map(item => ({ value: item.code, label: item.name + '（' + item.code + '）' }))} />
    {directory.error && <Alert showIcon type="error" message={directory.error} action={<Button onClick={directory.retry} style={{ minHeight: 44 }}>重新加载</Button>} />}
    {!directory.loading && !directory.error && !directory.items.length && <Typography.Text type="secondary">当前没有开放登录的租户，请联系管理员。</Typography.Text>}
  </Space>;
}
