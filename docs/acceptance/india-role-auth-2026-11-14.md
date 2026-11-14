# India 租户角色直接运营验证

任务：[india-short-drama #104](https://github.com/88jj-com/india-short-drama/issues/104)。员工使用 JJTV 的 India 租户角色直接运营，无需个人旧站账号连接。两端新模式默认关闭，本记录不表示生产已切换。

## 源码与协议绑定

- JJTV 实现：`5a8c9473f81c8f9c38c72c2fa9490cb5e0520b25`，从 `origin/main` 的 `fb2ed13` 开始。
- India provider 实现：`65ff7d3d5b1176270621a7720af2473c780ca043`；以组织当前 source-sync `5ef3ca1` 加既有密码 provider `21bcc5c` 构成完整开发基线。
- 两份 India OpenAPI 文件 SHA-256 均为 `fa39195622efe1eaaf1f3e2a03ca2d53a446234ed59deacacb4d94d4c992d3c5`；只给固定 23 个操作添加内部委托签名的认证选择。
- 协议和启用顺序见 [India 角色委托授权](../india-jjtv-role-auth.md) 与 provider 的 `docs/jjtv-delegated-auth.md`。独立密钥、30 秒有效期、一次性 nonce、真实账号/身份类型、方法/路径/查询/正文或上传元数据均参与校验。

## 已完成验证

| 检查 | 实际结果与边界 |
| --- | --- |
| JJTV 相关后端回归 | 163/163 通过，157.67 秒。包含 tenancy、tenant_permissions、independent_tenants、india_connector、india_delegation 五个文件；真实隔离 PostgreSQL 和 Redis DB 15，旧 India HTTP 由测试替身模拟。 |
| 新登录免旧账号连接 | 数据库用例覆盖登录和重新登录、未创建 IndiaConnection、不转发浏览器伪造身份、实时撤权、跨租户拒绝、逐字段定价权限、审核/发布分离及平台支持审计。 |
| India 新/旧 provider | Linux 24/24、零跳过。含签名/过期/篡改/并发重放、权限组合、上传归属、同人审核拒绝、视频任务、轮换和恢复屏障；旧密码 provider、旧网站 MFA 回归保留。 |
| India 已有内容/MFA/任务/契约 | 23 个不同用例最终全部通过。首轮 22 通过、1 个因未配置 FFMPEG_PATH 跳过；补充 FFMPEG_PATH=/usr/bin/ffmpeg 后该用例 1/1 通过。 |
| 管理端 | TypeScript、Vite 构建通过。Edge 7/7 UI 检查覆盖只读/编辑/上传/审核/发布、撤权刷新、配置错误、密码兼容及 375px 无页面横向溢出；接口响应为合成替身，单列于真实授权证据。 |
| 实际跨服务 HTTP | Python IndiaClient → HTTP → Linux Node provider，7/7 通过。真实请求覆盖创建、Unicode 查询、重放/篡改/过期拒绝、细粒度权限、实际版权文件流式上传的归属拒绝/所有者成功，以及真实操作人审计。全部使用合成内容与密钥，旧 users/sessions 均为 0。 |
| 本地独立审查 | 非实现该授权代码的代理只读追踪两端能力边界、上传及审计，未发现具体阻断问题；不冒充 GitHub 非作者批准。 |

JJTV 回归经 `scripts/release.py` 的 `video-test-services` 专用目标串行执行，受保护的测试凭据只用于已登记的临时容器；来源为完整提交，未加载生产环境。测试收据为 `deployments/releases/v2026.11.14.3.json`，该目标不是产品部署。第一次非终端连接中断未获得完整结果，不计为通过；随后新运行完整取得上述 163 通过。测试 runner 和源码临时目录已清理，发布锁已核实空闲；本轮临时 SSH 密钥副本、HTTP fixture 和签名密钥均已清理。

## 未通过及未执行项

India 全量 tools 类型检查仍报告 `services/api/test/mail-settings.test.ts(99,123)` 的 `TS2345: string | undefined`。在未修改的既有密码 provider 提交 `21bcc5c578d5f11f615c4ec53b5585027b5dfd16`、相同锁文件/工具下复现同一错误；该文件未在本任务修改。受影响 API 类型检查通过。Windows 目录 fsync 的已知 EPERM 不计为测试通过，相关契约已改在 Linux 实际运行通过。

本轮未执行生产上传/视频转码/真实版权审核或发布，未迁移业务数据、修改员工密码/MFA、部署或启用新通道。India 组织仓库当前没有 main，不能把功能分支、隔离通过或过去的单次发布例外当成正式准入；原四模块密码 provider overlay 也不能直接用于包含本次新模块的发布。后续正式切换仍需符合各仓源码、审查、CI、配套配置和发行规则。
