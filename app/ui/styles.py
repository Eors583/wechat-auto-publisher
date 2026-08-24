"""Desktop UI visual theme."""

from app.ui.style_tokens import style_css_variables

HEAD_HTML = """
<meta name="description" content="公众号智能运营助手：从选题、AI 创作和评审到微信公众号草稿，一站式完成内容生产。">
<script>document.documentElement.lang = 'zh-CN';</script>
"""

APP_CSS = style_css_variables() + """
html, body {
  margin: 0;
  min-width: 320px;
}

*, *::before, *::after { box-sizing: border-box; }

body, .q-page, .nicegui-content {
  background:
    radial-gradient(900px 380px at 8% -12%, rgba(16,163,127,0.12) 0%, transparent 62%),
    radial-gradient(760px 340px at 100% 0%, rgba(92,138,123,0.09) 0%, transparent 58%),
    linear-gradient(180deg, var(--bg1) 0%, var(--bg0) 100%) !important;
  color: var(--ink);
  font-family: var(--ui-font-sans);
  min-height: 100vh;
}

.nicegui-content {
  display: block !important;
  padding: 0 !important;
}

.ui-media-thumb {
  width: var(--ui-media-thumb-width) !important;
  height: var(--ui-media-thumb-height) !important;
  border-radius: var(--ui-radius-md) !important;
}
.ui-media-preview {
  height: var(--ui-media-preview-height) !important;
  background: var(--ui-color-surface-muted) !important;
}
.ui-media-option {
  height: var(--ui-media-option-height) !important;
  background: var(--ui-color-surface-muted) !important;
}
.ui-info-outline {
  border: 1px solid var(--ui-color-info-border) !important;
  box-shadow: none !important;
}
.ui-gap-zero { gap: 0 !important; }

.feishu-hero {
  background: linear-gradient(135deg, var(--ui-color-surface) 0%, var(--ui-color-info-soft) 100%);
}
.feishu-heading-row,
.feishu-actions {
  gap: var(--ui-space-3);
  min-width: 0;
}
.feishu-actions {
  display: flex;
  flex-wrap: wrap;
  width: 100%;
}
.feishu-config-grid,
.feishu-status-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-3);
  width: 100%;
  min-width: 0;
}
.feishu-status-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.feishu-status-item {
  min-width: 0;
  padding: var(--ui-space-3);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-bg-subtle);
}
.feishu-break-anywhere,
.feishu-break-anywhere * {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.feishu-config-grid :is(.q-field, .q-field__inner, .q-field__control, .q-field__native),
.feishu-actions .q-btn {
  min-width: 0;
  max-width: 100%;
}

@media (max-width: 860px) {
  .feishu-status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 600px) {
  .feishu-config-grid,
  .feishu-status-grid { grid-template-columns: minmax(0, 1fr); }
  .feishu-heading-row { align-items: flex-start; }
  .feishu-actions .q-btn { width: 100%; }
}

.shell {
  width: min(100%, var(--ui-layout-content-max));
  margin: 0 auto;
  padding: 24px 28px 52px;
}

.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 28px;
  margin-bottom: 16px;
  padding: 22px 24px;
  border-radius: 20px;
  background: rgba(255,255,255,0.78);
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  overflow: hidden;
  position: relative;
}
.hero::after {
  content: "";
  position: absolute;
  right: -70px;
  top: -90px;
  width: 250px;
  height: 250px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(16,163,127,0.10), transparent 68%);
  pointer-events: none;
}
.eyebrow {
  color: var(--accent);
  font-size: 10px;
  line-height: 1;
  letter-spacing: .18em;
  font-weight: 900;
  margin-bottom: 7px;
}
.brand {
  font-size: 28px;
  font-weight: 900;
  letter-spacing: -0.02em;
  line-height: 1.15;
  color: var(--ink);
}
.brand-sub {
  color: var(--muted);
  font-size: 13px;
  margin-top: 7px;
  line-height: 1.5;
  max-width: 560px;
}
.hero-badge {
  position: relative;
  z-index: 1;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 190px;
  padding: 11px 14px;
  border-radius: 14px;
  background: rgba(230,246,241,0.82);
  color: var(--accent-dark);
  border: 1px solid rgba(8,122,99,0.14);
}
.hero-badge-dot {
  width: 9px;
  height: 9px;
  flex: 0 0 auto;
  border-radius: 50%;
  background: var(--accent-2);
  box-shadow: 0 0 0 5px rgba(16,163,127,0.11);
}
.hero-badge b, .hero-badge small { display: block; }
.hero-badge b { font-size: 12px; line-height: 1.3; }
.hero-badge small { margin-top: 2px; font-size: 10px; color: var(--muted); }
.flow-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  margin-right: 12px;
}
.flow-chip span.dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--accent-2);
  opacity: 0.75;
}

.workspace-tabs {
  min-height: 46px !important;
  padding: 4px !important;
  margin-bottom: 14px !important;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255,255,255,0.72) !important;
  box-shadow: 0 1px 2px rgba(16,34,27,.03);
}
.q-tab {
  text-transform: none !important;
  font-weight: 700 !important;
  min-height: 36px !important;
  border-radius: 10px !important;
  color: var(--muted) !important;
  padding: 0 22px !important;
}
.q-tab--active {
  color: var(--accent-dark) !important;
  background: var(--panel-solid) !important;
  box-shadow: 0 2px 9px rgba(18,46,36,.08);
}
.q-tab__indicator { display: none !important; }
.q-tab-panels { background: transparent !important; }
.q-tab-panel { padding: 0 !important; }

.workflow-guide {
  clear: both;
  width: 100%;
  padding: 15px 18px;
  margin-bottom: 16px;
  border: 1px solid rgba(8,122,99,.16);
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(255,255,255,.98), rgba(230,246,241,.72));
  box-shadow: 0 8px 24px rgba(16,34,27,.05);
}
.workflow-guide__header { gap: 14px; margin-bottom: 12px; }
.workflow-guide__title { color: var(--ink); font-size: 14px; font-weight: 800; }
.workflow-guide__note {
  color: var(--accent-dark);
  font-size: 12px;
  font-weight: 700;
  text-align: right;
}
.workflow-steps {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}
.workflow-step {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255,255,255,.84);
}
.workflow-step__number {
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--muted);
  background: #edf2f0;
  font-size: 11px;
  font-weight: 900;
}
.workflow-step__copy { min-width: 0; }
.workflow-step__label {
  overflow: hidden;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.workflow-step__hint {
  overflow: hidden;
  color: #8a9691;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.workflow-step--done { border-color: rgba(16,163,127,.18); background: rgba(230,246,241,.62); }
.workflow-step--done .workflow-step__number { color: #fff; background: var(--accent-2); }
.workflow-step--done .workflow-step__label { color: var(--accent-dark); }
.workflow-step--active {
  border-color: rgba(8,122,99,.46);
  background: #fff;
  box-shadow: 0 6px 16px rgba(8,122,99,.10);
  transform: translateY(-1px);
}
.workflow-step--active .workflow-step__number { color: #fff; background: var(--accent); }
.workflow-step--active .workflow-step__label { color: var(--ink); }
.workflow-guide--compact { margin-bottom: 14px; padding: 12px 14px; }
.workflow-guide--compact .workflow-guide__header { margin-bottom: 9px; }
.workflow-guide--compact .workflow-step { padding: 7px 8px; }

.wizard-panel {
  display: block !important;
  overflow: visible;
}
.wizard-layout::after {
  display: block;
  clear: both;
  content: "";
}
.wizard-layout > .topic-card {
  float: left;
  width: calc(66% - 8px);
}
.wizard-layout > .source-card,
.wizard-layout > .action-card {
  float: right;
  clear: right;
  width: calc(34% - 8px);
}
.wizard-layout > .source-card { margin-bottom: 16px; }
.wizard-layout > .review-section {
  clear: both;
  padding-top: 16px;
}

.card {
  background: var(--panel-solid);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 20px;
  margin: 0;
  box-shadow: var(--shadow);
  min-width: 0;
}
.card:hover {
  border-color: var(--line-strong);
}
.action-card {
  background: linear-gradient(145deg, rgba(255,255,255,.98), rgba(240,249,246,.96));
}

.section-title {
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 8px;
}
.step-title {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 16px;
  font-weight: 800;
  color: var(--ink);
  margin-bottom: 7px;
}
.step-num {
  width: 26px; height: 26px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--accent);
  color: #fff;
  font-size: 12px;
  font-weight: 800;
  flex-shrink: 0;
  box-shadow: 0 4px 12px rgba(8,122,99,.18);
}
.muted { color: var(--muted); font-size: 13px; line-height: 1.55; }

.topic-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  margin-bottom: 8px;
  background: var(--panel-solid);
}
.topic-item:hover {
  border-color: rgba(8,122,99,0.34);
  background: #fbfffd;
}
.article-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 12px;
  margin: 6px 0;
  cursor: pointer;
  background: #f7faf9;
}
.article-item:hover {
  border-color: rgba(12,92,75,0.4);
  background: var(--accent-soft);
}

.preview-frame {
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fff;
  padding: 18px 16px;
  max-height: 560px;
  overflow: auto;
}
.preview-frame .article-preview {
  width: min(100%, 677px);
  margin-left: auto !important;
  margin-right: auto !important;
}
.preview-frame .wechat-preview-iframe {
  display: block;
  width: min(100%, 677px);
  height: 520px;
  margin-left: auto !important;
  margin-right: auto !important;
  border: 0;
  background: #fff;
}
.review-phone-preview {
  box-sizing: border-box;
  width: min(100%, 423px) !important;
  max-width: 423px;
  max-height: 760px;
  margin-left: auto;
  margin-right: auto;
  padding: 16px;
  border: 8px solid #17211f;
  border-radius: 30px;
  background: #eef2f1;
  box-shadow: 0 18px 45px rgba(15, 43, 38, 0.16);
}
.review-phone-preview .article-preview,
.review-phone-preview .wechat-preview-iframe {
  display: block;
  width: 375px;
  max-width: 100%;
  margin-left: auto !important;
  margin-right: auto !important;
  border-radius: 16px;
  background: #fff;
}
.review-phone-preview .wechat-preview-iframe {
  height: 667px;
}
@media (max-width: 460px) {
  .review-phone-preview {
    padding: 8px;
    border-width: 5px;
    border-radius: 22px;
  }
}
.request-loading-dialog .q-dialog__backdrop {
  display: none;
}
.request-loading-card {
  display: grid;
  grid-template-columns: 32px minmax(0, 1fr);
  align-items: center;
  min-width: 300px;
  max-width: min(88vw, 430px);
  margin-top: var(--ui-space-3);
  padding: var(--ui-space-3) var(--ui-space-4);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  box-shadow: var(--ui-shadow-dialog);
}
.request-loading-message {
  grid-column: 2;
  color: var(--ink);
  font-size: var(--ui-font-size-base);
  font-weight: var(--ui-font-weight-medium);
  line-height: var(--ui-line-height-base);
}
.request-loading-card > .muted,
.request-loading-card > .q-btn { grid-column: 2; }

.ops-interaction-feedback {
  position: fixed;
  z-index: 7000;
  top: calc(var(--ui-layout-topbar-height) + var(--ui-space-3));
  right: var(--ui-space-5);
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr);
  align-items: center;
  gap: var(--ui-space-3);
  width: min(340px, calc(100vw - 40px));
  padding: 10px var(--ui-space-3);
  border: 1px solid var(--ui-color-info-border);
  border-radius: var(--ui-radius-md);
  color: var(--ui-color-text-primary);
  background: var(--ui-color-surface-glass);
  box-shadow: var(--ui-shadow-dialog);
  pointer-events: none;
}
.ops-interaction-feedback[hidden] { display: none !important; }
.ops-interaction-marker {
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border-radius: var(--ui-radius-round);
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
  font-size: var(--ui-font-size-xs);
  font-weight: var(--ui-font-weight-bold);
  letter-spacing: 1px;
}
.ops-interaction-feedback-copy { display: grid; gap: 2px; min-width: 0; }
.ops-interaction-feedback-copy strong { font-size: var(--ui-font-size-sm); font-weight: var(--ui-font-weight-medium); }
.ops-interaction-feedback-copy span { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }
.ops-page-loading-overlay {
  position: fixed;
  z-index: 2100;
  top: var(--ui-layout-topbar-height);
  right: 0;
  bottom: 0;
  left: var(--ui-layout-sidebar-width);
  display: grid;
  min-width: 0;
  min-height: 0;
  place-items: center;
  overflow: hidden;
  background: color-mix(in srgb, var(--ui-color-surface) 72%, transparent);
}
.preview-article-title {
  width: min(100%, 677px);
  margin-left: auto;
  margin-right: auto;
  line-height: 1.35;
}

.status-pill {
  display: inline-block;
  padding: 4px 11px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  border: 1px solid rgba(12,92,75,0.12);
}
.status-pill.failed {
  background: var(--danger-soft);
  color: var(--danger);
  border-color: rgba(157,36,48,0.12);
}
.status-pill.ready_for_review {
  background: var(--warn-soft);
  color: var(--warn);
  border-color: rgba(138,90,18,0.14);
}

.rewrite-progress {
  margin: 2px 0 12px;
  padding: 13px 15px 12px;
  border: 1px solid rgba(12, 92, 75, 0.12);
  border-radius: 13px;
  background: linear-gradient(135deg, rgba(230, 246, 241, 0.82), rgba(255,255,255,0.96));
}
.progress-heading { margin-bottom: 8px; }
.progress-caption {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.progress-track-wrap {
  position: relative;
  height: 30px;
}
.progress-stage {
  position: absolute;
  inset: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  color: var(--ink);
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.2px;
  text-shadow: 0 1px 2px rgba(255,255,255,0.95);
}
.progress-percent {
  color: var(--accent);
  font-size: 13px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}
.progress-bar {
  height: 30px;
  overflow: hidden;
}
.progress-hint {
  margin-top: 8px;
  color: var(--muted);
  font-size: 12px;
}
.progress-elapsed {
  color: var(--muted);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.job-row {
  border-radius: 12px;
}
.job-row:hover { background: rgba(12,92,75,0.05); }

.review-action-bar {
  position: sticky;
  bottom: 0;
  z-index: 3;
  padding: 12px 0 4px;
  background: linear-gradient(180deg, rgba(255,255,255,0), #fff 22%);
}
.review-action-spacer {
  height: calc(88px + env(safe-area-inset-bottom));
  min-height: calc(88px + env(safe-area-inset-bottom));
}
.editorial-review-result-anchor,
.editorial-review-settings-anchor {
  scroll-margin-top: 20px;
  scroll-margin-bottom: calc(104px + env(safe-area-inset-bottom));
}

/* Quasar review workbench ------------------------------------------------- */
.review-workbench {
  width: min(1320px, calc(100vw - 40px)) !important;
  max-width: 1320px !important;
  max-height: 94vh !important;
  padding: 0 24px 28px !important;
  overflow-y: auto;
  border: 1px solid rgba(213, 223, 218, .92);
  border-radius: 24px !important;
  background: #f7faf9 !important;
  box-shadow: 0 28px 80px rgba(18, 42, 34, .20) !important;
}
.review-workbench__header {
  position: sticky;
  top: 0;
  z-index: 30;
  margin: 0 -24px 18px;
  padding: 18px 24px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, .94);
  box-shadow: 0 8px 24px rgba(16, 34, 27, .06);
}
.review-workbench__title-row { min-width: 0; }
.review-workbench__icon {
  color: #fff !important;
  background: linear-gradient(145deg, var(--accent), var(--accent-2)) !important;
  box-shadow: 0 8px 20px rgba(8, 122, 99, .22);
}
.review-quick-summary,
.review-surface,
.review-summary-card,
.review-comparison-card,
.review-progress-card,
.review-choice-card,
.review-risk-card,
.review-issue-card,
.review-score-card {
  border: 1px solid var(--line) !important;
  box-shadow: 0 8px 24px rgba(16, 34, 27, .045) !important;
}
.review-quick-summary {
  border-radius: 18px !important;
  background: #fff !important;
}
.review-jury {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: #fff;
  box-shadow: var(--shadow);
}
.review-jury > .q-expansion-item__container > .q-item {
  min-height: 64px;
  padding: 0 18px;
  color: var(--ink) !important;
  background: linear-gradient(135deg, #fff, #f0f8f5);
}
.review-jury > .q-expansion-item__container > .q-item .q-item__label {
  font-size: 16px;
  font-weight: 800;
}
.review-jury > .q-expansion-item__container > .q-expansion-item__content {
  padding: 0 18px 18px;
}
.review-jury-intro {
  margin-top: 4px;
  padding: 14px 16px;
  border: 1px solid rgba(8, 122, 99, .14) !important;
  border-radius: 14px !important;
  background: linear-gradient(135deg, #f0faf7, #fff) !important;
  box-shadow: none !important;
}
.review-jury-intro__icon {
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  border-radius: 12px;
  color: var(--accent-dark);
  background: var(--accent-soft);
}
.review-settings {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #fbfdfc;
}
.review-settings > .q-expansion-item__container > .q-item {
  min-height: 50px;
  padding: 0 14px;
}
.review-settings > .q-expansion-item__container > .q-expansion-item__content {
  padding: 0 14px 14px;
}
.review-surface {
  padding: 20px !important;
  border-radius: 18px !important;
  background: #fff !important;
}
.review-summary-card {
  padding: 16px 18px !important;
  border-radius: 16px !important;
  border-color: rgba(8, 122, 99, .18) !important;
  background: linear-gradient(135deg, #fff, #f2faf7) !important;
}
.review-result-header {
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--line);
}
.review-score-grid {
  display: grid !important;
  grid-template-columns: repeat(5, minmax(0, 1fr)) !important;
  gap: 10px !important;
}
.review-score-card {
  min-width: 0;
  padding: 14px !important;
  border-radius: 14px !important;
  background: linear-gradient(160deg, #fff, #f6faf8) !important;
}
.review-score-card .text-h6 {
  color: var(--accent-dark) !important;
  font-size: 24px;
  font-weight: 900;
}
.review-issue-card {
  padding: 16px !important;
  border-radius: 15px !important;
  background: #fff !important;
}
.review-status-progress { gap: 10px !important; }
.review-status-progress__percent {
  min-width: 48px;
  color: var(--accent-dark) !important;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.review-status-progress__bar { overflow: hidden; }
.review-issue-card:hover {
  border-color: rgba(8, 122, 99, .28) !important;
}
.review-issue-card--safety {
  border-left: 4px solid #f59e0b !important;
  background: linear-gradient(90deg, #fffaf0, #fff 18%) !important;
}
.review-comparison {
  padding: 18px;
  border: 1px solid var(--line);
  border-radius: 18px;
  background: #f8fbfa;
}
.review-comparison-card {
  height: 100%;
  padding: 18px !important;
  border-radius: 16px !important;
  background: #fff !important;
}
.review-comparison-card--candidate {
  border-color: rgba(8, 122, 99, .26) !important;
  background: linear-gradient(180deg, #fff, #f7fcfa) !important;
}
.review-comparison-body {
  max-height: 560px;
  padding: 4px 8px 4px 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: thin;
  scrollbar-color: #c8d7d1 transparent;
}
.review-risk-card {
  padding: 16px !important;
  border-color: #fdba74 !important;
  border-radius: 15px !important;
  background: #fff7ed !important;
}
.review-choice-card {
  padding: 16px 18px !important;
  border-color: rgba(8, 122, 99, .22) !important;
  border-radius: 15px !important;
  background: #f0faf7 !important;
}
.review-progress-card {
  padding: 15px 16px !important;
  border-radius: 15px !important;
  box-shadow: none !important;
}
.review-progress-card--running { border-color: #bfdbfe !important; background: #eff6ff !important; }
.review-progress-card--completed { border-color: #bbf7d0 !important; background: #f0fdf4 !important; }
.review-progress-card--failed { border-color: #fecaca !important; background: #fef2f2 !important; }
.review-body-editor .q-field__control { min-height: 440px; }
.review-body-editor textarea.q-field__native {
  min-height: 400px !important;
  line-height: 1.8 !important;
  resize: vertical !important;
}

@media (max-width: 980px) {
  .review-workbench {
    width: calc(100vw - 20px) !important;
    padding: 0 16px 22px !important;
    border-radius: 18px !important;
  }
  .review-workbench__header { margin: 0 -16px 14px; padding: 14px 16px; }
  .review-score-grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; }
}

@media (max-width: 620px) {
  .review-workbench { width: 100vw !important; max-height: 100vh !important; border-radius: 0 !important; }
  .review-workbench__header { align-items: flex-start; }
  .review-jury > .q-expansion-item__container > .q-expansion-item__content { padding: 0 12px 12px; }
  .review-score-grid { grid-template-columns: 1fr !important; }
  .review-comparison { padding: 12px; }
  .review-choice-card .q-btn { width: 100%; }
}

.background-activity-dock {
  position: fixed;
  top: 92px;
  right: 18px;
  z-index: 2200;
  width: min(340px, calc(100vw - 28px));
  max-height: calc(100vh - 116px);
  padding: 12px;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: rgba(255,255,255,.97);
  box-shadow: 0 18px 50px rgba(16,34,27,.16);
}
.background-activity-card {
  border: 1px solid var(--line);
  box-shadow: none;
}
.background-activity-progress-label {
  color: #fff;
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  text-shadow: 0 1px 3px rgba(0, 54, 45, .9), 0 0 1px rgba(0, 54, 45, 1);
}

@media (max-width: 760px) {
  .background-activity-dock {
    top: auto;
    right: 10px;
    bottom: 10px;
    left: 10px;
    width: auto;
    max-height: 42vh;
  }
}

.q-field--outlined .q-field__control {
  border-radius: 11px !important;
  background: rgba(255,255,255,0.96);
}
.q-field--outlined.q-field--focused .q-field__control::after {
  border-color: var(--accent) !important;
  border-width: 1px !important;
}
.q-toggle, .q-option-group { max-width: 100%; }
.q-btn-toggle {
  width: 100%;
  padding: 3px;
  border-radius: 12px !important;
  background: #f1f5f3;
}
.q-btn-toggle .q-btn {
  min-height: 36px;
  box-shadow: none !important;
}
.source-mode-toggle {
  display: grid !important;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding: 0;
  margin-bottom: 7px;
  background: transparent;
}
.source-mode-toggle .q-btn {
  width: 100%;
  min-height: 46px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: 10px !important;
  background: #f7faf9;
  line-height: 1.25;
}
.source-mode-toggle .q-btn.bg-teal-8 {
  border-color: var(--accent);
  background: var(--accent) !important;
}
.source-mode-hint {
  min-height: 20px;
  margin: 0 2px 10px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}
.article-body-input,
.article-body-input .q-field__inner,
.article-body-input .q-field__control,
.article-body-input .q-field__control-container,
.article-body-input textarea.q-field__native {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}
.article-body-input textarea.q-field__native {
  overflow-y: auto !important;
  resize: none !important;
}
.body-input-tools {
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 4px 0 8px;
}
.fullscreen-editor-dialog .q-dialog__inner {
  padding: 0;
}
.fullscreen-editor-card {
  display: flex !important;
  flex-direction: column;
  width: 100vw !important;
  max-width: none !important;
  height: 100vh !important;
  padding: 22px 28px 24px;
  border-radius: 0 !important;
  overflow: hidden;
}
.fullscreen-editor-header,
.fullscreen-editor-actions {
  width: 100%;
  align-items: center;
}
.fullscreen-body-textarea {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
  margin: 16px 0;
}
.fullscreen-body-textarea .q-field__control,
.fullscreen-body-textarea .q-field__native {
  height: 100% !important;
  min-height: 360px !important;
}
.fullscreen-body-textarea textarea.q-field__native {
  resize: none !important;
}
.fullscreen-editor-actions {
  justify-content: flex-end;
  gap: 8px;
}
.ops-user-models-body {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  padding-top: var(--ui-space-3);
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.ops-user-models-body > .nicegui-column,
.ops-user-models-body .card,
.ops-user-models-body .card > .nicegui-row,
.ops-user-models-body .card > .nicegui-column {
  min-width: 0;
  max-width: 100%;
}
.ops-user-models-body .card .q-label,
.ops-user-models-body .card .muted {
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.q-btn {
  border-radius: 10px !important;
  font-weight: 700 !important;
  letter-spacing: 0 !important;
}
.action-card > .q-btn {
  width: 100%;
  min-height: 44px;
  background: var(--accent) !important;
  box-shadow: 0 8px 18px rgba(8,122,99,.18) !important;
}

@media (max-width: 900px) {
  .shell { padding: 18px 16px 40px; }
  .wizard-layout > .topic-card,
  .wizard-layout > .source-card,
  .wizard-layout > .action-card {
    float: none;
    clear: none;
    width: 100%;
  }
  .wizard-layout > .topic-card,
  .wizard-layout > .source-card { margin-bottom: 16px; }
  .wizard-layout > .review-section { padding-top: 0; }
  .workflow-steps { grid-template-columns: repeat(5, minmax(112px, 1fr)); overflow-x: auto; padding-bottom: 4px; }
}

@media (max-width: 620px) {
  .shell { padding: 12px 10px 32px; }
  .hero { align-items: flex-start; padding: 18px; }
  .hero-badge { display: none; }
  .brand { font-size: 24px; }
  .flow-chip { display: none; }
  .workspace-tabs { margin-bottom: 10px !important; }
  .q-tab { flex: 1 1 0; padding: 0 8px !important; }
  .card { padding: 16px; border-radius: 14px; }
  .workflow-guide__header { align-items: flex-start; flex-direction: column; gap: 3px; }
  .workflow-guide__note { text-align: left; }
}

@media (max-width: 420px) {
  .source-mode-toggle { grid-template-columns: 1fr; }
  .body-input-tools { align-items: flex-start; }
  .fullscreen-editor-card { padding: 16px 14px; }
}
"""


