# 生产 PostgreSQL 第一阶段只读审计（Before）

> 审计日期：2026-08-26
>
> 审计对象：当前生产 PostgreSQL 与仓库 `main` 的数据库实现
>
> 审计边界：只读，不执行 DDL、DML、数据修复、迁移或部署
>
> 证据标记：**实库**＝生产聚合查询；**代码**＝当前仓库实现；**测试**＝本轮实际执行或现有测试契约

## 1. 一页结论

当前 47 表设计不需要推倒重建。迁移历史、物理外键、现有索引、JSON/时间/布尔数据、积分余额与流水投影总体稳定，内容生产主链也已经明确采用 `jobs` 作为当前态、`job_versions` 作为历史快照。

但当前数据库还不能直接认定为“企业级多租户、可正式售卖积分”的完成状态。问题主要在边界和数据库强制能力，而不是表数量：

| 优先级 | 结论 | 生产证据 | 上线判断 |
|---|---|---|---|
| P0 | 用户隔离只靠应用层，数据库层没有租户/RLS 防线 | 47 表均未启用 RLS；0 条 policy；应用连接角色同时是表 owner、superuser、`BYPASSRLS`；另有 16 条关注文章与关注账号归属不一致 | 企业多成员或外部客户开放前必须处理 |
| P0 | 商业积分已是 `live`，但未知成本与支付事实域未闭环 | 超时逻辑会以 0 实扣结束；`payment_orders`、Webhook inbox、`refunds`、订阅发放幂等表均不存在 | 正式售卖积分前必须处理 |
| P0 | 仍保留 6 个旧 `base64:` 敏感凭证值 | 模型 2 个、公众号 2 个、平台设置 2 个；Base64 可逆，不是加密 | 外部开放前应完成轮换/重加密 |
| P1 | 高价值业务关系仍有逻辑孤儿或无同归属约束 | 1 个公众号引用不存在的模型、1 个创作方案引用不存在的评审方案、1 个连接健康记录无公众号；5 个公众号使用 2 个空 owner 的平台模型 | 先定义“平台共享”例外，再清理和加约束 |
| P1 | Token 完整性有显式状态，但数据库仍允许“未知即数值 0”等矛盾组合 | 53 条事件全部为 `UNAVAILABLE`；14 条带估算 Token，39 条为固定单位；3 条影子事件同时为 `price_missing` 与 `billable=1` | 正式按 Token 扣费前必须补数据库不变量 |
| P1 | 积分账当前能对平，但并发与账本不可变性证明不足 | 5 个桶、9 条流水、41 个操作全部对平；仅有 2 并发冻结测试，尚无 100 并发证明；数据库没有禁止 UPDATE/DELETE 的账本权限策略 | 扩大商业用量前补强 |
| P2 | 当前态/兼容态一致，但兼容双写仍未退场 | 96 个 `jobs` 与 96 个 `batch_jobs` 全字段一致；两套公众号默认配置 0 差异 | 观察一个稳定发布周期后收敛 |
| P2 | PostgreSQL 原生类型尚未利用 | 546 个字段只有 `text`、`integer`、`bigint`；时间、JSON、布尔仍是兼容类型 | 最后做，不能先全库改型 |

**本阶段放行结论：**迁移基线可以继续沿用，但不建议立即按附件示例 SQL 改 47 张表。应先由产品确认“用户即租户”还是“企业可有多成员”，并确认积分是否已经对外售卖。数据库后续变更必须从 **0009 之后的新 checksum migration** 开始；现有 `20260826_0009` 已用于全库表/字段注释，不能复用或改写。

## 2. 审计范围与方法

生产查询全部在以下保护下执行：

- `BEGIN TRANSACTION READ ONLY`；结束统一 `ROLLBACK`。
- `statement_timeout = 60s`，`lock_timeout = 2s`。
- 只返回计数、状态分组、关系大小和约束元数据；没有读取或记录用户名、文章正文、密钥、Token、公众号名称和业务 ID。
- 没有执行示例 SQL、建表、加索引、修数据、备份恢复、容器重启或生产部署。

