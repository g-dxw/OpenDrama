import type { ReactNode } from 'react';
import { Card, Col, Row, Space, Statistic, Typography } from 'antd';
import './management-page.css';

export function ManagementPageHeader({ title, description, extra }: { title: string; description?: ReactNode; extra?: ReactNode }) {
  return <div className="management-page-header"><div className="management-page-heading"><Typography.Title level={4}>{title}</Typography.Title>{description && <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>}</div>{extra && <Space wrap className="management-page-actions">{extra}</Space>}</div>;
}
export function ManagementMetrics({ items }: { items: { title: string; value: number | string; hint?: ReactNode; prefix?: ReactNode }[] }) {
  return <Row gutter={[16, 16]} className="management-metrics">{items.map(item => <Col xs={12} xl={24 / Math.min(items.length, 4)} key={item.title}><Card size="small"><Statistic title={item.title} value={item.value} prefix={item.prefix} />{item.hint && <Typography.Text type="secondary" className="management-metric-hint">{item.hint}</Typography.Text>}</Card></Col>)}</Row>;
}
export function ManagementFormSection({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return <section className="management-form-section"><Typography.Title level={5}>{title}</Typography.Title>{description && <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>}{children}</section>;
}
