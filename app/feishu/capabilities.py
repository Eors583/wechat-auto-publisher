from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FeishuSupportStatus(StrEnum):
    """Declared Feishu parity for one public application-service operation."""

    SUPPORTED = "supported"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class FeishuCapability:
    """Map a user-facing service operation to its Feishu tool contract.

    Public service methods covered by an alignment table must have one entry.
    This makes adding a new desktop/service operation an explicit product
    decision:

    * ``SUPPORTED``: Feishu exposes the operation through the listed tools.
    * ``PARTIAL``: Feishu exposes only the documented subset.
    * ``NOT_APPLICABLE``: the method is infrastructure-only and the reason is
      recorded.

    The alignment tests fail when a new public service method is introduced
    without declaring its Feishu support status.
    """

    service_method: str
    feature: str
    status: FeishuSupportStatus
    tools: tuple[str, ...] = ()
    note: str = ""


BATCH_SERVICE_CAPABILITIES: tuple[FeishuCapability, ...] = (
    FeishuCapability(
        "list_accounts",
        "查看可用公众号",
        FeishuSupportStatus.SUPPORTED,
        ("list_accounts",),
    ),
    FeishuCapability(
        "add_listener",
        "进程内批次事件监听",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="进程内基础设施；飞书通过进度消息和状态查询获取进展。",
    ),
    FeishuCapability(
        "preflight",
        "生成前环境检查",
        FeishuSupportStatus.SUPPORTED,
        ("preflight_accounts",),
    ),
    FeishuCapability(
        "get_editorial_review_options",
        "查看 AI 评审角色、风格、严格程度和改写方式",
        FeishuSupportStatus.SUPPORTED,
        ("list_editorial_review_profiles",),
    ),
    FeishuCapability(
        "list_editorial_review_profiles",
        "列出内置和自定义 AI 评审方案",
        FeishuSupportStatus.SUPPORTED,
        ("list_editorial_review_profiles",),
    ),
    FeishuCapability(
        "save_editorial_review_profile",
        "保存自定义 AI 评审方案",
        FeishuSupportStatus.SUPPORTED,
        ("save_editorial_review_profile",),
    ),
    FeishuCapability(
        "delete_editorial_review_profile",
        "删除自定义 AI 评审方案",
        FeishuSupportStatus.SUPPORTED,
        ("delete_editorial_review_profile",),
    ),
    FeishuCapability(
        "get_account_editorial_review_default",
        "查看公众号默认 AI 评审方案",
        FeishuSupportStatus.SUPPORTED,
        ("get_account_editorial_review_default",),
    ),
    FeishuCapability(
        "set_account_editorial_review_default",
        "设置公众号默认 AI 评审方案",
        FeishuSupportStatus.SUPPORTED,
        ("set_account_editorial_review_default",),
    ),
    FeishuCapability(
        "run_editorial_review",
        "手动运行 AI 评审团",
        FeishuSupportStatus.SUPPORTED,
        ("run_editorial_review",),
    ),
    FeishuCapability(
        "list_editorial_reviews",
        "查询文章 AI 评审历史",
        FeishuSupportStatus.SUPPORTED,
        ("get_editorial_review",),
    ),
    FeishuCapability(
        "get_editorial_review",
        "查看 AI 评审建议和阻断项",
        FeishuSupportStatus.SUPPORTED,
        ("get_editorial_review",),
    ),
    FeishuCapability(
        "generate_editorial_rewrite_candidate",
        "按勾选建议生成 AI 候选修改稿",
        FeishuSupportStatus.SUPPORTED,
        (
            "generate_editorial_rewrite_candidate",
            "smart_rewrite_from_editorial_review",
        ),
    ),
    FeishuCapability(
        "list_editorial_review_applications",
        "查询 AI 候选修改稿",
        FeishuSupportStatus.SUPPORTED,
        ("get_editorial_review",),
    ),
    FeishuCapability(
        "get_editorial_review_application",
        "查看一个 AI 候选修改稿",
        FeishuSupportStatus.SUPPORTED,
        ("get_editorial_review",),
    ),
    FeishuCapability(
        "apply_editorial_review_application",
        "应用已确认的 AI 候选修改稿",
        FeishuSupportStatus.SUPPORTED,
        (
            "apply_editorial_review_application",
            "smart_rewrite_from_editorial_review",
        ),
    ),
    FeishuCapability(
        "resolve_editorial_review_issue",
        "人工核实或接受事实合规风险",
        FeishuSupportStatus.SUPPORTED,
        ("resolve_editorial_review_issue",),
    ),
    FeishuCapability(
        "create_batch",
        "多公众号并发生成文章",
        FeishuSupportStatus.SUPPORTED,
        ("create_rewrite_batch",),
    ),
    FeishuCapability(
        "get_batch",
        "查看批次、文章和素材详情",
        FeishuSupportStatus.SUPPORTED,
        ("get_batch_status", "get_article_result", "get_article_assets"),
    ),
    FeishuCapability(
        "list_batches",
        "查询任务批次",
        FeishuSupportStatus.SUPPORTED,
        ("list_batches",),
    ),
    FeishuCapability(
        "select_job",
        "选择文章标题和副标题",
        FeishuSupportStatus.SUPPORTED,
        ("select_article_title",),
    ),
    FeishuCapability(
        "mark_job_viewed",
        "标记文章已查看",
        FeishuSupportStatus.SUPPORTED,
        ("get_article_result",),
        "飞书读取文章预览时自动标记为已查看。",
    ),
    FeishuCapability(
        "confirm_job",
        "确认文章",
        FeishuSupportStatus.SUPPORTED,
        ("confirm_article",),
    ),
    FeishuCapability(
        "request_job_changes",
        "标记文章需要修改",
        FeishuSupportStatus.SUPPORTED,
        ("request_article_changes",),
    ),
    FeishuCapability(
        "update_job_content",
        "编辑标题、摘要和正文",
        FeishuSupportStatus.SUPPORTED,
        ("update_article_content",),
    ),
    FeishuCapability(
        "move_paragraph",
        "移动正文段落",
        FeishuSupportStatus.SUPPORTED,
        ("move_paragraph",),
    ),
    FeishuCapability(
        "delete_paragraph",
        "删除正文段落",
        FeishuSupportStatus.SUPPORTED,
        ("delete_paragraph",),
    ),
    FeishuCapability(
        "list_job_versions",
        "查看文章历史版本",
        FeishuSupportStatus.SUPPORTED,
        ("list_article_versions",),
    ),
    FeishuCapability(
        "restore_job_version",
        "恢复文章历史版本",
        FeishuSupportStatus.SUPPORTED,
        ("restore_article_version",),
    ),
    FeishuCapability(
        "rerender_job",
        "重新套用排版和模板",
        FeishuSupportStatus.SUPPORTED,
        ("rerender_article",),
    ),
    FeishuCapability(
        "regenerate_inline_images",
        "重新生成全部正文论点配图",
        FeishuSupportStatus.SUPPORTED,
        ("regenerate_inline_images",),
    ),
    FeishuCapability(
        "regenerate_inline_image",
        "按用户要求只重新生成一张正文配图",
        FeishuSupportStatus.SUPPORTED,
        ("get_article_assets", "regenerate_inline_image"),
    ),
    FeishuCapability(
        "remove_inline_image",
        "移除一张正文配图",
        FeishuSupportStatus.SUPPORTED,
        ("get_article_assets", "remove_inline_image"),
    ),
    FeishuCapability(
        "regenerate_cover",
        "按用户要求重新生成文章封面",
        FeishuSupportStatus.SUPPORTED,
        ("get_article_assets", "regenerate_cover"),
    ),
    FeishuCapability(
        "list_cover_options",
        "查看公众号素材封面",
        FeishuSupportStatus.SUPPORTED,
        ("list_cover_options",),
    ),
    FeishuCapability(
        "select_job_cover",
        "选择公众号素材封面",
        FeishuSupportStatus.SUPPORTED,
        ("select_cover",),
    ),
    FeishuCapability(
        "regenerate_paragraph",
        "按用户要求只二次改写一个正文段落",
        FeishuSupportStatus.SUPPORTED,
        ("get_article_result", "regenerate_paragraph"),
    ),
    FeishuCapability(
        "inject_batch",
        "全部确认后写入公众号草稿箱",
        FeishuSupportStatus.SUPPORTED,
        ("write_all_to_drafts",),
    ),
    FeishuCapability(
        "retry_failed",
        "仅重试批次中的失败任务",
        FeishuSupportStatus.SUPPORTED,
        ("retry_failed_batch",),
    ),
    FeishuCapability(
        "copy_batch",
        "复制批次重新生成",
        FeishuSupportStatus.SUPPORTED,
        ("copy_batch",),
    ),
    FeishuCapability(
        "archive_batch",
        "归档或取消归档批次",
        FeishuSupportStatus.SUPPORTED,
        ("archive_batch",),
    ),
    FeishuCapability(
        "cancel_batch",
        "请求停止生成批次",
        FeishuSupportStatus.SUPPORTED,
        ("cancel_rewrite_batch",),
    ),
)

