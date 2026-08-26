from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SchemaMigration:
    version: str
    name: str
    signature: str
    transactional: bool = True

    @property
    def checksum(self) -> str:
        payload = f"{self.version}\n{self.name}\n{self.signature}"
        return hashlib.sha256(payload.encode()).hexdigest()


BASELINE_SCHEMA = SchemaMigration(
    "20260824_0001",
    "legacy_schema_baseline",
    "37-table schema and compatibility migrations through 2026-08-20",
)
PHASE_ONE_COMPAT = SchemaMigration(
    "20260824_0002",
    "postgres_phase_one_compatibility",
    (
        "account default columns; job batch projection; safe default-reference "
        "foreign keys; status, boolean, and owner checks; scoped setting copy"
    ),
)
DROP_DUPLICATE_INDEXES = SchemaMigration(
    "20260824_0003",
    "drop_exact_duplicate_indexes",
    (
        "drop idx_draft_deliveries_revision, idx_feishu_integrations_owner, "
        "idx_feishu_integrations_callback concurrently on PostgreSQL"
    ),
    transactional=False,
)
SHADOW_BILLING_SCHEMA = SchemaMigration(
    "20260824_0004",
    "shadow_billing_schema",
    (
        "billing plans, subscriptions, model price cards, credit buckets and "
        "ledger, usage operations and AI usage events with seven lookup indexes"
    ),
)
STRICT_TOKEN_METERING = SchemaMigration(
    "20260825_0005",
    "strict_token_metering_status",
    (
        "AI usage token status and provider credits; model metering capability, "
        "strict eligibility, last probe timestamp, sanitized raw usage, and "
        "provider request/response trace-id uniqueness"
    ),
)
COMMERCIAL_POINTS_BILLING = SchemaMigration(
    "20260825_0006",
    "commercial_points_billing",
    (
        "versioned commercial pricing policy and task rates; TOKEN, FIXED, "
        "UNIT and BYOK price-card fields; operation pricing snapshots; "
        "credit reservation and release idempotency"
    ),
)
PLATFORM_JIZHILE_AND_FOLLOWED_REFRESH = SchemaMigration(
    "20260826_0007",
    "platform_jizhile_and_followed_refresh",
    (
        "move the legacy default-owner Jizhile credential into one platform "
        "setting and seed a fixed ten-point followed-article refresh task rate"
    ),
)
FOLLOWED_ARTICLE_REFRESH_TWENTY_POINTS = SchemaMigration(
    "20260826_0008",
    "followed_article_refresh_twenty_points",
    "raise the fixed followed-article refresh task rate and reserve cap to twenty points",
)
DATABASE_OBJECT_COMMENTS = SchemaMigration(
    "20260826_0009",
    "database_object_comments",
    "Chinese descriptions for all 47 managed PostgreSQL tables and every column",
)

SCHEMA_MIGRATIONS = (
    BASELINE_SCHEMA,
    PHASE_ONE_COMPAT,
    DROP_DUPLICATE_INDEXES,
    SHADOW_BILLING_SCHEMA,
    STRICT_TOKEN_METERING,
    COMMERCIAL_POINTS_BILLING,
    PLATFORM_JIZHILE_AND_FOLLOWED_REFRESH,
    FOLLOWED_ARTICLE_REFRESH_TWENTY_POINTS,
    DATABASE_OBJECT_COMMENTS,
)


_DATABASE_TABLE_COMMENTS = {
    "account_creation_plan_defaults": "公众号默认创作方案兼容映射表",
    "account_editorial_review_defaults": "公众号默认评审方案及覆盖配置兼容表",
    "ads": "文章可插入的推广内容与投放优先级表",
    "ai_models": "用户配置的云端或本地 AI 模型表",
    "ai_usage_events": "单次真实 AI 提供商调用的用量与成本明细表",
    "app_settings": "全应用共享的键值设置表",
    "batch_jobs": "运营批次与文章任务关系及审核状态兼容表",
    "batches": "一次内容运营动作的批次主表",
    "billing_plans": "平台订阅套餐及周期积分定义表",
    "billing_pricing_policies": "平台商业积分定价总策略表",
    "billing_task_rates": "各类业务任务的固定积分与冻结上限表",
    "bot_contexts": "旧机器人通道的会话上下文兼容表",
    "bot_sessions": "旧机器人通道的会话批次映射兼容表",
    "creation_plan_account_templates": "创作方案在指定公众号下的模板采样快照表",
    "creation_plans": "可复用的文章创作方案表",
    "credit_buckets": "按来源和有效期拆分的用户积分发放批次表",
    "credit_ledger": "积分发放、冻结与释放的不可变审计流水表",
    "draft_deliveries": "微信公众号草稿写入的幂等交付记录表",
    "editorial_review_applications": "用户应用 AI 评审修改建议的执行记录表",
    "editorial_review_profiles": "用户可复用的 AI 内容评审规则模板表",
    "editorial_reviews": "文章 AI 评审结果、选择与改写状态主表",
    "feishu_integration_accounts": "飞书机器人获准操作的公众号映射表",
    "feishu_integrations": "每个用户独立配置的飞书机器人集成主表",
    "feishu_processed_events": "飞书机器人事件幂等去重表",
    "feishu_sessions": "飞书机器人按会话保存的上下文与批次状态表",
    "followed_accounts": "用户持续关注并定时采集的账号配置表",
    "followed_articles": "从关注账号发现的文章及运营状态表",
    "job_attempts": "文章后台任务每次执行尝试、租约与重试记录表",
    "job_versions": "文章标题、正文、排版和封面的历史版本表",
    "jobs": "单个公众号对应的一篇文章生产任务当前态主表",
    "local_agent_pairings": "本地模型执行器短期安全配对流程表",
    "local_model_agents": "用户已绑定的本地模型执行器表",
    "local_model_requests": "云端派发给本地模型执行器的请求队列表",
    "model_price_cards": "AI 提供商模型资源成本与生效区间价卡表",
    "official_accounts": "用户管理的微信公众号主档及默认配置表",
    "processed_events": "通用或旧消息通道的事件幂等记录表",
    "prompt_templates": "用户可复用的文章或图片提示词模板表",
    "schema_migrations": "数据库迁移版本、校验和与应用时间记录表",
    "token_cache": "按作用域缓存的短期外部访问令牌表",
    "topic_items": "选题源采集到的候选选题条目表",
    "topic_sources": "用户配置的选题采集来源表",
    "usage_operations": "一次可计费业务操作的积分估算、冻结和结算主表",
    "user_sessions": "用户登录会话及过期状态表",
    "user_settings": "用户级键值设置表",
    "user_subscriptions": "用户订阅套餐与当前计费周期状态表",
    "users": "系统用户、角色和启用状态主表",
    "wechat_connection_health": "公众号连接探测与最近写入健康状态缓存表",
}


