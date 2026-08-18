from __future__ import annotations

import json
import re
import threading

import pytest

from app.config import load_config
from app.editorial_review import normalize_review_config, review_options
from app.services.batches import BatchService
from app.services.editorial_reviews import (
    EditorialReviewConflict,
    _issue_can_auto_apply,
    build_review_prompt,
    build_rewrite_prompt,
    merge_review_config,
    normalize_review_result,
    normalize_rewrite_candidate,
    paragraph_rewrite_schema,
    review_result_schema,
    rewrite_candidate_schema,
)


def test_review_prompt_only_claims_online_verification_when_enabled() -> None:
    config = normalize_review_config({})
    job = {
        "selected_title": "测试标题",
        "selected_subtitle": "",
        "digest": "测试摘要",
        "body": "正文包含一项需要核实的公开事实。",
        "raw_content": "原始资料",
        "required_facts": "",
    }

    offline = build_review_prompt(
        job=job,
        config=config,
        account_name="测试公众号",
    )
    config["online_fact_verification"] = True
    online = build_review_prompt(
        job=job,
        config=config,
        account_name="测试公众号",
    )

    assert "当前模型不具备受控联网核实能力" in offline
    assert "不得声称查询了互联网" in offline
    assert "事实问题必须先使用联网搜索核实" in online
    assert "evidence_sources 只能填写本次实际打开核对过的网页" in online


def test_online_fact_check_becomes_selectable_only_with_a_safe_action() -> None:
    config = normalize_review_config({})
    config["online_fact_verification"] = True
    payload = _review_payload_with_issues(
        [
            {
                "role_id": "fact_checker",
                "category": "事实",
                "severity": "high",
                "location": "正文整体",
                "excerpt": "项目于2025年完成",
                "problem": "公开资料显示年份可能不准确",
                "suggestion": "按官方公告改为2026年",
                "evidence_status": "conflict",
                "verification_summary": "官方公告与原文年份冲突。",
                "recommended_action": "correct_from_sources",
                "evidence_sources": [
                    {
                        "title": "项目官方公告",
                        "url": "https://example.gov.cn/notice",
                        "publisher": "主管部门",
                        "published_at": "2026-01-02",
                        "summary": "公告确认项目于2026年完成。",
                    },
                    {
                        "title": "行业主管机构公告",
                        "url": "https://authority.example.org/project",
                        "publisher": "行业主管机构",
                        "published_at": "2026-01-03",
                        "summary": "公告同样确认项目于2026年完成。",
                    },
                    {
                        "title": "无效来源",
                        "url": "javascript:alert(1)",
                        "publisher": "",
                        "published_at": "",
                        "summary": "",
                    },
                    {
                        "title": "内网来源",
                        "url": "http://127.0.0.1/private",
                        "publisher": "",
                        "published_at": "",
                        "summary": "",
                    },
                ],
                "can_auto_apply": False,
                "blocks_draft": True,
            }
        ]
    )

    issue = normalize_review_result(payload, config=config)["issues"][0]

    assert issue["verification_mode"] == "online"
    assert issue["recommended_action"] == "correct_from_sources"
    assert issue["can_auto_apply"] is True
    assert issue["blocks_draft"] is False
    assert [source["url"] for source in issue["evidence_sources"]] == [
        "https://example.gov.cn/notice",
        "https://authority.example.org/project",
    ]
    assert _issue_can_auto_apply(issue) is True


def test_online_correction_without_sources_falls_back_to_safe_rewrite() -> None:
    config = normalize_review_config({})
    config["online_fact_verification"] = True
    payload = _review_payload_with_issues(
        [
            {
                "role_id": "fact_checker",
                "category": "事实",
                "severity": "high",
                "location": "正文整体",
                "problem": "年份可能错误",
                "suggestion": "改成另一个年份",
                "evidence_status": "unverifiable",
                "verification_summary": "没有找到可靠来源。",
                "recommended_action": "correct_from_sources",
                "evidence_sources": [],
                "can_auto_apply": True,
                "blocks_draft": False,
            }
        ]
    )

    issue = normalize_review_result(payload, config=config)["issues"][0]

    assert issue["recommended_action"] == "soften_claim"
    assert issue["can_auto_apply"] is True
    assert issue["blocks_draft"] is False
    assert "删除无法由公开可靠来源支持的具体事实" in issue["suggestion"]
    assert _issue_can_auto_apply(issue) is True


class _QueuedClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected AI call")
        value = self.responses.pop(0)
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)