CREATION_PLAN_SERVICE_CAPABILITIES: tuple[FeishuCapability, ...] = (
    FeishuCapability(
        "list",
        "列出创作方案",
        FeishuSupportStatus.SUPPORTED,
        ("list_creation_plans",),
    ),
    FeishuCapability(
        "list_plans",
        "列出创作方案（可读别名）",
        FeishuSupportStatus.SUPPORTED,
        ("list_creation_plans",),
    ),
    FeishuCapability(
        "get",
        "按 ID 查看单个创作方案",
        FeishuSupportStatus.PARTIAL,
        note="飞书当前通过方案列表展示方案 ID 与完整组合，不提供单独查询工具。",
    ),
    FeishuCapability(
        "get_plan",
        "按 ID 查看单个创作方案（可读别名）",
        FeishuSupportStatus.PARTIAL,
        note="飞书当前通过方案列表展示方案 ID 与完整组合，不提供单独查询工具。",
    ),
    FeishuCapability(
        "save",
        "保存创作方案",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="首版飞书仅负责选择和应用方案；方案内容仍在桌面设置中维护。",
    ),
    FeishuCapability(
        "save_plan",
        "保存创作方案（可读别名）",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="首版飞书仅负责选择和应用方案；方案内容仍在桌面设置中维护。",
    ),
    FeishuCapability(
        "delete",
        "删除创作方案",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="首版飞书不开放方案删除，避免对其他公众号配置造成误操作。",
    ),
    FeishuCapability(
        "delete_plan",
        "删除创作方案（可读别名）",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="首版飞书不开放方案删除，避免对其他公众号配置造成误操作。",
    ),
    FeishuCapability(
        "list_account_template_bindings",
        "查看创作方案的公众号专属草稿模板绑定",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="草稿模板素材受公众号归属限制，首版仍在桌面模板管理中操作。",
    ),
    FeishuCapability(
        "save_account_template_binding",
        "保存创作方案的公众号专属草稿模板绑定",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="草稿模板素材受公众号归属限制，首版仍在桌面模板管理中操作。",
    ),
    FeishuCapability(
        "delete_account_template_binding",
        "删除创作方案的公众号专属草稿模板绑定",
        FeishuSupportStatus.NOT_APPLICABLE,
        note="草稿模板素材受公众号归属限制，首版仍在桌面模板管理中操作。",
    ),
    FeishuCapability(
        "get_account_default",
        "查看公众号默认创作方案",
        FeishuSupportStatus.PARTIAL,
        note="应用方案后会返回当前绑定结果，暂未提供独立查询入口。",
    ),
    FeishuCapability(
        "apply_to_account",
        "把创作方案应用到指定公众号",
        FeishuSupportStatus.SUPPORTED,
        ("apply_account_creation_plan",),
    ),
)


CAPABILITY_BY_SERVICE_METHOD = {
    item.service_method: item for item in BATCH_SERVICE_CAPABILITIES
}

CREATION_PLAN_CAPABILITY_BY_SERVICE_METHOD = {
    item.service_method: item for item in CREATION_PLAN_SERVICE_CAPABILITIES
}