_DATABASE_COLUMN_COMMENTS = {
    "account_id": "关联的微信公众号记录标识",
    "account_name": "公众号名称快照",
    "account_name_snapshot": "任务创建时的公众号名称快照",
    "actor_user_id": "执行该积分动作的管理员或用户标识",
    "ad_id": "文章选用的推广内容标识",
    "agent_id": "关联的本地模型执行器标识",
    "agent_model_id": "飞书智能体执行命令时使用的 AI 模型标识",
    "amount_points": "本次积分流水变动数量，正数增加、负数减少",
    "annual_price_fen": "套餐年付价格，单位为人民币分",
    "api_base": "AI 提供商兼容接口的基础地址",
    "api_key_encrypted": "AI 提供商 API Key 的加密密文",
    "app_id": "微信公众号或飞书应用的 AppID",
    "app_secret_encrypted": "应用 Secret 的加密密文",
    "applied_at": "记录或迁移实际应用完成时间，UTC ISO-8601",
    "approved_at": "本地执行器配对被用户批准的时间，UTC ISO-8601",
    "archived_at": "批次归档时间，UTC ISO-8601；为空表示未归档",
    "article_prompt_template_id": "创作方案引用的文章提示词模板标识",
    "attempt_id": "本地模型请求本次执行尝试的唯一标识",
    "attempt_no": "同一任务当前是第几次执行尝试",
    "attempts": "草稿交付已执行的尝试次数",
    "auto_renew": "订阅是否开启自动续费，1 是、0 否",
    "base_points": "该业务任务成功完成时收取的固定基础积分",
    "batch_id": "关联的内容运营批次标识",
    "billable": "该 AI 调用是否满足正式计费条件，1 是、0 否",
    "billing_cycle": "订阅计费周期，例如 monthly 或 annual",
    "blocking_count": "评审结果中阻止直接写入草稿的风险项数量",
    "body": "文章正文的结构化或 Markdown 内容",
    "bound_chat_id": "飞书机器人强绑定的会话标识",
    "bound_open_id": "飞书机器人强绑定的用户 OpenID",
    "bucket_id": "关联的积分发放批次标识",
    "byok_infrastructure_points": "用户自带模型密钥时收取的平台基础积分",
    "cached_input_micro_cny_per_million": "每百万缓存输入 Token 的成本，单位人民币微元",
    "cached_input_tokens": "提供商报告的缓存命中输入 Token 数量",
    "callback_key": "飞书事件回调路由使用的唯一公开键",
    "candidate_snapshot_json": "评审改写生成的候选文章快照 JSON",
    "capture_title": "采样公众号模板时捕获的素材标题",
    "category": "选题或关注账号的运营分类",
    "charged_points": "本次业务操作最终实际扣除的积分",
    "chat_id": "发起任务或保存会话上下文的聊天标识",
    "checked_at": "连接健康探测执行时间，UTC ISO-8601",
    "checksum": "迁移定义的 SHA-256 校验和",
    "claimed_by": "当前领取本地模型请求的执行器或租约持有者",
    "cockpit_status": "本地驾驶舱执行器最近上报的运行状态",
    "completed_at": "业务执行完成时间，UTC ISO-8601",
    "config_json": "当前记录的结构化配置 JSON",
    "confirmed_at": "文章被人工确认通过的时间，UTC ISO-8601",
    "consumed_at": "一次性配对凭据被领取使用的时间，UTC ISO-8601",
    "content": "提示词模板的完整文本内容",
    "content_fingerprint": "用于草稿幂等判断的文章内容指纹",
    "content_revision": "文章内容修订号，每次正式修改递增",
    "context_json": "机器人会话的结构化上下文 JSON",
    "contributes_to_result": "该 AI 调用结果是否参与最终输出，1 是、0 否",
    "course_start_at": "推广课程或活动开始时间，UTC ISO-8601",
    "cover_url": "采集文章的封面图片地址",
    "created_at": "记录创建时间，UTC ISO-8601",
    "creation_plan_id": "关联的创作方案标识",
    "current_period_end": "当前订阅计费周期结束时间，UTC ISO-8601",
    "current_period_start": "当前订阅计费周期开始时间，UTC ISO-8601",
    "deadline_at": "本地模型请求允许完成的最晚时间，UTC ISO-8601",
    "default_creation_plan_id": "公众号默认使用的创作方案标识",
    "default_editorial_review_profile_id": "公众号默认使用的评审方案标识",
    "description": "记录的业务说明",
    "details_json": "状态、错误或外部响应的脱敏详情 JSON",
    "device_code_hash": "本地执行器设备配对码的安全哈希",
    "device_name": "用户可识别的本地执行器设备名称",
    "digest": "文章摘要或微信草稿摘要",
    "discovered_at": "外部文章被系统发现的时间，UTC ISO-8601",
    "display_id": "提供给运营人员查看的短批次编号",
    "draft_media_id": "微信公众号草稿接口返回的 Media ID",
    "editorial_review_config_json": "公众号级 AI 评审覆盖配置 JSON",
    "editorial_review_profile_id": "创作方案默认引用的评审方案标识",
    "effective_from": "模型价卡开始生效时间，UTC ISO-8601",
    "effective_to": "模型价卡结束生效时间，UTC ISO-8601；为空表示长期有效",
    "enabled": "记录是否启用，1 是、0 否",
    "encrypt_key_encrypted": "飞书事件加密密钥的加密密文",
    "entitlements_json": "套餐包含的功能权益配置 JSON",
    "error": "最近一次失败的可读错误信息",
    "error_code": "结构化错误码，供程序判断和统计",
    "estimated_points": "按计价规则估算的积分数量",
    "event_id": "外部消息事件的唯一标识",
    "event_type": "积分流水事件类型，例如 grant、reserve 或 release",
    "expires_at": "记录或凭据失效时间，UTC ISO-8601",
    "external_key": "采集渠道提供的文章外部唯一键",
    "external_subscription_id": "支付或订阅平台返回的外部订阅标识",
    "failed_attempts": "安全校验或配对连续失败次数",
    "favorite": "选题是否收藏，1 是、0 否",
    "fetch_method": "关注账号文章的采集方式",
    "fixed_request_micro_cny": "每次固定请求成本，单位人民币微元",
    "fixed_units": "提供商按固定单位计量的本次使用数量",
    "followed_account_id": "文章所属的关注账号标识",
    "funding_source": "AI 调用成本来源，例如 platform 或 customer",
    "granted_points": "该积分批次最初发放的积分总数",
    "hash_iterations": "安全哈希使用的迭代次数",
    "heartbeat_at": "后台任务执行器最后心跳时间，UTC ISO-8601",
    "html_content": "文章渲染后的微信公众号 HTML 内容",
    "id": "记录唯一标识",
    "idempotency_key": "防止相同业务请求重复执行的幂等键",
    "image_count": "本次 AI 调用生成或处理的图片数量",
    "image_micro_cny_each": "每张图片的提供商成本，单位人民币微元",
    "image_prompt_template_id": "创作方案引用的图片提示词模板标识",
    "image_settings_json": "创作方案的图片生成与插图配置 JSON",
    "input_micro_cny_per_million": "每百万输入 Token 的成本，单位人民币微元",
    "input_tokens": "提供商报告的输入 Token 数量",
    "instruction": "用户针对本次评审改写补充的自然语言要求",
    "integration_id": "关联的飞书机器人集成标识",
    "is_default": "是否为该飞书机器人的默认公众号，1 是、0 否",
    "is_favorite": "关注文章是否收藏，1 是、0 否",
    "is_ignored": "关注文章是否已忽略，1 是、0 否",
    "is_owned": "关注账号是否为当前用户自有公众号，1 是、0 否",
    "is_read": "关注文章是否已读，1 是、0 否",
    "job_id": "关联的文章生产任务标识",
    "key": "设置项或缓存项的唯一键",
    "keywords_json": "关注账号采集与筛选关键词 JSON",
    "label": "任务费率在界面展示的中文名称",
    "last_error": "最近一次同步失败的错误摘要",
    "last_error_code": "最近一次失败的结构化错误码",
    "last_polled_at": "配对客户端最后轮询时间，UTC ISO-8601",
    "last_seen_at": "会话或本地执行器最后活跃时间，UTC ISO-8601",
    "last_successful_write_at": "公众号最近一次成功写入草稿的时间，UTC ISO-8601",
    "last_synced_at": "最近一次成功同步时间，UTC ISO-8601",
    "last_used_at": "推广内容最近一次被选用的时间，UTC ISO-8601",
    "latency_ms": "连接健康探测耗时，单位毫秒",
    "layout_json": "公众号或创作方案的排版配置 JSON",
    "lease_until": "本地模型请求当前租约截止时间，UTC ISO-8601",
    "local_agent_id": "云端模型配置绑定的本地执行器标识",
    "markup_basis_points": "模型价卡零售加价比例，单位基点，10000 为 100%",
    "max_package_discount_basis_points": "套餐允许的最大折扣比例，单位基点",
    "max_reserve_points": "任务开始前最多冻结的积分",
    "meta_json": "文章任务或版本的扩展元数据 JSON",
    "metering_mode": "模型价卡计量方式，例如 TOKEN、FIXED、UNIT 或 BYOK",
    "modality": "AI 调用或价卡适用的模态，例如 text 或 image",
    "mode": "当前记录使用的处理、计费或探测模式",
    "model": "提供商接口实际使用的模型名称",
    "model_id": "关联的 AI 模型配置标识",
    "model_name": "评审执行时使用的模型名称快照",
    "monthly_points": "套餐每个自然计费月发放的积分",
    "monthly_price_fen": "套餐月付价格，单位人民币分",
    "name": "记录名称",
    "next_grant_at": "订阅下次发放周期积分的时间，UTC ISO-8601",
    "next_retry_at": "失败任务允许再次重试的时间，UTC ISO-8601",
    "nonce": "本地模型请求用于防重放的随机数",
    "official_account_id": "关注账号对应的自有公众号标识",
    "operation": "本地模型请求的接口操作名称",
    "operation_id": "关联的可计费用量操作标识",
    "output_micro_cny_per_million": "每百万输出 Token 的成本，单位人民币微元",
    "output_tokens": "提供商报告的输出 Token 数量",
    "owner_session_id": "持有后台任务租约的应用进程会话标识",
    "owner_user_id": "该客户数据所属的系统用户标识",
    "pairing_code_hash": "飞书机器人一次性配对码的安全哈希",
    "pairing_expires_at": "飞书机器人配对码失效时间，UTC ISO-8601",
    "pairing_failed_attempts": "飞书机器人配对失败次数",
    "pairing_iterations": "飞书机器人配对码哈希迭代次数",
    "pairing_salt": "飞书机器人配对码哈希使用的随机盐",
    "pairing_used_at": "飞书机器人配对码被成功使用的时间，UTC ISO-8601",
    "paragraph_numbers_json": "用户选择改写的文章段落编号数组 JSON",
    "parent_batch_id": "派生或重试批次对应的父批次标识",
    "password_hash": "用户登录密码的不可逆安全哈希",
    "payment_fee_basis_points": "支付渠道费率，单位基点",
    "placeholder": "公众号模板采样时识别出的正文占位标记",
    "plan_id": "用户订阅的套餐标识",
    "platform_task_cost_micro_cny": "每次平台任务固定成本，单位人民币微元",
    "point_retail_micro_cny": "一个平台积分对应的零售价值，单位人民币微元",
    "points_per_cny": "旧价卡换算参数，每人民币元对应的积分数",
    "price_snapshot_json": "AI 调用发生时使用的模型价卡快照 JSON",
    "pricing_snapshot_json": "业务操作结算时使用的任务和定价策略快照 JSON",
    "pricing_status": "AI 调用的计价完整性状态",
    "priority": "推广内容选择优先级，数值越大越优先",
    "profile_id": "关联的 AI 评审方案标识",
    "profile_name": "评审执行时使用的评审方案名称快照",
    "provider": "AI 服务提供商标准标识",
    "provider_cost_micro_cny": "本次调用的提供商实际成本，单位人民币微元",
    "provider_credits": "提供商返回的非 Token 计量点数或 Credits",
    "provider_model": "AI 提供商实际返回或计价使用的模型名称",
    "provider_request_id": "AI 提供商返回的请求追踪标识",
    "provider_response_id": "AI 提供商返回的响应追踪标识",
    "provider_risk_basis_points": "该模型价卡的成本风险系数，单位基点",
    "provider_risk_reserve_basis_points": "平台统一提供商成本风险准备比例，单位基点",
    "provider_type": "AI 模型适配器或提供商类型",
    "provider_unit_micro_cny_each": "每个提供商计量单位的成本，单位人民币微元",
    "publish_id": "微信公众号发布接口返回的发布标识",
    "published_at": "外部内容原始发布时间，UTC ISO-8601",
    "purpose": "提示词模板用途，例如文章生成或图片生成",
    "raw_content": "导入后尚未改写的原始正文",
    "raw_json": "采集选题时保留的脱敏原始响应 JSON",
    "raw_title": "导入文章的原始标题",
    "raw_usage_json": "AI 提供商返回的脱敏原始用量 JSON",
    "reason": "本次版本或积分变动的业务原因",
    "reasoning_micro_cny_per_million": "每百万推理 Token 的成本，单位人民币微元",
    "reasoning_tokens": "提供商报告的推理 Token 数量",
    "reconciled_at": "草稿交付最后一次对账时间，UTC ISO-8601",
    "reference_urls_json": "生成任务引用的外部资料地址数组 JSON",
    "refresh_hours": "关注账号自动刷新间隔，单位小时",
    "remaining_points": "该积分批次当前仍可使用的积分",
    "request_json": "发送给本地模型执行器的请求参数 JSON",
    "requested_by": "批次发起人的外部身份或显示名称",
    "required_facts": "生成或改写时必须保留的事实要求",
    "reservation_expires_at": "本次积分冻结自动失效时间，UTC ISO-8601",
    "reserved_points": "任务开始时已经冻结的积分",
    "resource_points": "按 AI 资源实际成本换算的积分",
    "response_text": "本地模型执行器返回的响应正文",
    "result_error_code": "本地模型执行结果的结构化错误码",
    "result_json": "AI 评审的结构化结果 JSON",
    "retail_cost_micro_cny": "本次 AI 调用按零售规则计算的成本，单位人民币微元",
    "review_id": "关联的 AI 评审记录标识",
    "review_priority": "公众号进入审核队列时的优先级",
    "review_status": "文章人工审核状态",
    "revision": "AI 评审记录修订号",
    "revoked_at": "本地执行器授权撤销时间，UTC ISO-8601",
    "rewrite_intensity": "文章改写强度，例如轻度、标准或深度",
    "rewrite_mode": "评审修改稿的改写范围或方式",
    "rewritten_batch_id": "该外部文章被改写后生成的批次标识",
    "rewritten_snapshot_json": "AI 评审改写完成后的文章快照 JSON",
    "role": "系统用户角色，例如 admin 或 user",
    "rounding_points": "计费结果向上舍入的积分步长",
    "runtime_json": "飞书机器人运行时状态与能力配置 JSON",
    "sample_url": "关注账号用于识别采集规则的样例文章地址",
    "scene": "用量操作所属业务场景",
    "scheduled_at": "文章任务计划执行时间，UTC ISO-8601",
    "scope_id": "旧机器人通道使用的会话作用域标识",
    "selected_article_index": "公众号模板采样时选中的素材序号",
    "selected_issue_ids_json": "用户选择处理的评审问题标识数组 JSON",
    "selected_media_id": "公众号模板采样时选中的素材 Media ID",
    "selected_subtitle": "最终确认使用的文章副标题",
    "selected_title": "最终确认或模板采样选中的标题",
    "snapshot_html": "公众号模板采样时保存的 HTML 快照",
    "snapshot_sha256": "公众号模板 HTML 快照的 SHA-256 指纹",
    "source": "文章任务的旧来源字段，兼容历史调用",
    "source_app_id": "模板采样来源公众号的 AppID",
    "source_channel": "任务、文章或用量操作的发起渠道",
    "source_hash": "发起评审改写时原文快照的内容哈希",
    "source_id": "来源记录或上游对象标识",
    "source_integration_id": "发起该批次的飞书机器人集成标识",
    "source_key": "用户范围内稳定且唯一的选题来源键",
    "source_mode": "正文来源方式，例如手工、URL 或关注文章",
    "source_snapshot_json": "AI 评审开始时的文章原文快照 JSON",
    "source_type": "积分、选题或数据来源类型",
    "source_url": "内容、账号或批次对应的原始来源地址",
    "stage": "后台任务当前执行阶段",
    "started_at": "后台任务尝试开始时间，UTC ISO-8601",
    "status": "记录当前业务状态",
    "step": "文章生产流程当前步骤",
    "strict_token_eligible": "模型是否允许进入严格 Token 计量，1 是、0 否",
    "subject_id": "用量操作对应的业务对象标识",
    "subject_type": "用量操作对应的业务对象类型",
    "subtitle": "文章副标题",
    "subtitles_json": "AI 生成的副标题候选数组 JSON",
    "summary": "采集内容的摘要",
    "tags_json": "关注账号的运营标签数组 JSON",
    "target_margin_basis_points": "平台目标毛利率，单位基点",
    "task_base_points": "本次操作固化的任务基础积分",
    "task_code": "业务任务的稳定计费代码",
    "tax_basis_points": "税费比例，单位基点",
    "thumb_media_id": "微信公众号封面素材 Media ID",
    "title": "标题或名称正文",
    "title_candidates_json": "带评分等信息的标题候选对象数组 JSON",
    "titles_json": "AI 生成的标题候选数组 JSON",
    "token_hash": "登录会话或本地执行器令牌的安全哈希",
    "token_metering_capability": "模型支持的 Token 计量能力状态",
    "token_metering_checked_at": "最近一次 Token 计量能力探测时间，UTC ISO-8601",
    "token_usage_status": "本次 AI 调用 Token 用量是否真实、估算或不可用",
    "topic": "批次或文章任务的创作主题",
    "total_tokens": "提供商报告的总 Token 数量",
    "updated_at": "记录最后更新时间，UTC ISO-8601",
    "url": "内容或推广信息对应的外部地址",
    "usage_source": "用量数值来源，例如提供商真实值或估算值",
    "used": "选题是否已经用于创作，1 是、0 否",
    "user_code_hash": "用户输入配对码的安全哈希",
    "user_code_salt": "用户输入配对码哈希使用的随机盐",
    "user_id": "关联的系统用户标识",
    "username": "用户登录名，全局唯一",
    "value": "设置项或缓存项保存的值",
    "verification_token_encrypted": "飞书事件校验 Token 的加密密文",
    "version": "配置、费率或迁移的版本号",
    "viewed_at": "文章首次被人工查看的时间，UTC ISO-8601",
    "wechat_id": "关注账号的微信号",
}


