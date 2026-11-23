# MiniTV 多站点 V1 生产上线验收

- 版本：v2026.11.16.1；完成时间：2026-11-16 14:56:07（Asia/Shanghai）。
- 发布源码：`f5b8238f25bb22fecd4986e8f7ddca8306f9cf44`，[实现 PR #14](https://github.com/88jj-com/jjtv/pull/14)。合并后的源码树与已验收候选完全一致。
- [候选完整检查](https://github.com/88jj-com/jjtv/actions/runs/43815975002)及[主分支完整检查](https://github.com/88jj-com/jjtv/actions/runs/43819747638)均通过：后端 1,365 项、部署与代理工具 25 项，以及 Web/H5/后台浏览器验收。候选自动审查完成，无未解决问题。
- 唯一发布入口：`scripts/release.py run --target video`，子命令 `scripts/deploy_video.py --host video --phase http`；共享锁串行执行，现有 HTTPS 证书保留。

## 生产入口

- [移动 H5](https://jjtv-h5.8jj.net/)
- [桌面 Web](https://jjtv.8jj.net/)
- [JJTV 后台](https://jjtv-admin.8jj.net/admin/t/jjtv/dashboard)
- [API 健康检查](https://jjtv-api.8jj.net/health)

## 接收、备份和迁移

- 运行镜像：`jjtv-api:f5b8238f25bb22fecd4986e8f7ddca8306f9cf44`；接收目录：`/opt/jjtv/releases/f5b8238f25bb-3da77dfec15a`。
- 版本回执与实际镜像来源一致，服务器 23 个前端文件的 SHA-256 全部匹配正式构建清单。
- 私有备份：`/opt/jjtv/backups/20261116T065506Z-f5b8238f25bb22fecd4986e8f7ddca8306f9cf44`。数据库完整解码验证及数据库/config校验和均通过；备份与凭据不入库。
- Schema：`a1114cancel_intent → c9e13a2d6f42`。整库保留校验通过；8 个旧员工账号、41,655 条原审计记录保持，未初始化或重置任何员工密码。旧数据字段、余额与权益按升级 guard 验证保留。
- JJTV Web/H5 地址由原配置验证回填；发布锁已释放。其他 15 个服务容器的 ID 和镜像在发布前后完全一致。

## 公网与页面验收

- 36 个严格 GET 检查中 35 个直接通过。唯一摘要差异为隐私页的 Cloudflare 邮件保护：还原 3 个邮件链接并移除其 1 个解码脚本后，与正式文件逐字相同；还原摘要为 `6070cdb1fd67857f941e0eb9dde93a47a0593880e2dd2aae02c7e05f698d3cce`。
- Web、H5 和 API 的 bootstrap 均解析为 JJTV，Web/H5 地址正确；三端 HTML 使用 no-store，后台 CLI 下载与健康接口正常。未知域名 bootstrap 返回 404，开发支付回调保持 404。
- 后台域名两种签名回调均到达 API；未配置签名密钥时返回 JSON 503，未执行实际支付。完整验签、重复事件、退款和商户隔离由 CI 专项覆盖。
- 实际浏览器验证：H5 390×844 的五个底部入口、追剧/我的独立选中、中英文切换；桌面 1440×1000；桌面域名 390×844 的 `#mylist` 正确进入移动版且无横向溢出；后台登录入口正常。三个页面未观察到控制台错误。

## 当前配置边界

- 生产暂无已上架内容，3 部完整演示剧及演示账号/数据库仍仅保留在本地，没有混入生产发布包；播放器真实媒体流程由 CI 场景验收，本次没有声称生产内容播放验收。
- 生产 Stripe webhook signing secret 尚未配置，收款结算需配置商户及签名密钥后启用。
- 本次更新现有四个 JJTV 域名。新国家站点的默认/自定义域名仍需部署者配置 DNS、反向代理和 HTTPS；没有创建新的公网域名。
- 外部采购市场与原生 App 多品牌打包不属于此 V1 发布。
