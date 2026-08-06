import { computed, reactive } from 'vue'

import { api } from '@/api/client'

const STORAGE_KEY = 'wechat-publisher.activities'
const terminalStates = new Set(['completed', 'failed'])
const batchTerminalStates = new Set(['ready_for_review', 'ready_for_draft', 'drafted', 'published', 'failed', 'partial_failed', 'cancelled', 'completed'])
let syncTimer = null
let progressTimer = null

function readStored() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(parsed) ? parsed.slice(0, 20) : []
  } catch {
    return []
  }
}

const state = reactive({
  drawerOpen: false,
  items: readStored(),
})

function persist() {
  const serializable = state.items.map(({ timer, ...item }) => item)
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(serializable.slice(0, 20)))
}

function add({ type, title, description = '', context = {} }) {
  const item = reactive({
    id: `${type}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    type,
    title,
    description,
    context,
    status: 'running',
    progress: 4,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    error: '',
  })
  state.items.unshift(item)
  state.drawerOpen = true
  persist()
  return item
}

function complete(item, description = '') {
  if (!item) return
  item.status = 'completed'
  item.progress = 100
  item.description = description || item.description
  item.updatedAt = new Date().toISOString()
  persist()
}

function fail(item, error) {
  if (!item) return
  item.status = 'failed'
  item.error = String(error?.message || error || '操作失败')
  item.description = item.error
  item.updatedAt = new Date().toISOString()
  persist()
}

function remove(id) {
  const index = state.items.findIndex((item) => item.id === id)
  if (index >= 0) {
    state.items.splice(index, 1)
    persist()
  }
}

function clearFinished() {
  state.items = state.items.filter((item) => !terminalStates.has(item.status))
  persist()
}

function show() {
  state.drawerOpen = true
}

function approximateProgress() {
  let changed = false
  for (const item of state.items) {
    if (item.status !== 'running') continue
    const elapsed = Math.max(0, (Date.now() - new Date(item.createdAt).getTime()) / 1000)
    const estimated = Math.round(8 + 76 * (1 - Math.exp(-elapsed / 48)))
    const next = Math.min(92, Math.max(Number(item.progress || 0), estimated))
    if (next !== item.progress) {
      item.progress = next
      item.updatedAt = new Date().toISOString()
      changed = true
    }
  }
  if (changed) persist()
}

function batchProgress(batch) {
  const total = Number(batch?.progress?.total || batch?.jobs?.length || 0)
  const completed = Number(batch?.progress?.completed || 0)
  return total ? Math.round(completed / total * 100) : 0
}

function createdAfter(item, candidate) {
  const candidateTime = new Date(candidate?.created_at || candidate?.updated_at || 0).getTime()
  const itemTime = new Date(item.createdAt || 0).getTime()
  return Number.isFinite(candidateTime) && candidateTime >= itemTime - 15_000
}

async function syncGeneration(item) {
  const batch = await api.batch(item.context.batchId, false)
  item.progress = Math.max(Number(item.progress || 0), batchProgress(batch))
  const done = Number(batch.progress?.completed || 0)
  const total = Number(batch.progress?.total || batch.jobs?.length || 0)
  item.description = `${done}/${total} 篇已完成生成`
  if (!batchTerminalStates.has(batch.status)) return
  if (['failed', 'partial_failed', 'cancelled'].includes(batch.status)) fail(item, '文章生成存在失败或已终止，请进入任务中心查看详情')
  else complete(item, '文章已生成，等待人工审核')
}

async function syncReview(item) {
  const reviews = await api.reviews({
    batchId: item.context.batchId,
    jobId: item.context.jobId,
    limit: 5,
  })
  const candidate = (reviews || []).find((row) => createdAfter(item, row))
  if (!candidate) return
  item.context.reviewId = candidate.id
  if (candidate.status === 'failed') fail(item, candidate.error || 'AI 评审未完成，请查看详情后重试')
  else if (['completed', 'candidate_ready', 'applied', 'source_kept'].includes(candidate.status)) complete(item, '评审结论已生成，点击查看文章详情')
}

async function syncRewrite(item) {
  const applications = await api.reviewApplications(item.context.reviewId)
  const candidate = (applications || []).find((row) => createdAfter(item, row))
  if (!candidate) return
  if (candidate.status === 'failed') fail(item, candidate.error || 'AI 改写未完成，请查看详情后重试')
  else complete(item, '改写候选稿已生成，请对比后选择最终版本')
}

async function reconcile() {
  const running = state.items.filter((item) => item.status === 'running' && item.context?.batchId)
  await Promise.allSettled(running.map(async (item) => {
    if (item.type === 'generation') await syncGeneration(item)
    else if (item.type === 'review') await syncReview(item)
    else if (item.type === 'rewrite' && item.context.reviewId) await syncRewrite(item)
  }))
  persist()
}

function startSync() {
  if (syncTimer) return
  reconcile()
  syncTimer = window.setInterval(reconcile, 4_000)
  progressTimer = window.setInterval(approximateProgress, 1_200)
}

function stopSync() {
  if (syncTimer) window.clearInterval(syncTimer)
  if (progressTimer) window.clearInterval(progressTimer)
  syncTimer = null
  progressTimer = null
}

export const activity = {
  state,
  activeCount: computed(() => state.items.filter((item) => item.status === 'running').length),
  add,
  complete,
  fail,
  remove,
  clearFinished,
  show,
  reconcile,
  startSync,
  stopSync,
}