def _ready_service(tmp_path) -> tuple[BatchService, str, int]:
    config = {**load_config(), "_db_path": str(tmp_path / "editorial.db")}
    service = BatchService(config)
    batch_id = "batch-editorial"
    service.db.create_batch(
        batch_id,
        topic="管理测试",
        raw_content="原始资料确认公司在2026年完成项目，收入增长32%。",
    )
    job_id = service.db.create_job(
        topic="管理测试",
        raw_content="原始资料确认公司在2026年完成项目，收入增长32%。",
        source="test",
        meta={
            "official_account_id": "account-a",
            "official_account_name": "公众号A",
        },
    )
    service.db.update_job(
        job_id,
        status="ready_for_review",
        step="inject",
        selected_title="企业经营的新答案",
        selected_subtitle="从项目实践看增长",
        digest="一篇管理文章",
        body="第一段介绍背景。\n\n第二段称公司在2025年完成项目，收入增长32%。\n\n第三段给出行动建议。",
    )
    service.db.attach_batch_job(batch_id, job_id, "account-a", "公众号A")
    service.db.update_batch(batch_id, status="ready_for_review")
    return service, batch_id, job_id


def _review_payload() -> dict:
    return {
        "overall_score": 72,
        "summary": "结构清楚，但有一处年份冲突。",
        "strengths": ["结论明确"],
        "dimensions": [
            {"id": "structure", "name": "结构", "score": 80, "summary": "层次清楚"}
        ],
        "issues": [
            {
                "role_id": "chief_editor",
                "category": "结构",
                "severity": "medium",
                "location": "第1段",
                "excerpt": "第一段介绍背景",
                "problem": "铺垫略长",
                "suggestion": "压缩铺垫，提前给出结论",
                "can_auto_apply": True,
            },
            {
                "role_id": "fact_checker",
                "category": "时间",
                "severity": "high",
                "location": "第2段",
                "excerpt": "2025年完成项目",
                "problem": "与原始资料的2026年冲突",
                "suggestion": "请人工核对真实年份",
                "evidence_status": "conflict",
                "can_auto_apply": True,
                "blocks_draft": True,
            },
        ],
        "conclusion": "核实年份后可发布。",
    }


def _review_payload_with_issues(issues: list[dict]) -> dict:
    payload = _review_payload()
    payload["issues"] = issues
    payload["summary"] = "完成本轮评审。"
    payload["conclusion"] = "请按风险状态处理后再发布。"
    return payload


def _patch_text_model(monkeypatch, client: _QueuedClient) -> None:
    monkeypatch.setattr(
        "app.services.editorial_reviews.apply_account_selection",
        lambda *_args, **_kwargs: (
            {"ai": {"rewrite_prompt": "公众号A品牌规则：专业克制"}},
            {"model_id": "model-a", "name": "公众号A"},
        ),
    )
    monkeypatch.setattr(
        "app.services.editorial_reviews.build_text_client",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        "app.services.batches.apply_account_selection",
        lambda *_args, **_kwargs: ({}, {"model_id": "model-a", "name": "公众号A"}),
    )


def test_catalog_has_first_version_scope_and_hard_policy() -> None:
    options = review_options()
    assert len(options["schemes"]) == 5
    assert len(options["roles"]) == 7
    assert len(options["styles"]) == 8
    assert "engagement_optimization" in {
        str(item["id"]) for item in options["rewrite_modes"]
    }
    config = normalize_review_config(
        {
            "role_ids": ["fact_checker", "compliance_expert", "chief_editor"],
            "permissions": {
                "fact_advisory_only": False,
                "compliance_advisory_only": False,
                "can_block_draft": False,
            },
        }
    )
    assert config["permissions"]["fact_advisory_only"] is True
    assert config["permissions"]["compliance_advisory_only"] is True
    assert config["permissions"]["can_block_draft"] is True
    restricted = normalize_review_config(
        {
            "role_ids": ["chief_editor"],
            "permissions": {"allow_rewrite": False},
        }
    )
    merged = merge_review_config(
        restricted,
        {"permissions": {"default_scope": "title"}},
    )
    assert merged["permissions"]["allow_rewrite"] is False


def test_manual_review_uses_bound_model_and_protects_fact_issues(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient([_review_payload()])
    _patch_text_model(monkeypatch, client)

    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )

    assert review["status"] == "completed"
    assert review["blocking_count"] == 1
    assert review["model_id"] == "model-a"
    assert service.get_batch(batch_id)["jobs"][0]["review_status"] == "viewed"
    fact_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "fact_checker"
    )
    assert fact_issue["can_auto_apply"] is False
    assert fact_issue["blocks_draft"] is True
    assert "只做评审，不重写文章" in client.prompts[0]
    assert "原始资料确认公司在2026年" in client.prompts[0]
    assert "公众号A品牌规则：专业克制" in client.prompts[0]


