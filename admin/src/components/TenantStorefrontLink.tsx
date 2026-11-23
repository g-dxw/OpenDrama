import { Button, Tooltip } from 'antd';
import { ExportOutlined } from '@ant-design/icons';
import type { BusinessTenant } from '../identity';

function storefrontUrl(tenant: BusinessTenant): string | undefined {
  for (const value of [tenant.web_url, tenant.h5_url]) {
    if (typeof value !== 'string' || !value.trim()) continue;
    try {
      const url = new URL(value.trim());
      if (['https:', 'http:'].includes(url.protocol) && url.hostname && !url.username && !url.password) return url.href;
    } catch { /* Try the next configured surface. */ }
  }
}

export default function TenantStorefrontLink({ tenant }: { tenant: BusinessTenant }) {
  const href = storefrontUrl(tenant);
  return <Tooltip title={href ? '在新标签页打开当前平台前台' : '当前平台未配置前台地址，请联系管理员'}>
    <span className="tenant-storefront-action">
      <Button className="tenant-storefront-link" type="text" aria-label="访问前台" disabled={!href} href={href} target={href ? '_blank' : undefined} rel={href ? 'noopener noreferrer' : undefined} icon={<ExportOutlined aria-hidden />}>
        <span className="tenant-storefront-label">访问前台</span>
      </Button>
    </span>
  </Tooltip>;
}
