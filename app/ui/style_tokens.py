"""Single source of truth for the application visual design system."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StyleToken:
    value: str
    description: str


UI_STYLE_SPEC: dict[str, dict[str, StyleToken]] = {
    "颜色": {
        "color-bg-canvas": StyleToken("#f0f3f2", "页面画布背景"),
        "color-bg-subtle": StyleToken("#f8faf9", "浅色分区背景"),
        "color-surface": StyleToken("#ffffff", "卡片、弹框和输入面板"),
        "color-surface-glass": StyleToken("rgba(255, 255, 255, 0.96)", "悬浮玻璃面板"),
        "color-surface-muted": StyleToken("#f1f5f3", "占位和弱强调区域"),
        "color-text-primary": StyleToken("#16221e", "标题和正文主文字"),
        "color-text-secondary": StyleToken("#65736d", "说明和辅助文字"),
        "color-border": StyleToken("#e3e9e6", "默认分割线和边框"),
        "color-border-strong": StyleToken("#d5dfda", "强调边框"),
        "color-brand": StyleToken("#087a63", "主品牌色和主要操作"),
        "color-brand-hover": StyleToken("#10a37f", "品牌悬停和进度状态"),
        "color-brand-dark": StyleToken("#075f4e", "品牌深色文字"),
        "color-brand-soft": StyleToken("#e6f6f1", "品牌浅色背景"),
        "color-warning": StyleToken("#8a5a12", "警告文字"),
        "color-warning-soft": StyleToken("#fff3dd", "警告背景"),
        "color-danger": StyleToken("#9d2430", "错误和危险操作"),
        "color-danger-soft": StyleToken("#fce8ea", "错误背景"),
        "color-info-border": StyleToken("#cfd7f6", "信息型组件边框"),
    },
    "字体": {
        "font-sans": StyleToken(
            '"PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
            "全局中文界面字体",
        ),
        "font-mono": StyleToken('Consolas, "SFMono-Regular", monospace', "代码和凭证字体"),
        "font-size-xs": StyleToken("10px", "极弱辅助信息"),
        "font-size-sm": StyleToken("12px", "标签和说明"),
        "font-size-md": StyleToken("13px", "辅助正文"),
        "font-size-base": StyleToken("14px", "默认正文"),
        "font-size-lg": StyleToken("15px", "强调正文"),
        "font-size-xl": StyleToken("18px", "区块标题"),
        "font-size-2xl": StyleToken("24px", "页面标题"),
        "font-size-display": StyleToken("28px", "品牌标题"),
        "font-weight-regular": StyleToken("400", "普通正文"),
        "font-weight-medium": StyleToken("600", "按钮和标签"),
        "font-weight-bold": StyleToken("700", "标题"),
        "line-height-tight": StyleToken("1.2", "标题行高"),
        "line-height-base": StyleToken("1.5", "正文行高"),
        "line-height-relaxed": StyleToken("1.7", "长文说明行高"),
    },
    "间距": {
        "space-1": StyleToken("4px", "最小内联间距"),
        "space-2": StyleToken("8px", "紧凑组件间距"),
        "space-3": StyleToken("12px", "控件组间距"),
        "space-4": StyleToken("16px", "默认内容间距"),
        "space-5": StyleToken("20px", "卡片紧凑内边距"),
        "space-6": StyleToken("24px", "卡片标准内边距"),
        "space-7": StyleToken("28px", "宽松区块间距"),
        "space-8": StyleToken("32px", "页面区块间距"),
        "space-10": StyleToken("40px", "大区块间距"),
        "space-12": StyleToken("48px", "页面上下留白"),
    },
    "圆角": {
        "radius-sm": StyleToken("6px", "小标签和色块"),
        "radius-md": StyleToken("10px", "按钮和输入框"),
        "radius-lg": StyleToken("14px", "紧凑卡片"),
        "radius-xl": StyleToken("16px", "标准卡片"),
        "radius-2xl": StyleToken("20px", "大卡片和弹框"),
        "radius-round": StyleToken("999px", "胶囊和圆形"),
    },
    "阴影": {
        "shadow-sm": StyleToken("0 1px 2px rgba(16, 34, 27, 0.04)", "轻边界阴影"),
        "shadow-card": StyleToken(
            "0 1px 2px rgba(16, 34, 27, 0.04), 0 10px 30px rgba(16, 34, 27, 0.055)",
            "默认卡片阴影",
        ),
        "shadow-hover": StyleToken("0 14px 34px rgba(16, 34, 27, 0.09)", "悬停阴影"),
        "shadow-dialog": StyleToken("0 24px 70px rgba(15, 23, 42, 0.12)", "弹框阴影"),
    },
    "布局": {
        "layout-content-max": StyleToken("1240px", "主内容最大宽度"),
        "layout-auth-card": StyleToken("460px", "登录卡片最大宽度"),
        "layout-onboarding-max": StyleToken("940px", "首次配置内容最大宽度"),
        "layout-form-max": StyleToken("700px", "长表单最大宽度"),
        "layout-dialog-sm": StyleToken("620px", "提示弹框最大宽度"),
        "layout-dialog-md": StyleToken("760px", "表单弹框最大宽度"),
        "layout-dialog-lg": StyleToken("900px", "复杂配置弹框最大宽度"),
        "layout-dialog-xl": StyleToken("1120px", "工作台弹框最大宽度"),
        "control-height": StyleToken("40px", "标准按钮和输入控件高度"),
        "media-thumb-width": StyleToken("128px", "文章缩略图宽度"),
        "media-thumb-height": StyleToken("80px", "文章缩略图高度"),
        "media-preview-height": StyleToken("130px", "内容图片预览高度"),
        "media-option-height": StyleToken("120px", "图片选择项高度"),
    },
}


def style_css_variables() -> str:
    declarations = [
        f"  --ui-{name}: {token.value};"
        for group in UI_STYLE_SPEC.values()
        for name, token in group.items()
    ]
    aliases = [
        "  --bg0: var(--ui-color-bg-canvas);",
        "  --bg1: var(--ui-color-bg-subtle);",
        "  --panel: var(--ui-color-surface-glass);",
        "  --panel-solid: var(--ui-color-surface);",
        "  --ink: var(--ui-color-text-primary);",
        "  --muted: var(--ui-color-text-secondary);",
        "  --line: var(--ui-color-border);",
        "  --line-strong: var(--ui-color-border-strong);",
        "  --accent: var(--ui-color-brand);",
        "  --accent-2: var(--ui-color-brand-hover);",
        "  --accent-dark: var(--ui-color-brand-dark);",
        "  --accent-soft: var(--ui-color-brand-soft);",
        "  --warn: var(--ui-color-warning);",
        "  --warn-soft: var(--ui-color-warning-soft);",
        "  --danger: var(--ui-color-danger);",
        "  --danger-soft: var(--ui-color-danger-soft);",
        "  --shadow: var(--ui-shadow-card);",
        "  --shadow-hover: var(--ui-shadow-hover);",
        "  --radius: var(--ui-radius-xl);",
    ]
    return ":root {\n" + "\n".join([*declarations, *aliases]) + "\n}\n"


def style_spec_markdown() -> str:
    lines = [
        "# UI 样式规范表",
        "",
        "本表由 `app/ui/style_tokens.py` 中的 `UI_STYLE_SPEC` 生成；代码中的组件样式应使用对应的 `var(--ui-*)`，不得重复写颜色、字号、间距、圆角或阴影常量。",
        "",
    ]
    for group, tokens in UI_STYLE_SPEC.items():
        lines.extend((f"## {group}", "", "| CSS 变量 | 值 | 用途 |", "|---|---|---|"))
        lines.extend(
            f"| `--ui-{name}` | `{token.value}` | {token.description} |"
            for name, token in tokens.items()
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