def test_candidate_only_uses_selected_safe_issue_and_keeps_original(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    candidate_payload = {
        "title": "企业经营的新答案",
        "subtitle": "从项目实践看增长",
        "digest": "一篇更聚焦的管理文章",
        "body": "第一段直接给出结论。\n\n第二段称公司在2025年完成项目，收入增长32%。\n\n第三段给出行动建议。",
        "change_summary": "压缩第一段",
    }
    client = _QueuedClient([_review_payload(), candidate_payload])
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )
    safe_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "chief_editor"
    )
    fact_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "fact_checker"
    )

    with pytest.raises(ValueError, match="只能人工处理"):
        service.generate_editorial_rewrite_candidate(
            batch_id,
            job_id,
            review["id"],
            issue_ids=[fact_issue["id"]],
        )

    result = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[safe_issue["id"]],
    )
    application = result["application"]
    assert application["status"] == "candidate_ready"
    assert application["candidate_snapshot"]["body"].startswith("第一段直接给出结论")
    assert service.get_batch(batch_id, include_content=True)["jobs"][0][
        "body"
    ].startswith("第一段介绍背景")
    assert safe_issue["id"] in client.prompts[-1]
    assert fact_issue["id"] not in client.prompts[-1]


def test_rewrite_candidate_restores_double_escaped_paragraphs() -> None:
    source = {
        "title": "原标题",
        "subtitle": "原副标题",
        "digest": "原摘要",
        "body": "第一段。\n\n原小标题。\n\n第二段保留2025年和32%。",
    }
    candidate = normalize_rewrite_candidate(
        {
            "title": "新标题",
            "subtitle": "新副标题",
            "digest": "新摘要",
            "body": r"第一段。\n\n## 开放反馈\n\n第二段保留2025年和32%。",
            "change_summary": "增加小标题",
        },
        source=source,
        rewrite_mode="selected_issues",
    )

    assert candidate["body"] == (
        "第一段。\n\n## 开放反馈\n\n第二段保留2025年和32%。"
    )
    assert r"\n" not in candidate["body"]


def test_operator_can_explicitly_keep_source_after_comparing_candidate(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        [
            _review_payload(),
            {
                "title": "AI 候选标题",
                "subtitle": "AI 候选副标题",
                "digest": "AI 候选摘要",
                "body": (
                    "AI 候选第一段。\n\n"
                    "AI 候选第二段保留2025年完成项目、收入增长32%的事实。"
                ),
                "change_summary": "优化标题和开头",
            },
        ]
    )
    _patch_text_model(monkeypatch, client)
    original = service.get_batch(batch_id, include_content=True)["jobs"][0]
    review = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    safe_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "chief_editor"
    )
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[safe_issue["id"]],
    )
    application_id = generated["application"]["id"]

    retained = service.keep_editorial_review_source(
        batch_id,
        job_id,
        application_id,
    )

    assert retained["selected_title"] == original["selected_title"]
    assert retained["body"] == original["body"]
    assert service.get_editorial_review(review["id"])["status"] == "source_kept"
    assert service.get_editorial_review_application(application_id)["status"] == (
        "source_kept"
    )


def test_stale_review_cannot_overwrite_later_manual_edit(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient([_review_payload()])
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )
    safe_issue = review["result"]["issues"][0]
    service.update_job_content(
        batch_id,
        job_id,
        body="运营人员刚刚修改的新正文。\n\n不能被旧评审覆盖。",
    )

    with pytest.raises(EditorialReviewConflict, match="已过期"):
        service.generate_editorial_rewrite_candidate(
            batch_id,
            job_id,
            review["id"],
            issue_ids=[safe_issue["id"]],
        )


def test_blocker_requires_resolution_before_confirmation(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient([_review_payload()])
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )
    fact_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "fact_checker"
    )
    with pytest.raises(ValueError, match="事实或合规风险"):
        service.confirm_job(batch_id, job_id)

    resolved = service.resolve_editorial_review_issue(
        review["id"],
        fact_issue["id"],
        resolution="resolved",
        note="已对照合同确认年份为2026年，并已人工处理正文",
        resolved_by="tester",
    )
    assert resolved["blocking_count"] == 0
    assert service.confirm_job(batch_id, job_id)["review_status"] == "confirmed"


def test_apply_candidate_rerenders_and_requires_confirmation_again(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    candidate_payload = {
        "title": "更清晰的经营结论",
        "subtitle": "从项目实践看增长",
        "digest": "修改后的摘要",
        "body": (
            "第一段直接给出结论。\n\n"
            "第二段保留公司在2025年完成项目、收入增长32%的事实。\n\n"
            "第三段给出行动建议。"
        ),
        "change_summary": "优化结构",
    }
    client = _QueuedClient([_review_payload(), candidate_payload])
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )
    safe_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "chief_editor"
    )
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[safe_issue["id"]],
    )

    def fake_rerender(
        _selected_batch_id: str,
        selected_job_id: int,
        _config: dict,
    ) -> None:
        service.db.update_job(
            selected_job_id,
            status="ready_for_review",
            step="inject",
            html_content="<p>新排版</p>",
        )

    monkeypatch.setattr(
        service,
        "_rerender_claimed_editorial_job",
        fake_rerender,
    )
    updated = service.apply_editorial_review_application(
        batch_id,
        job_id,
        generated["application"]["id"],
    )
    assert updated["selected_title"] == "更清晰的经营结论"
    assert updated["body"].startswith("第一段直接给出结论")
    assert updated["review_status"] == "viewed"
    assert service.get_editorial_review(review["id"])["status"] == "applied"
    assert service.list_job_versions(batch_id, job_id)


