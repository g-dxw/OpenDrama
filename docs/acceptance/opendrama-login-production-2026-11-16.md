# OpenDrama 品牌与管理后台登录页生产验收

- 版本：v2026.11.16.2，2026-11-16 17:37:59（Asia/Shanghai）发布完成，退出码 0。
- 运行源码：`700d2262fc81993f90267981edc568b02467f62b`；[PR #16](https://github.com/88jj-com/jjtv/pull/16) 已合并。
- 发布入口：`scripts/release.py run --target video`，子命令 `scripts/deploy_video.py --host video --phase http`；干净源码、共享发布锁，保留既有 TLS。

## 交付与线上入口

平台名称统一为 OpenDrama，管理后台采用左侧团队图片、右侧白色表单的登录布局。公司总控入口移到右上角，显示“管理系统登录”；中央主入口使用“选择平台”，对应提示、按钮及可访问名称同步调整。各站沿用自己的品牌配置，JJTV 站点保持 JJTV。

- [平台选择与登录入口](https://jjtv-admin.8jj.net/admin/login)
- [管理系统登录](https://jjtv-admin.8jj.net/admin/platform/login)
- [JJTV 平台登录](https://jjtv-admin.8jj.net/admin/t/jjtv/login)
- [桌面 Web](https://jjtv.8jj.net/)、[移动 H5](https://jjtv-h5.8jj.net/)、[API 健康检查](https://jjtv-api.8jj.net/health)

## 检查与构建证据

[候选完整检查](https://github.com/88jj-com/jjtv/actions/runs/43834168013) 的后台、Web、H5、后端及迁移任务均通过。[主分支检查](https://github.com/88jj-com/jjtv/actions/runs/43840306049) 的后端、迁移及三个前端任务均已通过；后端 1,365 项、仓库检查 25 项全部通过。主分支 CI 的 admin 4 个、H5 4 个、Web 16 个产物，与实际发布的 24 个文件逐项摘要相同。

接收目录为 `/opt/jjtv/releases/700d2262fc81-c8853224415a`；服务器 current、发布回执及运行 API 镜像均指向上述源码。回执列出的 24 个资源及服务器实际文件，均与正式构建清单完全一致，无缺失、额外文件或摘要差异。

## 公网与页面验收

37 项只读 GET 检查中 36 项直接通过。唯一差异为隐私页的 Cloudflare 邮件保护：还原 3 个邮件链接并移除 1 个解码脚本后，内容与正式文件完整一致，恢复摘要为 `e1293cf74e1e647a940698eb4c049c1dd8d16449a80cea16bd5204ef01997e9d`。原始检查记录保留该差异，转换证据单独保存。

Web、H5、API 的 bootstrap 均解析为 JJTV，公司名为 OpenDrama，站点品牌为 JJTV；Web/H5 地址正确。三端 HTML 使用 no-store，健康检查及 CLI 安装脚本正常，开发支付回调保持 404。

实际公网浏览器在 1440×1000 和 375×844 验证了右上角“管理系统登录”、“选择平台”、双向入口跳转以及 JJTV 登录 URL。截图为 `public-admin-entry-1440.jpg`、`public-tenant-login-375.jpg`。

## 数据与其他服务

Schema 保持 `c9e13a2d6f42`。8 个原员工账号及 41,656 条审计记录保留，未初始化或重置账号；租户、观众、剧目和分集行数均未变化。私有升级回执确认前后 revision 相同，保留检查通过。

原有 20 个非 JJTV 容器的 ID、镜像及状态完全一致。另有 2 个并行 pins 测试容器新增，已单独记录，未触碰。

## 验收边界与证据

本次验证匿名登录页面及跳转，没有提交真实账号完成登录，未执行真实支付或媒体播放验收。既有 CLI 命令、环境变量、存储标识及各站品牌保持兼容。

本地忽略目录 `outputs/release-opendrama-20261116/` 保存 `before.json`、`after.json`、`comparison.json`、`public-acceptance.json`、`privacy-cdn-transform.json`、`ci-artifact-comparison.json`、发布日志及公网截图；凭据和私有备份内容不入库。
