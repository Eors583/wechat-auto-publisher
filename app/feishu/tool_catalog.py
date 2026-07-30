from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolSpec:
    category: str
    summary: str
    arguments: str = "无"
    requires_confirmation: bool = False
    confirmation_hint: str = ""

    def prompt_line(self) -> str:
        text = f"- {self.summary}；arguments：{self.arguments}。"
        if self.requires_confirmation:
            text += (
                " 这是敏感或不可逆操作，仅当用户本条消息明确确认时调用，"
                "并传 confirmation=true。"
            )
        return text


# Parameter names intentionally follow the BatchService, account/model helpers and
# FastAPI request models.  The planner may select only one of these tools per turn;
# tools that need a prerequisite list therefore naturally become a short dialogue.
TOOL_SPECS: dict[str, ToolSpec] = {
    "chat": ToolSpec("对话", "chat：解释、帮助、补充信息或确认敏感操作", "reply 写中文回复"),
    "list_accounts": ToolSpec("公众号", "list_accounts：列出可用公众号及其绑定模型"),
    "preflight_accounts": ToolSpec(
        "公众号",
        "preflight_accounts：生成前检查公众号凭证、模型、模板、封面和配图环境",
        "account_ids 或 account_names；可选 deep_model_check",
    ),
    "get_account_config": ToolSpec(
        "公众号",
        "get_account_config：查看一个公众号当前模型、提示词、排版、草稿模板和生图配置（不返回密钥）",
        "account_id 或 account_name",
    ),
    "test_account_connection": ToolSpec(
        "公众号",
        "test_account_connection：检查指定公众号凭证、白名单、草稿、模型、模板和配图环境",
        "account_id 或 account_name；可选 deep_model_check",
    ),
    "set_account_model": ToolSpec(
        "公众号",
        "set_account_model：给公众号切换文本改写模型",
        "account_id 或 account_name，model_id，confirmation",
        True,
        "请明确回复“确认更换公众号模型”。",
    ),
    "set_official_account_enabled": ToolSpec(
        "公众号",
        "set_official_account_enabled：启用或停用一个自有公众号",
        "account_id 或 account_name，enabled",
    ),
    "delete_official_account": ToolSpec(
        "公众号",
        "delete_official_account：删除一个自有公众号配置",
        "account_id 或 account_name，confirmation",
        True,
        "请明确回复“确认删除自有公众号 + 名称”。",
    ),
    "create_rewrite_batch": ToolSpec(
        "内容生产",
        "create_rewrite_batch：按链接、粘贴正文、多篇参考资料或话题原创，为多个公众号并发生成文章",
        (
            "source_mode（link/text/references/topic）及对应的 source_url、raw_content、"
            "reference_urls 或 topic；可选 required_facts、rewrite_intensity、account_ids、"
            "account_names、hot_topic_number、followed_article_number、followed_article_id"
        ),
    ),
    "get_batch_status": ToolSpec(
        "批次",
        "get_batch_status：查看当前或指定批次的整体进度和各公众号状态",
        "可选 batch_id",
    ),
    "list_batches": ToolSpec(
        "批次",
        "list_batches：查询历史批次、待审核和失败任务",
        "可选 limit、include_archived、status、keyword",
    ),
    "list_review_inbox": ToolSpec(
        "审核收件箱",
        "list_review_inbox：查询待我审核、写入失败、生成失败或今日完成的文章级待办",
        "可选 bucket（review/write_failed/generation_failed/today_completed）、account_id、limit、cursor",
    ),
    "get_article_attempts": ToolSpec(
        "失败恢复",
        "get_article_attempts：查看一篇文章各处理阶段的执行与重试记录",
        "batch_id、job_id",
    ),
    "retry_article_step": ToolSpec(
        "失败恢复",
        "retry_article_step：从失败步骤继续处理一篇文章，并保留已经完成的上游结果",
        (
            "batch_id、job_id；可选 step（auto/ingest/rewrite/title_optimize/render/"
            "images/inject）、model_id、source_url、raw_content、confirmation"
        ),
        True,
        "请明确回复“确认从失败步骤重试文章”。",
    ),
    "retry_failed_batch": ToolSpec(
        "批次",
        "retry_failed_batch：只重试批次中失败的公众号任务",
        "batch_id，confirmation",
        True,
        "请明确回复“确认重试失败公众号”。",
    ),
    "copy_batch": ToolSpec(
        "批次",
        "copy_batch：复制原批次配置并重新生成（会再次调用模型）",
        "batch_id，confirmation",
        True,
        "请明确回复“确认复制批次重新生成”。",
    ),
    "archive_batch": ToolSpec(
        "批次",
        "archive_batch：归档批次，使其从默认任务列表隐藏",
        "batch_id；可选 archived（true=归档，false=取消归档）；confirmation",
        True,
        "请明确回复“确认归档批次 + 批次号”。",
    ),
    "cancel_rewrite_batch": ToolSpec(
        "批次",
        "cancel_rewrite_batch：请求终止当前或指定批次，不再执行后续步骤",
        "可选 batch_id",
    ),
    "get_article_result": ToolSpec(
        "审核编辑",
        "get_article_result：查看当前或指定公众号文章的标题、副标题和正文预览",
        "可选 batch_id、job_id、account_name",
    ),
    "list_editorial_review_profiles": ToolSpec(
        "AI评审团",
        "list_editorial_review_profiles：列出内置和自定义 AI 评审方案，并显示角色、风格与严格程度",
        "可选 include_builtin、enabled_only",
    ),
    "save_editorial_review_profile": ToolSpec(
        "AI评审团",
        "save_editorial_review_profile：保存一个自定义 AI 评审方案；系统 JSON 协议不受自定义规则影响",
        (
            "name；可选 description、profile_number、profile_id、enabled、role_ids、"
            "style_ids、strictness、focus、target_audience、required_checks、ignored_items、"
            "banned_expressions、must_keep、dimension_strictness、score_weights、"
            "good_example、bad_example、advanced_rules、permissions、confirmation"
        ),
        True,
        "请明确回复“确认保存 AI 评审方案”。",
    ),
    "delete_editorial_review_profile": ToolSpec(
        "AI评审团",
        "delete_editorial_review_profile：删除一个自定义 AI 评审方案；内置方案不可删除",
        "profile_number 或 profile_name；confirmation",
        True,
        "请明确回复“确认删除 AI 评审方案”。",
    ),
    "get_account_editorial_review_default": ToolSpec(
        "AI评审团",
        "get_account_editorial_review_default：查看一个公众号默认使用的 AI 评审方案",
        "account_id 或 account_name",
    ),
    "set_account_editorial_review_default": ToolSpec(
        "AI评审团",
        "set_account_editorial_review_default：给公众号绑定默认 AI 评审方案并可保存覆盖项",
        (
            "account_id 或 account_name；profile_number 或 profile_name；可选 role_ids、"
            "style_ids、strictness、focus 等覆盖项；confirmation"
        ),
        True,
        "请明确回复“确认更换公众号默认 AI 评审方案”。",
    ),
    "run_editorial_review": ToolSpec(
        "AI评审团",
        "run_editorial_review：手动评估标题、开头、预计完读率、点赞意愿和转发动机，只返回少量整体建议，会产生模型费用",
        (
            "可选 batch_id、job_id、profile_number、profile_name，以及 role_ids、"
            "style_ids、strictness、focus 等本次覆盖项；confirmation"
        ),
        True,
        "请明确回复“确认开始 AI 评审”。",
    ),
    "get_editorial_review": ToolSpec(
        "AI评审团",
        "get_editorial_review：查看当前文章最新 AI 评审、建议编号、阻断项和候选修改稿",
        "可选 batch_id、job_id、review_number、limit",
    ),
    "generate_editorial_rewrite_candidate": ToolSpec(
        "AI评审团",
        "generate_editorial_rewrite_candidate：只按人工勾选的建议生成候选修改稿，不立即覆盖原稿",
        (
            "可选 batch_id、job_id、review_number；issue_numbers（评审建议编号）；"
            "rewrite_mode（engagement_optimization/selected_issues/role_guided/"
            "target_style/high_priority/title_only/selected_paragraphs/full_rewrite）；"
            "可选 paragraph_numbers、"
            "instruction；confirmation"
        ),
        True,
        "请明确回复“确认按 AI 评审建议生成修改稿”。",
    ),
    "smart_rewrite_from_editorial_review": ToolSpec(
        "AI评审团",
        "smart_rewrite_from_editorial_review：按用户勾选建议整体优化标题、开头、阅读节奏和点赞/转发动机，直接原位返回待确认，不要求填写修改意见、不逐段润色",
        (
            "可选 batch_id、job_id、review_number；issue_numbers（评审结论中用户可见的建议编号，"
            "至少一项）；confirmation"
        ),
        True,
        "请明确回复“确认智能修改原文，并接受第 N 条评审建议”。",
    ),
    "apply_editorial_review_application": ToolSpec(
        "AI评审团",
        "apply_editorial_review_application：将已生成并对比过的 AI 候选修改稿应用到文章",
        "可选 batch_id、job_id；application_number（修改稿编号）；confirmation",
        True,
        "请明确回复“确认应用 AI 修改稿”。",
    ),
    "resolve_editorial_review_issue": ToolSpec(
        "AI评审团",
        "resolve_editorial_review_issue：更新一条事实或合规建议的人工核实状态",
        (
            "可选 batch_id、job_id、review_number；issue_number；"
            "resolution（已核实/接受风险/重新打开）；"
            "已核实或接受风险时必须填写 note 说明依据；confirmation"
        ),
        True,
        "请明确回复“确认更新 AI 评审核实结果”。",
    ),
    "select_article_title": ToolSpec(
        "审核编辑",
        "select_article_title：选择候选标题和副标题；选择后仍需单独确认文章",
        "可选 batch_id、job_id；title_number（从 1 开始），可选 subtitle_number（从 1 开始）",
    ),
    "request_article_changes": ToolSpec(
        "审核编辑",
        "request_article_changes：把一篇待审核文章标记为需要修改",
        "batch_id，job_id",
    ),
    "confirm_article": ToolSpec(
        "审核编辑",
        "confirm_article：确认一篇已查看或编辑完成的文章并加入批量写入队列",
        "可选 batch_id、job_id",
    ),
    "update_article_content": ToolSpec(
        "审核编辑",
        "update_article_content：直接修改待审核文章内容，并自动保留历史版本",
        "batch_id，job_id；至少一个 title、subtitle、digest、body；confirmation",
        True,
        "请明确回复“确认保存文章修改”。",
    ),
    "move_paragraph": ToolSpec(
        "审核编辑",
        "move_paragraph：移动待审核文章中的一个正文段落并重新排版",
        "batch_id，job_id；paragraph_number（从 1 开始）及 target_paragraph_number（从 1 开始）或 direction=up/down；也支持对应的 paragraph_index、target_index（从 0 开始）",
        True,
        "请明确回复“确认移动这个段落”。",
    ),
    "delete_paragraph": ToolSpec(
        "审核编辑",
        "delete_paragraph：删除待审核文章中的一个正文段落并重新排版",
        "batch_id，job_id；paragraph_number（从 1 开始）；也支持 paragraph_index（从 0 开始）",
        True,
        "请明确回复“确认删除这个段落”。",
    ),
    "regenerate_paragraph": ToolSpec(
        "审核编辑",
        "regenerate_paragraph：按要求只重新生成一个正文段落",
        "batch_id，job_id；paragraph_number（从 1 开始）或 paragraph_index（从 0 开始）；instruction（必填修改要求），confirmation",
        True,
        "请明确回复“确认重新生成这个段落”。",
    ),
    "rerender_article": ToolSpec(
        "审核编辑",
        "rerender_article：使用公众号当前排版和草稿模板重新渲染文章",
        "batch_id，job_id，confirmation",
        True,
        "请明确回复“确认重新排版”。",
    ),
    "list_article_versions": ToolSpec(
        "审核编辑",
        "list_article_versions：列出一篇文章可恢复的历史版本",
        "batch_id，job_id",
    ),
    "restore_article_version": ToolSpec(
        "审核编辑",
        "restore_article_version：恢复文章历史版本，恢复前会自动保存当前版本",
        "batch_id，job_id；version_id 或上次列表中的 version_number；confirmation",
        True,
        "请明确回复“确认恢复文章历史版本”。",
    ),
    "write_all_to_drafts": ToolSpec(
        "审核编辑",
        "write_all_to_drafts：把已经全部确认的批次并发写入各公众号草稿箱",
        "可选 batch_id；confirmation",
        True,
        "只有明确回复“确认全部写入草稿箱”才能执行。",
    ),
    "get_article_assets": ToolSpec(
        "配图封面",
        "get_article_assets：查看文章当前正文配图、生成警告和封面信息",
        "batch_id，job_id",
    ),
    "regenerate_inline_images": ToolSpec(
        "配图封面",
        "regenerate_inline_images：按公众号当前生图配置重新生成全部论点配图",
        "batch_id，job_id，confirmation",
        True,
        "请明确回复“确认重新生成正文配图”。",
    ),
    "regenerate_inline_image": ToolSpec(
        "配图封面",
        "regenerate_inline_image：按用户要求只重新生成一张指定的正文论点配图，其他图片保持不变",
        "batch_id，job_id，image_index（配图编号，从 1 开始），instruction，confirmation",
        True,
        "请明确回复“确认按要求重新生成这张正文配图”。",
    ),
    "remove_inline_image": ToolSpec(
        "配图封面",
        "remove_inline_image：从待审核文章移除一张正文配图",
        "batch_id，job_id，image_index，confirmation",
        True,
        "请明确回复“确认移除第 N 张正文配图”。",
    ),
    "regenerate_cover": ToolSpec(
        "配图封面",
        "regenerate_cover：根据最终标题、正文主题和核心论点重新生成 AI 封面",
        "batch_id，job_id；可选 instruction；confirmation",
        True,
        "请明确回复“确认重新生成文章封面”。",
    ),
    "list_cover_options": ToolSpec(
        "配图封面",
        "list_cover_options：读取对应公众号素材库中的可选封面",
        "batch_id，job_id；可选 limit、offset",
    ),
    "select_cover": ToolSpec(
        "配图封面",
        "select_cover：选择公众号素材库图片作为文章封面",
        "batch_id，job_id；thumb_media_id/media_id 或上次列表中的 cover_number；confirmation",
        True,
        "请明确回复“确认更换文章封面”。",
    ),
    "configure_account_images": ToolSpec(
        "配图封面",
        "configure_account_images：配置公众号正文论点配图和 AI 封面",
        (
            "account_id 或 account_name；可选 enabled、generate_cover、source_mode"
            "（generate/hybrid/library）、image_model_id、min_count、max_count、min_spacing、"
            "max_spacing、generation_concurrency、prompt_template_id"
        ),
        True,
        "请明确回复“确认修改公众号生图配置”。",
    ),
    "update_account_layout": ToolSpec(
        "排版模板",
        "update_account_layout：修改公众号字号、颜色、行距、段距、缩进等排版字段",
        "account_id 或 account_name，layout_patch（只填写需要变更的字段），confirmation",
        True,
        "请明确回复“确认修改公众号排版”。",
    ),
    "list_draft_templates": ToolSpec(
        "排版模板",
        "list_draft_templates：读取该公众号草稿箱中标题包含“模板”的候选模板",
        "account_id 或 account_name；可选 keyword、placeholder",
    ),
    "select_draft_template": ToolSpec(
        "排版模板",
        "select_draft_template：选择草稿模板并设置要替换的正文占位文字",
        (
            "account_id 或 account_name；template_number，或 selected_media_id 和 "
            "selected_article_index；可选 placeholder"
        ),
        True,
        "请明确回复“确认更换公众号草稿模板”。",
    ),
    "get_recent_hot_topics": ToolSpec(
        "选题来源",
        "get_recent_hot_topics：查询近 7 天热点；带 keyword 时跨多个启用来源搜索",
        "可选 keyword、limit、source_ids、days",
    ),
    "list_topics": ToolSpec(
        "选题来源",
        "list_topics：按来源、日期、关键词、收藏或未使用状态查询选题池",
        "可选 source_ids、days、keyword、favorite_only、unused_only、limit",
    ),
    "update_topic_state": ToolSpec(
        "选题来源",
        "update_topic_state：收藏或取消收藏选题，并标记是否已使用",
        "topic_id 或 topic_number；可选 favorite、used",
    ),
    "list_topic_sources": ToolSpec(
        "选题来源",
        "list_topic_sources：列出热点来源及启用状态",
        "可选 enabled_only",
    ),
    "save_topic_source": ToolSpec(
        "选题来源",
        "save_topic_source：新增或更新 RSS、新闻搜索、自定义 API 等热点来源",
        "name，source_type，config；可选 id、enabled",
    ),
    "delete_topic_source": ToolSpec(
        "选题来源",
        "delete_topic_source：删除一个热点来源",
        "source_id，confirmation",
        True,
        "请明确回复“确认删除热点来源 + 名称或来源 ID”。",
    ),
    "refresh_topic_sources": ToolSpec(
        "选题来源",
        "refresh_topic_sources：刷新全部或指定热点来源",
        "可选 source_ids",
    ),
    "add_manual_topic": ToolSpec(
        "选题来源",
        "add_manual_topic：向选题池添加一条人工选题",
        "title；可选 url、summary、category",
    ),
    "collect_article_link": ToolSpec(
        "关注公众号",
        "collect_article_link：解析一个公开微信文章链接并加入关注文章池，不启动改写",
        "source_url；可选 followed_account_id",
    ),
    "list_followed_accounts": ToolSpec(
        "关注公众号",
        "list_followed_accounts：列出关注公众号及最近同步状态",
        "可选 enabled_only",
    ),
    "import_owned_followed_accounts": ToolSpec(
        "关注公众号",
        "import_owned_followed_accounts：把已管理的自有公众号同步到关注列表",
    ),
    "save_followed_account": ToolSpec(
        "关注公众号",
        "save_followed_account：新增或更新一个关注公众号",
        (
            "name；可选 id、wechat_id、category、tags、fetch_method、sample_url、source_url、"
            "official_account_id、keywords、is_owned、enabled、refresh_hours"
        ),
    ),
    "delete_followed_account": ToolSpec(
        "关注公众号",
        "delete_followed_account：删除一个关注公众号",
        "account_id 或上次列表中的 account_number，confirmation",
        True,
        "请明确回复“确认删除关注公众号 + 名称或 ID”。",
    ),
    "refresh_followed_articles": ToolSpec(
        "关注公众号",
        "refresh_followed_articles：从微信公众号后台刷新一个或全部关注公众号的近期公开文章",
        "可选 account_id、account_name 或 account_number；可选 limit；不填则刷新全部",
    ),
    "load_more_followed_articles": ToolSpec(
        "关注公众号",
        "load_more_followed_articles：在上次结果基础上继续获取同一公众号更早的公开文章",
        "account_id 或 account_name；可选 increment（默认 8）、days",
    ),
    "list_followed_articles": ToolSpec(
        "关注公众号",
        "list_followed_articles：按公众号、日期和状态查询近期公开文章并支持加载更多",
        (
            "可选 account_ids、account_names、account_name、account_number、days、keyword、unread_only、favorite_only、"
            "unrewritten_only、include_ignored、limit、offset"
        ),
    ),
    "update_followed_article": ToolSpec(
        "关注公众号",
        "update_followed_article：标记关注文章已读、收藏、忽略或已改写",
        "article_id 或上次列表中的 article_number；至少一个 is_read、is_favorite、is_ignored、rewritten_batch_id",
    ),
    "get_wechat_backend_status": ToolSpec(
        "关注公众号",
        "get_wechat_backend_status：查看微信公众号后台搜索登录态（不返回 token/cookie）",
    ),
    "list_prompt_templates": ToolSpec(
        "提示词",
        "list_prompt_templates：分别列出文章或图片提示词模板；默认模板由代码维护，不展示正文",
        "purpose（article/image）；可选 enabled_only",
    ),
    "save_prompt_template": ToolSpec(
        "提示词",
        "save_prompt_template：新增或更新文章/图片提示词模板",
        "name，content，purpose（article/image）；可选 template_id、enabled",
    ),
    "delete_prompt_template": ToolSpec(
        "提示词",
        "delete_prompt_template：删除未被公众号使用的自定义提示词模板",
        "template_id，confirmation",
        True,
        "请明确回复“确认删除提示词模板 + 名称或模板 ID”。",
    ),
    "bind_account_prompt_template": ToolSpec(
        "提示词",
        "bind_account_prompt_template：给公众号选择文章或图片提示词模板；空 template_id 恢复默认模板",
        "account_id 或 account_name，purpose（article/image），template_id，confirmation",
        True,
        "请明确回复“确认更换公众号提示词模板”。",
    ),
    "list_creation_plans": ToolSpec(
        "创作方案",
        "list_creation_plans：列出当前可用的创作方案，以及方案包含的文章提示词、图片提示词和 AI 评审方案",
        "可选 enabled_only（默认 true）",
    ),
    "apply_account_creation_plan": ToolSpec(
        "创作方案",
        "apply_account_creation_plan：把一个创作方案完整应用到指定公众号",
        (
            "account_id 或 account_name，plan_id 或 plan_name，confirmation；"
            "会同时更新文章提示词、图片提示词和默认 AI 评审方案"
        ),
        True,
        "请明确回复“确认给公众号应用创作方案”。",
    ),
    "list_models": ToolSpec(
        "模型",
        "list_models：列出文本模型、生图模型和 config/.env 主模型（不返回 API Key）",
        "可选 purpose（text/image/all）、enabled_only",
    ),
    "test_model": ToolSpec(
        "模型",
        "test_model：测试一个文本或生图模型的真实连接",
        "model_id 或 model_name",
    ),
    "generate_model_test_image": ToolSpec(
        "模型",
        "generate_model_test_image：真实调用已配置的生图模型生成测试图并发送到飞书",
        "model_id 或 model_name，confirmation",
        True,
        "真实出图可能产生费用，请明确回复“确认生成模型测试图”。",
    ),
    "set_model_enabled": ToolSpec(
        "模型",
        "set_model_enabled：启用或停用一个已添加的模型",
        "model_id 或 model_name，enabled",
    ),
    "delete_model": ToolSpec(
        "模型",
        "delete_model：删除一个未被公众号使用的自定义模型",
        "model_id 或 model_name，confirmation",
        True,
        "请明确回复“确认删除模型 + 名称”。",
    ),
    "get_feishu_runtime_status": ToolSpec(
        "数据运维",
        "get_feishu_runtime_status：查看飞书机器人运行状态、最近消息、回复和失败原因",
    ),
    "get_operational_overview": ToolSpec(
        "数据运维",
        "get_operational_overview：查看批次、文章、待审核、草稿、失败和处理中数量概览",
        "可选 date（YYYY-MM-DD）",
    ),
}


ALLOWED_TOOLS = frozenset(TOOL_SPECS)


def render_tool_catalog() -> str:
    categories: dict[str, list[str]] = {}
    for name, spec in TOOL_SPECS.items():
        line = spec.prompt_line().replace(f"- {spec.summary}", f"- {spec.summary}")
        categories.setdefault(spec.category, []).append(line)
    return "\n\n".join(
        f"【{category}】\n" + "\n".join(lines)
        for category, lines in categories.items()
    )


def confirmation_hint(tool: str) -> str:
    spec = TOOL_SPECS.get(tool)
    return spec.confirmation_hint if spec else ""


def requires_confirmation(tool: str) -> bool:
    spec = TOOL_SPECS.get(tool)
    return bool(spec and spec.requires_confirmation)