def test_account_defaults_are_isolated(tmp_path) -> None:
    service, _batch_id, _job_id = _ready_service(tmp_path)
    for account_id, name in (("account-a", "公众号A"), ("account-b", "公众号B")):
        service.db.upsert_official_account(
            {
                "id": account_id,
                "name": name,
                "app_id": f"app-{account_id}",
                "app_secret_encrypted": "secret",
                "model_id": "model-a",
                "layout": {},
                "enabled": True,
            }
        )
    custom = service.save_editorial_review_profile(
        name="公众号A主编",
        description="只给公众号A使用",
        config={
            "role_ids": ["chief_editor", "brand_advisor"],
            "style_ids": ["concise"],
            "strictness": "strict",
        },
    )
    service.set_account_editorial_review_default(
        "account-a", profile_id=custom["id"]
    )
    default_a = service.get_account_editorial_review_default("account-a")
    default_b = service.get_account_editorial_review_default("account-b")
    assert default_a["profile_id"] == custom["id"]
    assert default_a["config"]["strictness"] == "strict"
    assert default_b["profile_id"] == "professional_depth"


def test_invalid_json_is_repaired_once(tmp_path, monkeypatch) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        ["这不是JSON", _review_payload()]
    )
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )
    assert review["status"] == "completed"
    assert len(client.prompts) == 2
    assert "修复为一个语法有效的严格 JSON" in client.prompts[1]
    assert "必须严格符合以下 JSON Schema" in client.prompts[1]


def test_review_uses_native_structured_schema_when_provider_supports_it(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)

    class NativeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, str]] = []

        def complete_json(
            self,
            prompt: str,
            schema: dict,
            *,
            title: str,
        ) -> dict:
            self.calls.append((prompt, schema, title))
            return _review_payload()

        def complete(self, _prompt: str) -> str:
            raise AssertionError("native structured provider must not use text wrapper")

    client = NativeClient()
    _patch_text_model(monkeypatch, client)  # type: ignore[arg-type]
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )

    assert review["status"] == "completed"
    assert len(client.calls) == 1
    assert client.calls[0][1] == review_result_schema()
    assert client.calls[0][2] == "公众号评审结果"


def test_review_schema_requires_every_decision_field() -> None:
    schema = review_result_schema()

    assert set(schema["required"]) == {
        "overall_score",
        "summary",
        "strengths",
        "dimensions",
        "issues",
        "conclusion",
    }
    assert schema["additionalProperties"] is False


def test_irreparable_missing_review_fields_become_a_blocking_safe_result(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        [
            {"status": "partial"},
            {"status": "invalid_input", "reason": "没有完整评审内容"},
        ]
    )
    _patch_text_model(monkeypatch, client)

    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )

    assert review["status"] == "completed"
    assert review["error"] == ""
    assert review["blocking_count"] == 1
    result = review["result"]
    assert set(review_result_schema()["required"]) <= set(result)
    assert result["overall_score"] == 0
    assert result["issues"][0]["blocks_draft"] is True
    assert result["issues"][0]["can_auto_apply"] is False
    assert "需重新评审或人工复核" in result["conclusion"]


def test_rewrite_contracts_require_complete_candidate_fields() -> None:
    candidate = rewrite_candidate_schema()
    paragraphs = paragraph_rewrite_schema()

    assert set(candidate["required"]) == {
        "title",
        "subtitle",
        "digest",
        "body",
        "change_summary",
    }
    assert candidate["additionalProperties"] is False
    assert paragraphs["required"] == ["paragraph_updates", "change_summary"]
    assert paragraphs["properties"]["paragraph_updates"]["items"][
        "required"
    ] == ["number", "text"]


def test_rewrite_uses_native_candidate_schema(tmp_path, monkeypatch) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)

    class NativeClient:
        def __init__(self) -> None:
            self.schemas: list[dict] = []

        def complete_json(
            self,
            _prompt: str,
            schema: dict,
            *,
            title: str,
        ) -> dict:
            self.schemas.append(schema)
            if title == "公众号评审结果":
                return _review_payload()
            return {
                "title": "结构化修改后的标题",
                "subtitle": "结构化副标题",
                "digest": "结构化摘要",
                "body": "结构化修改后的完整正文。",
                "change_summary": "完成修改",
            }

    client = NativeClient()
    _patch_text_model(monkeypatch, client)  # type: ignore[arg-type]
    review = service.run_editorial_review(
        batch_id,
        job_id,
        profile_id="professional_depth",
    )
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[],
        rewrite_mode="title_only",
    )

    assert generated["status"] == "candidate_ready"
    assert client.schemas == [review_result_schema(), rewrite_candidate_schema()]


