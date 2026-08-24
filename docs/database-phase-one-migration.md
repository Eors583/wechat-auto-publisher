# PostgreSQL 结构优化第一阶段（2026-08-24）

本阶段采用“新增主字段 + 兼容双写 + 可验证迁移”，不删除历史表、不转换大批量字段类型，也不改变 API、UI、飞书和草稿写入合同。生产发布前后都可以用 `python scripts/audit_database.py` 获取只包含计数和结构名称的审计结果；该工具不会读取或输出凭证值。

## 已实施

- `schema_migrations` 记录版本、名称、SHA-256 checksum 和应用时间。启动时使用 PostgreSQL advisory lock 串行升级，checksum 不一致或数据库版本高于代码时拒绝启动。
- `20260824_0002` 修复公众号默认创作方案和默认评审方案的悬空/跨用户引用。无效创作方案引用置空；无效评审引用置空并清空旧配置。PostgreSQL 外键通过 `NOT VALID → 清理 → VALIDATE` 安全落地，删除方案时使用 `ON DELETE SET NULL`。
- `official_accounts.default_creation_plan_id`、`default_editorial_review_profile_id`、`editorial_review_config_json` 是新的默认配置事实来源。原 `account_*_defaults` 表保留一个兼容周期并继续双写。
- `jobs.batch_id`、`account_id`、`account_name_snapshot`、`review_status`、`viewed_at`、`confirmed_at` 是任务归属/审核事实来源。原 `batch_jobs` 保留一个兼容周期并继续双写；升级前若同一 job 属于多个 batch，迁移会停止并要求人工消歧。
- `jobs.source_channel` 是新代码读取/写入的来源字段；旧 `source` 同步写入以保持合同。`source_mode`、`reference_urls_json`、`required_facts`、`rewrite_intensity` 本阶段只标记为弃用候选，不删除。
- 用户级设置只写 `user_settings`。`onboarding.guide`、`ui.last_target_account_ids`、`wechat_backend_search`、`jizhile_api` 和 `ui.*` 仅在 `migration.customer_data_owner.v1` 指向真实历史用户时复制；其他用户不会读取同一份旧值。
- `20260824_0003` 在 PostgreSQL 使用 `DROP INDEX CONCURRENTLY` 删除三条已确认完全重复的显式索引：`idx_draft_deliveries_revision`、`idx_feishu_integrations_owner`、`idx_feishu_integrations_callback`。
- `20260824_0004` 统一创建 Token 成本/积分影子计量所需 7 张表和 7 条查询索引。计费业务不再拥有独立的启动建表入口，仍由同一 advisory lock、checksum 和幂等迁移合同管理。

## 兼容边界

- `account_creation_plan_defaults`、`account_editorial_review_defaults`、`batch_jobs` 本次不删除。下一发行版先验证线上双写一致性和调用路径，再安排退役。
- `bot_sessions`、`bot_contexts` 只服务旧飞书兼容路径；多租户 Webhook 使用 `(integration_id, chat_id)` 的 `feishu_sessions`。
- `processed_events` 仍被微信命令 Agent Webhook 用作带入口前缀的幂等记录，不能和旧飞书表一起退役。后续应先迁到 owner/account-scoped 的微信事件表，再讨论删除。
- 审计会把 `followed_articles(owner_user_id, url)` 的约束索引与显式唯一索引组合计为性能警告（不判定数据完整性失败）。本阶段未在没有真实 `EXPLAIN`/线上调用观察的情况下删除它。

## 暂缓的类型转换清单

审计基线包含 94 个 TEXT 时间字段、30 个 TEXT JSON 字段和 18 个 INTEGER 布尔字段。第一阶段不进行全表重写：

- 时间：后续按单一领域逐批引入 `TIMESTAMPTZ` 影子列、双写、UTC/时区校验、回填和读切换，再删除旧列。
- JSON：后续按查询收益排序迁到 `JSONB`；先验证所有历史值可解析并为必要路径设计 GIN/表达式索引，不能只为类型统一而迁移。
- 布尔：后续在已具备 0/1 CHECK 且无非法值的表上逐批迁到 BOOLEAN，并同步 PostgreSQL/SQLite 测试合同。

## 上线与回滚口径

1. 先备份并在隔离 PostgreSQL 副本运行全新库、旧库升级、重复启动、checksum 篡改、并发初始化和审计测试。
2. 升级前后比较关键表总行数、按 owner 分组计数、默认配置关联数和 batch/job 关联数；日志与报告不得包含密钥、Cookie、Token 或加密字段值。
3. `0002` 是事务迁移，失败自动回滚；`0003` 每条 `DROP INDEX CONCURRENTLY IF EXISTS` 可幂等重试。兼容表仍在，因此应用代码可以在紧急回滚版本中继续读取旧结构。
4. 观察一个兼容周期后，只有在审计为零、双写一致且生产查询路径确认完成时，才可另建迁移删除旧表/旧列。
