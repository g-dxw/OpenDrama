import { setOperatorSite } from './site-format';
import { SiteSettingsPage, BrandPage, MerchantBindingPage } from './pages/SiteSettings';
import LibraryPage from './pages/Library';
import { useCallback, useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Space, Spin, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { api, completeLogout, errorText, forceReauth, getToken } from './api';
import { CURRENT_ENTRY, contextApi, supportSession } from './entry';
import { IdentityContext, type Identity } from './identity';
import { MeContext, type Me } from './me';
import { TenantContext, type Tenant } from './tenant';
import { setCurrentTenantId } from './tenant-scope';
import { navigationLeaves, visibleNavigation, type NavigationNode } from './navigation';
import BackOfficeShell from './components/BackOfficeShell';
import TenantStorefrontLink from './components/TenantStorefrontLink';
import { ChangePasswordModal } from './components/ChangePasswordModal';
import EntryChooser from './pages/EntryChooser';
import Login from './pages/Login';
import Tenants from './pages/Tenants';
import PlatformAudit from './pages/PlatformAudit';
import TenantStaff from './pages/TenantStaff';
import TenantRolesPage from './pages/TenantRolesPage';
import IndiaContent from './pages/IndiaContent';
import Dramas from './pages/Dramas';
import DramaEdit from './pages/DramaEdit';
import Reviews from './pages/Reviews';
import Tags from './pages/Tags';
import GroupsPage, { GroupDetailPage } from './pages/Groups';
import { HomeOpsPage } from './pages/HomeOps';
import Moderation from './pages/Moderation';
import Rewards from './pages/Rewards';
import ApiKeys from './pages/ApiKeys';
import Suppliers from './pages/Suppliers';
import Settlements from './pages/Settlements';
import PartnerDramas from './pages/PartnerDramas';
import PartnerDramaEdit from './pages/PartnerDramaEdit';
import PartnerApiKeys from './pages/PartnerApiKeys';
import PartnerStats from './pages/PartnerStats';
import PartnerSettlements from './pages/PartnerSettlements';
import { CommentsPage, DashboardPage, UsersPage } from './pages/StagePages';

import LoginSettings from './pages/LoginSettings';
import {CoinPacksPage,CoinLedgerPage,StripeAccountsPage,CommercePlansPage,CommerceFinancePage} from './pages/CommercePages';
import {SupportWorkbenchPage,SupportQueuePage,SupportReportsPage,SupportSettingsPage} from './pages/SupportPages';
import AttributionPage from './pages/AttributionPage';

const PLATFORM_NAV: NavigationNode[] = [{ key: '/tenants', label: '站点管理' }, { key: '/library', label: '公司内容库' }, { key: '/stripe-accounts', label: '公司收款账号' }, { key: '/audit', label: '平台审计' }];
function identityMe(value: Identity): Me {
  return { ...value.user, account_scope: value.account_scope, session_kind: value.session_kind, is_platform_admin: value.is_platform_admin, is_tenant_admin: value.is_tenant_admin,
    permissions: value.permissions, menu_keys: value.menu_keys, enabled_modules: value.enabled_modules, tenant_role_name: value.role_name };
}
function validIdentity(value: Identity): boolean {
  if (!value?.user || !Array.isArray(value.permissions) || !Array.isArray(value.menu_keys) || !Array.isArray(value.enabled_modules)) return false;
  if (CURRENT_ENTRY.kind === 'platform') return value.account_scope === 'platform' && value.session_kind === 'normal' && value.is_platform_admin;
  if (CURRENT_ENTRY.kind === 'tenant') return value.account_scope === 'tenant' && value.session_kind === 'normal' && value.tenant?.code === CURRENT_ENTRY.code;
  if (CURRENT_ENTRY.kind === 'support') return value.session_kind === 'support' && value.tenant?.code === CURRENT_ENTRY.code;
  return false;
}
export default function App() {
  const token = getToken(); const [identity, setIdentity] = useState<Identity | null>(null); const [state, setState] = useState<'loading' | 'ready' | 'failed'>(token ? 'loading' : 'ready');
  const [error, setError] = useState(''); const [logoutLoading, setLogoutLoading] = useState(false); const [passwordOpen, setPasswordOpen] = useState(false);
  const acceptIdentity = useCallback((value: Identity) => { if (!validIdentity(value)) throw new Error('登录身份与当前入口不匹配，请从正确入口重新登录。'); setCurrentTenantId(value.tenant?.id || ''); setOperatorSite(value.tenant ?? {}); setIdentity(value); setState('ready'); setError(''); }, []);
  const refresh = useCallback(async () => { const value = await api<Identity>(contextApi() + '/context'); acceptIdentity(value); }, [acceptIdentity]);
  useEffect(() => {
    if (!token) { setOperatorSite({}); setIdentity(null); setState('ready'); if (CURRENT_ENTRY.kind === 'support') forceReauth('support_expired'); return; }
    let live = true; setState('loading');
    void api<Identity>(contextApi() + '/context').then(value => { if (live) acceptIdentity(value); }).catch(e => { if (live) { setError(errorText(e)); setState('failed'); } });
    return () => { live = false; };
  }, [token, acceptIdentity]);
  const logout = useCallback(async () => {
    setLogoutLoading(true);
    try { await api(contextApi() + '/auth/logout', { method: 'POST', body: '{}' }); } catch { /* Local logout always completes. */ }
    setOperatorSite({}); completeLogout();
  }, []);
  if (CURRENT_ENTRY.kind === 'choose') return <EntryChooser />;
  if (!token) return CURRENT_ENTRY.kind === 'support' ? <div className="entry-page"><Spin /></div> : <Login onLogin={acceptIdentity} />;
  if (state === 'loading') return <div className="entry-page"><Card><Space><Spin /><Typography.Text>正在验证当前入口的登录身份…</Typography.Text></Space></Card></div>;
  if (state === 'failed' || !identity) return <div className="entry-page"><Card className="entry-card"><Space direction="vertical" size="middle"><Alert showIcon type="error" message={error || '暂时无法验证身份'} /><Button type="primary" onClick={() => { setState('loading'); void refresh().catch(e => { setError(errorText(e)); setState('failed'); }); }}>重试</Button><Button onClick={() => forceReauth('manual')}>重新登录当前入口</Button></Space></Card></div>;
  const me = identityMe(identity);
  return <IdentityContext.Provider value={identity}><MeContext.Provider value={me}>
    {identity.user.must_change_password && identity.session_kind !== 'support' ? <div className="entry-page"><Card className="entry-card"><Typography.Title level={4}>请先设置自己的密码</Typography.Title><Typography.Text type="secondary">当前临时密码尚未更换，业务功能将在修改后开放。</Typography.Text><Button block style={{ marginTop: 16 }} onClick={() => void logout()}>退出登录</Button></Card><ChangePasswordModal open force onClose={() => {}} onChanged={acceptIdentity} onLogout={() => void logout()} /></div>
      : CURRENT_ENTRY.kind === 'platform' ? <PlatformApplication identity={identity} onLogout={logout} loggingOut={logoutLoading} onPassword={() => setPasswordOpen(true)} />
      : <TenantApplication identity={identity} refresh={refresh} onLogout={logout} loggingOut={logoutLoading} onPassword={identity.session_kind === 'support' ? undefined : () => setPasswordOpen(true)} />}
    {identity.session_kind !== 'support' && !identity.user.must_change_password && <ChangePasswordModal open={passwordOpen} onClose={() => setPasswordOpen(false)} onChanged={acceptIdentity} />}
  </MeContext.Provider></IdentityContext.Provider>;
}
function PlatformApplication({ identity, onLogout, loggingOut, onPassword }: { identity: Identity; onLogout: () => void; loggingOut: boolean; onPassword: () => void }) {
  const location = useLocation(); const navigate = useNavigate(); const notice = new URLSearchParams(location.search).get('notice');
  return <BackOfficeShell brand="OpenDrama 公司总控" items={PLATFORM_NAV} route={location.pathname} scope="platform" onNavigate={key => navigate(key)} accountName={identity.user.display_name || identity.user.username} accountLabel="平台管理员" onLogout={onLogout} loadingLogout={loggingOut} onPassword={onPassword}>
    {notice === 'support-ended' && <Alert showIcon type="info" message="临时租户访问已结束，已返回平台管理。" style={{ marginBottom: 16 }} />}
    <Routes><Route path="/tenants" element={<Tenants />} /><Route path="/library" element={<LibraryPage platform />} /><Route path="/stripe-accounts" element={<StripeAccountsPage platform />} /><Route path="/audit" element={<PlatformAudit />} /><Route path="*" element={<Navigate to="/tenants" replace />} /></Routes>
  </BackOfficeShell>;
}
function TenantApplication({ identity, refresh, onLogout, loggingOut, onPassword }: { identity: Identity; refresh: () => Promise<void>; onLogout: () => void; loggingOut: boolean; onPassword?: () => void }) {
  const location = useLocation(); const navigate = useNavigate(); const [refreshing, setRefreshing] = useState(false); const [error, setError] = useState('');
  const tenant = identity.tenant!; const support = identity.session_kind === 'support'; const systemAdmin = identity.is_tenant_admin || support; const partner = identity.user.role === 'partner';
  const current: Tenant = { ...tenant, permissions: identity.permissions, menu_keys: identity.menu_keys, role_name: identity.role_name };
  const refreshPermissions = useCallback(async () => { setRefreshing(true); try { await refresh(); setError(''); } catch (e) { setError(errorText(e)); } finally { setRefreshing(false); } }, [refresh]);
  useEffect(() => { const focus = () => void refreshPermissions(); window.addEventListener('focus', focus); return () => window.removeEventListener('focus', focus); }, [refreshPermissions]);
  useEffect(() => {
    if (!support) return; const expires = supportSession()?.expires_at; if (!expires) return; const remaining = new Date(expires).getTime() - Date.now();
    if (!Number.isFinite(remaining)) return; const timer = window.setTimeout(() => forceReauth('support_expired'), Math.max(0, remaining)); return () => window.clearTimeout(timer);
  }, [support]);
  const items = visibleNavigation({ menuKeys: identity.menu_keys, platformAdmin: false, partner, india: tenant.kind === 'india_legacy', tenantAdmin: systemAdmin });
  const leaves = navigationLeaves(items); const first = leaves[0]?.key; const requested = [...leaves].sort((a, b) => b.key.length - a.key.length).find(item => location.pathname === item.key || location.pathname.startsWith(item.key + '/'));
  const reviewDetail = tenant.kind === 'native' && /^\/dramas\/[^/]+$/.test(location.pathname) && identity.permissions.includes('reviews.view');
  const contentKey = tenant.id + ':' + identity.permissions.slice().sort().join(',') + ':' + identity.menu_keys.slice().sort().join(',') + ':' + systemAdmin + ':' + identity.enabled_modules.slice().sort().join(',');
  const session = supportSession();
  const banner = support ? <Alert className="support-session-banner" type="warning" showIcon message={'平台临时访问 · ' + tenant.name}
    description={<Space wrap><span>操作人：{session?.operator || identity.user.display_name || identity.user.username}</span>{session?.expires_at && <span>到期：{new Date(session.expires_at).toLocaleString()}</span>}<span>本次访问有时限，操作将记录审计。</span></Space>}
    action={<Button onClick={onLogout} loading={loggingOut}>退出租户，返回平台</Button>} /> : undefined;
  return <TenantContext.Provider value={{ current, items: [current], refresh, select: () => {} }}><BackOfficeShell brand={tenant.name + ' 后台'} items={items} route={location.pathname} scope={tenant.id} onNavigate={key => navigate(key)} accountName={identity.user.display_name || identity.user.username}
    accountLabel={support ? '平台支持访问' : identity.is_tenant_admin ? '租户管理员' : identity.role_name} onLogout={onLogout} loadingLogout={loggingOut} onPassword={onPassword} contentKey={contentKey} banner={banner}
    headerExtra={<Space size={4}><TenantStorefrontLink tenant={tenant} /><Button type="text" aria-label="刷新当前租户权限" icon={<ReloadOutlined aria-hidden />} loading={refreshing} onClick={() => void refreshPermissions()} /></Space>}>
    {error ? <Alert type="error" showIcon message={error} action={<Button onClick={() => void refreshPermissions()}>重试</Button>} /> : tenant.status !== 'active' ? <Alert type="warning" showIcon message="此租户已暂停" description="请联系平台管理员重新启用。" /> : !first ? <Alert type="info" showIcon message="当前账号没有已开放的菜单" description="请联系本租户管理员分配权限。" /> : !requested && !reviewDetail && location.pathname !== '/' ? <Navigate to={first} replace /> : <BusinessRoutes first={first} systemAdmin={systemAdmin} />}
  </BackOfficeShell></TenantContext.Provider>;
}
function BusinessRoutes({ first, systemAdmin }: { first: string; systemAdmin: boolean }) {
  return <Routes>
    <Route path="/" element={<Navigate to={first} replace />} />
    <Route path="/site-settings" element={systemAdmin ? <SiteSettingsPage /> : <Navigate to={first} replace />} /><Route path="/brand" element={systemAdmin ? <BrandPage /> : <Navigate to={first} replace />} /><Route path="/merchant-binding" element={systemAdmin ? <MerchantBindingPage /> : <Navigate to={first} replace />} /><Route path="/library" element={<LibraryPage />} /><Route path="/login-settings" element={<LoginSettings/>}/><Route path="/stripe-accounts" element={<StripeAccountsPage/>}/><Route path="/coin-packs" element={<CoinPacksPage/>}/><Route path="/coin-ledger" element={<CoinLedgerPage/>}/><Route path="/attribution" element={<AttributionPage/>}/><Route path="/support/workbench" element={<SupportWorkbenchPage/>}/><Route path="/support/queue" element={<SupportQueuePage/>}/><Route path="/support/reports" element={<SupportReportsPage/>}/><Route path="/support/settings" element={<SupportSettingsPage/>}/><Route path="/dashboard" element={<DashboardPage />} /><Route path="/dramas" element={<Dramas />} /><Route path="/dramas/:id" element={<DramaEdit />} /><Route path="/reviews" element={<Reviews />} />
    <Route path="/groups" element={<GroupsPage />} /><Route path="/groups/:id" element={<GroupDetailPage />} /><Route path="/home-ops" element={<HomeOpsPage />} /><Route path="/tags" element={<Tags />} />
    <Route path="/users" element={<UsersPage />} /><Route path="/rewards" element={<Rewards />} /><Route path="/comments" element={<CommentsPage />} /><Route path="/moderation" element={<Moderation />} />
    <Route path="/plans" element={<CommercePlansPage />} /><Route path="/finance" element={<CommerceFinancePage />} /><Route path="/suppliers" element={<Suppliers />} /><Route path="/settlements" element={<Settlements />} />
    <Route path="/staff" element={systemAdmin ? <TenantStaff /> : <Navigate to={first} replace />} /><Route path="/roles" element={systemAdmin ? <TenantRolesPage /> : <Navigate to={first} replace />} /><Route path="/api-keys" element={systemAdmin ? <ApiKeys /> : <Navigate to={first} replace />} />
    <Route path="/india-content" element={<IndiaContent />} />
    <Route path="/partner/dramas" element={<PartnerDramas />} /><Route path="/partner/dramas/:id" element={<PartnerDramaEdit />} /><Route path="/partner/stats" element={<PartnerStats />} /><Route path="/partner/settlements" element={<PartnerSettlements />} /><Route path="/partner/api-keys" element={<PartnerApiKeys />} />
    <Route path="*" element={<Navigate to={first} replace />} />
  </Routes>;
}