def test_title_only_candidate_keeps_body_byte_for_byte(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        [
            _review_payload(),
            {
                "title": "只修改后的标题",
                "subtitle": "新副标题",
                "digest": "模型企图修改摘要",
                "body": "模型企图修改正文",
                "change_summary": "只改标题",
            },
        ]
    )
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    original = service.get_batch(batch_id, include_content=True)["jobs"][0]
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[],
        rewrite_mode="title_only",
    )
    candidate = generated["application"]["candidate_snapshot"]
    assert candidate["title"] == "只修改后的标题"
    assert candidate["body"] == original["body"]
    assert candidate["digest"] == original["digest"]


def test_selected_paragraph_mode_preserves_every_other_paragraph(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        [
            _review_payload(),
            {
                "paragraph_updates": [
                    {
                        "number": 2,
                        "text": (
                            "第二段改得更清楚，公司仍是在2025年完成项目，"
                            "收入增长32%。"
                        ),
                    }
                ],
                "change_summary": "只修改第二段",
            },
        ]
    )
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    original_paragraphs = service.get_batch(
        batch_id, include_content=True
    )["jobs"][0]["body"].split("\n\n")
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[],
        rewrite_mode="selected_paragraphs",
        paragraph_numbers=[2],
        instruction="更简洁",
    )
    candidate_paragraphs = generated["application"]["candidate_snapshot"][
        "body"
    ].split("\n\n")
    assert candidate_paragraphs[0] == original_paragraphs[0]
    assert candidate_paragraphs[1].startswith("第二段改得更清楚")
    assert candidate_paragraphs[2] == original_paragraphs[2]


def test_failed_rerender_restores_original_article(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        [
            _review_payload(),
            {
                "title": "候选标题",
                "subtitle": "候选副标题",
                "digest": "候选摘要",
                "body": (
                    "候选第一段内容。\n\n"
                    "候选第二段保留公司在2025年完成项目、收入增长32%的事实信息。\n\n"
                    "候选第三段给出行动建议。"
                ),
                "change_summary": "候选改动",
            },
        ]
    )
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    original = service.get_batch(batch_id, include_content=True)["jobs"][0]
    safe_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "chief_editor"
    )
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[safe_issue["id"]],
    )
    monkeypatch.setattr(
        service,
        "_rerender_claimed_editorial_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("排版失败")
        ),
    )
    with pytest.raises(RuntimeError, match="排版失败"):
        service.apply_editorial_review_application(
            batch_id,
            job_id,
            generated["application"]["id"],
        )
    restored = service.get_batch(batch_id, include_content=True)["jobs"][0]
    assert restored["selected_title"] == original["selected_title"]
    assert restored["body"] == original["body"]
    application_id = generated["application"]["id"]
    failed_application = service.get_editorial_review_application(
        application_id
    )
    assert failed_application["status"] == "failed"
    assert "排版失败" in failed_application["error"]
    failed_review = service.get_editorial_review(review["id"])
    assert failed_review["status"] == "stale"
    assert "排版失败" in failed_review["error"]
    with pytest.raises(ValueError, match="请先生成修改稿"):
        service.apply_editorial_review_application(
            batch_id,
            job_id,
            application_id,
        )