审计覆盖：迁移与 checksum、47 表/546 字段、106 个约束、111 个索引、RLS/角色、触发器/函数/视图、逐表行数与关系大小、物理和逻辑孤儿、归属冲突、活动记录重复、时间/JSON/布尔合法性、积分三方对账、Token/价卡语义、当前态兼容双写、支付与 Outbox 缺口。

仓库自带的 [`app/db_audit.py`](../../app/db_audit.py) 返回 `ok=true`，但它当前只检查 3 组孤儿、4 组 owner 冲突和少量枚举。本报告在此基础上补查了平台模型、关注内容、计费、凭证、时间/JSON、兼容双写和权限边界，因此不能用内置 `ok=true` 代替本报告的 P0/P1 结论。

## 3. 基线盘点

### 3.1 PostgreSQL、迁移与数据库对象

| 项目 | 实库结果 | 判断 |
|---|---:|---|
| PostgreSQL | 17.10 | 正常 |
| 表 | 47 | 与代码清单一致 |
| 字段 | 546 | 0009 后所有表/字段均有中文注释 |
| 约束 | 106：47 PK、39 FK、9 UNIQUE、11 CHECK | 全部 validated |
| 索引 | 111 | 0 invalid、0 not-ready、0 精确重复 |
| 部分索引 | 5 | 覆盖供应商请求/响应去重、积分冻结/释放、活动评审和活动尝试 |
| RLS | 0 表启用、0 policy | 不满足数据库级租户隔离 |
| 用户触发器/公开函数/视图/物化视图 | 0 / 0 / 0 / 0 | 账本不可变和复杂不变量未由数据库过程保护；FK 内部触发器均启用 |
| 生产关系总大小 | 8,536,064 bytes（约 8.14 MiB） | 当前数据量很小，尚不能证明大数据性能 |

生产已应用且 checksum 与代码完全一致的迁移为：

1. `20260824_0001 legacy_schema_baseline`
2. `20260824_0002 postgres_phase_one_compatibility`
3. `20260824_0003 drop_exact_duplicate_indexes`
4. `20260824_0004 shadow_billing_schema`
5. `20260825_0005 strict_token_metering_status`
6. `20260825_0006 commercial_points_billing`
7. `20260826_0007 platform_jizhile_and_followed_refresh`
8. `20260826_0008 followed_article_refresh_twenty_points`
9. `20260826_0009 database_object_comments`

迁移定义见 [`app/schema_migrations.py`](../../app/schema_migrations.py)。**不得修改已应用的 0001—0009。**

### 3.2 47 表逐表行数

以下是审计时点的精确 `COUNT(*)`，只用于迁移规模评估：

| 表 | 行数 | 表 | 行数 | 表 | 行数 |
|---|---:|---|---:|---|---:|
| account_creation_plan_defaults | 2 | account_editorial_review_defaults | 3 | ads | 1 |
| ai_models | 3 | ai_usage_events | 53 | app_settings | 14 |
| batch_jobs | 96 | batches | 86 | billing_plans | 0 |
| billing_pricing_policies | 1 | billing_task_rates | 12 | bot_contexts | 0 |
| bot_sessions | 0 | creation_plan_account_templates | 0 | creation_plans | 1 |
| credit_buckets | 5 | credit_ledger | 9 | draft_deliveries | 29 |
| editorial_review_applications | 20 | editorial_review_profiles | 0 | editorial_reviews | 46 |
| feishu_integration_accounts | 0 | feishu_integrations | 0 | feishu_processed_events | 0 |
| feishu_sessions | 0 | followed_accounts | 8 | followed_articles | 16 |
| job_attempts | 436 | job_versions | 19 | jobs | 96 |
| local_agent_pairings | 1 | local_model_agents | 1 | local_model_requests | 1 |
| model_price_cards | 2 | official_accounts | 8 | processed_events | 0 |
| prompt_templates | 2 | schema_migrations | 9 | token_cache | 4 |
| topic_items | 438 | topic_sources | 18 | usage_operations | 41 |
| user_sessions | 47 | user_settings | 17 | user_subscriptions | 0 |
| users | 5 | wechat_connection_health | 8 |  |  |

