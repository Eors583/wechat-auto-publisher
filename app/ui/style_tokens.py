"""Single source of truth for the operations workbench design system."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StyleToken:
    value: str
    description: str


UI_STYLE_SPEC: dict[str, dict[str, StyleToken]] = {
    "颜色": {
        "color-bg-canvas": StyleToken("#f6f8fc", "应用画布"),
        "color-bg-subtle": StyleToken("#f7f9fd", "浅色分区"),
        "color-surface": StyleToken("#ffffff", "卡片与表单表面"),
        "color-surface-glass": StyleToken("rgba(255, 255, 255, 0.96)", "悬浮表面"),
        "color-surface-muted": StyleToken("#f0f3f8", "禁用与占位表面"),
        "color-text-primary": StyleToken("#121d36", "标题与正文"),
        "color-text-secondary": StyleToken("#8190a9", "说明文字"),
        "color-text-sidebar": StyleToken("#101d38", "侧栏主文字"),
        "color-text-sidebar-muted": StyleToken("#68758d", "侧栏辅助文字"),
        "color-border": StyleToken("#e6eaf1", "默认分隔线"),
        "color-border-strong": StyleToken("#d7deea", "强调边框"),
        "color-brand": StyleToken("#2167ff", "主操作蓝"),
        "color-brand-hover": StyleToken("#1453e8", "主操作悬停"),
        "color-brand-dark": StyleToken("#1453e8", "品牌强调文字"),
        "color-brand-soft": StyleToken("#eaf2ff", "品牌浅背景"),
        "color-purple": StyleToken("#8b48ff", "AI 与评审"),
        "color-purple-dark": StyleToken("#6b46e8", "AI 渐变深色"),
        "color-purple-soft": StyleToken("#f2eaff", "AI 浅背景"),
        "color-orange": StyleToken("#ff6a00", "待处理状态"),
        "color-orange-soft": StyleToken("#fff0e5", "待处理浅背景"),
        "color-success": StyleToken("#00bf62", "成功状态"),
        "color-success-soft": StyleToken("#e6faef", "成功浅背景"),
        "color-warning": StyleToken("#e69015", "警告状态"),
        "color-warning-soft": StyleToken("#fff7e8", "警告浅背景"),
        "color-danger": StyleToken("#ff4d55", "错误与风险"),
        "color-danger-soft": StyleToken("#fff0f1", "错误浅背景"),
        "color-info-border": StyleToken("#d8e7ff", "信息边框"),
        "color-info-soft": StyleToken("#f0f6ff", "信息提示浅背景"),
    },
    "字体": {
        "font-sans": StyleToken(
            '"Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", system-ui, sans-serif',
            "全局中文界面字体",
        ),
        "font-mono": StyleToken('Consolas, "SFMono-Regular", monospace', "凭证与代码字体"),
        "font-size-xs": StyleToken("12px", "标签和辅助信息"),
        "font-size-sm": StyleToken("13px", "说明文字"),
        "font-size-md": StyleToken("16px", "区块小标题"),
        "font-size-base": StyleToken("14px", "默认正文"),
        "font-size-lg": StyleToken("20px", "页面区块标题"),
        "font-size-xl": StyleToken("24px", "页面标题"),
        "font-size-2xl": StyleToken("28px", "品牌标题"),
        "font-size-display": StyleToken("32px", "数据强调"),
        "font-weight-regular": StyleToken("400", "普通正文"),
        "font-weight-medium": StyleToken("500", "按钮与标签"),
        "font-weight-bold": StyleToken("700", "强标题"),
        "line-height-tight": StyleToken("1.25", "标题行高"),
        "line-height-base": StyleToken("1.5", "正文行高"),
        "line-height-relaxed": StyleToken("1.7", "长文说明行高"),
    },
    "间距": {
        "space-1": StyleToken("4px", "最小间距"),
        "space-2": StyleToken("8px", "紧凑间距"),
        "space-3": StyleToken("12px", "控件组间距"),
        "space-4": StyleToken("16px", "默认间距"),
        "space-5": StyleToken("20px", "页面水平边距"),
        "space-6": StyleToken("24px", "标准卡片边距"),
        "space-8": StyleToken("32px", "大区块间距"),
        "space-10": StyleToken("40px", "宽松区块间距"),
        "space-12": StyleToken("48px", "页面大留白"),
    },
    "圆角": {
        "radius-xs": StyleToken("7px", "步骤与小图标"),
        "radius-sm": StyleToken("10px", "按钮与输入框"),
        "radius-md": StyleToken("12px", "列表与状态卡"),
        "radius-lg": StyleToken("16px", "面板"),
        "radius-xl": StyleToken("18px", "大型面板"),
        "radius-2xl": StyleToken("22px", "对话框"),
        "radius-round": StyleToken("999px", "胶囊与圆形"),
    },
    "阴影": {
        "shadow-sm": StyleToken("0 3px 10px rgba(35, 65, 120, 0.07)", "控件轻阴影"),
        "shadow-card": StyleToken("0 8px 22px rgba(43, 70, 122, 0.06)", "面板阴影"),
        "shadow-hover": StyleToken("0 12px 30px rgba(43, 70, 122, 0.09)", "悬停阴影"),
        "shadow-dialog": StyleToken("0 14px 40px rgba(36, 61, 110, 0.10)", "悬浮层阴影"),
    },
    "布局": {
        "layout-content-max": StyleToken("none", "全屏工作区"),
        "layout-sidebar-width": StyleToken("220px", "桌面侧栏宽度"),
        "layout-sidebar-compact": StyleToken("76px", "紧凑侧栏宽度"),
        "layout-topbar-height": StyleToken("64px", "顶部栏高度"),
        "layout-page-inline": StyleToken("20px", "页面水平内边距"),
        "layout-page-block": StyleToken("16px", "页面顶部内边距"),
        "layout-page-block-end": StyleToken("18px", "页面底部内边距"),
        "layout-page-gap": StyleToken("12px", "页面纵向区块间距"),
        "layout-auth-card": StyleToken("460px", "登录卡片宽度"),
        "layout-onboarding-max": StyleToken("940px", "向导宽度"),
        "layout-form-max": StyleToken("700px", "长表单宽度"),
        "layout-dialog-sm": StyleToken("620px", "小弹框"),
        "layout-dialog-md": StyleToken("760px", "表单弹框"),
        "layout-dialog-lg": StyleToken("900px", "复杂配置弹框"),
        "layout-dialog-xl": StyleToken("1120px", "审核工作台弹框"),
        "control-height-sm": StyleToken("34px", "紧凑控件"),
        "control-height": StyleToken("38px", "标准控件"),
        "control-height-button": StyleToken("39px", "主按钮与分段项高度"),
        "control-height-field": StyleToken("39px", "外置标签表单控件高度"),
        "segment-height": StyleToken("47px", "分段控件容器高度"),
        "field-gap": StyleToken("7px", "外置标签与输入控件间距"),
        "review-gap": StyleToken("14px", "全文审核栏间距"),
        "task-row-height": StyleToken("68px", "任务队列固定行高"),
        "topic-row-height": StyleToken("52px", "选题列表固定行高"),
        "topic-source-column": StyleToken("136px", "选题列表来源列宽度"),
        "topic-actions-column": StyleToken("260px", "选题列表操作列宽度"),
        "topic-nav-width": StyleToken("300px", "选题中心一级页签宽度"),
        "topic-heading-action-width": StyleToken("135px", "选题页头主操作宽度"),
        "media-thumb-width": StyleToken("128px", "文章缩略图宽度"),
        "media-thumb-height": StyleToken("80px", "文章缩略图高度"),
        "media-preview-height": StyleToken("130px", "内容图片预览高度"),
        "media-option-height": StyleToken("120px", "图片选项高度"),
        "breakpoint-sidebar": StyleToken("1100px", "侧栏收窄断点"),
        "breakpoint-stack": StyleToken("860px", "双栏堆叠断点"),
        "breakpoint-mobile": StyleToken("600px", "移动导航断点"),
        "motion-fast": StyleToken("80ms", "即时交互与弹层关闭"),
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
        "  --q-primary: var(--ui-color-brand);",
        "  --q-dark: var(--ui-color-text-primary);",
        "  --warn: var(--ui-color-warning);",
        "  --warn-soft: var(--ui-color-warning-soft);",
        "  --danger: var(--ui-color-danger);",
        "  --danger-soft: var(--ui-color-danger-soft);",
        "  --shadow: var(--ui-shadow-card);",
        "  --shadow-hover: var(--ui-shadow-hover);",
        "  --radius: var(--ui-radius-lg);",
    ]
    return ":root {\n" + "\n".join([*declarations, *aliases]) + "\n}\n"


def style_spec_markdown() -> str:
    lines = [
        "# UI 样式规范表",
        "",
        "本表由 `app/ui/style_tokens.py` 的 `UI_STYLE_SPEC` 生成。业务页面应使用公共组件类和 `var(--ui-*)`，不得重复硬编码颜色、字号、间距、圆角或阴影。",
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