def test_duplicate_review_click_is_rejected_before_second_model_charge(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    class SlowClient:
        def complete(self, _prompt: str) -> str:
            entered.set()
            assert release.wait(timeout=3)
            return json.dumps(_review_payload(), ensure_ascii=False)

    _patch_text_model(monkeypatch, SlowClient())  # type: ignore[arg-type]
    errors: list[Exception] = []

    def first_call() -> None:
        try:
            service.run_editorial_review(
                batch_id,
                job_id,
                profile_id="professional_depth",
            )
        except Exception as exc:  # pragma: no cover - diagnostic capture
            errors.append(exc)

    worker = threading.Thread(target=first_call)
    worker.start()
    assert entered.wait(timeout=3)
    try:
        with pytest.raises(EditorialReviewConflict, match="正在评审"):
            service.run_editorial_review(
                batch_id,
                job_id,
                profile_id="professional_depth",
            )
    finally:
        release.set()
        worker.join(timeout=3)
    assert not errors


def test_candidate_number_changes_are_preserved_as_confirmation_warnings(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    client = _QueuedClient(
        [
            _review_payload(),
            {
                "title": "企业经营的新答案",
                "subtitle": "从项目实践看增长",
                "digest": "候选摘要",
                "body": "第一段介绍背景。\n\n第二段擅自改成公司在2026年完成项目，收入增长45%。\n\n第三段给出行动建议。",
                "change_summary": "擅改数字",
            },
        ]
    )
    _patch_text_model(monkeypatch, client)
    review = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    safe_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "chief_editor"
    )
    generated = service.generate_editorial_rewrite_candidate(
        batch_id,
        job_id,
        review["id"],
        issue_ids=[safe_issue["id"]],
    )

    application = generated["application"]
    candidate = application["candidate_snapshot"]
    assert application["status"] == "candidate_ready"
    assert candidate["body"].startswith("第一段介绍背景")
    assert candidate["risk_warnings"] == [
        {
            "code": "body_material_numbers_changed",
            "severity": "high",
            "title": "候选正文的关键数字与原稿不一致",
            "message": "新增 2026年、45%；删除 2025年、32%",
            "added": ["2026年", "45%"],
            "removed": ["2025年", "32%"],
            "requires_confirmation": True,
        }
    ]
    current = service.get_batch(batch_id, include_content=True)["jobs"][0]
    assert "2025年" in current["body"]
    assert "32%" in current["body"]
    assert "2026年" not in current["body"]


def test_candidate_header_numbers_are_warnings_instead_of_errors() -> None:
    source = {
        "title": "原标题",
        "subtitle": "原副标题",
        "digest": "原摘要",
        "body": "第一段介绍背景。\n\n第二段给出行动建议。",
    }

    candidate = normalize_rewrite_candidate(
        {
            "title": "2026年企业经营新答案",
            "subtitle": "原副标题",
            "digest": "效率提升40%的实践方法",
            "body": source["body"],
            "change_summary": "优化标题和摘要",
        },
        source=source,
        rewrite_mode="selected_issues",
    )

    warning = candidate["risk_warnings"][0]
    assert warning["code"] == "header_material_numbers_added"
    assert warning["added"] == ["2026年", "40%"]
    assert warning["requires_confirmation"] is True


def test_same_risk_cannot_be_downgraded_by_a_later_model_review(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    first_risk = {
        "role_id": "fact_checker",
        "category": "时间",
        "severity": "high",
        "location": "第2段",
        "excerpt": "2025年完成项目",
        "problem": "年份与原始资料冲突",
        "suggestion": "请人工核实年份",
        "evidence_status": "conflict",
        "can_auto_apply": False,
        "blocks_draft": True,
    }
    downgraded_risk = {
        "role_id": "chief_editor",
        "category": "时间",
        "severity": "low",
        "location": "第2段",
        "excerpt": "2025年完成项目",
        "problem": "年份表达可以更清楚",
        "suggestion": "润色表达",
        "evidence_status": "confirmed",
        "can_auto_apply": True,
        "blocks_draft": False,
    }
    client = _QueuedClient(
        [
            _review_payload_with_issues([first_risk]),
            _review_payload_with_issues([downgraded_risk]),
        ]
    )
    _patch_text_model(monkeypatch, client)

    first = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    second = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )

    assert first["result"]["issues"][0]["risk_id"] == second["result"]["issues"][0][
        "risk_id"
    ]
    inherited = second["result"]["issues"][0]
    assert inherited["role_id"] == "fact_checker"
    assert inherited["severity"] == "high"
    assert inherited["blocks_draft"] is True
    assert inherited["can_auto_apply"] is False
    assert inherited["resolution"] == "open"
    assert inherited["carried_from_review_id"] == first["id"]


@pytest.mark.parametrize("resolution", ["resolved", "waived"])
def test_only_latest_review_is_inherited_and_closed_risk_does_not_revive(
    tmp_path, monkeypatch, resolution
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    blocker = {
        "role_id": "fact_checker",
        "category": "时间",
        "severity": "high",
        "location": "第2段",
        "problem": "年份与原始资料冲突",
        "suggestion": "请人工核实年份",
        "evidence_status": "conflict",
        "can_auto_apply": False,
        "blocks_draft": True,
    }
    client = _QueuedClient(
        [
            _review_payload_with_issues([blocker]),
            _review_payload_with_issues([]),
            _review_payload_with_issues([]),
        ]
    )
    _patch_text_model(monkeypatch, client)

    service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    latest = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    carried = latest["result"]["issues"][0]
    service.resolve_editorial_review_issue(
        latest["id"],
        carried["id"],
        resolution=resolution,
        note="运营人员已核对原始合同并记录处理结论",
        resolved_by="tester",
    )
    third = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )

    assert third["blocking_count"] == 0
    assert not third["result"]["issues"]


def test_carried_blocker_has_priority_over_five_overall_suggestions(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    blocker = {
        "role_id": "fact_checker",
        "category": "时间",
        "severity": "high",
        "location": "第2段",
        "problem": "年份与原始资料冲突",
        "suggestion": "请人工核实年份",
        "evidence_status": "conflict",
        "blocks_draft": True,
    }
    normal_issues = [
        {
            "role_id": "chief_editor",
            "category": "结构",
            "severity": "low",
            "location": f"第{i + 1}处",
            "problem": f"第{i + 1}处表达略显冗长",
            "suggestion": "适度压缩表达",
            "evidence_status": "not_applicable",
            "can_auto_apply": True,
        }
        for i in range(60)
    ]
    client = _QueuedClient(
        [
            _review_payload_with_issues([blocker]),
            _review_payload_with_issues(normal_issues),
        ]
    )
    _patch_text_model(monkeypatch, client)

    first = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )
    second = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )

    issues = second["result"]["issues"]
    assert len(issues) == 6
    assert issues[0]["blocks_draft"] is True
    assert issues[0]["risk_id"] == first["result"]["issues"][0]["risk_id"]
    assert issues[0]["carried_from_review_id"] == first["id"]
    assert sum(bool(item["blocks_draft"]) for item in issues) == 1
    assert sum(not item["blocks_draft"] for item in issues) == 5


