import type { ReactNode } from 'react';
import { ArrowRightOutlined, GlobalOutlined, PlaySquareOutlined, TeamOutlined } from '@ant-design/icons';
import workspacePhoto from '../assets/login-workspace.webp';
import './auth-layout.css';

export default function AuthLayout({ children, chooser = false }: { children: ReactNode; chooser?: boolean }) {
  return <main className="auth-layout" data-testid="auth-layout">
    <aside className="auth-hero" data-testid="auth-hero" aria-label="MiniTV 团队工作空间">
      <img className="auth-hero-image" src={workspacePhoto} alt="" width={1122} height={1402} loading="eager" decoding="async" />
      <div className="auth-hero-shade" />
      <div className="auth-hero-label"><span className="auth-live-mark" /> STORIES WITHOUT BORDERS</div>
      <div className="auth-story">
        <p className="auth-story-eyebrow">创意有无限可能</p>
        <h2>让好故事，<br />走向更大的世界。</h2>
        <p className="auth-story-description">连接内容、团队与每一个市场，<br className="auth-story-break" />让短剧运营更从容。</p>
        <ul className="auth-capabilities">
          <li><GlobalOutlined aria-hidden />多站点协同</li>
          <li><PlaySquareOutlined aria-hidden />内容灵活分发</li>
          <li><TeamOutlined aria-hidden />团队高效协作</li>
        </ul>
      </div>
      <span className="auth-hero-footnote">A workspace for your next great story.</span>
    </aside>
    <section className="auth-panel" data-testid="auth-panel" aria-label="管理后台登录">
      <header className="auth-header">
        <a className="auth-brand" href="/admin/login" aria-label="MiniTV 管理入口">
          <svg viewBox="0 0 36 36" width="36" height="36" aria-hidden="true"><rect width="36" height="36" rx="10" fill="currentColor" /><path d="M14 10.5 26 18 14 25.5Z" fill="white" /></svg>
          <span>MiniTV</span><span className="auth-brand-caption">管理控制台</span>
        </a>
        {!chooser && <a className="auth-switch" href="/admin/login">切换入口<ArrowRightOutlined aria-hidden /></a>}
      </header>
      <div className="auth-content"><div className="auth-form-wrap">{children}</div></div>
      <footer className="auth-footer">© {new Date().getFullYear()} MiniTV<span>让内容连接世界</span></footer>
    </section>
  </main>;
}