最大关系是 `jobs`，总大小约 2.88 MiB；其后是 `editorial_reviews` 约 0.91 MiB。当前库整体很小，索引优化应以真实查询和未来增长率为依据，不能因为“索引扫描次数为 0”就机械删索引。

## 4. 七个维度的逐项审计

### 4.1 业务边界与租户隔离：P0

**实库事实**

- 5 个用户共用同一数据库；没有 `tenants`、`tenant_memberships`。
- 47 张表均未启用 RLS，也没有 policy。
- 当前应用数据库角色是表 owner，同时具有 `SUPERUSER` 与 `BYPASSRLS`。即使未来只“打开 RLS”，该连接角色仍可绕过。
- 39 个物理 FK 全部有效，物理 FK 孤儿为 0；但大量业务关系不是带 owner 的复合 FK。
- 16/16 条已关联的 `followed_articles` 与其 `followed_accounts` 的 `owner_user_id` 不一致。物理 FK 只校验 ID 存在，不能阻止跨用户拼接。
- 2 个 `ai_models.owner_user_id=''`，5 个公众号引用这些模型。代码意图是“平台模型可共享”，但数据库没有 `scope=platform/customer` 一类明确规则。
- 另有 1 个公众号引用不存在的模型。`official_accounts.model_id` 当前没有物理 FK。

**架构判断**

当前实际边界是“一名用户拥有一套客户数据”，并不是“企业租户下有多成员”。如果产品未来允许同一家企业多人协作，应现在引入 `tenants + tenant_memberships`；如果未来 12 个月明确保持一人一空间，则可暂时把 `users.id` 定义为 tenant key，但必须写成正式不变量，不能继续依赖开发者记得在每条 SQL 上加 owner 条件。

**下一阶段验收口径**

1. 产品先二选一确认：`user = tenant`，或 `tenant + membership`；未确认前不做全库 FK 改造。
2. 数据库 owner/migrator 与 runtime 角色分离；runtime 必须 `NOSUPERUSER NOBYPASSRLS`，不能拥有表。
3. 客户表启用 RLS；连接池每个事务显式设置当前 owner/tenant，上下文为空时默认拒绝。
4. `job-account-model-batch-operation` 等高价值关系增加不可变 owner/tenant 列与复合 FK，或通过不可绕过的数据库约束实现同等保证。
5. 平台共享模型必须成为显式类型，禁止用空字符串 owner 隐式表达。
6. 清理后，跨归属拼接、非空 owner 找不到用户、逻辑孤儿均为 0；增加四角色 RLS 验证和跨租户负向测试。

### 4.2 数据一致性与删除生命周期：P1

**通过项**

- 39 个 FK 均 validated，RI 触发器均启用，物理 FK 孤儿为 0。
- 活动 `job_attempts` 重复组为 0；活动 `editorial_reviews` 重复组为 0；活动订阅重复组为 0；默认飞书公众号重复组为 0。
- `jobs` 与 `batch_jobs` 各 96 行，批次、公众号、快照名称、审核状态和时间字段差异全部为 0。
- 两套公众号默认创作方案、默认评审方案当前差异全部为 0。

**未通过/待确认项**