def test_rewrite_prompt_has_mode_specific_editing_boundaries() -> None:
    review = {
        "source_snapshot": {
            "title": "原稿标题",
            "subtitle": "原稿副标题",
            "digest": "原稿摘要",
            "body": "原稿正文。",
        },
        "config": normalize_review_config(
            {
                "role_ids": ["chief_editor"],
                "style_ids": ["professional"],
            }
        ),
    }
    selected = [
        {
            "id": "issue-safe",
            "role_id": "chief_editor",
            "location": "第1段",
            "problem": "铺垫过长",
            "suggestion": "压缩铺垫",
        }
    ]

    selected_prompt = build_rewrite_prompt(
        review=review,
        selected_issues=selected,
        rewrite_mode="selected_issues",
        instruction="",
    )
    full_prompt = build_rewrite_prompt(
        review=review,
        selected_issues=selected,
        rewrite_mode="full_rewrite",
        instruction="",
    )
    target_prompt = build_rewrite_prompt(
        review=review,
        selected_issues=selected,
        rewrite_mode="target_style",
        instruction="",
    )

    assert "只处理本次勾选建议" in selected_prompt
    assert "未涉及的标题、段落、事实和表达必须保留" in selected_prompt
    assert "只处理本次勾选建议" not in full_prompt
    assert "只处理本次勾选建议" not in target_prompt
    assert "可以重组全文结构和表达" in full_prompt
    assert "可以在全文范围调整语言和表达" in target_prompt


def test_review_prompt_prioritizes_engagement_outcomes_without_line_editing() -> None:
    """The jury evaluates reader behavior, not every paragraph or sentence."""

    prompt = build_review_prompt(
        job={
            "selected_title": "一篇需要评审的公众号文章",
            "selected_subtitle": "从经营问题切入",
            "digest": "文章摘要",
            "body": "开头提出问题。\n\n正文分析原因并给出行动建议。",
            "raw_content": "原始资料。",
        },
        config=normalize_review_config(
            {
                "role_ids": [
                    "chief_editor",
                    "target_reader",
                    "growth_operator",
                ],
                "style_ids": ["rigorous", "accessible"],
            }
        ),
        account_name="测试公众号",
    )

    for focus in ("标题", "开头", "完读", "点赞", "转发"):
        assert focus in prompt
    assert "预估" in prompt or "预计" in prompt
    compact = re.sub(r"\s+", "", prompt)
    assert "逐段" in compact and "逐句" in compact
    assert any(
        marker in compact
        for marker in ("不得逐段", "禁止逐段", "不要逐段")
    )

    contract = json.loads(prompt.rsplit("结构示例：", 1)[1])
    dimension_ids = {
        str(item["id"]) for item in contract.get("dimensions") or []
    }
    assert {
        "title_click",
        "opening_retention",
        "completion_potential",
        "like_potential",
        "share_potential",
    }.issubset(dimension_ids)
    location_contract = str(contract["issues"][0]["location"])
    assert not re.search(
        r"第\s*(?:N|\d+)\s*[段句]|paragraph",
        location_contract,
        flags=re.IGNORECASE,
    )