def apply_shadow_billing_schema(conn: Any) -> None:
    """Create the additive token-cost/points shadow schema exactly once."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS billing_plans (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            monthly_price_fen INTEGER NOT NULL DEFAULT 0,
            annual_price_fen INTEGER NOT NULL DEFAULT 0,
            monthly_points INTEGER NOT NULL DEFAULT 0,
            entitlements_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            billing_cycle TEXT NOT NULL,
            status TEXT NOT NULL,
            current_period_start TEXT,
            current_period_end TEXT,
            next_grant_at TEXT,
            auto_renew INTEGER NOT NULL DEFAULT 0,
            external_subscription_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id) REFERENCES billing_plans(id)
        );

        CREATE TABLE IF NOT EXISTS model_price_cards (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            provider_model TEXT NOT NULL,
            modality TEXT NOT NULL DEFAULT 'text',
            input_micro_cny_per_million INTEGER NOT NULL DEFAULT 0,
            cached_input_micro_cny_per_million INTEGER NOT NULL DEFAULT 0,
            output_micro_cny_per_million INTEGER NOT NULL DEFAULT 0,
            image_micro_cny_each INTEGER NOT NULL DEFAULT 0,
            fixed_request_micro_cny INTEGER NOT NULL DEFAULT 0,
            markup_basis_points INTEGER NOT NULL DEFAULT 10000,
            points_per_cny INTEGER NOT NULL DEFAULT 100,
            effective_from TEXT NOT NULL,
            effective_to TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS credit_buckets (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT,
            granted_points INTEGER NOT NULL,
            remaining_points INTEGER NOT NULL CHECK (remaining_points >= 0),
            expires_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS usage_operations (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            scene TEXT NOT NULL,
            source_channel TEXT NOT NULL,
            subject_type TEXT NOT NULL,
            subject_id TEXT,
            idempotency_key TEXT NOT NULL,
            status TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'shadow',
            job_id INTEGER,
            estimated_points INTEGER NOT NULL DEFAULT 0,
            reserved_points INTEGER NOT NULL DEFAULT 0,
            charged_points INTEGER NOT NULL DEFAULT 0,
            reservation_expires_at TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL,
            UNIQUE (owner_user_id, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS credit_ledger (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            bucket_id TEXT,
            operation_id TEXT,
            amount_points INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            actor_user_id TEXT,
            reason TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (bucket_id) REFERENCES credit_buckets(id),
            FOREIGN KEY (operation_id) REFERENCES usage_operations(id)
        );

        CREATE TABLE IF NOT EXISTS ai_usage_events (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            job_id INTEGER,
            model_id TEXT,
            provider TEXT NOT NULL,
            provider_model TEXT,
            funding_source TEXT NOT NULL,
            modality TEXT NOT NULL,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            image_count INTEGER NOT NULL DEFAULT 0,
            fixed_units INTEGER NOT NULL DEFAULT 0,
            usage_source TEXT NOT NULL,
            provider_request_id TEXT,
            provider_response_id TEXT,
            provider_cost_micro_cny INTEGER NOT NULL DEFAULT 0,
            retail_cost_micro_cny INTEGER NOT NULL DEFAULT 0,
            estimated_points INTEGER NOT NULL DEFAULT 0,
            pricing_status TEXT NOT NULL DEFAULT 'price_missing',
            price_snapshot_json TEXT NOT NULL DEFAULT '{}',
            contributes_to_result INTEGER NOT NULL DEFAULT 1,
            billable INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            error_code TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (operation_id) REFERENCES usage_operations(id) ON DELETE CASCADE,
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_subscriptions_owner
        ON user_subscriptions(owner_user_id, status);
        CREATE INDEX IF NOT EXISTS idx_price_cards_lookup
        ON model_price_cards(provider, provider_model, modality, effective_from);
        CREATE INDEX IF NOT EXISTS idx_credit_buckets_owner_expiry
        ON credit_buckets(owner_user_id, expires_at, created_at);
        CREATE INDEX IF NOT EXISTS idx_credit_ledger_owner_created
        ON credit_ledger(owner_user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_operations_owner_created
        ON usage_operations(owner_user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_events_operation
        ON ai_usage_events(operation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_usage_events_owner_created
        ON ai_usage_events(owner_user_id, created_at);
        """
    )