APP_CSS += """
/* Operations workbench shell ------------------------------------------------
   This layer implements the approved full-screen information architecture.
   Business panels keep their existing service bindings and are mounted lazily. */
html,
body,
#q-app,
.nicegui-content {
  width: 100%;
  max-width: 100vw;
  height: 100vh;
  height: 100dvh;
  max-height: 100vh;
  max-height: 100dvh;
  overflow: hidden !important;
}

body,
.q-page,
.nicegui-content {
  --q-primary: var(--ui-color-brand) !important;
  --q-dark: var(--ui-color-text-primary) !important;
  background: var(--ui-color-bg-canvas) !important;
  color: var(--ui-color-text-primary);
  font-family: var(--ui-font-sans);
  font-size: var(--ui-font-size-base);
  line-height: var(--ui-line-height-base);
}

.ops-workbench-shell {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  margin: 0;
  padding: 0 0 0 var(--ui-layout-sidebar-width);
  overflow: clip !important;
  background: var(--ui-color-bg-canvas);
}

.ops-sidebar-brand {
  position: absolute;
  z-index: 10;
  top: 0;
  left: 0;
  display: flex;
  align-items: center;
  gap: var(--ui-space-3);
  width: var(--ui-layout-sidebar-width);
  height: var(--ui-layout-topbar-height);
  padding: 0 var(--ui-space-6);
  border-right: 1px solid var(--ui-color-border);
  background: var(--ui-color-surface);
}

.ops-sidebar-brand-mark {
  display: grid !important;
  place-items: center;
  color: var(--ui-color-surface) !important;
  background: linear-gradient(145deg, var(--ui-color-brand), var(--ui-color-brand-hover)) !important;
  border-radius: var(--ui-radius-sm) !important;
  box-shadow: var(--ui-shadow-sm);
}

.ops-sidebar-brand-copy,
.ops-sidebar-health-copy,
.ops-sidebar-profile-copy {
  min-width: 0;
  gap: 0 !important;
}

.ops-sidebar-brand-copy .q-field,
.ops-sidebar-brand-copy .q-label { min-width: 0; }
.ops-sidebar-brand-copy > :first-child {
  color: var(--ui-color-text-sidebar);
  font-weight: var(--ui-font-weight-medium);
}
.ops-sidebar-brand-copy > :last-child {
  color: var(--ui-color-text-sidebar-muted);
  font-size: var(--ui-font-size-xs);
}

.ops-main-nav {
  position: absolute !important;
  z-index: 9;
  top: var(--ui-layout-topbar-height);
  bottom: 118px;
  left: 0;
  width: var(--ui-layout-sidebar-width) !important;
  min-height: 0 !important;
  height: auto !important;
  max-height: none !important;
  margin: 0 !important;
  padding: var(--ui-space-5) var(--ui-space-4) !important;
  overflow: hidden;
  border: 0 !important;
  border-right: 1px solid var(--ui-color-border) !important;
  border-radius: 0 !important;
  background: var(--ui-color-surface) !important;
  box-shadow: none !important;
}

.ops-main-nav .q-tabs__content {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--ui-space-2);
}

.ops-main-nav .q-tab {
  flex: 0 0 auto;
  justify-content: flex-start;
  width: 100%;
  min-height: var(--ui-control-height) !important;
  padding: 0 var(--ui-space-3) !important;
  color: var(--ui-color-text-sidebar-muted) !important;
  border-radius: var(--ui-radius-sm) !important;
}

.ops-main-nav .q-tab .q-tab__content {
  flex-direction: row;
  justify-content: flex-start;
  gap: var(--ui-space-3);
  min-width: 0;
}

.ops-main-nav .q-tab--active {
  color: var(--ui-color-brand) !important;
  background: var(--ui-color-brand-soft) !important;
  box-shadow: none !important;
}

.ops-sidebar-footer {
  position: absolute;
  z-index: 10;
  bottom: 0;
  left: 0;
  display: grid;
  gap: var(--ui-space-2);
  width: var(--ui-layout-sidebar-width);
  min-height: 118px;
  padding: var(--ui-space-3) var(--ui-space-4);
  border-right: 1px solid var(--ui-color-border);
  background: var(--ui-color-surface);
}

.ops-sidebar-health,
.ops-sidebar-profile,
.ops-safe-mode,
.ops-activity-dock-heading,
.ops-activity-title-row,
.ops-activity-footer {
  align-items: center;
  margin: 0;
}

.ops-sidebar-health {
  gap: var(--ui-space-2);
  padding: var(--ui-space-2) var(--ui-space-3);
  color: var(--ui-color-success);
  border: 1px solid var(--ui-color-info-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-brand-soft);
}
.ops-sidebar-health-copy > :first-child { font-weight: var(--ui-font-weight-medium); }
.ops-sidebar-health-copy > :last-child,
.ops-sidebar-profile-copy > :last-child {
  color: var(--ui-color-text-sidebar-muted);
  font-size: var(--ui-font-size-xs);
}
.ops-sidebar-profile { gap: var(--ui-space-2); padding: 0 var(--ui-space-2); }
.ops-sidebar-profile-copy > :first-child { color: var(--ui-color-text-sidebar); }

.ops-topbar {
  flex: 0 0 var(--ui-layout-topbar-height);
  width: 100%;
  min-height: var(--ui-layout-topbar-height);
  margin: 0;
  padding: var(--ui-space-3) var(--ui-space-6);
  border: 0;
  border-bottom: 1px solid var(--ui-color-border);
  border-radius: 0;
  background: var(--ui-color-surface) !important;
  box-shadow: none;
}
.ops-topbar::after { display: none; }
.ops-topbar-title {
  color: var(--ui-color-text-primary);
  font-size: var(--ui-font-size-md);
  font-weight: var(--ui-font-weight-medium);
}
.ops-topbar-subtitle {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
}
.ops-safe-mode {
  gap: var(--ui-space-2);
  padding: var(--ui-space-2) var(--ui-space-3);
  color: var(--ui-color-brand-dark);
  border: 1px solid var(--ui-color-info-border);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-color-brand-soft);
  white-space: nowrap;
}
.ops-semantic-icon { display: grid !important; place-items: center; }

.ops-config-health {
  flex: 0 0 auto;
  min-height: 0;
  padding: var(--ui-space-1) var(--ui-layout-page-inline) 0;
}
.ops-config-health:empty { display: none; }
.ops-config-health > .q-row { margin: 0 !important; }

.ops-main-panels {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden !important;
}

.ops-workbench-shell .bg-primary {
  background: var(--ui-color-brand) !important;
}
.ops-workbench-shell .text-primary {
  color: var(--ui-color-brand) !important;
}
.ops-workbench-shell .q-btn:not(.q-btn--round) {
  height: var(--ui-control-height-button) !important;
  max-height: var(--ui-control-height-button) !important;
  min-height: var(--ui-control-height-button) !important;
  padding: 8px 13px !important;
  border-radius: var(--ui-radius-sm) !important;
  font-size: var(--ui-font-size-base);
  font-weight: var(--ui-font-weight-regular) !important;
  line-height: 21px;
}
.ops-workbench-shell .q-btn.bg-primary:not(.q-btn--round) {
  border: 1px solid var(--ui-color-brand) !important;
  background: var(--ui-color-brand) !important;
  color: var(--ui-color-surface) !important;
}
.ops-workbench-shell .q-btn:not(.q-btn--round) .q-btn__content {
  line-height: 21px;
}
.ops-main-panels > .q-panel-parent,
.ops-main-panels > .q-panel-parent > .q-panel,
.ops-main-panels > .q-panel-parent > .q-panel > .q-tab-panel {
  height: 100%;
  min-height: 0;
}
.ops-main-panels > .q-panel-parent > .q-panel > .q-tab-panel {
  padding: var(--ui-layout-page-block) var(--ui-layout-page-inline) !important;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.ops-secondary-nav {
  position: sticky;
  z-index: 5;
  top: calc(-1 * var(--ui-layout-page-block));
  margin-bottom: var(--ui-space-3) !important;
  background: var(--ui-color-surface) !important;
}

.ops-page-heading {
  display: grid;
  gap: var(--ui-space-1);
  min-width: 0;
  margin-bottom: var(--ui-space-3);
}
.ops-page-eyebrow {
  color: var(--ui-color-brand);
  font-size: var(--ui-font-size-xs);
  font-weight: var(--ui-font-weight-medium);
  letter-spacing: .09em;
}
.ops-page-title {
  color: var(--ui-color-text-primary);
  font-size: var(--ui-font-size-xl);
  font-weight: var(--ui-font-weight-medium);
  line-height: var(--ui-line-height-tight);
}
.ops-page-description { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-sm); }

.card,
.q-card {
  border-color: var(--ui-color-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-card);
}
.card:hover { border-color: var(--ui-color-border-strong); box-shadow: var(--ui-shadow-hover); }
.q-btn { min-height: var(--ui-control-height-sm); border-radius: var(--ui-radius-sm) !important; font-weight: var(--ui-font-weight-regular) !important; }
.q-field--outlined .q-field__control { min-height: var(--ui-control-height); border-radius: var(--ui-radius-sm) !important; }
.workspace-tabs:not(.ops-main-nav) { border-color: var(--ui-color-border); border-radius: var(--ui-radius-sm); background: var(--ui-color-bg-subtle) !important; box-shadow: none; }
.workspace-tabs:not(.ops-main-nav) .q-tab--active { color: var(--ui-color-brand) !important; background: var(--ui-color-surface) !important; }

.ops-global-activity-dock {
  position: fixed;
  z-index: 2200;
  top: calc(var(--ui-layout-topbar-height) + var(--ui-space-3));
  right: var(--ui-space-4);
  display: grid;
  gap: var(--ui-space-2);
  width: min(340px, calc(100vw - var(--ui-layout-sidebar-width) - var(--ui-space-8)));
  max-height: calc(100vh - var(--ui-layout-topbar-height) - var(--ui-space-6));
  padding: var(--ui-space-3);
  overflow-y: auto;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-color-surface-glass);
  box-shadow: var(--ui-shadow-dialog);
}
.ops-activity-dock-heading { gap: var(--ui-space-2); color: var(--ui-color-brand); font-weight: var(--ui-font-weight-medium); }
.ops-activity-card { display: grid; gap: var(--ui-space-2); padding: var(--ui-space-3); border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-md); background: var(--ui-color-surface); }
.ops-activity-title-row,
.ops-activity-footer { justify-content: space-between; gap: var(--ui-space-2); }
.ops-activity-title-row > :first-child { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: var(--ui-font-weight-medium); }
.ops-activity-percent { color: var(--ui-color-brand); font-variant-numeric: tabular-nums; }
.ops-activity-stage,
.ops-activity-footer { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }
.ops-activity-progress { height: 7px !important; }

.ops-task-list {
  align-content: start !important;
  justify-content: flex-start !important;
}
.ops-task-queue-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 252px;
  align-items: start;
  gap: var(--ui-space-3);
  width: 100%;
}
.ops-task-order-card {
  position: sticky;
  top: var(--ui-space-3);
  display: grid;
  gap: var(--ui-space-3);
  padding: var(--ui-space-4);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-card);
}
.ops-task-order-heading { align-items: center; gap: var(--ui-space-2); color: var(--ui-color-text-primary); font-weight: var(--ui-font-weight-bold); }
.ops-task-order-note { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); line-height: var(--ui-line-height-base); }
.ops-task-order-item { align-items: flex-start; gap: var(--ui-space-2); }
.ops-task-order-number {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  place-items: center;
  border-radius: var(--ui-radius-xs);
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
  font-size: var(--ui-font-size-xs);
  font-weight: var(--ui-font-weight-bold);
}
.ops-task-order-title { color: var(--ui-color-text-primary); font-size: var(--ui-font-size-sm); font-weight: var(--ui-font-weight-medium); }
.ops-task-order-detail { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); line-height: var(--ui-line-height-base); }
.ops-task-row-card {
  display: grid !important;
  grid-template-columns: 38px minmax(220px, 1fr) auto minmax(140px, .55fr) auto;
  align-items: center;
  gap: var(--ui-space-3);
  width: 100%;
  min-height: var(--ui-task-row-height) !important;
  height: var(--ui-task-row-height) !important;
  padding: var(--ui-space-2) var(--ui-space-3) !important;
  overflow: hidden;
  border: 1px solid var(--ui-color-border) !important;
  border-radius: var(--ui-radius-md) !important;
  background: var(--ui-color-surface) !important;
  box-shadow: none !important;
}
.ops-task-row-card:hover { border-color: var(--ui-color-brand) !important; }
.ops-task-row-icon {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: var(--ui-radius-sm);
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
}
.ops-task-row-copy { min-width: 0; gap: 0 !important; }
.ops-task-row-title,
.ops-task-row-meta,
.ops-task-row-state {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-task-row-title { color: var(--ui-color-text-primary); font-weight: var(--ui-font-weight-medium); }
.ops-task-row-meta,
.ops-task-row-state { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }
.ops-task-row-actions { flex-wrap: nowrap; align-items: center; gap: var(--ui-space-1); }
.q-badge.ops-task-row-badge {
  justify-self: start;
  width: max-content;
  max-width: 100%;
  min-height: 24px;
  padding: 4px 8px !important;
  border-radius: var(--ui-radius-round) !important;
}
.q-badge.ops-task-row-badge.bg-orange-7,
.q-badge.ops-task-row-badge.bg-orange-8 {
  color: var(--ui-color-orange) !important;
  background-color: var(--ui-color-orange-soft) !important;
}
.q-badge.ops-task-row-badge.bg-green-7,
.q-badge.ops-task-row-badge.bg-green-8 {
  color: var(--ui-color-success) !important;
  background-color: var(--ui-color-success-soft) !important;
}
.ops-workbench-shell .ops-task-row-card .q-badge.ops-task-row-badge.bg-orange-7,
.ops-workbench-shell .ops-task-row-card .q-badge.ops-task-row-badge.bg-orange-8 {
  color: var(--ui-color-orange) !important;
  background-color: var(--ui-color-orange-soft) !important;
  box-shadow: inset 0 0 0 999px var(--ui-color-orange-soft) !important;
}
.ops-workbench-shell .ops-task-row-card .q-badge.ops-task-row-badge.bg-green-7,
.ops-workbench-shell .ops-task-row-card .q-badge.ops-task-row-badge.bg-green-8 {
  color: var(--ui-color-success) !important;
  background-color: var(--ui-color-success-soft) !important;
  box-shadow: inset 0 0 0 999px var(--ui-color-success-soft) !important;
}
.ops-task-detail-dialog {
  width: min(var(--ui-layout-dialog-lg), calc(100vw - var(--ui-space-8))) !important;
  max-width: var(--ui-layout-dialog-lg) !important;
  max-height: calc(100vh - var(--ui-space-8));
  padding: var(--ui-space-6) !important;
  overflow-y: auto;
  border-radius: var(--ui-radius-2xl) !important;
}
.ops-flex-copy { min-width: 0; flex: 1 1 auto; }
.ops-min-width-zero { min-width: 0 !important; }
.ops-dialog-sm { width: min(var(--ui-layout-dialog-sm), calc(100vw - var(--ui-space-8))) !important; max-width: var(--ui-layout-dialog-sm) !important; }
.ops-dialog-md { width: min(var(--ui-layout-dialog-md), calc(100vw - var(--ui-space-8))) !important; max-width: var(--ui-layout-dialog-md) !important; }
.ops-dialog-lg { width: min(var(--ui-layout-dialog-lg), calc(100vw - var(--ui-space-8))) !important; max-width: var(--ui-layout-dialog-lg) !important; }
.ops-dialog-xl { width: min(var(--ui-layout-dialog-xl), calc(100vw - var(--ui-space-8))) !important; max-width: var(--ui-layout-dialog-xl) !important; }
.ops-dialog-scroll { max-height: calc(100vh - var(--ui-space-8)); overflow-y: auto; }
.ops-preflight-dialog { min-width: 0; overflow-x: hidden; }
.ops-preflight-issue {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--ui-space-3);
  width: 100%;
  min-width: 0;
  max-width: 100%;
  padding: var(--ui-space-3);
  border: 1px solid var(--ui-color-warning);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-warning-soft);
  box-sizing: border-box;
}
.ops-preflight-issue-copy { min-width: 0; max-width: 100%; }
.ops-preflight-reason {
  min-width: 0;
  max-width: 100%;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.ops-dialog-model-editor {
  width: min(var(--ui-layout-dialog-md), calc(100vw - var(--ui-space-8))) !important;
  max-width: var(--ui-layout-dialog-md) !important;
  min-width: 0;
  max-height: calc(100dvh - var(--ui-space-8));
  padding: var(--ui-space-6) !important;
  overflow-x: hidden;
  overflow-y: auto;
}
.ops-dialog-model-editor > *,
.ops-dialog-model-editor .q-field,
.ops-dialog-model-editor .q-field__control,
.ops-dialog-model-editor .q-field__control-container,
.ops-dialog-model-editor .q-field__native {
  min-width: 0;
  max-width: 100%;
}
.ops-dialog-model-editor .q-label,
.ops-dialog-model-editor .muted {
  overflow-wrap: anywhere;
  word-break: break-word;
}
.ops-break-anywhere {
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.ops-wrap-actions {
  min-width: 0;
  flex-wrap: wrap;
  row-gap: var(--ui-space-2);
}
.ops-dialog-model-editor > .q-row {
  min-width: 0;
  flex-wrap: wrap;
  row-gap: var(--ui-space-2);
}
.ops-model-kind-toggle {
  min-height: var(--ui-segment-height);
  padding: var(--ui-space-1);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-color-bg-subtle);
}
.ops-model-kind-toggle .q-btn {
  min-height: var(--ui-control-height-button);
  border-radius: var(--ui-radius-xs) !important;
}
.ops-review-copy { white-space: pre-wrap; line-height: var(--ui-line-height-relaxed); overflow-wrap: anywhere; }
.ops-cover-thumb { width: 180px; aspect-ratio: 2.35 / 1; }
.ops-cover-ratio { aspect-ratio: 2.35 / 1; }
.ops-cover-placeholder { height: 120px; }
.ops-color-swatch {
  display: inline-block;
  width: 24px;
  height: 24px;
  border: 1px solid var(--ui-color-border-strong);
  border-radius: var(--ui-radius-xs);
  background: var(--ui-color-brand);
}
.ops-color-swatch-transparent { border-style: dashed; background: var(--ui-color-surface); }
.ops-config-version-row {
  display: flex;
  align-items: center;
  gap: var(--ui-space-3);
  width: 100%;
  padding: var(--ui-space-3);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-bg-subtle);
}

.ops-account-center {
  display: grid !important;
  grid-template-columns: 260px minmax(0, 1fr);
  align-content: start;
  align-items: start;
  gap: var(--ui-space-3) !important;
}
.ops-account-center-header { grid-column: 1 / -1; }
.ops-account-directory-panel {
  position: sticky;
  top: calc(var(--ui-control-height) + var(--ui-space-6));
  display: grid;
  align-content: start;
  gap: var(--ui-space-2);
  min-width: 0;
  max-height: calc(100vh - var(--ui-layout-topbar-height) - 150px);
  padding: var(--ui-space-3);
  overflow-y: auto;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-card);
}
.ops-account-directory-heading {
  align-items: center;
  gap: var(--ui-space-2);
  color: var(--ui-color-text-primary);
  font-weight: var(--ui-font-weight-medium);
}
.ops-account-directory-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  align-items: center;
  gap: var(--ui-space-2);
  width: 100%;
  padding: var(--ui-space-2);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-surface);
  color: var(--ui-color-text-primary);
  cursor: pointer;
  font: inherit;
  text-align: left;
}
.ops-account-directory-item:hover,
.ops-account-directory-item.is-selected {
  border-color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
}
.ops-account-directory-icon {
  display: grid;
  width: 36px;
  height: 36px;
  place-items: center;
  border-radius: var(--ui-radius-sm);
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
}
.ops-account-directory-copy { min-width: 0; gap: 0 !important; }
.ops-account-directory-copy > :first-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: var(--ui-font-weight-medium); }
.ops-account-directory-copy > :last-child { overflow: hidden; color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); text-overflow: ellipsis; white-space: nowrap; }
.ops-account-capabilities { grid-column: 1 / -1; gap: var(--ui-space-1); padding-top: var(--ui-space-2); border-top: 1px solid var(--ui-color-border); }
.ops-account-config-card { min-width: 0; }
.ops-selected-account-capabilities { gap: var(--ui-space-2) !important; margin-bottom: var(--ui-space-3); }
.ops-selected-account-capability {
  align-items: center;
  gap: var(--ui-space-2);
  width: 100%;
  padding: var(--ui-space-2);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-bg-subtle);
}
.ops-icon-blue {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  border-radius: var(--ui-radius-xs);
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
}

@media (max-width: 1100px) {
  .ops-workbench-shell { padding-left: var(--ui-layout-sidebar-compact); }
  .ops-page-loading-overlay { left: var(--ui-layout-sidebar-compact); }
  .ops-sidebar-brand,
  .ops-main-nav,
  .ops-sidebar-footer { width: var(--ui-layout-sidebar-compact) !important; }
  .ops-sidebar-brand { justify-content: center; padding: 0; }
  .ops-sidebar-brand-copy,
  .ops-sidebar-health-copy,
  .ops-sidebar-profile-copy,
  .ops-sidebar-health { display: none !important; }
  .ops-main-nav { padding-inline: var(--ui-space-2) !important; }
  .ops-main-nav .q-tab { justify-content: center; padding-inline: var(--ui-space-2) !important; }
  .ops-main-nav .q-tab .q-tab__content { justify-content: center; }
  .ops-main-nav .q-tab__label { display: none; }
  .ops-sidebar-profile { justify-content: center; padding: 0; }
  .ops-global-activity-dock { width: min(320px, calc(100vw - var(--ui-layout-sidebar-compact) - var(--ui-space-8))); }
  .ops-task-row-card { grid-template-columns: 38px minmax(180px, 1fr) auto minmax(120px, .45fr) auto; }
}

@media (max-width: 860px) {
  .ops-task-queue-layout { grid-template-columns: 1fr; }
  .ops-task-order-card { position: static; order: -1; }
  .ops-account-center { grid-template-columns: 1fr; }
  .ops-account-center-header,
  .ops-account-directory-panel,
  .ops-account-config-card { grid-column: 1; }
  .ops-account-directory-panel { position: static; max-height: none; }
  .ops-preflight-issue { grid-template-columns: minmax(0, 1fr); }
  .ops-preflight-issue > .q-btn { width: 100%; max-width: 100%; }
}

@media (max-height: 820px) {
  .ops-topbar,
  .ops-sidebar-brand { min-height: 58px; height: 58px; }
  .ops-topbar { flex: 0 0 58px; }
  .ops-main-nav { top: 58px; bottom: 58px; padding-block: var(--ui-space-3) !important; }
  .ops-sidebar-footer { min-height: 58px; }
  .ops-sidebar-health { display: none !important; }
  .ops-topbar { flex-basis: 58px; padding-block: var(--ui-space-2); }
  .ops-main-panels > .q-panel-parent > .q-panel > .q-tab-panel { padding-block: var(--ui-space-3) !important; }
}
"""


