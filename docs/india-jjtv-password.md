# India Short Drama 专用账号密码连接

仅用于现有 India Short Drama 租户。首次连接无需验证码；直接登录旧站仍保留原验证器。此功能需要旧 API 的独立 jjtv_client_password provider，部署 JJTV 消费端不会自动开启旧 API 能力。

## 配置

默认关闭。JJTV 后端需 INDIA_JJTV_CLIENT_ENABLED=true、INDIA_JJTV_CLIENT_SECRET_FILE=/run/secrets/india-client/jjtv.key，并保留既有 INDIA_API_BASE_URL 与 TENANT_CONNECTION_KEY。密钥为独立的至少32随机字节base64url文本，43–128字符；只写入受保护文件，不复用验证器/支付密钥、不返回浏览器、不写入Git或发布附件。

video 的发布脚本仅建立 /opt/jjtv/shared/india-client 私有目录并以只读方式挂载到API。它不会生成客户端密钥或自动启用通道。旧 API 需匹配密钥与明确的物理managed staff allowlist；首期账号由受控运行配置决定。

浏览器提交账号密码后，后端从当前已认证用户生成operator ID，注入固定的三个客户端headers。连接仍限定当前JJTV登录会话，业务token在IndiaConnection中加密；不保存旧密码或OTP，不建立跨登录的长期授权。旧MFA连接记录需重新连接，旧MFA接口返回410。

access临近到期时在分布式锁内先刷新，再分发内容请求；不会自动重发已提交的业务写操作或文件流。断开连接只调用专用family退出，旧站网页登录不受影响。配置关闭、密钥无效、物理账号/角色/凭据变化或授权过期时连接失败并要求处理配置或重新连接。

## 验证与发布

验证覆盖真实MockTransport合同、错误脱敏、账号/原生会话隔离、旧模式拒绝、key rotation、刷新并发与CAS、流式大小校验、租户权限与审计回滚，以及本地合成数据UI流程。结果需绑定最终提交；本地通过不等于生产已启用。

上线必须先满足旧API准入及双端合同，JJTV仍只经 scripts/release.py 发布并持共享锁。回退时先关闭专用provider与JJTV开关，再按各项目规定回退匹配产物；无需修改旧站验证器、账号密码或既有财务/worker模块。