- 1 个 `creation_plans.editorial_review_profile_id` 找不到评审方案；1 个 `wechat_connection_health.account_id` 找不到公众号；1 个 `official_accounts.model_id` 找不到模型。这些字段是逻辑引用，数据库没有 FK。
- `jobs.batch_id -> batches` 使用 `ON DELETE SET NULL`，但 `job_attempts.batch_id`、`editorial_reviews.batch_id` 使用 `ON DELETE CASCADE`。如果直接删除批次，文章任务可保留，而执行尝试和评审审计会消失。当前业务只归档批次，风险尚未触发，但 DDL 的生命周期语义自相矛盾。
- 计费域的用户 FK 多为 `ON DELETE CASCADE`。删除用户会同时删除积分桶、流水、用量和订阅，不满足商业账务通常要求的长期审计留存。

**建议**

- 先把生命周期写成矩阵：业务归档、软删、匿名化、法定保留、最终物理删除分别允许影响哪些表。
- 批次、任务、尝试、评审、用量、积分流水采用一致的审计保留规则；流水和已结算用量优先 `RESTRICT` 或主体匿名化，不应级联抹除。
- 逻辑孤儿先生成脱敏修复清单并人工确认，再使用 `NOT VALID -> 回填/清理 -> VALIDATE CONSTRAINT` 增加约束；不得用示例 SQL直接覆盖生产。

### 4.3 并发安全、幂等和重试：P1

**已有能力**

- `usage_operations` 有 `UNIQUE(owner_user_id, idempotency_key)`。
- 供应商 request/response ID 有按 owner+provider 的部分唯一索引，生产重复组为 0。
- 积分 reserve/release 有 `(operation_id, bucket_id, event_type)` 部分唯一索引。
- 冻结代码先锁 operation，再按到期时间/创建时间锁积分桶，并使用带余额条件的原子 UPDATE；结算再次锁 operation。实现见 [`app/db.py`](../../app/db.py) 的 `reserve_credit_points`、`settle_credit_operation`。
- 草稿写入有 `draft_deliveries` 幂等主键与唯一内容版本指纹；本地模型请求使用 lease、attempt、nonce 防旧 Worker 回写。

**缺口**

- 现有 PostgreSQL 自动测试只证明 2 个并发冻结者争抢 1,000 积分时不会同时各取 600；附件要求的 100 并发、多个积分桶、相同到期时间、失败重试与死锁重放尚未证明。
- 积分桶排序缺少最终 `id` tie-breaker；相同 `expires_at + created_at` 时不能从 SQL 文本证明所有事务锁顺序绝对一致。
- `credit_ledger` 在应用公开方法层“只追加”，但数据库没有权限隔离或触发器阻止 runtime UPDATE/DELETE；当前连接还是表 owner/superuser。
- `grant_credit_points` 使用随机 bucket/ledger ID，`source_type + source_id` 没有唯一幂等约束。未来支付或订阅 Webhook 重放会有重复发放风险。
- 5 个 shadow operation 仍为 `running`，全部超过 1 小时，其中 3 个超过 24 小时，最旧约 30 小时，说明“创建成功但最终关闭失败”已有生产样本。

**验收标准**

- 在隔离 PostgreSQL 上完成 100 并发冻结/结算/失败释放；余额不为负、不超发、无重复流水、无死锁或可幂等重试。
- 固定锁序包含唯一键；所有状态迁移使用 compare-and-set 或版本号。
- runtime 对 ledger 只有 SELECT/INSERT，无 UPDATE/DELETE；运维修复走独立审计角色。
- 发放动作拥有外部事实唯一键；同一个支付、退款、订阅周期和人工工单重放 100 次只产生一次财务效果。
- stale running operation 有明确的恢复队列和告警，而不是长期悬挂。

### 4.4 AI 计量、积分与商业审计：P0/P1

**当前对账通过**

| 项目 | 实库结果 |
|---|---:|
| 积分桶 | 5 个；总发放 5,000；当前剩余 4,925 |
| 积分流水 | 9 条：grant +5,000、reserve -800、release +725 |
| 桶余额 vs 流水投影 | 0 个不一致，绝对差额 0 |
| 操作 reserved vs reserve 流水 | 0 个不一致 |
| 已完成操作 charged vs reserve-release | 0 个不一致 |
| 负余额/剩余大于发放 | 0 / 0 |
| 过期但仍 running 的 live 操作 | 0 |
| 价卡有效期重叠 | 0 |

