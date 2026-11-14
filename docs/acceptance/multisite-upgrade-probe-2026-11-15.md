# 多站点升级与备份恢复验收

验收日期：2026-11-15（Asia/Shanghai）。只使用 WSL `8JJ-Dev` 的本地 PostgreSQL，专用数据库为 `minitv_multisite_upgrade_probe` 和 `minitv_multisite_upgrade_probe_restored`；未连接或修改生产数据库。

## 已执行

1. 从空数据库迁移到原版本 `a1114cancel_intent`，使用原始 SQL 写入用户、钱包、金币流水、套餐版本、会员、订单、剧目、分集和已购权益；未用当前 ORM 访问旧结构。
2. 使用 `pg_dump --format=custom --no-owner --no-acl` 创建升级前备份。
3. 迁移到 `c9e13a2d6f42`，核对 76 张历史表计数，以及上述十类样本的全部原有字段、主键、外键值和序列状态。
4. 将升级前备份 `pg_restore --exit-on-error` 到第二个专用数据库：先核对恢复后旧数据，再独立升级并再次比较。
5. 将升级后的完整数据库重新备份、恢复到第二个专用数据库，核对 87 张表的全部行、计数和序列状态。

## 结果

全部比较通过。样本用户 ID `4101`、剧目 ID `4401`、分集 ID `4501`、已购权益 ID `4701`、付费订单 ID `4901` 保留；钱包余额保持 `127`。会员期限、订单金额、币种和来源剧目保持原值。历史消费者记录归属 `jjtv`；已有剧目没有自动写入内容库来源，仍保持私有。

- 最后复验包含站点按内容版本独立引入的唯一约束 `tenant_id + library_title_id + library_version`，以及中央分集字幕 `subtitles` 的空数组默认值。
- 升级前、升级后及恢复后历史样本摘要一致：`291fcb4f93ceb43fe0c8447e942dcc112553b90783b131d7aacf0cdd9b42ed69`。
- 新版完整数据库及其恢复副本摘要一致：`92c8082dfe2c72389ff59d3e030910e1f5b421e45bca33dbe3e4af59473e9d05`。
- 本地完整结果：`outputs/multisite-upgrade-probe/report.json`。两个数据库备份只含本次合成样本，保留在同目录；该目录由 `.gitignore` 排除。

## 重复执行

在已安装后端依赖及 PostgreSQL 客户端的本地环境设置 `DATABASE_URL`，指向本地 `postgres` 管理库，然后从仓库根目录执行：

```sh
python scripts/verify_multisite_upgrade.py
```

脚本只接受本机 PostgreSQL 地址及管理库/固定探针库名，重建上述两个固定探针数据库。升级后的消费者身份和账务无法安全合并回旧结构，因此迁移不提供有损降级；回退使用经过验证的完整备份。此验收证明合成业务样本和完整备份恢复的一致性，不代表已经对真实生产数据执行迁移。