APP_CSS += """
/* Approved operations workbench visual contract -------------------------- */
.ops-visually-hidden,
.ops-hidden-control,
.ops-review-route-tab {
  display: none !important;
}
.ops-hidden-create-topic-card {
  position: absolute !important;
  width: 0 !important;
  height: 0 !important;
  overflow: hidden !important;
  visibility: hidden !important;
  pointer-events: none !important;
}

.ops-workbench-shell {
  width: 100vw !important;
  height: 100vh !important;
  height: 100dvh !important;
  padding-left: var(--ui-layout-sidebar-width) !important;
  overflow: hidden !important;
}

.ops-sidebar-brand {
  align-items: flex-start;
  gap: var(--ui-space-3);
  width: var(--ui-layout-sidebar-width);
  height: var(--ui-layout-topbar-height);
  padding: var(--ui-space-6) var(--ui-space-6) 0;
}
.ops-sidebar-brand-mark {
  position: relative;
  display: grid !important;
  width: 34px !important;
  height: 34px !important;
  flex: 0 0 34px;
  place-items: center;
  padding: 0 !important;
  border-radius: var(--ui-radius-sm) !important;
  line-height: 0;
}
.ops-sidebar-brand-mark .q-icon,
.ops-metric-icon .q-icon,
.ops-task-avatar .q-icon,
.ops-task-row-icon .q-icon,
.ops-recent-icon .q-icon,
.ops-config-entry-icon .q-icon {
  position: absolute;
  top: 50%;
  left: 50%;
  margin: 0;
  transform: translate(-50%, -50%);
}
.ops-sidebar-brand-copy > :first-child { font-size: var(--ui-font-size-base); }

.ops-main-nav {
  top: var(--ui-layout-topbar-height);
  bottom: 182px;
  padding: var(--ui-space-5) var(--ui-space-4) !important;
}
.ops-main-nav .q-tabs__content::before {
  display: block;
  padding: 0 10px 15px;
  color: var(--ui-color-text-sidebar-muted);
  font-size: var(--ui-font-size-xs);
  line-height: 21px;
  letter-spacing: .08em;
  text-transform: uppercase;
  content: "WORKSPACE";
}
.ops-main-nav .q-tab {
  min-height: 41px !important;
  height: 41px;
  font-weight: var(--ui-font-weight-regular) !important;
  border-radius: var(--ui-radius-sm) !important;
}
.ops-main-nav .q-tab__indicator { display: none; }
.ops-main-nav .q-tab--active::after {
  width: 5px;
  height: 5px;
  margin-left: auto;
  border-radius: var(--ui-radius-round);
  background: var(--ui-color-brand);
  content: "";
}
.ops-sidebar-footer {
  bottom: var(--ui-layout-page-block-end);
  left: var(--ui-space-4);
  gap: var(--ui-space-3);
  width: calc(var(--ui-layout-sidebar-width) - 32px);
  min-height: 148px;
  height: 148px;
  padding: 0;
  border: 0;
  background: transparent;
}
.ops-sidebar-health {
  display: block !important;
  padding: var(--ui-space-3);
}
.ops-sidebar-health-copy > :last-child { margin-top: var(--ui-space-1); }
.ops-sidebar-profile { flex-wrap: nowrap; }
.ops-sidebar-profile > .q-btn { margin-left: auto; }
.ops-sidebar-avatar {
  color: var(--ui-color-surface) !important;
  background: linear-gradient(145deg, var(--ui-color-purple), var(--ui-color-purple-dark)) !important;
}

.ops-topbar {
  flex: 0 0 var(--ui-layout-topbar-height);
  align-items: center;
  justify-content: space-between;
  min-height: var(--ui-layout-topbar-height);
  height: var(--ui-layout-topbar-height);
  padding: var(--ui-space-3) var(--ui-space-6);
  gap: var(--ui-space-4);
}
.ops-topbar-title { font-size: var(--ui-font-size-base); }
.ops-topbar-actions { align-items: center; gap: 9px; }
.ops-topbar-review-button {
  min-height: var(--ui-control-height) !important;
  padding-inline: var(--ui-space-3) !important;
  color: var(--ui-color-brand) !important;
  background: var(--ui-color-brand-soft) !important;
}
.ops-topbar-icon-button {
  width: var(--ui-control-height) !important;
  height: var(--ui-control-height) !important;
  min-height: var(--ui-control-height) !important;
  border: 1px solid var(--ui-color-border);
  color: var(--ui-color-text-primary) !important;
  background: var(--ui-color-surface) !important;
}

.ops-config-health { display: none !important; }
.ops-main-panels { overflow: hidden !important; }
.ops-main-panels > .q-panel-parent,
.ops-main-panels > .q-panel-parent > .q-panel,
.ops-main-panels > .q-panel-parent > .q-panel > .q-tab-panel {
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden !important;
}
.ops-main-panels > .q-panel-parent > .q-panel > .q-tab-panel.ops-page {
  display: block;
  padding: var(--ui-layout-page-block) var(--ui-layout-page-inline) var(--ui-layout-page-block-end) !important;
}
.ops-main-panels .q-tab-panel.ops-page {
  display: block;
  box-sizing: border-box;
  padding: var(--ui-layout-page-block) var(--ui-layout-page-inline) var(--ui-layout-page-block-end) !important;
}
.ops-page-host,
.wizard-layout {
  position: relative;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.ops-feishu-page .ops-page-host {
  align-content: flex-start;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.ops-page-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--ui-space-4);
  min-width: 0;
  margin: 0;
}
.ops-page-heading-copy { gap: 0 !important; min-width: 0; }
.ops-page-eyebrow {
  margin: 0 0 7px;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
  font-weight: var(--ui-font-weight-regular);
  line-height: 18px;
  letter-spacing: .09em;
}
.ops-page-title {
  font-size: var(--ui-font-size-xl);
  font-weight: var(--ui-font-weight-medium);
}
.ops-page-description {
  margin-top: 7px;
  font-size: var(--ui-font-size-base);
  line-height: 21px;
}
.ops-inline-status {
  align-items: center;
  gap: var(--ui-space-2);
  padding: var(--ui-space-2) 11px;
  border: 1px solid var(--ui-color-info-border);
  border-radius: var(--ui-radius-sm);
  color: var(--ui-color-brand-hover);
  background: var(--ui-color-info-soft);
  white-space: nowrap;
}

.ops-panel {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-card);
}
.ops-panel-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ui-space-3);
  min-height: 70px;
  padding: 13px 15px;
  border-bottom: 1px solid var(--ui-color-border);
}
.ops-panel-title,
.ops-review-page-title {
  color: var(--ui-color-text-primary);
  font-size: var(--ui-font-size-md);
  font-weight: var(--ui-font-weight-medium);
  line-height: var(--ui-line-height-tight);
}
.ops-panel-subtitle {
  margin-top: var(--ui-space-1);
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
}
.ops-panel-body { padding: 14px; }
.ops-badge {
  display: inline-flex !important;
  align-items: center;
  min-height: 24px;
  padding: 4px 8px !important;
  border-radius: var(--ui-radius-round) !important;
  color: var(--ui-color-text-secondary) !important;
  background: var(--ui-color-bg-subtle) !important;
  white-space: nowrap;
}
.ops-badge-green { color: var(--ui-color-success) !important; background: var(--ui-color-success-soft) !important; }
.ops-badge-warm { color: var(--ui-color-orange) !important; background: var(--ui-color-orange-soft) !important; }
.ops-badge-danger { color: var(--ui-color-danger) !important; background: var(--ui-color-danger-soft) !important; }
.q-badge.ops-badge { color: var(--ui-color-text-secondary) !important; background-color: var(--ui-color-bg-subtle) !important; }
.q-badge.ops-badge.ops-badge-green { color: var(--ui-color-success) !important; background-color: var(--ui-color-success-soft) !important; }
.q-badge.ops-badge.ops-badge-warm { color: var(--ui-color-orange) !important; background-color: var(--ui-color-orange-soft) !important; }
.q-badge.ops-badge.ops-badge-danger { color: var(--ui-color-danger) !important; background-color: var(--ui-color-danger-soft) !important; }
.ops-workbench-shell .q-badge.ops-badge { box-shadow: inset 0 0 0 999px var(--ui-color-bg-subtle) !important; }
.ops-workbench-shell .q-badge.ops-badge.ops-badge-green { box-shadow: inset 0 0 0 999px var(--ui-color-success-soft) !important; }
.ops-workbench-shell .q-badge.ops-badge.ops-badge-warm { box-shadow: inset 0 0 0 999px var(--ui-color-orange-soft) !important; }
.ops-workbench-shell .q-badge.ops-badge.ops-badge-danger { box-shadow: inset 0 0 0 999px var(--ui-color-danger-soft) !important; }

/* Create page */
.wizard-layout {
  display: grid !important;
  grid-template-areas:
    "heading heading"
    ". ."
    "metrics metrics"
    ". ."
    "workflow priority"
    ". ."
    "recent recent";
  grid-template-columns: minmax(0, 1.55fr) minmax(270px, .85fr);
  grid-template-rows: 83px 12px 104px 12px minmax(0, 1fr) 12px 164px;
  gap: 0 var(--ui-layout-page-gap) !important;
  align-items: stretch !important;
  align-content: stretch;
}
.wizard-layout > .ops-page-heading { grid-area: heading; }
.wizard-layout > .ops-metric-grid { grid-area: metrics; }
.wizard-layout > .ops-recent-panel { grid-area: recent; }
.ops-create-workflow-panel {
  grid-area: workflow;
  display: grid;
  grid-template-rows: max-content max-content;
  align-content: start;
  width: 100%;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.ops-metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(118px, 1fr));
  gap: var(--ui-space-3);
  width: 100%;
  min-height: 0;
  height: 104px;
  max-height: 104px;
}
.ops-metric-item {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr);
  align-items: center;
  gap: 11px;
  min-width: 0;
  min-height: 104px;
  padding: var(--ui-space-3);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  color: var(--ui-color-text-primary);
  background: var(--ui-color-surface);
  box-shadow: 0 7px 18px rgba(43, 70, 122, .05);
  cursor: pointer;
  text-align: left;
}
.ops-metric-icon,
.ops-recent-icon,
.ops-config-entry-icon {
  position: relative;
  display: grid;
  place-items: center;
  padding: 0;
  line-height: 0;
}
.ops-metric-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--ui-radius-md);
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
}
.ops-metric-purple .ops-metric-icon { color: var(--ui-color-purple); background: var(--ui-color-purple-soft); }
.ops-metric-orange .ops-metric-icon { color: var(--ui-color-orange); background: var(--ui-color-orange-soft); }
.ops-metric-green .ops-metric-icon { color: var(--ui-color-success); background: var(--ui-color-success-soft); }
.ops-metric-red .ops-metric-icon { color: var(--ui-color-danger); background: var(--ui-color-danger-soft); }
.ops-metric-copy { gap: 0 !important; min-width: 0; color: var(--ui-color-text-secondary); }
.ops-metric-value { margin-top: 2px; color: var(--ui-color-text-primary); font-weight: var(--ui-font-weight-medium); }
.ops-metric-hint {
  grid-column: 1 / -1;
  padding-top: 6px;
  overflow: hidden;
  border-top: 1px solid var(--ui-color-border);
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-create-source-section {
  display: grid;
  grid-template-rows: 70px max-content;
  float: none !important;
  clear: none !important;
  width: 100% !important;
  min-width: 0;
  height: auto;
  margin: 0 !important;
  overflow: visible;
  border-bottom: 1px solid var(--ui-color-border);
  border-radius: 0;
  box-shadow: none;
}
.ops-create-account-section {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-rows: auto auto auto auto auto;
  float: none !important;
  clear: none !important;
  width: 100% !important;
  position: relative;
  min-width: 0;
  min-height: 0;
  height: auto !important;
  margin: 0 !important;
  padding: 6px 14px 10px;
  overflow: visible;
  border-top: 0;
  border-radius: 0;
  box-shadow: none;
}
.ops-create-source-section .ops-panel-heading { min-height: 70px; }
.ops-create-form-body {
  display: grid;
  align-content: start;
  gap: var(--ui-space-2);
  min-width: 0;
  min-height: max-content;
  padding: 14px;
  overflow: visible;
}
.ops-field {
  display: grid;
  grid-template-rows: 21px minmax(0, var(--ui-control-height-field));
  gap: var(--ui-field-gap);
  min-width: 0;
  min-height: 67px;
}
.ops-field-label {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-base);
  line-height: 21px;
}
.ops-compact-hint { display: none; }
.source-mode-toggle.ops-segment,
.ops-task-segment,
.ops-topic-view-segment,
.ops-config-tabs,
.ops-review-mode-tabs {
  display: grid !important;
  min-height: var(--ui-segment-height) !important;
  height: var(--ui-segment-height);
  gap: 5px;
  padding: var(--ui-space-1);
  border-radius: var(--ui-radius-sm) !important;
  background: var(--ui-color-bg-subtle);
  box-shadow: none;
}
.source-mode-toggle.ops-segment { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.ops-workbench-shell .ops-segment .q-btn,
.ops-workbench-shell .ops-segment .q-tab {
  min-width: 0;
  min-height: var(--ui-control-height-button) !important;
  height: var(--ui-control-height-button);
  padding: 8px 9px !important;
  border: 0 !important;
  border-radius: 8px !important;
  color: var(--ui-color-text-secondary) !important;
  background: transparent !important;
  box-shadow: none !important;
  font-size: var(--ui-font-size-base);
  font-weight: var(--ui-font-weight-regular) !important;
  line-height: 21px;
}
.ops-workbench-shell .ops-segment .q-btn[aria-pressed="true"],
.ops-workbench-shell .ops-segment .q-btn--active,
.ops-workbench-shell .ops-segment .q-tab--active {
  color: var(--ui-color-text-primary) !important;
  background: var(--ui-color-surface) !important;
  box-shadow: 0 3px 10px rgba(35, 65, 120, .09) !important;
  font-weight: var(--ui-font-weight-regular) !important;
}
.ops-workbench-shell .ops-segment .q-btn.bg-primary.text-white[aria-pressed="true"] {
  border: 0 !important;
  color: var(--ui-color-text-primary) !important;
  background: var(--ui-color-surface) !important;
}
.ops-workbench-shell .source-mode-toggle.ops-segment .q-btn.bg-primary.text-white[aria-pressed="true"],
.ops-workbench-shell .ops-task-segment.ops-segment .q-btn.bg-primary.text-white[aria-pressed="true"],
.ops-workbench-shell .ops-config-tabs.ops-segment .q-btn.bg-primary.text-white[aria-pressed="true"] {
  border-color: transparent !important;
  color: var(--ui-color-text-primary) !important;
  background: var(--ui-color-surface) !important;
  background-color: var(--ui-color-surface) !important;
}
.ops-workbench-shell .source-mode-toggle.ops-segment.q-btn-toggle,
.ops-workbench-shell .ops-task-segment.ops-segment.q-btn-toggle,
.ops-workbench-shell .ops-topic-view-segment.ops-segment.q-btn-toggle,
.ops-workbench-shell .ops-config-tabs.ops-segment.q-btn-toggle {
  border-radius: var(--ui-radius-sm) !important;
}
.ops-create-field-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--ui-space-3);
}
.ops-create-source-section .q-field__control,
.ops-create-account-section .q-field:not(.ops-create-log-area) .q-field__control {
  min-height: var(--ui-control-height-field) !important;
  height: var(--ui-control-height-field);
}
.ops-create-source-section .q-field__native,
.ops-create-source-section .q-field__input {
  min-height: 0 !important;
  padding: 8px 10px !important;
  line-height: 21px;
}
.ops-create-source-section .q-field--outlined .q-field__control::before {
  border-color: var(--ui-color-border);
}
.ops-source-text-field {
  grid-template-rows: 21px minmax(0, 132px);
  min-height: 160px;
}
.ops-create-source-section .article-body-input,
.ops-create-source-section .article-body-input .q-field__inner,
.ops-create-source-section .article-body-input .q-field__control,
.ops-create-source-section .article-body-input .q-field__control-container {
  min-height: 0 !important;
  height: 132px !important;
  max-height: 132px;
  overflow: hidden;
}
.ops-create-source-section .article-body-input textarea.q-field__native {
  min-height: 0 !important;
  height: 100% !important;
  max-height: 132px;
  overflow-y: auto !important;
  overflow-wrap: anywhere;
}
.ops-create-account-section .ops-account-step-line { margin-bottom: var(--ui-space-1); }
.ops-create-account-section .ops-account-step-line {
  grid-column: 1 / -1;
  grid-row: 1;
  width: 100%;
}
.ops-create-account-pager {
  width: 24px !important;
  min-width: 24px !important;
  min-height: 24px !important;
  height: 24px !important;
}
.ops-step-line { align-items: center; gap: var(--ui-space-2); color: var(--ui-color-text-secondary); }
.ops-step-number {
  display: grid;
  width: 22px;
  height: 22px;
  place-items: center;
  border-radius: var(--ui-radius-xs);
  color: var(--ui-color-brand-hover);
  background: var(--ui-color-brand-soft);
}
.ops-create-account-select { display: none !important; }
.ops-create-account-list {
  grid-column: 1 / -1;
  grid-row: 2;
  display: grid !important;
  grid-auto-rows: max-content;
  align-content: start;
  gap: 6px !important;
  min-height: 0;
  height: auto;
  max-height: none;
  padding-right: var(--ui-space-1);
  overflow: visible;
}
.ops-create-account-choice {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--ui-space-3);
  width: 100%;
  min-height: 59px;
  height: 59px;
  padding: 9px;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-sm);
  color: var(--ui-color-text-primary);
  background: var(--ui-color-surface);
  cursor: pointer;
  text-align: left;
}
.ops-create-account-choice[aria-pressed="true"] {
  border-color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
}
.ops-create-account-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border-radius: var(--ui-radius-xs);
  color: var(--ui-color-brand-hover);
  background: var(--ui-color-brand-soft);
}
.ops-create-account-name { font-weight: var(--ui-font-weight-medium); }
.ops-create-account-model {
  margin-top: 2px;
  overflow: hidden;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-create-account-readiness {
  color: var(--ui-color-success);
  font-size: var(--ui-font-size-xs);
  white-space: nowrap;
}
.ops-create-account-readiness::before {
  display: inline-block;
  width: 7px;
  height: 7px;
  margin-right: var(--ui-space-1);
  border-radius: var(--ui-radius-round);
  background: currentColor;
  content: "";
}
.ops-create-account-readiness-warning { color: var(--ui-color-warning); }
.ops-create-account-section > .muted { display: none; }
.ops-create-status-row {
  position: static;
  grid-column: 1;
  grid-row: 5;
  align-self: center;
  display: grid !important;
  width: 100%;
  min-width: 0;
  margin-top: 0;
  gap: 2px !important;
}
.ops-create-submit-title {
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: var(--ui-font-weight-medium);
}
.ops-create-status-row .progress-elapsed {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
}
.ops-create-action-row {
  position: static;
  grid-column: 2;
  grid-row: 5;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  width: auto;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.ops-create-action-row .q-btn { min-height: var(--ui-control-height) !important; }
.ops-create-account-section .rewrite-progress {
  grid-column: 1 / -1;
  grid-row: 3;
  min-width: 0;
  margin: var(--ui-space-2) 0 0;
  padding: 9px 10px;
  overflow: hidden;
}
.ops-create-log-area {
  grid-column: 1 / -1;
  grid-row: 4;
  min-width: 0;
  max-width: 100%;
  margin-top: var(--ui-space-2);
}
.ops-create-log-area .q-field__inner,
.ops-create-log-area .q-field__control,
.ops-create-log-area .q-field__control-container {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  height: auto !important;
  box-sizing: border-box;
}
.ops-create-log-area textarea.q-field__native {
  min-width: 0;
  min-height: calc(5 * 1.5em) !important;
  max-height: calc(10 * 1.5em);
  overflow-x: hidden;
  overflow-y: auto !important;
  overflow-wrap: anywhere;
  word-break: break-word;
  white-space: pre-wrap;
}
.ops-create-account-section .progress-heading { min-width: 0; margin-bottom: var(--ui-space-1); }
.ops-create-account-section .progress-track-wrap,
.ops-create-account-section .progress-bar { height: 24px; }
.ops-create-account-section .progress-stage {
  min-width: 0;
  padding: 0 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-create-account-section .progress-hint {
  min-width: 0;
  margin-top: var(--ui-space-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-create-priority-panel {
  grid-area: priority;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  height: 100%;
}
.ops-priority-body { display: flex; flex-direction: column; min-height: 0; }
.ops-priority-row {
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--ui-space-2);
  min-height: 68px;
  border-bottom: 1px solid var(--ui-color-border);
}
.ops-priority-number { color: var(--ui-color-brand); }
.ops-priority-title { font-weight: var(--ui-font-weight-medium); }
.ops-priority-detail { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }
.ops-tip {
  display: flex;
  align-items: flex-start;
  gap: var(--ui-space-2);
  margin-top: auto;
  padding: 10px;
  border-radius: var(--ui-radius-sm);
  color: var(--ui-color-warning);
  background: var(--ui-color-warning-soft);
  font-size: var(--ui-font-size-xs);
}
.ops-recent-panel { min-height: 0; }
.ops-recent-panel .ops-panel-heading { min-height: 58px; padding-block: 10px; }
.ops-recent-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--ui-space-3);
  padding: var(--ui-space-3) 14px;
}
.ops-recent-item {
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  align-items: center;
  gap: var(--ui-space-3);
  min-width: 0;
  padding: var(--ui-space-2) var(--ui-space-3);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-color-bg-subtle);
}
.ops-recent-icon { width: 36px; height: 36px; border-radius: var(--ui-radius-sm); color: var(--ui-color-brand); background: var(--ui-color-brand-soft); }
.ops-recent-green .ops-recent-icon { color: var(--ui-color-success); background: var(--ui-color-success-soft); }
.ops-recent-orange .ops-recent-icon { color: var(--ui-color-orange); background: var(--ui-color-orange-soft); }
.ops-recent-purple .ops-recent-icon { color: var(--ui-color-purple); background: var(--ui-color-purple-soft); }
.ops-recent-title,
.ops-recent-detail { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ops-recent-title { font-weight: var(--ui-font-weight-medium); }
.ops-recent-detail { color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }

/* Topic radar */
.ops-topics-page .ops-page-host {
  display: grid !important;
  grid-template-rows: 79px minmax(0, 1fr);
  gap: var(--ui-layout-page-gap) !important;
}
.ops-topics-page .ops-page-heading { min-height: 79px; height: 79px; }
.ops-topic-secondary-tabs {
  position: absolute !important;
  z-index: 2;
  top: var(--ui-control-height-button);
  right: calc(var(--ui-topic-heading-action-width) + var(--ui-space-3));
  display: flex !important;
  width: var(--ui-topic-nav-width) !important;
  min-height: var(--ui-control-height-button) !important;
  height: var(--ui-control-height-button) !important;
  padding: var(--ui-space-1);
  overflow: hidden;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-color-bg-subtle);
}
.ops-topic-secondary-tabs .q-tabs__content {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  width: 100%;
}
.ops-topic-secondary-tabs .q-tab {
  min-height: calc(var(--ui-control-height-button) - var(--ui-space-2)) !important;
  height: calc(var(--ui-control-height-button) - var(--ui-space-2)) !important;
  padding: 0 var(--ui-space-2) !important;
  border-radius: var(--ui-radius-xs);
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-sm);
  font-weight: var(--ui-font-weight-regular);
}
.ops-topic-secondary-tabs .q-tab--active {
  color: var(--ui-color-brand);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-sm);
}
.ops-topic-secondary-tabs .ops-topic-primary-tab {
  color: var(--ui-color-brand);
  background: var(--ui-color-brand-soft);
  font-weight: var(--ui-font-weight-bold);
}
.ops-topic-secondary-tabs .ops-topic-primary-tab.q-tab--active {
  color: var(--ui-color-surface) !important;
  background: var(--ui-color-brand) !important;
  box-shadow: var(--ui-shadow-sm);
}
.ops-topic-secondary-tabs .q-tab__indicator { display: none; }
.ops-topic-heading-action {
  position: absolute !important;
  z-index: 2;
  top: var(--ui-control-height-button);
  min-height: var(--ui-control-height-button) !important;
}
.ops-topic-heading-action {
  right: 0;
  width: var(--ui-topic-heading-action-width);
}
.ops-topic-heading-action .q-btn__content {
  flex-wrap: nowrap;
  gap: 4px;
  white-space: nowrap;
  font-size: var(--ui-font-size-sm);
}
.ops-topic-heading-action .q-icon { font-size: var(--ui-font-size-md); }
.ops-topic-secondary-panels,
.ops-topic-secondary-panels > .q-panel-parent,
.ops-topic-secondary-panels > .q-panel-parent > .q-panel,
.ops-topic-secondary-panels .q-tab-panel {
  position: static !important;
  min-height: 0;
  height: 100%;
  overflow: visible !important;
}
.ops-topic-secondary-panels .q-tab-panel { padding: 0 !important; }
.ops-topic-primary-view {
  position: static !important;
  display: grid !important;
  grid-template-areas:
    "toolbar"
    "segment"
    "results";
  grid-template-rows: var(--ui-control-height-button) var(--ui-segment-height) minmax(0, 1fr);
  gap: var(--ui-layout-page-gap) !important;
  min-height: 0;
  height: 100%;
  align-items: stretch !important;
}
.ops-topic-toolbar {
  grid-area: toolbar;
  display: grid !important;
  grid-template-columns: minmax(0, 1fr) 82px 80px 80px;
  align-items: stretch !important;
  gap: var(--ui-space-2) !important;
}
.ops-topic-search { order: 1; width: auto !important; }
.ops-topic-days-filter { order: 2; width: auto !important; }
.ops-topic-source-filter { order: 3; min-width: 0; }
.ops-topic-unused-filter { order: 4; align-self: center; white-space: nowrap; }
.ops-topic-toolbar .q-field,
.ops-topic-toolbar .q-field__control {
  min-height: var(--ui-control-height-button) !important;
  height: var(--ui-control-height-button) !important;
}
.ops-topic-toolbar .q-field__native,
.ops-topic-toolbar .q-field__input { min-height: 0 !important; }
.ops-topic-source-filter .q-field__native { overflow: hidden; white-space: nowrap; }
.ops-topic-source-filter,
.ops-topic-source-filter .q-field__native { font-size: var(--ui-font-size-sm); }
.ops-topic-view-segment {
  grid-area: segment;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: 100%;
  min-height: var(--ui-segment-height) !important;
  height: var(--ui-segment-height) !important;
  align-self: stretch !important;
}
.ops-topic-actions { position: static; height: 0; overflow: visible; }
.ops-topic-helper { display: none !important; }
.ops-topic-results {
  grid-area: results;
  display: grid !important;
  grid-template-rows: auto minmax(0, 1fr) auto;
  gap: var(--ui-space-2) !important;
  min-height: 0;
  height: 100% !important;
  align-self: stretch !important;
  align-items: stretch !important;
  overflow: hidden;
}
.ops-topic-result-count { min-height: 18px; }
.ops-topic-table {
  display: grid;
  grid-template-rows: var(--ui-control-height-sm) minmax(0, 1fr);
  min-height: 0;
  height: 100%;
  overflow: hidden;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-surface);
}
.ops-topic-table-head,
.ops-topic-card {
  display: grid;
  grid-template-columns:
    var(--ui-topic-source-column)
    minmax(0, .9fr)
    minmax(0, 1.2fr)
    var(--ui-topic-actions-column);
  column-gap: var(--ui-space-3);
  align-items: center;
}
.ops-topic-table-head {
  padding: 0 var(--ui-space-4);
  color: var(--ui-color-text-secondary);
  background: var(--ui-color-bg-subtle);
  border-bottom: 1px solid var(--ui-color-border);
  font-size: var(--ui-font-size-xs);
  font-weight: var(--ui-font-weight-medium);
}
.ops-topic-table-body {
  display: grid;
  grid-auto-rows: var(--ui-topic-row-height);
  min-height: 0;
  align-content: start;
  overflow: hidden;
}
.ops-topic-pagination {
  width: 100%;
  min-height: var(--ui-control-height-sm);
  align-items: center;
  justify-content: space-between;
  gap: var(--ui-space-3) !important;
}
.ops-topic-page-summary {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
  line-height: var(--ui-line-height-xs);
  white-space: nowrap;
}
.ops-topic-pagination .q-pagination {
  flex-wrap: nowrap;
}
.ops-topic-pagination .q-btn {
  min-width: var(--ui-control-height-sm) !important;
  min-height: var(--ui-control-height-sm) !important;
  height: var(--ui-control-height-sm) !important;
  padding: 0 var(--ui-space-2) !important;
}
.ops-topic-card {
  min-width: 0;
  min-height: var(--ui-topic-row-height);
  height: var(--ui-topic-row-height);
  padding: 0 var(--ui-space-4);
  overflow: hidden;
  border-bottom: 1px solid var(--ui-color-border);
  background: var(--ui-color-surface);
}
.ops-topic-card:last-child { border-bottom: 0; }
.ops-topic-card-meta {
  min-width: 0;
  gap: var(--ui-space-1);
  flex-wrap: nowrap;
  overflow: hidden;
}
.ops-topic-card-title {
  min-width: 0;
  overflow: hidden;
  color: var(--ui-color-text-primary);
  font-size: var(--ui-font-size-base);
  font-weight: var(--ui-font-weight-medium);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-topic-card-summary {
  min-width: 0;
  overflow: hidden;
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-topic-card-actions {
  align-items: center;
  justify-content: flex-end;
  min-width: 0;
  width: 100%;
  flex-wrap: nowrap;
  gap: var(--ui-space-1) !important;
}
.ops-topic-card-actions .q-space { display: none; }
.ops-topic-card-actions .q-btn,
.ops-topic-card-actions .ops-topic-source-link {
  flex: 0 0 auto;
  white-space: nowrap;
}
.ops-topic-source-link { color: var(--ui-color-brand-hover); }
.ops-topic-empty {
  display: grid;
  height: 100%;
  place-items: center;
  min-height: 0;
  width: 100%;
  align-self: stretch;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-lg);
  background: var(--ui-color-surface);
  box-shadow: var(--ui-shadow-card);
}
.ops-topic-results:has(.ops-topic-empty) { grid-template-rows: minmax(0, 1fr); }
.ops-topic-results:has(.ops-topic-empty) .ops-topic-result-count { display: none; }
.ops-topic-results:has(.ops-topic-empty) .ops-topic-empty { grid-row: 1; }
.ops-topic-load-more { justify-self: center; }
.ops-topic-detail-view { height: 100%; min-height: 0; overflow: hidden; }

/* Task queue */
.ops-tasks-page .ops-page-host {
  display: grid !important;
  grid-template-rows: 79px var(--ui-segment-height) var(--ui-control-height-button) minmax(0, 1fr);
  gap: var(--ui-layout-page-gap) !important;
}
.ops-task-page-actions {
  position: absolute;
  z-index: 2;
  top: 28px;
  right: 0;
}
.ops-task-segment { grid-template-columns: repeat(5, minmax(0, 1fr)); }
.ops-toolbar { display: flex; align-items: center; gap: 9px; }
.ops-task-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 94px 52px;
  gap: 9px;
}
.ops-task-toolbar .ops-search { width: 100%; min-width: 0; }
.ops-task-toolbar .q-field__control {
  min-height: var(--ui-control-height-button) !important;
  height: var(--ui-control-height-button);
}
.ops-filter-account { width: 94px; min-width: 0; }
.ops-filter-account .q-field__native,
.ops-filter-account .q-field__input {
  min-width: 0;
  padding-right: 0 !important;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-workbench-shell .ops-task-today-filter.q-btn {
  width: 52px;
  min-width: 52px !important;
  padding: 8px 11px !important;
}
.ops-queue-workspace {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 290px;
  gap: var(--ui-space-3);
  width: 100%;
  min-width: 0;
  max-width: 100%;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
.ops-list-panel,
.ops-flow-panel {
  display: grid;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  min-height: 0;
  overflow: hidden;
}
.ops-list-panel {
  container-type: inline-size;
  grid-template-rows: auto minmax(0, 1fr);
}
.ops-list-panel .ops-panel-heading,
.ops-flow-panel .ops-panel-heading { min-height: 72px; }
.ops-task-list {
  display: grid !important;
  grid-template-columns: minmax(0, 1fr);
  grid-auto-rows: minmax(var(--ui-task-row-height), auto);
  align-content: start !important;
  align-items: stretch !important;
  justify-items: stretch !important;
  gap: var(--ui-space-2) !important;
  width: 100%;
  min-width: 0;
  max-width: 100%;
  min-height: 0;
  padding: var(--ui-space-3);
  overflow: hidden !important;
}
.ops-task-row-card,
.ops-batch-row-card {
  grid-template-columns: 42px minmax(0, 1.5fr) minmax(0, .7fr) minmax(0, .7fr) var(--ui-task-actions-column) !important;
  align-self: stretch;
  width: 100% !important;
  min-height: var(--ui-task-row-height) !important;
  height: var(--ui-task-row-height) !important;
  padding: 10px 13px !important;
  box-shadow: none !important;
}
.ops-task-row-icon { position: relative; width: 38px; height: 38px; }
.ops-task-row-actions .q-btn:last-child { display: none; }
.ops-task-row-actions .q-btn { min-height: 38px; }
.ops-task-row-archive-action {
  width: var(--ui-task-archive-action-width);
  min-width: var(--ui-task-archive-action-width);
  white-space: nowrap;
}
.ops-task-row-archive-action .q-btn__content { flex-wrap: nowrap; }
.ops-task-row-primary-action {
  width: 84px;
  min-width: 84px;
  max-width: 84px;
  justify-content: center;
  white-space: nowrap;
}
.ops-task-row-primary-action .q-btn__content {
  min-height: 21px;
  height: 21px;
  flex: 0 0 21px;
  flex-flow: row nowrap;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}
.ops-task-row-primary-action .q-icon { display: none !important; }
@container (max-width: 720px) {
  .ops-task-row-card,
  .ops-batch-row-card {
    grid-template-columns: 38px minmax(0, 1fr) 128px !important;
  }
  .ops-task-row-state,
  .ops-task-row-badge { display: none !important; }
  .ops-task-row-archive-action {
    width: 38px;
    min-width: 38px;
    padding-inline: 0 !important;
    font-size: 0 !important;
  }
  .ops-task-row-archive-action .q-icon {
    margin: 0 !important;
    font-size: 20px !important;
  }
}
.ops-flow-panel { grid-template-rows: auto minmax(0, 1fr) auto; }
.ops-flow-list { display: grid; align-content: start; gap: var(--ui-space-2); padding: var(--ui-space-3); }
.ops-flow-step { display: grid !important; grid-template-columns: 24px minmax(0, 1fr); gap: var(--ui-space-2); padding: 9px; border-radius: var(--ui-radius-sm); background: var(--ui-color-bg-subtle); }
.ops-flow-number { display: grid; width: 24px; height: 24px; place-items: center; border-radius: var(--ui-radius-xs); color: var(--ui-color-brand-hover); background: var(--ui-color-brand-soft); }
.ops-flow-footer { padding: var(--ui-space-3); border-top: 1px solid var(--ui-color-border); }

/* Account center */
.ops-accounts-page .ops-page-host {
  display: grid !important;
  grid-template-rows: 78px minmax(0, 1fr);
  gap: var(--ui-layout-page-gap) !important;
}
.ops-account-add-top { position: absolute; z-index: 2; top: 0; right: 0; width: 121px; }
.ops-account-add-top .q-btn__content { flex-wrap: nowrap; gap: 5px; white-space: nowrap; }
.ops-account-add-top .q-icon { font-size: var(--ui-font-size-md); }
.ops-account-center { display: contents !important; }
.ops-account-workspace {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: var(--ui-space-3);
  min-height: 0;
  height: 100%;
  overflow: hidden;
}
.ops-account-directory,
.ops-account-config { display: grid; min-height: 0; height: 100%; overflow: hidden; }
.ops-account-directory { grid-template-rows: 72px minmax(0, 1fr) auto; }
.ops-account-directory > .ops-panel-heading { min-height: 72px; height: 72px; }
.ops-account-directory-list {
  display: grid;
  align-content: start;
  gap: var(--ui-space-2);
  padding: var(--ui-space-3);
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.ops-account-directory-row { position: relative; min-width: 0; }
.ops-account-directory-item {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr);
  grid-template-rows: minmax(38px, auto) auto;
  align-items: center;
  align-content: start;
  gap: var(--ui-space-2);
  width: 100%;
  min-height: 109px;
  height: auto;
  padding: 10px calc(var(--ui-control-height-sm) + var(--ui-space-2)) 10px 10px;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  color: var(--ui-color-text-primary);
  background: var(--ui-color-surface);
  text-align: left;
  cursor: pointer;
}
.ops-account-directory-item[aria-pressed="true"] { border-color: var(--ui-color-brand); background: var(--ui-color-brand-soft); }
.ops-account-directory-more {
  position: absolute !important;
  z-index: 1;
  top: var(--ui-space-2);
  right: var(--ui-space-2);
  width: var(--ui-control-height-sm) !important;
  min-width: var(--ui-control-height-sm) !important;
  height: var(--ui-control-height-sm) !important;
  min-height: var(--ui-control-height-sm) !important;
  color: var(--ui-color-brand) !important;
}
.ops-account-directory-more .q-btn__content { flex-wrap: nowrap; }
.ops-task-avatar { position: relative; display: grid; width: 38px; height: 38px; place-items: center; border-radius: 11px; color: var(--ui-color-brand-hover); background: var(--ui-color-brand-soft); }
.ops-account-name { overflow: hidden; font-weight: var(--ui-font-weight-medium); text-overflow: ellipsis; white-space: nowrap; }
.ops-account-directory-item .ops-panel-subtitle {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-account-directory-status {
  grid-column: 1 / -1;
  flex-wrap: wrap;
  min-width: 0;
  gap: var(--ui-space-1);
  padding-top: var(--ui-space-2);
  border-top: 1px solid var(--ui-color-border);
}
.ops-account-directory-status .ops-badge { max-width: 100%; }
.ops-account-pagination { align-items: center; justify-content: center; gap: var(--ui-space-2); min-height: 34px; }
.ops-account-directory-footer { padding: var(--ui-space-3); border-top: 1px solid var(--ui-color-border); }
.ops-account-config { grid-template-rows: 72px minmax(0, 1fr) 60px; }
.ops-account-config > .ops-panel-heading { min-height: 72px; height: 72px; }
.ops-config-header-actions,
.ops-config-footer-actions { align-items: center; gap: var(--ui-space-2); }
.ops-config-tabs { grid-template-columns: repeat(4, minmax(0, 1fr)); margin: var(--ui-space-3) var(--ui-space-3) 0; }
.ops-config-body { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-content: start; gap: var(--ui-space-3); min-height: 0; padding: var(--ui-space-3); overflow: hidden; }
.ops-config-body-unified {
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.ops-prompt-binding-host { flex: 0 0 auto; }
.ops-config-section { min-width: 0; padding: var(--ui-space-3); border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-md); background: var(--ui-color-bg-subtle); }
.ops-config-section:not(.ops-config-section-wide) {
  min-height: 204px;
  height: 204px;
}
.ops-config-section-wide { grid-column: 1 / -1; }
.ops-config-section-view {
  grid-column: 1 / -1;
  min-height: 0 !important;
  height: auto !important;
}
.ops-config-section-heading { display: flex; align-items: center; justify-content: space-between; gap: var(--ui-space-2); margin-bottom: var(--ui-space-3); }
.ops-config-status-list { display: grid; gap: var(--ui-space-2); }
.ops-config-status-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: var(--ui-space-2); min-height: 42px; padding: 7px 9px; border-radius: var(--ui-radius-sm); background: var(--ui-color-surface); }
.ops-config-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--ui-space-2); }
.ops-config-field {
  display: grid;
  grid-template-rows: 18px var(--ui-control-height-field);
  gap: var(--ui-space-1);
  min-width: 0;
  min-height: 61px;
}
.ops-config-field-label {
  color: var(--ui-color-text-secondary);
  font-size: var(--ui-font-size-xs);
  line-height: 18px;
}
.ops-config-field .q-field__label { display: none !important; }
.ops-config-field > .q-field,
.ops-config-field .q-field__control,
.ops-config-field .q-field__control-container,
.ops-config-field .q-field__native {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}
.ops-config-field .q-select .q-field__native { overflow: hidden; }
.ops-config-field .q-select .q-field__native > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-config-form .q-field__control { min-height: var(--ui-control-height-field) !important; height: var(--ui-control-height-field); }
.ops-config-form .q-field__control-container {
  justify-content: center;
  padding-top: 0 !important;
}
.ops-config-form .q-field__native,
.ops-config-form .q-field__input {
  min-height: var(--ui-control-height-field) !important;
  height: var(--ui-control-height-field) !important;
  padding: 0 !important;
}
.ops-config-form div.q-field__native {
  align-items: center;
  line-height: var(--ui-line-height-body);
}
.ops-config-form input.q-field__native {
  line-height: var(--ui-control-height-field);
}
.ops-model-option {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: var(--ui-control-height-button);
  padding: var(--ui-space-2) var(--ui-space-3);
}
.ops-model-option .q-item__section--avatar {
  min-width: 0;
  padding-right: var(--ui-space-2);
}
.ops-model-option-copy { min-width: 0; }
.ops-model-option-copy .q-item__label {
  overflow: hidden;
  color: var(--ui-color-text-primary);
  font-size: var(--ui-font-size-base);
  line-height: var(--ui-line-height-base);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ops-model-option-badge {
  min-width: var(--ui-control-height-button);
  font-size: var(--ui-font-size-xs);
  line-height: 1;
}
.ops-badge.ops-model-option-badge-official {
  color: var(--ui-color-brand-dark) !important;
  background: var(--ui-color-brand-soft) !important;
}
.ops-badge.ops-model-option-badge-custom {
  color: var(--ui-color-purple-dark) !important;
  background: var(--ui-color-purple-soft) !important;
}
.ops-model-option-actions {
  display: flex !important;
  flex: 0 0 auto;
  flex-direction: row !important;
  flex-wrap: nowrap;
  gap: var(--ui-space-1);
  min-width: 0;
  padding-left: var(--ui-space-2);
}
.ops-model-option-action {
  width: var(--ui-control-height-sm);
  min-width: var(--ui-control-height-sm);
  height: var(--ui-control-height-sm);
}
.ops-model-option-edit { color: var(--ui-color-brand); }
.ops-model-option-delete { color: var(--ui-color-danger); }
.ops-model-select-menu {
  width: auto !important;
  max-width: min(
    var(--ui-layout-dialog-sm),
    calc(100vw - (2 * var(--ui-space-4)))
  ) !important;
  overflow-x: hidden;
}
.ops-config-entry-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: var(--ui-space-2); }
.ops-config-entry-grid-single {
  grid-template-columns: minmax(0, 1fr);
  margin-top: var(--ui-space-3);
}
.ops-config-entry { display: grid; grid-template-columns: 30px minmax(0, 1fr); align-items: center; gap: var(--ui-space-2); min-width: 0; min-height: 58px; padding: 9px; border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-sm); color: var(--ui-color-text-primary); background: var(--ui-color-surface); text-align: left; cursor: pointer; }
.ops-config-entry-icon { width: 30px; height: 30px; border-radius: var(--ui-radius-xs); color: var(--ui-color-brand-hover); background: var(--ui-color-brand-soft); }
.ops-config-entry:nth-child(2) .ops-config-entry-icon,
.ops-config-entry:nth-child(5) .ops-config-entry-icon { color: var(--ui-color-success); background: var(--ui-color-success-soft); }
.ops-config-entry:nth-child(3) .ops-config-entry-icon,
.ops-config-entry:nth-child(6) .ops-config-entry-icon { color: var(--ui-color-purple); background: var(--ui-color-purple-soft); }
.ops-config-entry-title,
.ops-config-entry-detail { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ops-config-entry-title { font-weight: var(--ui-font-weight-medium); }
.ops-config-entry-detail { margin-top: 2px; color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }
.ops-config-footer { display: flex; align-items: center; justify-content: space-between; gap: var(--ui-space-3); min-height: 60px; padding: 10px var(--ui-space-3); border-top: 1px solid var(--ui-color-border); color: var(--ui-color-text-secondary); background: var(--ui-color-bg-subtle); }

/* Import reusable layout rules from a public WeChat article. */
.wechat-layout-import-dialog { gap: var(--ui-space-3); }
.wechat-layout-import-result { gap: var(--ui-space-3); }
.wechat-layout-paste-expansion,
.wechat-layout-html-input,
.wechat-layout-html-input :is(.q-field__inner, .q-field__control, .q-field__control-container) {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}
.wechat-layout-html-input textarea.q-field__native {
  width: 100%;
  min-width: 0;
  height: 150px;
  max-height: 150px;
  overflow-y: auto;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  box-sizing: border-box;
}
.wechat-layout-import-summary { display: grid; gap: var(--ui-space-2); padding: var(--ui-space-3); }
.wechat-layout-change-table {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-2);
}
.wechat-layout-change-row {
  display: grid;
  grid-template-columns: minmax(90px, 1fr) minmax(64px, auto) 18px minmax(64px, auto);
  align-items: center;
  gap: var(--ui-space-2);
  min-height: var(--ui-control-height-field);
  padding: 7px 10px;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-color-bg-subtle);
}
.wechat-layout-import-previews {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--ui-space-3);
  min-height: 0;
}
.wechat-layout-preview-panel { min-width: 0; padding: var(--ui-space-3); }
.wechat-layout-import-preview,
.wechat-layout-preview-panel .wechat-preview-iframe {
  display: block;
  width: 100%;
  height: 460px;
  margin-top: var(--ui-space-3);
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-sm);
  background: var(--ui-color-surface);
}
.wechat-layout-import-error { display: grid; gap: var(--ui-space-2); padding: var(--ui-space-4); }

/* Full-page review */
.ops-review-page .ops-page-host {
  display: grid !important;
  grid-template-rows: 50px var(--ui-segment-height) minmax(0, 1fr);
  gap: var(--ui-review-gap) !important;
}
.ops-review-bar { display: flex; align-items: center; justify-content: space-between; gap: var(--ui-space-3); min-height: 50px; height: 50px; }
.ops-review-title,
.ops-review-controls { align-items: center; gap: 11px; }
.ops-review-title > .ops-icon-button {
  width: var(--ui-control-height) !important;
  min-width: var(--ui-control-height) !important;
  height: var(--ui-control-height) !important;
  min-height: var(--ui-control-height) !important;
  padding: 0 !important;
  border: 1px solid var(--ui-color-border) !important;
  border-radius: var(--ui-radius-sm) !important;
  color: var(--ui-color-text-primary) !important;
  background: var(--ui-color-surface) !important;
}
.ops-review-mode-tabs {
  display: flex !important;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  width: 100%;
}
.ops-review-mode-tabs .q-tabs__content {
  display: grid !important;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 5px;
  width: 100%;
}
.ops-review-mode-tabs .q-tab {
  width: 100%;
  max-width: none;
}
.ops-workbench-shell .ops-review-mode-tabs .q-tab--active {
  color: var(--ui-color-text-primary) !important;
  background: var(--ui-color-surface) !important;
  box-shadow: 0 3px 10px rgba(35, 65, 120, .09) !important;
  font-weight: var(--ui-font-weight-regular) !important;
}
.ops-review-layout { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(260px, .75fr); gap: var(--ui-review-gap); min-height: 0; height: 100%; overflow: hidden; }
.ops-review-document,
.ops-review-side { min-height: 0; height: 100%; overflow: hidden; }
.ops-review-document-panels,
.ops-review-document-panels > .q-panel-parent,
.ops-review-document-panels .q-panel,
.ops-review-document-panels .q-tab-panel { min-height: 0; height: 100%; overflow: hidden; }
.ops-review-mode-panel { padding: 0 !important; }
.ops-document-tools { display: flex; align-items: center; justify-content: space-between; gap: var(--ui-space-2); min-height: 54px; padding: var(--ui-space-3) var(--ui-space-4); border-bottom: 1px solid var(--ui-color-border); background: var(--ui-color-bg-subtle); }
.ops-document-canvas { display: block; max-width: 650px; height: calc(100% - 54px); margin: 0 auto; padding: 28px 30px 38px; overflow: auto; scrollbar-width: none; }
.ops-document-canvas::-webkit-scrollbar,
.ops-review-body-editor textarea::-webkit-scrollbar { display: none; }
.ops-document-canvas iframe { width: 100%; min-height: 100%; border: 0; }
.ops-inline-comparison {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  align-items: start;
  gap: var(--ui-space-3);
  width: 100%;
  height: calc(100% - 54px);
  min-width: 0;
  min-height: 0;
  padding: var(--ui-space-3);
  overflow-x: hidden;
  overflow-y: auto;
  background: var(--ui-color-bg-subtle);
}
.ops-inline-comparison-version {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  border: 1px solid var(--ui-color-border);
  border-radius: var(--ui-radius-md);
  background: var(--ui-color-surface);
}
.ops-inline-comparison-version--candidate {
  border-color: var(--ui-color-brand);
}
.ops-inline-comparison-heading {
  display: grid;
  gap: var(--ui-space-1);
  min-width: 0;
  padding: var(--ui-space-3);
  border-bottom: 1px solid var(--ui-color-border);
  background: var(--ui-color-surface);
}
.ops-inline-comparison-heading .ops-panel-title,
.ops-inline-comparison-heading .ops-panel-subtitle {
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  white-space: normal;
}
.ops-inline-comparison-canvas {
  min-width: 0;
  max-width: 100%;
  padding: var(--ui-space-3);
  overflow: hidden;
}
.ops-inline-comparison-canvas iframe {
  display: block;
  width: 100%;
  min-width: 0;
  border: 0;
}
.ops-inline-comparison-error {
  min-width: 0;
  max-width: 100%;
  padding: var(--ui-space-4);
  color: var(--ui-color-danger);
  overflow-wrap: anywhere;
  white-space: normal;
}
.ops-review-editor-grid { display: grid; flex: 1 1 auto; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto auto minmax(280px, 1fr) auto; gap: var(--ui-space-3); width: 100%; height: 100%; min-width: 0; min-height: 0; padding: var(--ui-space-4); overflow: hidden; }
.ops-review-editor-grid > * { width: 100%; min-width: 0; box-sizing: border-box; }
.ops-review-digest-editor,
.ops-review-body-editor { grid-column: 1 / -1; }
.ops-review-body-editor { min-height: 0; }
.ops-review-body-editor .q-field__control,
.ops-review-body-editor .q-field__control-container,
.ops-review-body-editor textarea.q-field__native { width: 100%; min-width: 0; height: 100%; min-height: 280px; box-sizing: border-box; }
.ops-review-editor-grid > .q-btn { justify-self: end; grid-column: 1 / -1; }
.ops-assets-grid { display: grid; flex: 1 1 auto; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: var(--ui-space-3); width: 100%; height: 100%; min-width: 0; min-height: 0; padding: var(--ui-space-4); overflow-y: auto; }
.ops-assets-grid > .ops-config-section { width: 100%; min-width: 0; height: auto; min-height: 204px; box-sizing: border-box; }
.ops-title-candidates { width: 100%; max-width: 100%; max-height: 360px; min-width: 0; overflow-x: hidden; overflow-y: auto; overflow-wrap: anywhere; }
.ops-title-candidates .q-radio { max-width: 100%; }
.ops-title-candidates .q-radio__label { min-width: 0; overflow-wrap: anywhere; }
.ops-review-failure-status { cursor: pointer; }
.ops-review-failure-reason { max-width: min(560px, calc(100vw - 64px)); white-space: pre-wrap; overflow-wrap: anywhere; }
.ops-review-cover-preview { width: 100%; aspect-ratio: 2.35 / 1; border-radius: var(--ui-radius-sm); overflow: hidden; }
.ops-assets-actions { gap: var(--ui-space-2); margin-top: var(--ui-space-3); }
.ops-history-row { display: flex; align-items: center; gap: var(--ui-space-3); margin: var(--ui-space-3); padding: var(--ui-space-3); border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-md); background: var(--ui-color-bg-subtle); }
.ops-empty-state { display: grid; min-height: 260px; place-items: center; color: var(--ui-color-text-secondary); }
.ops-review-side { display: grid; grid-template-rows: minmax(0, 1fr); gap: var(--ui-space-3); }
.ops-review-ai-panel { display: grid; grid-template-rows: auto minmax(0, 1fr); min-height: 0; height: 100%; }
.ops-review-ai-panel .ops-panel-heading { min-height: 70px; flex-wrap: wrap; align-items: flex-start; }
.ops-review-ai-panel .ops-panel-body { min-height: 0; max-height: none; overflow: auto; scrollbar-width: none; }
.ops-review-progress-runtime { display: contents; }
.ops-review-progress-host { width: min(320px, 48%); min-width: 0; margin-left: auto; }
.ops-review-progress-box { width: 100%; border: 1px solid var(--ui-color-border); border-radius: var(--ui-radius-sm); background: var(--ui-color-bg-subtle); }
.ops-review-progress-box .q-item { min-height: 36px; padding: 5px 9px; }
.ops-review-progress-box .q-expansion-item__content { padding: 0 9px 9px; }
.ops-score-line { display: grid; grid-template-columns: 58px minmax(0, 1fr); align-items: center; gap: var(--ui-space-3); }
.ops-score { flex: 0 0 58px; color: var(--ui-color-brand-hover); }
.ops-score-value { font-weight: var(--ui-font-weight-medium); font-variant-numeric: tabular-nums; }
.ops-review-conclusion { font-weight: var(--ui-font-weight-medium); }
.ops-review-summary { margin-top: var(--ui-space-1); color: var(--ui-color-text-secondary); font-size: var(--ui-font-size-xs); }
.ops-issue-list { display: grid; gap: var(--ui-space-2); margin-top: var(--ui-space-3); }
.ops-issue { display: grid; gap: var(--ui-space-1); padding: 10px; border-left: 3px solid var(--ui-color-warning); color: var(--ui-color-warning); background: var(--ui-color-warning-soft); }
.ops-issue-risk { border-left-color: var(--ui-color-danger); color: var(--ui-color-danger); background: var(--ui-color-danger-soft); }
.ops-issue-manual { border-left-color: var(--ui-color-orange); }
.ops-issue-label { font-weight: var(--ui-font-weight-medium); }
.ops-issue-content,
.ops-issue-manual-note {
  min-width: 0;
  max-width: 100%;
  line-height: var(--ui-line-height-base);
  overflow-wrap: anywhere;
  white-space: normal;
}
.ops-issue-suggestion { color: var(--ui-color-text-primary); }
.ops-issue-verification { color: var(--ui-color-brand-hover); }
.ops-issue-sources {
  min-width: 0;
  max-width: 100%;
  flex-wrap: wrap;
  gap: var(--ui-space-2);
}
.ops-issue-source-link { min-width: 0; max-width: 100%; overflow-wrap: anywhere; }
.ops-issue-manual-note { font-size: var(--ui-font-size-xs); }
.ops-issue-actions { flex-wrap: wrap; gap: var(--ui-space-2); }
.ops-issue-actions .q-btn { max-width: 100%; }
.ops-review-footer-actions { display: grid; grid-template-columns: 1fr 1fr; gap: var(--ui-space-2); margin-top: 14px; }
.ops-review-background-actions { grid-column: 1 / -1; width: 100%; gap: var(--ui-space-2); }
.ops-review-background-actions > * { width: 100%; max-width: 100%; }
.ops-review-rewrite-action { grid-column: 1 / -1; width: 100%; }
.ops-review-confirm-hint { grid-column: 1 / -1; min-width: 0; color: var(--ui-color-warning); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.ops-version-choice-panel {
  display: grid;
  align-content: start;
  gap: var(--ui-space-3);
}
.ops-version-choice-actions {
  display: grid;
  gap: var(--ui-space-2);
  width: 100%;
  min-width: 0;
}
.ops-version-choice-actions .q-btn {
  width: 100%;
  max-width: 100%;
  white-space: normal;
}

.ops-global-activity-dock { top: 58px; right: 18px; width: min(360px, calc(100% - 36px)); max-height: calc(100vh - 76px); border-radius: 15px; box-shadow: var(--ui-shadow-dialog); }
.ops-activity-dock-heading { justify-content: space-between; }
.ops-document-preview-badge { align-items: center; gap: var(--ui-space-1); }

@media (max-width: 1300px) {
  .ops-task-row-card,
  .ops-batch-row-card {
    grid-template-columns: 42px minmax(0, 1fr) minmax(110px, auto) auto !important;
  }
  .ops-task-row-state { display: none !important; }
  .ops-inline-comparison { grid-template-columns: minmax(0, 1fr); }
}

@media (max-height: 820px) {
  .ops-topbar,
  .ops-sidebar-brand { min-height: 58px; height: 58px; }
  .ops-topbar { flex: 0 0 58px; }
  .ops-workbench-shell { padding-left: var(--ui-layout-sidebar-width) !important; }
  .ops-main-nav { top: 58px; bottom: 58px; padding-block: 14px !important; }
  .ops-sidebar-footer { min-height: 58px; }
  .ops-sidebar-health { display: none !important; }
  .ops-main-panels .q-tab-panel.ops-page { padding: 11px 16px 13px !important; }
  .ops-page-description { display: none; }
  .wizard-layout {
    grid-template-areas:
      "heading heading"
      ". ."
      "metrics metrics"
      ". ."
      "workflow priority";
    grid-template-rows: 60px 9px 64px 9px minmax(0, 1fr);
    gap: 0 9px !important;
  }
  .ops-metric-grid { height: 64px; max-height: 64px; }
  .ops-metric-item { min-height: 64px; padding: 9px 10px; }
  .ops-metric-hint { display: none; }
  .ops-recent-panel { display: none; }
  .ops-panel-heading { min-height: 56px; padding: 10px 13px; }
  .ops-panel-subtitle { display: none; }
  .ops-create-form-body { padding: 10px 11px; }
  .ops-create-account-section { padding: 7px 11px 10px; }
  .ops-create-account-choice { min-height: 50px; padding: 6px 8px; }
  .ops-account-directory-item { min-height: 82px; height: auto; }
  .ops-config-body { padding: 9px; gap: 9px; }
  .ops-config-section { padding: 9px; }
  .ops-config-entry { min-height: 48px; padding: 7px; }
  .ops-config-footer { min-height: 50px; }
}

@media (max-width: 1100px) {
  .ops-workbench-shell { padding-left: var(--ui-layout-sidebar-compact) !important; }
  .ops-sidebar-brand,
  .ops-main-nav,
  .ops-sidebar-footer { width: var(--ui-layout-sidebar-compact) !important; }
  .ops-sidebar-brand { justify-content: center; padding: 0; }
  .ops-sidebar-brand-copy,
  .ops-main-nav .q-tabs__content::before,
  .ops-main-nav .q-tab__label,
  .ops-main-nav .q-tab--active::after,
  .ops-sidebar-health,
  .ops-sidebar-profile-copy,
  .ops-sidebar-profile > .q-btn { display: none !important; }
  .ops-main-nav { padding-inline: 10px !important; }
  .ops-main-nav .q-tab { justify-content: center; padding-inline: var(--ui-space-2) !important; }
  .ops-sidebar-profile { justify-content: center; }
  .ops-metric-icon { width: 32px; height: 32px; border-radius: 9px; }
  .ops-metric-item { grid-template-columns: 32px minmax(0, 1fr); gap: 7px; }
  .ops-metric-hint { display: none; }
  .ops-create-priority-panel { display: none; }
  .ops-queue-workspace { grid-template-columns: minmax(0, 1fr); }
  .ops-flow-panel { display: none; }
  .wizard-layout {
    grid-template-areas:
      "heading"
      "."
      "metrics"
      "."
      "workflow"
      "."
      "recent";
    grid-template-columns: 1fr;
    grid-template-rows: 83px 12px 104px 12px minmax(0, 1fr) 12px 164px;
  }
  .ops-task-row-card,
  .ops-batch-row-card { grid-template-columns: 38px minmax(0, 1fr) auto !important; }
  .ops-task-row-state,
  .ops-task-row-badge { display: none !important; }
  .ops-review-layout { grid-template-columns: minmax(0, 1.35fr) minmax(225px, .65fr); }
  .ops-review-progress-host { width: 100%; flex-basis: 100%; }
}
.review-issue-card--verified {
  border-left: 4px solid var(--ui-color-brand) !important;
  background: linear-gradient(90deg, var(--ui-color-brand-soft), #fff 20%) !important;
}
.review-verification-summary {
  min-width: 0;
  max-width: 100%;
  color: var(--accent-dark);
  overflow-wrap: anywhere;
}
.review-evidence-sources {
  min-width: 0;
  max-width: 100%;
  flex-wrap: wrap;
  gap: 8px;
}
.review-evidence-link { min-width: 0; max-width: 100%; overflow-wrap: anywhere; }

@media (max-width: 1100px) and (max-height: 820px) {
  .wizard-layout {
    grid-template-areas:
      "heading"
      "."
      "metrics"
      "."
      "workflow";
    grid-template-rows: 60px 9px 64px 9px minmax(0, 1fr);
  }
}

@media (max-width: 600px) {
  .ops-create-account-section {
    grid-template-columns: 1fr;
    grid-template-rows: auto minmax(0, 1fr) auto auto auto auto;
  }
  .ops-create-status-row { grid-column: 1; grid-row: 5; }
  .ops-create-action-row { grid-column: 1; grid-row: 6; justify-content: stretch; }
  .ops-create-action-row .q-btn { width: 100%; }
}

@media (max-width: 860px) {
  .ops-account-workspace { grid-template-columns: 1fr; }
  .ops-account-directory { display: none; }
  .ops-queue-workspace { grid-template-columns: minmax(0, 1fr); }
  .ops-flow-panel { display: none; }
  .ops-config-entry-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wechat-layout-change-table,
  .wechat-layout-import-previews { grid-template-columns: 1fr; }
}

/* Generated Quasar internals must participate in the workbench's width
   constraints. Dynamic prose wraps; compact stages keep their explicit
   ellipsis rules above. */
.ops-workbench-shell :is(
  .q-field,
  .q-field__inner,
  .q-field__control,
  .q-field__control-container,
  .q-field__native,
  .q-field__input,
  .q-item__section,
  .q-item__label,
  .q-btn__content,
  .q-chip__content
) {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}
.ops-workbench-shell textarea.q-field__native {
  overflow-wrap: anywhere;
  word-break: break-word;
  white-space: pre-wrap;
}

/* Keep navigation and dialogs responsive. Quasar's default 300ms color and
   scale transitions made every tab click and dialog close feel blocked even
   when no application work was running. */
.ops-workbench-shell .q-tab,
.ops-workbench-shell .q-tab *,
.ops-workbench-shell .q-btn,
.ops-workbench-shell .q-btn * {
  transition-duration: var(--ui-motion-fast) !important;
}
.q-dialog__backdrop,
.q-dialog__inner,
.q-dialog__inner > div {
  transition-duration: var(--ui-motion-fast) !important;
  animation-duration: var(--ui-motion-fast) !important;
}
"""


def step_title_html(num: int, text: str) -> str:
    return (
        f'<div class="step-title"><span class="step-num">{num}</span>'
        f"<span>{text}</span></div>"
    )