**计量语义缺口**

- 商业策略当前为 `live`；12 个启用任务费率、2 个启用价卡，但套餐和用户订阅均为 0。
- 53 条 AI 用量事件全部是 `token_usage_status=UNAVAILABLE`：14 条是估算 Token（合计 31,793），39 条是固定单位调用。生产尚无一条 `RECORDED/provider_actual` 样本，因此不能证明严格 Token 计费链在线上闭环。
- Token 数值字段仍是 `NOT NULL DEFAULT 0`。虽然 `token_usage_status` 能区分未知，但数据库没有 CHECK 阻止 `RECORDED + 非 provider_actual`、`UNAVAILABLE + 被当作真实 Token` 等非法组合。
- 3 条历史 shadow 成功事件同时为 `pricing_status=price_missing`、`billable=1`；它们没有产生实际扣分，但说明 `billable` 与价态可被写成矛盾组合。
- [`release_expired_credit_reservations`](../../app/db.py) 当前把超时 live 操作结算为 `expired`，并写 `charged_points=0, estimated_points=0`。超时只说明结果未知，不能证明供应商没有执行或不会迟到回传。
- `model_price_cards` 可按 ID upsert 改写。操作内已保存 pricing snapshot，这是优点；但价卡历史自身尚不是不可变版本，也没有数据库排斥重叠区间的约束。

**商业上线前验收标准**

1. 增加 `result_unknown / pricing_pending` 等显式状态；超时不得直接把未知成本认定为 0。迟到结果只能幂等结算一次，并进入人工可见的异常队列。
2. Token 不可得时，真实 Token 字段使用 NULL 或由 CHECK 保证不会被解释为实测 0；`RECORDED` 必须来自 provider actual，并满足非负和总量关系。
3. `billable=1` 只能出现在成功、可定价、结果有效的事件；现有 3 条矛盾 shadow 数据先按审计流程解释/修复。
4. 价卡采用新增版本而不是覆盖历史；同 provider/model/modality 的有效区间不得重叠。
5. 在生产影子阶段先取得每类服务商的真实/固定单位证据，再允许该模型进入严格计费。

### 4.5 索引与性能：当前规模通过，增长前 P1

生产共有 111 个索引，均 valid/ready，没有精确重复。附件的 `db_preflight_audit.sql` 在检查 FK 前导索引时使用了 `indkey` 的错误数组下标：PostgreSQL `int2vector` 的下界为 0，原表达式会误报 35 个缺失。修正为 `[0:cardinality(conkey)-1]` 后，得到 12 个“没有完全相同前导列”的候选：

1. `account_editorial_review_defaults(profile_id)`
2. `ai_usage_events(job_id)`
3. `batch_jobs(job_id)`
4. `credit_ledger(bucket_id)`
5. `feishu_integration_accounts(account_id)`
6. `job_attempts(batch_id)`
7. `job_versions(job_id)`
8. `local_model_requests(model_id)`
9. `official_accounts(default_creation_plan_id)`
10. `official_accounts(default_editorial_review_profile_id)`
11. `usage_operations(job_id)`
12. `user_subscriptions(plan_id)`

这 12 个是候选，不是“必须立即创建”的结论。当前总库约 8.14 MiB，多数表不足百行。下一阶段优先对代码中真实高频的 `batch_jobs(job_id)`、`job_versions(job_id, id DESC)`、`ai_usage_events(job_id)`、`usage_operations(job_id)`、`job_attempts(batch_id)` 执行 `EXPLAIN (ANALYZE, BUFFERS)` 的隔离数据集验证；删除级联相关 FK 也应评估前导索引。其余索引按增长量和执行计划决定。

### 4.6 可迁移性与当前态权威源：基线通过，收敛 P2

**数据类型现状**

