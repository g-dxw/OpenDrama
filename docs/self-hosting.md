# 自行部署 OpenDrama 多站点版本

一套安装服务一家公司。公司总控创建国家或品牌站点，每站使用独立的运营账号、观众、会员、钱包、订单和统计。公司内容库通过明确授权向站点分发草稿。

## 环境与配置

准备 Linux、Docker Engine、Docker Compose v2，以及指向反向代理的域名。首次配置 `*.streams.example.com` 的 DNS 与 HTTPS；默认桌面站为 `code.streams.example.com`，H5 为 `m-code.streams.example.com`，后台为 `admin.streams.example.com/admin/`。自定义 H5 域名需要在 nginx 的 `$site_root` 映射中指向 `/srv/h5`。

```sh
python3 deploy/selfhost/init_env.py
```

编辑生成的私有 `deploy/selfhost/.env`：设置公司名、域名、管理员入口及 Bunny 资源。环境模板不包含当前 JJTV 的域名或凭据。生产播放必须配置 Bunny Stream；邮件验证码登录必须配置邮件服务。Stripe 账户从总控界面创建并显式绑定到站点。未配置的集成不会请求 JJTV 现有账户。

Bunny Stream 的库、API 密钥、CDN 主机和视频令牌密钥使用自己的账户配置。封面及本站上传字幕还需配置 Storage Zone、上传密码和图片 CDN；`BUNNY_IMAGE_TOKEN_KEY` 必须与该 CDN 的令牌认证密钥一致，用于签发字幕访问地址，不能用安装脚本生成的加密密钥代替。

`SETTINGS_ENCRYPTION_KEY` 用于已保存凭据；必须与数据库一起备份，不能在升级时重新生成。Redis 存放会话与短期任务状态，生产环境必须保留其数据卷。

```sh
docker compose --env-file deploy/selfhost/.env -f deploy/selfhost/compose.yml up -d --build
docker compose --env-file deploy/selfhost/.env -f deploy/selfhost/compose.yml exec api python -m app.seed_admin
```

首个总控账号的随机初始密码仅在初始化命令中显示一次；再次运行不会覆盖已有账号。通过反向代理访问后台创建站点，下载或妥善保存该站管理员的临时密码，首次登录完成密码更换。

新安装不会在站点列表中显示尚未使用的 JJTV / India 兼容占位记录；原有业务有账号使用记录、观众、内容或域名时仍保留可见。可选 India 连接器显式启用后可管理。

Compose 默认只将 HTTP 入口绑定本机 `127.0.0.1:8080`；部署侧 HTTPS 反代转发到该端口并保留原始 `Host`。公司实例内部服务不向公网暴露数据库、Redis 或 API 端口。无需旧 India 系统网络、凭据或已有 Docker 网络。

## 站点、域名和品牌

- 创建站点时自动登记默认泛域名；访问必须匹配已激活的站点域名。
- 自定义域名登记后，添加界面给出的 `_minitv-verification.<hostname>` TXT 记录，再执行验证。域名通过所有权验证后，仍需部署侧正确配置 DNS 路由和 TLS。
- 在站点设置中选择默认语言、启用语言、币种、时区及已验证的 Web/H5 地址。所有排期以 UTC 保存。
- 品牌编辑保存到草稿；预览链接短时有效且绑定站点。发布后前台才读取新配置；回退会创建一个新的发布版本。
- 安装默认关闭旧 JJTV 域名兼容。已有 JJTV 升级可显式配置 `SITE_LEGACY_HOSTS` 和 `SITE_LEGACY_TENANT`，不能将未知主机映射到默认业务。

## 内容与地域授权

总控上传母版和分集，等待 Bunny 转码并执行服务端同步、质检批准，再给目标站点授予语言、地域及期限范围。站点引入后得到独立草稿，自行编辑标题、封面、价格及排期。重复引入同一授权返回原记录。

旧内容默认保持本站私有；视频资源共享不代表内容自动获得跨站版权。母版新版本不会覆盖已经引入的分集和购买权益。

普通引入返回本站已有版本；在“采用新版”操作中明确确认后，创建新的独立草稿，保留旧版剧目及分集 ID、发布状态和已购记录。销售截止 `sales_until` 停止新增销售和上架；播放截止 `play_until`、撤销或归档授权会停止签发新播放凭证，已签发凭证到期失效。保留历史权益记录不代表可绕过当前播放授权。

地域受限内容要求可信来源的国家代码。默认入口清除客户端的 `X-Site-Country`，不把观众填写的国家当作证据。若使用可信地理识别反代，必须限制其网络来源、由该反代覆盖请求头，并调整入口转发及 `SITE_TRUSTED_PROXIES`；配置完成前受限内容拒绝签发播放凭证。地域不明不能绕过授权。

## 支付

Stripe 商户属于安装实例，由总控维护。每个站点显式绑定商户；复用商户需要允许复用，订单和权益仍各站独立。为每个商户登记后台显示的专属 webhook，使用对应签名密钥。续费和退款沿原订单与商户快照处理，不能依赖请求携带的站点 ID。

从旧版本升级时，若已有收款商户尚未验证 Stripe 身份，绑定另一商户前应先执行旧商户的连接测试。验证后系统按 Stripe 账户身份识别不同名称下的同一商户，避免绕过显式复用确认。该检查不阻断旧订单的已验签回调。

订阅模式需要配置对应的真实 Stripe Price；未配置价格的套餐不能作为有效订阅出售。Bunny fake、Stripe fake 和测试邮件只用于独立开发环境，不代表真实媒体或真实支付验收。

## 升级、备份与恢复

先备份数据库和私有环境配置，在测试环境验证升级，再重建镜像并运行迁移。迁移保留历史用户、剧目、分集、余额、订单及权益 ID；不支持把已拆分的多站数据直接降回全局数据结构。

```sh
sh deploy/selfhost/backup.sh /absolute/path/minitv-backup.dump
sh deploy/selfhost/restore.sh /absolute/path/minitv-backup.dump
```

恢复脚本建立新数据库并导入，不覆盖运行中的数据库。核对 Alembic 版本、用户/订单数、钱包流水及已购分集，再将安装切换到恢复库。Bunny 媒体和加密密钥需要分别保全；数据库备份不包含外部视频文件。

原 JJTV 生产环境继续遵循 `AGENTS.md` 和 `docs/release-process.md` 的唯一发布入口；通用安装命令不替代现网发布流程。