def apply_strict_token_metering_schema(conn: Any) -> None:
    """Add explicit Token completeness without rewriting the billing baseline."""

    usage_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(ai_usage_events)").fetchall()
    }
    for name, declaration in {
        "token_usage_status": "TEXT NOT NULL DEFAULT 'RECORDED'",
        "provider_credits": "INTEGER",
        "raw_usage_json": "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in usage_columns:
            conn.execute(
                f"ALTER TABLE ai_usage_events ADD COLUMN {name} {declaration}"
            )
    conn.execute(
        """
        UPDATE ai_usage_events
        SET token_usage_status = CASE
            WHEN usage_source = 'provider_actual'
             AND (input_tokens > 0 OR output_tokens > 0 OR total_tokens > 0)
            THEN 'RECORDED'
            ELSE 'UNAVAILABLE'
        END
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_provider_request_unique
        ON ai_usage_events(owner_user_id, provider, provider_request_id)
        WHERE provider_request_id IS NOT NULL AND provider_request_id <> ''
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_events_provider_response_unique
        ON ai_usage_events(owner_user_id, provider, provider_response_id)
        WHERE provider_response_id IS NOT NULL AND provider_response_id <> ''
        """
    )

    model_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(ai_models)").fetchall()
    }
    for name, declaration in {
        "token_metering_capability": "TEXT NOT NULL DEFAULT 'unverified'",
        "strict_token_eligible": "INTEGER NOT NULL DEFAULT 0",
        "token_metering_checked_at": "TEXT",
    }.items():
        if name not in model_columns:
            conn.execute(f"ALTER TABLE ai_models ADD COLUMN {name} {declaration}")
    conn.execute(
        """
        UPDATE ai_models
        SET token_metering_capability = CASE
                WHEN provider_type = 'manus' THEN 'no_token_usage'
                WHEN provider_type = 'local_openai_compatible' THEN 'estimated_only'
                WHEN provider_type IN (
                    'image_alibaba', 'image_minimax', 'image_volcengine',
                    'image_zhipu', 'openai_image'
                ) THEN 'not_applicable'
                ELSE COALESCE(NULLIF(token_metering_capability, ''), 'unverified')
            END,
            strict_token_eligible = CASE
                WHEN provider_type IN (
                    'manus', 'local_openai_compatible', 'image_alibaba',
                    'image_minimax', 'image_volcengine', 'image_zhipu',
                    'openai_image'
                ) THEN 0
                ELSE strict_token_eligible
            END
        """
    )