- 449 个 `text`、94 个 `integer`、3 个 `bigint`；没有原生 `timestamptz`、`jsonb`、`boolean`、enum/domain。
- 120 个命名为时间的文本字段中，所有非空值均能解析为 `timestamptz`。
- 35 个命名为 JSON 的文本字段全部是合法 JSON。
- 26 个整型布尔字段全部为 0/1。

这说明“先校验、后分批转型”可行，但不构成一次性全库改型的授权。应先加约束和新列双写，再回填、校验、切读，最后删除旧列。

**权威源判断**

- **代码证据：**分析与任务查询已把 `jobs.batch_id` 定义为批次关系权威源，`batch_jobs` 标注为一个发布周期的兼容双写；生产 96 对 96、所有投影差异为 0。
- `job_versions` 由 `save_job_version` 保存变更前快照，`jobs` 保存当前正文；它们不是当前态双主。但应补充“版本只追加、恢复产生新版本、迟到结果比较 content revision”的正式不变量。
- `official_accounts.default_*` 与两张 default 映射表仍双写/COALESCE 读取；当前 0 差异，不代表可以永久保留双主。

**迁移规则**

- 0001—0009 永不改写；任何后续工作从新的 `..._0010` checksum migration 开始。
- 使用 expand → backfill → validate → switch reads → contract；每阶段可独立回滚应用，不回滚已提交的事实数据。
- `NOT VALID` 约束在清理后单独 `VALIDATE`；大表索引使用受控的 `CREATE INDEX CONCURRENTLY`，不得放进普通事务迁移。
- 每个迁移必须保存迁移前后行数、孤儿数、余额差、约束状态和应用版本；不接受“进程启动时再建一次表”的第二入口。

### 4.7 隐私与密钥安全：P0

**正向能力**

- 密码和会话保存 hash；公众号、模型、飞书和平台设置的新增敏感值通过 Fernet/服务器密钥写入。
- 本轮审计没有输出任何密钥、密文、用户 ID、文章或账号名称。

**生产遗留**

| 位置 | 空值 | Fernet | 旧 Base64 | 其他明文格式 |
|---|---:|---:|---:|---:|
| `ai_models.api_key_encrypted` | 1 | 0 | 2 | 0 |
| `official_accounts.app_secret_encrypted` | 0 | 6 | 2 | 0 |
| `app_settings` 内敏感字段 | 0 | 1 | 2 | 0 |
| `user_settings` 内敏感字段 | 0 | 4 | 0 | 0 |

旧 Base64 共 6 个值。它是历史回滚兼容格式，不提供静态数据保密能力。建议在确认旧 release 不再回滚后，执行一次受控凭证轮换或用当前服务器密钥重新保存；校验所有值均为 `fernet:` 后，再移除 Linux 端对裸值/Base64 的兼容读取。操作必须先做可恢复备份，并只报告格式计数，不得把密钥写进迁移、日志或审计文档。

## 5. 支付、Outbox 与 `credit_tickets` 结论

实库不存在以下表：`credit_tickets`、`payment_orders`、`payment_webhook_inbox`、`refunds`、`subscription_point_grants`、通用 `outbox_events`。

现有 `draft_deliveries` 是公众号草稿交付专用 Outbox，`feishu_processed_events`/`processed_events` 是事件 Inbox，`local_model_requests` 是带租约的请求队列；它们不能替代支付事实域。

在正式售卖积分前，至少需要：

- `payment_orders`：订单金额、币种、渠道、状态机、外部订单号、payer/tenant、创建/支付/关闭时间。
- `payment_webhook_inbox`：原始事件摘要、验签状态、处理状态、失败原因；`UNIQUE(provider, external_event_id)`。
- `refunds`：退款事实、原订单、外部退款号、状态和对应负向积分流水。
- `subscription_point_grants`：`UNIQUE(subscription_id, period_start)`，保证周期积分只发一次。
- 若保留 `credit_tickets`，它只能作为人工加减积分工单/审批事实，不能替代支付订单；工单 ID 必须成为 ledger 的唯一来源键。
- 可靠 Outbox：支付状态提交与待发送事件同事务写入，消费者按 event ID 幂等。

