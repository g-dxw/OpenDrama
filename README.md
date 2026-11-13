# JJTV

JJTV 是基于 MiniTV 的短剧分发与运营项目，包含官网、H5、Flutter App、管理后台和 FastAPI 服务。

当前完成 JJTV 品牌更名与独立部署准备。站点级租户管理、通过适配层接入 india-short-drama 属于后续建设范围，目前尚未实现。

## 访问地址

| 服务 | 地址 |
| --- | --- |
| 官网 | https://jjtv.8jj.net |
| H5 | https://jjtv-h5.8jj.net |
| 管理后台 | https://jjtv-admin.8jj.net |
| API | https://jjtv-api.8jj.net |

这些是 JJTV 的部署目标，公网可用性取决于 DNS、证书和服务器发布状态。老站的 8jj.net / 8jj.ai 域名继续由原系统提供服务。

## 技术栈

React 18 · TypeScript · Vite · Ant Design · FastAPI · SQLAlchemy · PostgreSQL · Redis · Flutter。媒体、邮件和支付分别提供 Bunny、Resend、Stripe 集成，启用时需单独配置服务凭据。

## 目录

- `backend/`：业务 API、数据库迁移和测试。
- `admin/`：管理后台与上剧方后台。
- `web/`：官网。
- `h5/`：移动网页应用。
- `app/`：Flutter Android / iOS 应用。
- `cli/`：命令行管理工具，兼容原有 `minitv` 命令。
- `scripts/`：开发、数据导入和发布工具。
- `docs/`：架构、业务与发布文档。

## 本地运行

需要 Python 3.11+、Node.js 18+ 和 Docker；移动端开发另需 Flutter。

```bash
git clone https://github.com/88jj-com/jjtv.git
cd jjtv
cp .env.example .env
docker compose up -d
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
(cd backend && ../.venv/bin/alembic upgrade head)
.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 18110
```

分别在 admin、web、h5 目录执行 `npm ci` 和 `npm run dev`。健康检查为 `GET /health`。

创建首个后台账号：

```bash
cd backend
SEED_ADMIN_PASSWORD='请设置独立密码' ../.venv/bin/python -m app.seed_admin
```

本地默认使用 `BUNNY_MODE=fake`，不填第三方密钥也可联调。支付和邮件未配置时不可视为可用的商业服务。

Flutter 连接 JJTV API：

```bash
cd app
flutter pub get
flutter run --dart-define=API_BASE=https://jjtv-api.8jj.net
```

## 发布

所有发布必须通过 `python3 scripts/release.py`，遵守共享发布锁、先提交源码和自动版本号规则。发布前后执行 `python3 scripts/release.py status`。

`video` 上的 JJTV 使用独立服务、数据库与 Redis，并添加独立域名路由；不接管旧站域名或复用旧站业务数据。详细步骤见 [video 部署文档](docs/video-deployment.md)。原上游 `scripts/deploy_server.sh` 针对原环境，不能直接用于现有 video 服务器。

## 兼容与来源

品牌展示统一为 JJTV。为兼容已有开发工具和应用配置，内部数据库默认名称、存储键、CLI 命令及 Android/iOS 应用标识暂保留原名称；新品牌 App 安装包需另行构建。

已有 CLI 用户请创建并切换独立配置，避免继续使用旧 API：

```bash
minitv config set-key --profile jjtv --base-url https://jjtv-api.8jj.net
minitv config use jjtv
```

项目基于 MiniTV，保留原始 [MIT License](LICENSE) 和上游版权声明。部分原始截图、设计稿、品牌源文件与历史验收记录保留原名称，作为来源记录。

架构与流程：[技术架构](docs/technical-architecture.md)、[软件架构](docs/software-architecture.md)、[发布流程](docs/release-process.md)。
