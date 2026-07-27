from __future__ import annotations

import uuid
import json
import shutil
from pathlib import Path
from typing import Any

from app.ai.model_registry import (
    apply_model_selection,
    configured_models,
    decrypt_api_key,
    encrypt_api_key,
)
from app.config import load_config
from app.db import Database
from app.layout_profiles import (
    layout_from_config,
    layout_to_template_config,
    normalize_layout,
    validate_layout,
)
from app.prompt_templates import (
    ARTICLE_PROMPT_PURPOSE,
    DEFAULT_IMAGE_PROMPT_STYLE,
    IMAGE_PROMPT_PURPOSE,
    PROMPT_MODE_DEFAULT,
    PROMPT_MODE_TEMPLATE,
    resolve_article_prompt_instructions,
    resolve_image_prompt_style,
)


DEFAULT_ACCOUNT_ID = "__config_default__"
IMPORTED_DEFAULT_ACCOUNT_ID = "account_config_default"
IMPORTED_BENCHMARK_ACCOUNT_ID = "account_config_benchmark"


def save_account(
    db: Database,
    *,
    name: str,
    app_id: str,
    app_secret: str | None,
    model_id: str,
    enabled: bool = True,
    account_id: str | None = None,
) -> str:
    name = name.strip()
    app_id = app_id.strip()
    model_id = model_id.strip()
    if not name or not app_id:
        raise ValueError("公众号名称和 AppID 不能为空")
    config = load_config()
    model = db.get_ai_model(model_id)
    config_model_ids = {item["id"] for item in configured_models(config)}
    if not model and model_id not in config_model_ids:
        raise ValueError("请先选择一个已添加的大模型")
    existing = db.get_official_account(account_id) if account_id else None
    encrypted = encrypt_api_key(app_secret.strip()) if app_secret and app_secret.strip() else ""
    if not encrypted and existing:
        encrypted = str(existing["app_secret_encrypted"])
    if not encrypted:
        raise ValueError("AppSecret 不能为空")
    account_id = account_id or f"account_{uuid.uuid4().hex[:12]}"
    db.upsert_official_account(
        {
            "id": account_id,
            "name": name,
            "app_id": app_id,
            "app_secret_encrypted": encrypted,
            "model_id": model_id,
            "layout": (
                json.loads(str(existing.get("layout_json") or "{}"))
                if existing
                else layout_from_config(config)
            ),
            "enabled": enabled,
            "created_at": existing.get("created_at") if existing else None,
        }
    )
    configured_db = Path(str(config.get("_db_path") or "")).resolve()
    if Path(db.path).resolve() == configured_db:
        _copy_shared_template_snapshot(config, account_id)
    return account_id


def ensure_config_account_imported(
    db: Database, config: dict[str, Any]
) -> str | None:
    """Backward-compatible wrapper returning the primary imported account."""
    imported = ensure_config_accounts_imported(db, config)
    return imported[0] if imported else None