def apply_commercial_points_billing_schema(conn: Any) -> None:
    """Add configurable pricing plus reversible point reservations."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS billing_pricing_policies (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'shadow',
            point_retail_micro_cny INTEGER NOT NULL DEFAULT 10000,
            max_package_discount_basis_points INTEGER NOT NULL DEFAULT 2000,
            payment_fee_basis_points INTEGER NOT NULL DEFAULT 150,
            tax_basis_points INTEGER NOT NULL DEFAULT 600,
            target_margin_basis_points INTEGER NOT NULL DEFAULT 6500,
            provider_risk_reserve_basis_points INTEGER NOT NULL DEFAULT 1500,
            platform_task_cost_micro_cny INTEGER NOT NULL DEFAULT 30000,
            rounding_points INTEGER NOT NULL DEFAULT 5,
            byok_infrastructure_points INTEGER NOT NULL DEFAULT 15,
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS billing_task_rates (
            task_code TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            base_points INTEGER NOT NULL DEFAULT 0,
            max_reserve_points INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_billing_task_rates_enabled
        ON billing_task_rates(enabled, task_code);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_ledger_operation_bucket_event
        ON credit_ledger(operation_id, bucket_id, event_type)
        WHERE operation_id IS NOT NULL
          AND bucket_id IS NOT NULL
          AND event_type IN ('reserve', 'release');
        """
    )

    price_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(model_price_cards)").fetchall()
    }
    for name, declaration in {
        "metering_mode": "TEXT NOT NULL DEFAULT 'TOKEN'",
        "reasoning_micro_cny_per_million": "INTEGER NOT NULL DEFAULT 0",
        "provider_unit_micro_cny_each": "INTEGER NOT NULL DEFAULT 0",
        "provider_risk_basis_points": "INTEGER NOT NULL DEFAULT 10000",
    }.items():
        if name not in price_columns:
            conn.execute(f"ALTER TABLE model_price_cards ADD COLUMN {name} {declaration}")

    operation_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(usage_operations)").fetchall()
    }
    for name, declaration in {
        "task_code": "TEXT NOT NULL DEFAULT ''",
        "task_base_points": "INTEGER NOT NULL DEFAULT 0",
        "resource_points": "INTEGER NOT NULL DEFAULT 0",
        "pricing_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
    }.items():
        if name not in operation_columns:
            conn.execute(f"ALTER TABLE usage_operations ADD COLUMN {name} {declaration}")

    seed_time = "2026-08-25T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO billing_pricing_policies (
            id, name, mode, point_retail_micro_cny,
            max_package_discount_basis_points, payment_fee_basis_points,
            tax_basis_points, target_margin_basis_points,
            provider_risk_reserve_basis_points,
            platform_task_cost_micro_cny, rounding_points,
            byok_infrastructure_points, enabled, version, created_at, updated_at
        ) VALUES (
            'default', '默认商业积分政策', 'shadow', 10000,
            2000, 150, 600, 6500, 1500, 30000, 5, 15,
            1, 1, ?, ?
        ) ON CONFLICT(id) DO NOTHING
        """,
        (seed_time, seed_time),
    )
    for task_code, label, base_points, max_reserve_points in (
        ("article_light", "轻度润色", 30, 200),
        ("article_standard", "标准改写", 60, 400),
        ("article_deep", "深度改写", 120, 800),
        ("research_longform", "研究型长文", 240, 1200),
        ("editorial_review", "AI 评审", 30, 200),
        ("editorial_rewrite", "评审修改稿", 60, 400),
        ("paragraph_regeneration", "单段轻度改写", 30, 150),
        ("title_summary", "标题与摘要", 20, 100),
        ("inline_images_regeneration", "正文配图", 0, 400),
        ("inline_image_regeneration", "单张配图", 0, 200),
        ("cover_regeneration", "封面生成", 0, 200),
    ):
        conn.execute(
            """
            INSERT INTO billing_task_rates (
                task_code, label, base_points, max_reserve_points,
                enabled, version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
            ON CONFLICT(task_code) DO NOTHING
            """,
            (
                task_code,
                label,
                base_points,
                max_reserve_points,
                seed_time,
                seed_time,
            ),
        )


def apply_platform_jizhile_and_followed_refresh(conn: Any) -> None:
    """Centralize Jizhile credentials and price one upstream article refresh."""

    platform_key = "platform.jizhile_api"
    existing = conn.execute(
        "SELECT 1 FROM app_settings WHERE key = ?",
        (platform_key,),
    ).fetchone()
    if not existing:
        legacy = conn.execute(
            """
            SELECT customer.value
            FROM user_settings AS customer
            JOIN app_settings AS claim
              ON claim.key = 'migration.customer_data_owner.v1'
             AND claim.value = customer.user_id
            WHERE customer.key = 'jizhile_api'
            LIMIT 1
            """
        ).fetchone()
        if not legacy:
            legacy = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'jizhile_api' LIMIT 1"
            ).fetchone()
        if legacy:
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (platform_key, str(legacy["value"]), "2026-08-26T00:00:00+00:00"),
            )

    seed_time = "2026-08-26T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO billing_task_rates (
            task_code, label, base_points, max_reserve_points,
            enabled, version, created_at, updated_at
        ) VALUES (
            'followed_articles_refresh', '获取公众号文章', 10, 10,
            1, 1, ?, ?
        )
        ON CONFLICT(task_code) DO NOTHING
        """,
        (seed_time, seed_time),
    )


