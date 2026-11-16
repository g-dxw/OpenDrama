# 多站点 V1 开发说明

## 边界

`Tenant` 仍表示独立站点。安装级环境配置表示所属公司；公司总控使用平台账号，各站员工继续使用各自账号。消费者邮箱和第三方身份按站点唯一，登录凭证不能跨站使用。

公共 `/v1/` 请求由已验证的 Host 解析站点。集中 API 域名只有在显式配置 `SITE_API_HOSTS` 后才按已验证 Origin 解析。后台通过既有会话绑定站点；Stripe 回调在商户验签后从数据库订单解析归属。未知域名没有默认站点。

## 接口

- `GET /v1/site/bootstrap`：站点资料、已发布品牌、功能开关及配置版本。
- `GET/PATCH /tenant/v1/site`：本站市场、语言、币种、时区、网址和功能开关。
- `GET/PUT /tenant/v1/brand`，`POST .../publish`、`.../rollback`、`.../preview`：版本化品牌。`version` 为草稿并发版本，`published_version` 为发布版本。
- `POST /platform/v1/tenants/{id}/domains/{domain_id}/verify`：验证登记域名的 DNS TXT 挑战。
- `/platform/v1/library`：公司母版、分集、媒体上传、服务器转码状态、质检和分发授权。
- `GET /tenant/v1/library` 与 `POST /tenant/v1/library/{id}/import`：本站可引入内容及幂等创建草稿。
- `/platform/v1/stripe/accounts` 与 `GET/PUT /tenant/v1/merchant-binding`：实例商户和本站显式绑定。

## 数据与兼容

新迁移按 `a7c91e0b4d20 → b8d02f1c5e31 → c9e13a2d6f42` 顺序追加在既有主线迁移之后。站点设置、品牌及品牌修订独立存储；消费者模型补齐站点归属和约束；公司内容库通过可空来源字段关联已有 Drama/Episode。

旧剧目和分集 ID 不变。旧目录没有自动进入共享库。媒体源版本和授权与本站封面、文案、定价、发布状态分开；后续市场采购可生成同一种授权，复用分发流程。

普通引入保留本站既有版本；采用母版新版必须显式提交 `POST /tenant/v1/library/{id}/import`，请求体为 `{"grant_id": 123, "adopt_new_version": true}`。这会建立独立新版草稿及分集 ID；旧版发布、编辑及已购记录不变，同版本重试仍幂等。

`sales_until` 限制新增销售、引入和发布，`play_until` 限制新播放凭证；撤销或归档授权同样拒绝签发新播放凭证，已有凭证按其有效期失效。历史权益不免除当前地域、语言及播放期限检查。中央字幕随审核版本冻结，导入时按授权语言筛选；本站字幕修改不反向改变母版。

首版不将旧任务奖励、评论自动审核、原供应商归因结算自动开放给新站点。服务器能力和前台功能开关共同限制入口。原生 App 的多品牌发布及外部版权交易不属于这一交付。

## 验证

`backend/tests/test_multisite_runtime.py` 覆盖双域名、未知域名拒绝、品牌草稿/预览/发布/回退、并发保存、管理员权限、配置验证和 DNS 挑战。

内容库、消费者和支付测试分别覆盖双站隔离、授权分发、重复引入、媒体版本、播放范围、登录隔离和支付事件。所有测试使用独立 PostgreSQL 数据库和 Redis DB，Bunny、邮件、Stripe 均使用测试替身；真实第三方账户的运营验收另行完成。