def test_review_result_removes_paragraph_and_sentence_level_locations() -> None:
    """Even a model that line-edits must be normalized to article-level scope."""

    result = normalize_review_result(
        {
            "overall_score": 70,
            "summary": "开头吸引力不足。",
            "dimensions": [
                {
                    "id": "title_click",
                    "name": "标题点击潜力",
                    "score": 65,
                    "summary": "标题信息量尚可。",
                },
                {
                    "id": "opening_retention",
                    "name": "开头留存潜力",
                    "score": 55,
                    "summary": "进入核心冲突较慢。",
                },
                {
                    "id": "completion_potential",
                    "name": "预计完读率潜力",
                    "score": 60,
                    "summary": "中部节奏需要整体收紧。",
                },
                {
                    "id": "like_potential",
                    "name": "点赞潜力",
                    "score": 58,
                    "summary": "读者认同点不够鲜明。",
                },
                {
                    "id": "share_potential",
                    "name": "转发潜力",
                    "score": 52,
                    "summary": "缺少值得分享的明确结论。",
                },
            ],
            "issues": [
                {
                    "role_id": "chief_editor",
                    "category": "阅读留存",
                    "severity": "medium",
                    "location": "第3段第2句",
                    "excerpt": "这一句只是模型用来定位问题的原文摘录",
                    "problem": "模型试图逐句挑错",
                    "suggestion": "从文章整体优化开头与阅读节奏",
                    "can_auto_apply": True,
                }
            ],
            "conclusion": "整体优化后可发布。",
        },
        config=normalize_review_config(
            {
                "role_ids": ["chief_editor", "target_reader"],
                "style_ids": ["rigorous"],
            }
        ),
    )

    assert not re.search(
        r"第\s*(?:N|\d+)\s*[段句]|paragraph",
        str(result["issues"][0]["location"]),
        flags=re.IGNORECASE,
    )
    assert {
        "title_click",
        "opening_retention",
        "completion_potential",
        "like_potential",
        "share_potential",
    }.issubset(
        {str(item["id"]) for item in result["dimensions"]}
    )


def test_engagement_optimization_rewrites_the_article_as_a_whole() -> None:
    review = {
        "source_snapshot": {
            "title": "原稿标题",
            "subtitle": "原稿副标题",
            "digest": "原稿摘要",
            "body": "原稿开头。\n\n原稿正文。\n\n原稿结尾。",
        },
        "config": normalize_review_config(
            {
                "role_ids": ["chief_editor", "target_reader", "growth_operator"],
                "style_ids": ["rigorous", "accessible"],
            }
        ),
    }
    prompt = build_rewrite_prompt(
        review=review,
        selected_issues=[
            {
                "id": "issue-engagement",
                "role_id": "growth_operator",
                "location": "正文整体",
                "problem": "预计完读和转发潜力偏低",
                "suggestion": "优化标题、开头、阅读节奏与分享动机",
            }
        ],
        rewrite_mode="engagement_optimization",
        instruction="",
    )

    for focus in ("标题", "开头", "阅读节奏", "点赞", "转发"):
        assert focus in prompt
    assert "整体" in prompt
    compact = re.sub(r"\s+", "", prompt)
    assert "逐段" in compact and "逐句" in compact
    assert "事实" in prompt and "核心观点" in prompt


def test_editorial_role_owns_auto_apply_permission_even_if_model_says_false() -> None:
    result = normalize_review_result(
        {
            "overall_score": 60,
            "issues": [
                {
                    "role_id": "chief_editor",
                    "category": "标题",
                    "severity": "medium",
                    "problem": "标题不够具体",
                    "suggestion": "改成更具体的利益点",
                    "can_auto_apply": False,
                }
            ],
        },
        config=normalize_review_config({"role_ids": ["chief_editor"]}),
    )

    assert result["issues"][0]["can_auto_apply"] is True


def test_fact_and_compliance_signals_cannot_hide_under_other_roles(
    tmp_path, monkeypatch
) -> None:
    service, batch_id, job_id = _ready_service(tmp_path)
    misleading_issues = [
        {
            "role_id": "chief_editor",
            "category": "结构",
            "severity": "high",
            "location": "第2段",
            "problem": "模型声称数据已经核实",
            "suggestion": "直接自动修改数据",
            "evidence_status": "conflict",
            "can_auto_apply": True,
        },
        {
            "role_id": "growth_operator",
            "category": "传播表达",
            "severity": "high",
            "location": "标题",
            "problem": "标题含广告法绝对化承诺",
            "suggestion": "检查违规表达后人工处理",
            "evidence_status": "not_applicable",
            "can_auto_apply": True,
        },
    ]
    client = _QueuedClient([_review_payload_with_issues(misleading_issues)])
    _patch_text_model(monkeypatch, client)

    review = service.run_editorial_review(
        batch_id, job_id, profile_id="professional_depth"
    )

    fact_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "fact_checker"
    )
    compliance_issue = next(
        item
        for item in review["result"]["issues"]
        if item["role_id"] == "compliance_expert"
    )
    for issue in (fact_issue, compliance_issue):
        assert issue["severity"] == "high"
        assert issue["can_auto_apply"] is False
        assert issue["blocks_draft"] is True
    assert review["blocking_count"] == 2


def test_non_content_job_updates_do_not_change_content_revision(tmp_path) -> None:
    service, _batch_id, job_id = _ready_service(tmp_path)
    before = service.db.get_job(job_id)
    assert before is not None

    service.db.update_job(
        job_id,
        status="rendering",
        step="render",
        html_content="<p>仅刷新排版</p>",
        error="仅更新运行信息",
        meta_json={"render_attempt": 2},
    )
    after = service.db.get_job(job_id)

    assert after is not None
    assert after["content_revision"] == before["content_revision"]