def apply_followed_article_refresh_twenty_points(conn: Any) -> None:
    """Raise one followed-account article refresh to twenty points."""

    conn.execute(
        """
        UPDATE billing_task_rates
        SET base_points = 20,
            max_reserve_points = 20,
            version = version + 1,
            updated_at = ?
        WHERE task_code = 'followed_articles_refresh'
        """,
        ("2026-08-26T00:00:00+00:00",),
    )


def apply_database_object_comments(conn: Any) -> None:
    """Describe every managed PostgreSQL table and column in Chinese."""

    def identifier(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    for table_name, table_comment in _DATABASE_TABLE_COMMENTS.items():
        columns = [
            str(row["name"])
            for row in conn.execute(
                f"PRAGMA table_info({identifier(table_name)})"
            ).fetchall()
        ]
        if not columns:
            raise RuntimeError(f"数据库注释迁移缺少表：{table_name}")
        conn.execute(
            f"COMMENT ON TABLE {identifier(table_name)} "
            f"IS {literal(table_comment)}"
        )
        for column_name in columns:
            column_comment = _DATABASE_COLUMN_COMMENTS.get(column_name)
            if not column_comment:
                raise RuntimeError(
                    f"数据库注释迁移缺少字段说明：{table_name}.{column_name}"
                )
            conn.execute(
                f"COMMENT ON COLUMN {identifier(table_name)}."
                f"{identifier(column_name)} IS {literal(column_comment)}"
            )


def ensure_schema_migrations(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def validate_schema_migrations(conn: Any) -> None:
    expected = {migration.version: migration for migration in SCHEMA_MIGRATIONS}
    rows = conn.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    for row in rows:
        version = str(row["version"])
        migration = expected.get(version)
        if migration is None:
            raise RuntimeError(
                f"数据库迁移版本 {version} 高于当前应用，拒绝以旧代码启动"
            )
        if str(row["name"]) != migration.name or str(
            row["checksum"]
        ) != migration.checksum:
            raise RuntimeError(f"数据库迁移 {version} checksum 不一致，拒绝启动")


def migration_applied(conn: Any, migration: SchemaMigration) -> bool:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE version = ?",
        (migration.version,),
    ).fetchone()
    if not row:
        return False
    if str(row["checksum"]) != migration.checksum:
        raise RuntimeError(
            f"数据库迁移 {migration.version} checksum 不一致，拒绝启动"
        )
    return True


def record_schema_migration(
    conn: Any,
    migration: SchemaMigration,
    *,
    applied_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO schema_migrations (version, name, checksum, applied_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(version) DO NOTHING
        """,
        (
            migration.version,
            migration.name,
            migration.checksum,
            applied_at,
        ),
    )


__all__ = [
    "BASELINE_SCHEMA",
    "COMMERCIAL_POINTS_BILLING",
    "DATABASE_OBJECT_COMMENTS",
    "DROP_DUPLICATE_INDEXES",
    "FOLLOWED_ARTICLE_REFRESH_TWENTY_POINTS",
    "PHASE_ONE_COMPAT",
    "PLATFORM_JIZHILE_AND_FOLLOWED_REFRESH",
    "SCHEMA_MIGRATIONS",
    "SHADOW_BILLING_SCHEMA",
    "STRICT_TOKEN_METERING",
    "SchemaMigration",
    "apply_commercial_points_billing_schema",
    "apply_database_object_comments",
    "apply_followed_article_refresh_twenty_points",
    "apply_platform_jizhile_and_followed_refresh",
    "apply_shadow_billing_schema",
    "apply_strict_token_metering_schema",
    "ensure_schema_migrations",
    "migration_applied",
    "record_schema_migration",
    "validate_schema_migrations",
]
