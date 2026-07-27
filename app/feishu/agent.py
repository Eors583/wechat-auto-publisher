from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.model_registry import build_text_client
from app.db import Database
from app.feishu.tool_catalog import (
    ALLOWED_TOOLS,
    confirmation_hint,
    render_tool_catalog,
    requires_confirmation,
)


@dataclass(slots=True)
class AgentPlan:
    intent: str
    analysis_summary: str
    steps: list[str] = field(default_factory=list)
    tool: str = "chat"
    arguments: dict[str, Any] = field(default_factory=dict)
    reply: str = ""

    @property
    def plan_text(self) -> str:
        steps = "；".join(self.steps[:3])
        parts = [f"已识别意图：{self.intent or self.tool}"]
        if self.analysis_summary:
            parts.append(f"判断：{self.analysis_summary}")
        if steps:
            parts.append(f"执行流程：{steps}")
        return "\n".join(parts)


class FeishuToolAgent:
    """Use a selected LLM to plan one whitelisted application tool call."""

    def __init__(
        self,
        db: Database,
        config: dict[str, Any],
        model_id: str,
    ) -> None:
        self.model_id = model_id
        self.client = build_text_client(db, config, model_id)

    def plan(
        self,
        user_text: str,
        *,
        accounts: list[dict[str, Any]],
        current_batch: dict[str, Any] | None,
        recent_hot_topics: list[dict[str, Any]] | None = None,
        review_state: dict[str, Any] | None = None,
    ) -> AgentPlan:
        account_context = [
            {"id": item["id"], "name": item["name"], "model": item.get("model_name")}
            for item in accounts
        ]
        batch_context: dict[str, Any] | None = None
        if current_batch:
            batch_context = {
                "id": current_batch.get("id"),
                "status": current_batch.get("status"),
                "jobs": [
                    {
                        "id": job.get("id"),
                        "account_name": job.get("account_name"),
                        "status": job.get("status"),
                    }
                    for job in current_batch.get("jobs") or []
                ],
            }
        prompt = f"""请先分析用户意图和所需流程，再为公众号内容系统选择至多一个工具。
一次回复只允许调用一个工具；如果任务需要多步，先调用当前最必要的一步，等结果返回后再决定下一步。

可用工具：
{render_tool_catalog()}

安全规则：
1. 不得编造公众号 ID、任务 ID、批次 ID、模型 ID、模板 ID、素材 ID、链接或工具。
2. 当修改操作缺少 ID 时，应先选择对应的 list 工具查找真实 ID，不得猜测；每次仍然只能选一个工具。
3. 用户没有指定公众号时，create_rewrite_batch 的公众号参数留空，由系统使用默认公众号。
4. create_rewrite_batch 必须匹配来源模式：link/source_url、text/raw_content、references/reference_urls、topic/topic。
5. “可以、没问题、差不多”不等于确认写入草稿箱，也不等于确认删除、归档或保存密钥。
6. 删除、归档、移除图片、写入草稿箱以及保存 AppSecret/API Key，必须由用户在当前消息明确说“确认”及具体动作；否则使用 chat 给出确认话术，不得调用工具。
7. 任何回复、分析摘要和步骤都不得复述 API Key、AppSecret、Cookie、Token；查询工具也不得要求返回密钥明文。
8. 重新生成段落、正文配图、封面，重试失败任务和复制批次可能再次调用模型并产生费用；只有用户明确要求相应动作时调用。
8.1 AI 评审、生成 AI 候选修改稿和智能修改原文都会调用公众号绑定模型；只能在用户明确确认后调用。评审建议和候选修改稿必须使用会话中展示的编号，不得编造内部 ID。
8.2 用户看完评审结论后，只需选择接受哪些可见建议编号：选择“使用原文”时用 chat 告知已保留原文；选择“智能修改原文”时调用 smart_rewrite_from_editorial_review，一步生成并应用，不要要求用户另填修改意见。
8.3 AI 评审只关注标题、开头、预计完读率、点赞意愿和转发动机，并给少量整体建议；智能修改按这些整体目标优化，不要引导用户逐段挑字眼或填写逐段润色要求。
9. 不输出详细思维链，只给简短意图说明和可核验步骤。
10. 只输出一个 JSON 对象，不要 Markdown；arguments 只填写目标工具支持的字段。
11. 用户意图能由工具完成且参数足够时必须选择工具，不要退回 chat。

意图映射示例：
- “有哪些公众号/能发到哪里” => list_accounts
- “生成前检查A和B能不能用” => preflight_accounts，填写 account_names
- “查询7日热点/最近一周有什么热点/给我热点选题” => get_recent_hot_topics
- “搜索AI热点/查一下组织变革相关热点” => get_recent_hot_topics，并填写 keyword
- “有哪些热点来源” => list_topic_sources
- “刷新36氪和微博” => refresh_topic_sources
- “收藏/收集这个链接/加入关注文章池” => collect_article_link，并填写 source_url
- “把这个链接改写到A和B” => create_rewrite_batch，并填写 source_mode=link、source_url、account_names
- “参考这三个链接写一篇，必须保留这些事实” => create_rewrite_batch，并填写 source_mode=references、reference_urls、required_facts
- “只按这个话题原创一篇” => create_rewrite_batch，并填写 source_mode=topic、topic
- “用第3条给A和B改写” => create_rewrite_batch，并填写 hot_topic_number=3、account_names
- “用关注文章列表第2篇给A和B改写” => create_rewrite_batch，并填写 followed_article_number=2、account_names
- “现在做到哪了/进度如何” => get_batch_status
- “列出最近批次” => list_batches
- “只重试这个批次失败的公众号” => retry_failed_batch
- “复制批次重新生成” => copy_batch；“确认归档批次B123” => archive_batch，并填写 confirmation=true
- “看看A账号生成的文章/查看任务12正文” => get_article_result
- “预览下一篇/看看下一个公众号” => get_article_result；任务号留空，由系统使用当前审核项
- “有哪些 AI 评审方案/评审团方案” => list_editorial_review_profiles
- “查看A公众号默认评审团” => get_account_editorial_review_default
- “确认把A公众号默认评审团换成专业深度型” => set_account_editorial_review_default，填写 account_name、profile_name
- “确认开始 AI 评审任务12/用爆款传播型评审这篇” => run_editorial_review，填写 job_id、可选 profile_name；系统重点评估标题、开头、预计完读率、点赞意愿和转发动机
- “查看当前 AI 评审建议” => get_editorial_review；评审号留空，使用当前文章最近一次评审
- “使用原文/保留原文，不采用评审建议” => chat，告知原文保持不变并仍处于待确认状态
- “确认智能修改原文，接受第1、3条建议” => smart_rewrite_from_editorial_review，填写 issue_numbers=[1,3]；只使用用户在评审结论中看到并明确接受的编号，不得猜 issue_id，也不要填写 instruction；系统会按整体传播目标优化，不做逐段润色
- “确认按 AI 评审建议修改第1、3条” => generate_editorial_rewrite_candidate，填写 issue_numbers=[1,3]；只能使用用户看到的建议编号，不得猜 issue_id
- “确认应用 AI 修改稿1” => apply_editorial_review_application，填写 application_number=1；不得猜 application_id
- “确认把评审建议2标记为已核实，依据是已对照原始财报” => resolve_editorial_review_issue，填写 issue_number=2、resolution=已核实、note=已对照原始财报；已核实或接受风险时 note 必填
- “任务12用标题2、副标题1” => select_article_title；只选择标题，不代表确认文章
- “标题2、副标题1” => select_article_title；job_id 留空，由系统使用当前审核项；之后仍需用户明确“确认此文章”
- “把任务12摘要改成……” => update_article_content，填写 job_id、digest
- “任务12第3段换一种更克制的表达” => regenerate_paragraph，paragraph_number=3、instruction
- “查看任务12的历史版本” => list_article_versions
- “恢复任务12的版本5” => restore_article_version
- “看看任务12配图和封面” => get_article_assets
- “重做任务12全部论点配图” => regenerate_inline_images
- “任务12第2张图改成供应链仓库现场” => regenerate_inline_image，填写 image_index=2、instruction
- “删除任务12第2张正文配图” => remove_inline_image，填写 image_index=2
- “任务12封面改成科技感办公场景，不要文字” => regenerate_cover，填写 instruction
- “列出公众号素材封面” => list_cover_options；“选择封面 2” => select_cover，并填写 cover_number=2
- “列出我关注的公众号” => list_followed_accounts
- “把自有公众号同步到关注列表” => import_owned_followed_accounts
- “查看蓝血研究最近30天文章” => list_followed_articles
- “刷新蓝血研究近期文章” => refresh_followed_articles
- “有哪些文章提示词” => list_prompt_templates；“把A的文章提示词切到模板T” => bind_account_prompt_template
- “有哪些创作方案” => list_creation_plans
- “确认给A公众号应用专业深度方案” => apply_account_creation_plan，填写 account_name、plan_name
- “有哪些生图模型” => list_models；“测试模型M” => test_model；“用生图模型M出一张测试图” => generate_model_test_image
- “飞书机器人运行正常吗” => get_feishu_runtime_status；“今天有多少待审核和失败文章” => get_operational_overview
- “查看A公众号配置” => get_account_config；“把A切到模型M” => set_account_model
- “读取A草稿箱模板” => list_draft_templates；“A选择模板 2并用‘正文’替换” => select_draft_template，并填写 template_number=2、placeholder=正文
- “可以了/没问题” => chat，提醒必须明确确认，绝不能写入
- “确认全部写入草稿箱” => write_all_to_drafts，并填写 confirmation=true
- “不要写了/停止当前改写” => cancel_rewrite_batch
- “删除热点来源/删除提示词模板/删除关注公众号”但没有“确认” => chat，返回对应的明确确认话术
- “确认保存模型密钥，名称…API Key…” => save_model，并填写 confirmation=true，reply 和步骤不得回显密钥

输出格式：
{{"intent":"简短意图","analysis_summary":"判断依据摘要","steps":["步骤1","步骤2"],"tool":"工具名","arguments":{{}},"reply":"无需工具或需要确认时的中文回复"}}

可用公众号：{json.dumps(account_context, ensure_ascii=False)}
最近一次热点列表：{json.dumps(recent_hot_topics or [], ensure_ascii=False)}
当前顺序审核状态：{json.dumps(review_state or {}, ensure_ascii=False)}
当前会话批次：{json.dumps(batch_context, ensure_ascii=False)}
用户消息：{user_text}
"""
        raw = self._complete(prompt)
        value = _parse_json_object(raw)
        tool = str(value.get("tool") or "chat")
        if tool not in ALLOWED_TOOLS:
            raise ValueError(f"智能体选择了未授权工具：{tool}")
        arguments = value.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if requires_confirmation(tool):
            # The executor owns the deterministic confirmation boundary and
            # pending-action cache. The planner only records whether this
            # message already contained an explicit confirmation.
            arguments["confirmation"] = _has_explicit_confirmation(user_text, tool)
        steps = value.get("steps")
        if not isinstance(steps, list):
            steps = []
        return AgentPlan(
            intent=_redact_secret_values(
                str(value.get("intent") or tool).strip(), arguments
            ),
            analysis_summary=_redact_secret_values(
                str(value.get("analysis_summary") or "").strip(), arguments
            ),
            steps=[
                _redact_secret_values(str(item).strip(), arguments)
                for item in steps
                if str(item).strip()
            ][:3],
            tool=tool,
            arguments=arguments,
            reply=_redact_secret_values(
                str(value.get("reply") or "").strip(), arguments
            ),
        )

    def _complete(self, prompt: str) -> str:
        try:
            return str(
                self.client.complete(
                    prompt,
                    system="你是谨慎的工具调用规划器，只能输出合法 JSON。",
                    max_tokens=1200,
                    temperature=0.1,
                    max_attempts=2,
                )
            )
        except TypeError:
            return str(self.client.complete(prompt))


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("智能体没有返回合法的工具计划")
        value = json.loads(raw[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("智能体工具计划必须是 JSON 对象")
    return value


_CONFIRMATION_ACTIONS: dict[str, tuple[str, ...]] = {
    "write_all_to_drafts": ("写入草稿箱",),
    "archive_batch": ("归档",),
    "delete_topic_source": ("删除热点来源", "删除来源"),
    "delete_followed_account": ("删除关注公众号",),
    "delete_prompt_template": ("删除提示词模板", "删除模板"),
    "remove_inline_image": ("移除正文配图", "删除正文配图", "移除图片", "删除图片"),
    "regenerate_inline_image": ("重新生成这张正文配图", "重做这张配图", "修改这张配图"),
    "save_model": ("保存模型密钥", "保存模型", "更新模型密钥", "更新模型"),
    "save_official_account": (
        "保存公众号密钥",
        "保存公众号",
        "更新公众号密钥",
        "更新公众号",
    ),
    "delete_official_account": ("删除自有公众号", "删除公众号"),
    "save_wechat_backend_login": ("保存微信公众号后台登录态", "保存后台登录态"),
    "clear_wechat_backend_login": ("清除微信公众号后台登录态", "清除后台登录态"),
    "delete_model": ("删除模型",),
    "save_editorial_review_profile": ("保存 AI 评审方案", "保存AI评审方案"),
    "delete_editorial_review_profile": ("删除 AI 评审方案", "删除AI评审方案"),
    "set_account_editorial_review_default": (
        "更换公众号默认 AI 评审方案",
        "更换公众号默认AI评审方案",
    ),
    "apply_account_creation_plan": (
        "给公众号应用创作方案",
        "更换公众号创作方案",
        "应用创作方案",
    ),
    "run_editorial_review": ("开始 AI 评审", "开始AI评审"),
    "generate_editorial_rewrite_candidate": (
        "按 AI 评审建议生成修改稿",
        "按AI评审建议生成修改稿",
    ),
    "smart_rewrite_from_editorial_review": (
        "智能修改原文",
        "按 AI 评审建议智能修改原文",
        "按AI评审建议智能修改原文",
    ),
    "apply_editorial_review_application": ("应用 AI 修改稿", "应用AI修改稿"),
    "resolve_editorial_review_issue": (
        "更新 AI 评审核实结果",
        "更新AI评审核实结果",
        "标记为已核实",
        "接受风险",
    ),
}


def _has_explicit_confirmation(user_text: str, tool: str) -> bool:
    normalized = re.sub(r"\s+", "", str(user_text or "")).casefold()
    if "确认" not in normalized:
        return False
    return any(
        action.casefold() in normalized
        for action in _CONFIRMATION_ACTIONS.get(tool, ())
    )


def _redact_secret_values(text: str, arguments: dict[str, Any]) -> str:
    result = str(text or "")
    for key in ("api_key", "app_secret", "token", "cookie"):
        secret = str(arguments.get(key) or "").strip()
        if len(secret) >= 4:
            result = result.replace(secret, "[已隐藏]")
    return result
