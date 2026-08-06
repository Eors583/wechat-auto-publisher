# UI 样式规范表

界面的颜色、字体、字号、间距、圆角、阴影、布局宽度和媒体尺寸统一由 [`app/ui/style_tokens.py`](../app/ui/style_tokens.py) 中的 `UI_STYLE_SPEC` 维护。该表是唯一数值来源，运行时会自动生成 `--ui-*` CSS 变量；组件和页面只引用变量，不重复填写视觉常量。

## 分类与命名

| 分类 | CSS 变量前缀 | 使用范围 | 示例 |
|---|---|---|---|
| 颜色 | `--ui-color-*` | 背景、文字、边框、品牌及状态色 | `var(--ui-color-brand)` |
| 字体 | `--ui-font-*` | 字体族、字号、字重和行高 | `var(--ui-font-size-base)` |
| 间距 | `--ui-space-*` | padding、margin 和 gap | `var(--ui-space-4)` |
| 圆角 | `--ui-radius-*` | 按钮、输入框、卡片和弹框 | `var(--ui-radius-xl)` |
| 阴影 | `--ui-shadow-*` | 卡片、悬停和弹框层级 | `var(--ui-shadow-card)` |
| 布局 | `--ui-layout-*` | 内容、表单和弹框最大宽度 | `var(--ui-layout-dialog-lg)` |
| 组件尺寸 | `--ui-control-*`、`--ui-media-*` | 控件高度和图片预览尺寸 | `var(--ui-media-thumb-width)` |

## 编码约束

1. 新增或调整视觉数值时，先更新 `UI_STYLE_SPEC`，说明该值的语义和用途。
2. CSS、页面和组件代码只能引用 `var(--ui-*)`；不得在组件文件里新增十六进制色值、`rgb/rgba`、独立字号、间距、圆角或阴影常量。
3. 同一语义复用同一个 token，不为单个页面创建近似但不同的数值。
4. 可复用的组合样式应放入 `app/ui/styles.py` 并使用语义化类名，例如 `ui-media-thumb`，页面不拼接重复的内联样式。
5. 响应式 `clamp()`、百分比、视口单位和内容比例可以留在布局规则中，但其上下限应优先引用规范变量。
6. 业务数据中的颜色值（例如用户可编辑的公众号排版颜色）不是界面设计样式，不受本规范限制。

## 使用示例

```css
.feature-card {
  padding: var(--ui-space-6);
  color: var(--ui-color-text-primary);
  background: var(--ui-color-surface);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-xl);
  box-shadow: var(--ui-shadow-card);
}
```

不要这样编写：

```css
.feature-card {
  padding: 23px;
  color: #16221e;
  border-radius: 15px;
}
```

## 兼容策略

历史样式使用的 `--accent`、`--muted`、`--line` 等变量已由规范表生成的变量提供别名，因此可以渐进迁移而不改变现有视觉结果。所有新代码必须直接使用 `--ui-*` 命名；修改旧组件时，应顺带迁移所涉及的硬编码。