前端“支付成功页”、同步 API 返回或人工按钮都不能直接作为发积分事实。

## 6. 附件强制场景的当前差距

| 强制场景 | 当前状态 | 证据/缺口 |
|---|---|---|
| 跨租户拼接必须失败 | **失败** | 生产已有 16 条关注文章/账号 owner 不一致；无 RLS/复合 FK |
| 100 并发积分冻结 | **未证明** | 现有 PostgreSQL 测试仅 2 并发；生产余额当前能对平 |
| 幂等重放 | **部分通过** | usage、供应商 trace、reserve/release 有唯一键；发放/支付缺失 |
| Token 缺失不得写成真实 0 | **部分通过** | 有 `UNAVAILABLE` 状态，但数值列仍 NOT NULL DEFAULT 0，缺 CHECK |
| 迟到 AI 结果不得覆盖新正文 | **应用测试通过、DB 未完全强制** | 已有 stale review 测试；仍需 revision/CAS 数据库契约 |
| 旧 Worker 租约回写 | **应用测试通过** | local request 使用 lease+attempt+nonce；需保留负向测试 |
| 支付 Webhook 重放 | **未实现** | 支付 Inbox/订单/退款/周期发放表均不存在 |
| RLS 四角色验证 | **失败** | 当前只有可绕过 RLS 的高权限运行角色 |
| 批次删除后执行审计保留 | **失败（DDL 语义）** | job SET NULL，但 attempt/review 对 batch CASCADE |
| 积分三方对账 | **当前通过** | 桶、流水、operation 投影差额均为 0 |

本轮本地聚焦测试：`tests/test_billing.py + tests/test_commercial_billing.py` 共 **18 passed**。这批测试验证业务计算、幂等和应用层隔离，但主要使用隔离 SQLite；不能替代附件要求的 100 并发 PostgreSQL、RLS 四角色和支付重放验收。

## 7. 建议的后续实施顺序（本轮未执行）

1. **产品决策门：**确认 user/tenant 模型、团队成员需求、积分是否已正式售卖、账务保留周期。
2. **数据修复方案：**针对 16 条跨 owner、3 类逻辑孤儿、5 条 stale operation 和 3 条计量矛盾记录，输出只含内部 ID hash/计数的修复预览，经人工确认后再执行。
3. **租户与角色基础：**分离 migrator/runtime，建立 RLS 上下文；若选择企业租户，再引入 tenant/membership。
4. **同租户复合约束与生命周期：**先新增不可变归属，再补 FK；统一 batch/job/attempt/review/usage/ledger 删除规则。
5. **UNKNOWN 与 Token 语义：**加入 result unknown、pricing pending、迟到结果幂等结算和 Token CHECK。
6. **积分并发、账本与支付事实域：**100 并发验证、ledger 权限、支付 Inbox/Outbox、退款和周期发放幂等。
7. **价卡版本、当前态收敛：**价卡不可变区间；退役 `batch_jobs` 和 default 兼容双写前先观察并对账。
8. **类型与索引现代化：**最后分批转换 `timestamptz/jsonb/boolean`，以执行计划决定新增索引。

每一阶段都应先在生产快照的隔离 PostgreSQL 上演练，输出 before/after 审计和回滚方案，再申请生产变更窗口。

## 8. 本轮边界与最终状态

- 生产数据库：**0 次写入、0 次迁移、0 次结构变更、0 次业务数据修复**。
- 仓库业务代码：未修改。
- 附件中的 SQL：未在生产原样执行；FK 索引检查的数组下标误报已在本报告中纠正。
- 线上服务：未部署、未重启。
- 本报告是“改造前审计”，不是迁移授权；下一步必须先确认 P0 决策与修复口径。