def _import_config_account(
    db: Database,
    config: dict[str, Any],
    *,
    section: str,
    account_id: str,
    fallback_name: str,
) -> str | None:
    account_cfg = config.get(section) or {}
    app_id = str(account_cfg.get("app_id") or "").strip()
    app_secret = str(account_cfg.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        return None
    desired_name = str(
        account_cfg.get("account_name") or account_cfg.get("name") or fallback_name
    ).strip()
    for account in db.list_official_accounts():
        if str(account.get("app_id") or "").strip() == app_id:
            if (
                desired_name
                and desired_name != fallback_name
                and str(account.get("name") or "").strip() in {"", "默认公众号"}
            ):
                account["name"] = desired_name
                db.upsert_official_account(account)
            return str(account["id"])
    primary = str((config.get("ai") or {}).get("primary") or "").strip()
    model_id = f"config:{primary}" if primary else ""
    valid_models = {str(item["id"]) for item in configured_models(config)}
    if model_id not in valid_models:
        return None
    db.upsert_official_account(
        {
            "id": account_id,
            "name": desired_name or fallback_name,
            "app_id": app_id,
            "app_secret_encrypted": encrypt_api_key(app_secret),
            "model_id": model_id,
            "enabled": True,
        }
    )
    return account_id


def ensure_config_accounts_imported(
    db: Database, config: dict[str, Any]
) -> list[str]:
    """Import both publishing and benchmark WeChat credentials into management."""
    imported: list[str] = []
    for section, account_id, fallback_name in (
        ("wechat", IMPORTED_DEFAULT_ACCOUNT_ID, "默认公众号"),
        ("benchmark", IMPORTED_BENCHMARK_ACCOUNT_ID, "对标公众号"),
    ):
        result = _import_config_account(
            db,
            config,
            section=section,
            account_id=account_id,
            fallback_name=fallback_name,
        )
        if result and result not in imported:
            imported.append(result)
    return imported


def public_accounts(db: Database, enabled_only: bool = False) -> list[dict[str, Any]]:
    model_names = {
        str(model["id"]): str(model["name"])
        for model in db.list_ai_models()
    }
    model_names.update(
        {str(item["id"]): str(item["name"]) for item in configured_models(load_config())}
    )
    accounts = db.list_official_accounts(enabled_only=enabled_only)
    for item in accounts:
        try:
            raw_layout = json.loads(str(item.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            raw_layout = {}
        item["layout"] = normalize_layout(raw_layout)
        item["has_custom_layout"] = bool(raw_layout)
        item.pop("app_secret_encrypted", None)
        item.pop("layout_json", None)
        item["enabled"] = bool(item.get("enabled"))
        item["model_name"] = model_names.get(str(item.get("model_id")), "模型已删除")
        item["has_app_secret"] = True
    return accounts


def save_account_layout(db: Database, account_id: str, layout: dict[str, Any]) -> None:
    account = db.get_official_account(account_id)
    if not account:
        raise ValueError("公众号不存在")
    account["layout"] = validate_layout(layout)
    db.upsert_official_account(account)


def save_account_prompt_selection(
    db: Database,
    account_id: str,
    template_id: str | None,
    *,
    purpose: str = IMAGE_PROMPT_PURPOSE,
) -> str:
    """Assign one reusable article or image prompt template to one account."""
    account = db.get_official_account(account_id)
    if not account:
        raise ValueError("公众号不存在")
    if purpose not in {ARTICLE_PROMPT_PURPOSE, IMAGE_PROMPT_PURPOSE}:
        raise ValueError("提示词模板类型无效")
    try:
        stored = json.loads(str(account.get("layout_json") or "{}"))
    except json.JSONDecodeError:
        stored = {}
    layout = normalize_layout(stored)
    section_name = (
        "article_prompt"
        if purpose == ARTICLE_PROMPT_PURPOSE
        else "inline_images"
    )
    settings = dict(layout.get(section_name) or {})
    clean_template_id = str(template_id or "").strip()
    if not clean_template_id:
        settings["prompt_mode"] = PROMPT_MODE_DEFAULT
        settings["prompt_template_id"] = ""
        prompt_name = "默认模板"
    else:
        template = db.get_prompt_template(clean_template_id)
        if (
            not template
            or str(template.get("purpose") or "") != purpose
            or not bool(template.get("enabled"))
        ):
            label = "文章" if purpose == ARTICLE_PROMPT_PURPOSE else "图片"
            raise ValueError(f"所选{label}提示词模板不存在或已停用")
        settings["prompt_mode"] = PROMPT_MODE_TEMPLATE
        settings["prompt_template_id"] = clean_template_id
        prompt_name = str(template.get("name") or "自定义提示词模板")
    if purpose == IMAGE_PROMPT_PURPOSE:
        # Keep free text out of account settings. Effective custom content is
        # always resolved from the selected reusable image template.
        settings["prompt_style"] = DEFAULT_IMAGE_PROMPT_STYLE
    layout[section_name] = settings
    save_account_layout(db, account_id, layout)
    return prompt_name


def save_account_article_prompt_selection(
    db: Database,
    account_id: str,
    template_id: str | None,
) -> str:
    return save_account_prompt_selection(
        db,
        account_id,
        template_id,
        purpose=ARTICLE_PROMPT_PURPOSE,
    )


def save_account_image_prompt_selection(
    db: Database,
    account_id: str,
    template_id: str | None,
) -> str:
    return save_account_prompt_selection(
        db,
        account_id,
        template_id,
        purpose=IMAGE_PROMPT_PURPOSE,
    )


def ensure_account_layouts_initialized(db: Database, config: dict[str, Any]) -> int:
    """Give legacy accounts independent layout data and independent template files."""
    initialized = 0
    for account in db.list_official_accounts():
        try:
            raw_layout = json.loads(str(account.get("layout_json") or "{}"))
        except json.JSONDecodeError:
            raw_layout = {}
        if raw_layout:
            continue
        account_id = str(account["id"])
        account["layout"] = layout_from_config(config)
        db.upsert_official_account(account)
        _copy_shared_template_snapshot(config, account_id)
        initialized += 1
    return initialized


def _copy_shared_template_snapshot(config: dict[str, Any], account_id: str) -> None:
    root = Path(str(config.get("_root") or Path.cwd()))
    editor = config.get("editor_template") or {}
    shared_path = Path(str(editor.get("snapshot_path") or "data/editor_template.html"))
    if not shared_path.is_absolute():
        shared_path = root / shared_path
    private_path = root / "data" / "templates" / f"{account_id}.html"
    if shared_path.exists() and shared_path != private_path and not private_path.exists():
        private_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_path, private_path)


def apply_account_selection(
    config: dict[str, Any],
    db: Database,
    account_id: str,
    *,
    allow_disabled: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Inject one account's WeChat credentials and one-to-one model binding."""
    record = db.get_official_account(account_id)
    if not record or (not allow_disabled and not bool(record.get("enabled"))):
        raise ValueError(f"公众号不可用或已停用：{account_id}")
    config = apply_model_selection(config, db, str(record["model_id"]), str(record["model_id"]))
    result = dict(config)
    wechat = dict(result.get("wechat") or {})
    wechat["app_id"] = str(record["app_id"])
    wechat["app_secret"] = decrypt_api_key(str(record["app_secret_encrypted"]))
    result["wechat"] = wechat
    try:
        raw_layout = json.loads(str(record.get("layout_json") or "{}"))
    except json.JSONDecodeError:
        raw_layout = {}
    layout = normalize_layout(raw_layout)
    ai = dict(result.get("ai") or {})
    rewrite_instruction, title_instruction, article_mode, article_name = (
        resolve_article_prompt_instructions(
            dict(layout.get("article_prompt") or {}),
            db,
            rewrite_instruction=str(ai.get("rewrite_prompt") or ""),
            title_instruction=str(ai.get("title_prompt") or ""),
        )
    )
    ai["rewrite_prompt"] = rewrite_instruction
    ai["title_prompt"] = title_instruction
    ai["article_prompt_mode"] = article_mode
    ai["article_prompt_template_name"] = article_name
    result["ai"] = ai
    if raw_layout:
        template = dict(result.get("template") or {})
        template.update(layout_to_template_config(layout))
        result["template"] = template
        editor = dict(result.get("editor_template") or {})
        editor.update(layout["editor_template"])
        editor["snapshot_path"] = f"data/templates/{account_id}.html"
        result["editor_template"] = editor
        result["inline_images"] = dict(layout["inline_images"])
        prompt_style, prompt_mode, prompt_name = resolve_image_prompt_style(
            result["inline_images"], db
        )
        result["inline_images"]["prompt_style"] = prompt_style
        result["inline_images"]["prompt_mode"] = prompt_mode
        result["inline_images"]["prompt_template_name"] = prompt_name
    return result, record
