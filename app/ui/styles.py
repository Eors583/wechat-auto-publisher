"""Desktop UI visual theme."""

HEAD_HTML = """
<meta name="description" content="公众号智能运营助手：从选题、AI 创作和评审到微信公众号草稿，一站式完成内容生产。">
<script>document.documentElement.lang = 'zh-CN';</script>
"""

APP_CSS = """
:root {
  --bg0: #f0f3f2;
  --bg1: #f8faf9;
  --panel: rgba(255,255,255,0.96);
  --panel-solid: #ffffff;
  --ink: #16221e;
  --muted: #65736d;
  --line: #e3e9e6;
  --line-strong: #d5dfda;
  --accent: #087a63;
  --accent-2: #10a37f;
  --accent-dark: #075f4e;
  --accent-soft: #e6f6f1;
  --warn: #8a5a12;
  --warn-soft: #fff3dd;
  --danger: #9d2430;
  --danger-soft: #fce8ea;
  --shadow: 0 1px 2px rgba(16, 34, 27, 0.04), 0 10px 30px rgba(16, 34, 27, 0.055);
  --shadow-hover: 0 14px 34px rgba(16, 34, 27, 0.09);
  --radius: 16px;
}

@keyframes fade-up {
  from { opacity: 0; transform: translateY(7px); }
  to { opacity: 1; transform: translateY(0); }
}
@keyframes soft-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(12, 92, 75, 0.0); }
  50% { box-shadow: 0 0 0 6px rgba(12, 92, 75, 0.08); }
}

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
  font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
  min-height: 100vh;
}

.nicegui-content {
  display: block !important;
  padding: 0 !important;
}

.shell {
  width: min(100%, 1240px);
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
  backdrop-filter: blur(14px);
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
  transition: border-color .18s ease, background .18s ease, transform .18s ease;
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
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 20px;
  margin: 0;
  box-shadow: var(--shadow);
  backdrop-filter: blur(12px);
  min-width: 0;
  transition: border-color .2s ease, box-shadow .2s ease;
}
.card:hover {
  border-color: var(--line-strong);
  box-shadow: var(--shadow-hover);
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
  transition: border-color .18s ease, transform .18s ease, background .18s ease, box-shadow .18s ease;
}
.topic-item:hover {
  border-color: rgba(8,122,99,0.34);
  transform: translateY(-2px);
  background: #fbfffd;
  box-shadow: 0 8px 20px rgba(16,34,27,.06);
}
.article-item {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 10px 12px;
  margin: 6px 0;
  cursor: pointer;
  background: #f7faf9;
  transition: background .18s ease, border-color .18s ease, transform .18s ease;
}
.article-item:hover {
  border-color: rgba(12,92,75,0.4);
  background: var(--accent-soft);
  transform: translateX(2px);
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
  background: rgba(9, 37, 36, 0.42);
  backdrop-filter: blur(2px);
}
.request-loading-card {
  min-width: 320px;
  max-width: min(88vw, 460px);
  padding: 30px 34px;
  border-radius: 18px;
  box-shadow: 0 20px 60px rgba(8, 48, 46, 0.22);
}
.request-loading-message {
  color: var(--ink);
  font-size: 17px;
  font-weight: 650;
  line-height: 1.6;
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
  animation: soft-pulse 2.4s ease infinite;
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
  transition: background .15s ease;
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
  backdrop-filter: blur(14px);
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
.article-body-input .q-field__control {
  min-height: 300px;
}
.article-body-input textarea.q-field__native {
  min-height: 250px !important;
  resize: vertical !important;
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


def step_title_html(num: int, text: str) -> str:
    return (
        f'<div class="step-title"><span class="step-num">{num}</span>'
        f"<span>{text}</span></div>"
    )
