export function formatDateTime(value) {
  if (!value) return '—'
  const raw = String(value)
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : `${raw.replace(' ', 'T')}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return raw
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date).replaceAll('/', '-')
}

export function relativeTime(value) {
  if (!value) return '—'
  const raw = String(value)
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/.test(raw) ? raw : `${raw.replace(' ', 'T')}Z`
  const delta = Date.now() - new Date(normalized).getTime()
  if (!Number.isFinite(delta)) return raw
  const minutes = Math.max(0, Math.floor(delta / 60000))
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

export function plainText(value) {
  if (!value) return ''
  const documentValue = new DOMParser().parseFromString(String(value), 'text/html')
  return (documentValue.body.textContent || '')
    .replace(/\\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export function statusLabel(status) {
  return {
    pending: '等待开始',
    processing: '生成中',
    ingesting: '采集素材',
    rewriting: '生成正文',
    title_optimizing: '优化标题',
    rendering: '文章排版',
    ready_for_review: '待审核',
    ready_for_draft: '可写入草稿',
    injecting: '写入草稿中',
    drafted: '已写入草稿',
    published: '已发布',
    failed: '失败',
    partial_failed: '部分失败',
    cancelled: '已终止',
    running: 'AI 评审中',
    completed: '评审完成',
    candidate_ready: '候选稿待选择',
    applied: '已采用改写稿',
    source_kept: '已保留原文',
  }[status] || status || '未知状态'
}

export function statusType(status) {
  if (['drafted', 'published', 'completed', 'applied', 'source_kept'].includes(status)) return 'success'
  if (['failed', 'partial_failed'].includes(status)) return 'danger'
  if (['ready_for_review', 'ready_for_draft', 'candidate_ready'].includes(status)) return 'warning'
  return 'primary'
}
