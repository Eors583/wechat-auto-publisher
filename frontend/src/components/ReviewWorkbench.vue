<script setup>
import {
  ArrowLeft,
  Check,
  CircleCheck,
  Close,
  Document,
  EditPen,
  MagicStick,
  Refresh,
  Select,
  Warning,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { api } from '@/api/client'
import { activity } from '@/stores/activity'
import { formatDateTime, plainText, statusLabel, statusType } from '@/utils/format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  batchId: { type: String, default: '' },
  jobId: { type: [String, Number], default: 0 },
})
const emit = defineEmits(['update:modelValue', 'updated'])

const loading = ref(false)
const saving = ref(false)
const action = ref('')
const batch = ref(null)
const job = ref(null)
const review = ref(null)
const application = ref(null)
const profiles = ref([])
const options = ref({})
const tab = ref('quick')
const profileId = ref('')
const strictness = ref('standard')
const roleIds = ref([])
const selectedIssueIds = ref([])
const editor = ref({ title: '', subtitle: '', digest: '', body: '' })
const resultAnchor = ref(null)
const versions = ref([])
const attempts = ref([])
const covers = ref([])
const advancedLoading = ref(false)
const selectedTitleIndex = ref(0)
const selectedSubtitleIndex = ref(null)
const paragraphIndex = ref(0)
const paragraphInstruction = ref('')
let pollTimer = null

const visible = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})
const result = computed(() => review.value?.result || {})
const issues = computed(() => Array.isArray(result.value.issues) ? result.value.issues : [])
const editableIssues = computed(() => issues.value.filter(canAutoApply))
const safetyIssues = computed(() => issues.value.filter((item) => !canAutoApply(item)))
const sourceSnapshot = computed(() => review.value?.source_snapshot || {})
const candidateSnapshot = computed(() => review.value?.rewritten_snapshot || application.value?.candidate_snapshot || {})
const isBusy = computed(() => ['running', 'rewriting'].includes(review.value?.status) || Boolean(action.value))
const hasCandidate = computed(() => ['candidate_ready', 'applied', 'source_kept'].includes(review.value?.status) && Boolean(candidateSnapshot.value.body))
const articlePosition = computed(() => Math.max(1, (batch.value?.jobs || []).findIndex((item) => Number(item.id) === Number(props.jobId)) + 1))
const inlineImages = computed(() => Array.isArray(job.value?.meta?.inline_images) ? job.value.meta.inline_images : [])
const paragraphs = computed(() => String(editor.value.body || '').replace(/\r\n/g, '\n').split(/\n\s*\n/).map((item) => item.trim()).filter(Boolean))

function canAutoApply(issue) {
  return Boolean(issue?.can_auto_apply) && !issue?.blocks_draft && !['fact_checker', 'compliance_expert'].includes(issue?.role_id)
}

function severityType(severity) {
  return severity === 'high' ? 'danger' : severity === 'low' ? 'info' : 'warning'
}

function severityLabel(severity) {
  return { high: '高优先级', medium: '中优先级', low: '低优先级' }[severity] || severity
}

async function loadLatestReview() {
  const rows = await api.reviews({ jobId: Number(props.jobId), batchId: props.batchId, limit: 1 })
  review.value = rows[0] || null
  if (review.value) {
    const apps = await api.reviewApplications(review.value.id).catch(() => [])
    application.value = review.value.application || apps[0] || null
  } else application.value = null
  return review.value
}

function syncEditor() {
  editor.value = {
    title: job.value?.selected_title || job.value?.titles?.[0] || '',
    subtitle: job.value?.selected_subtitle || job.value?.subtitles?.[0] || '',
    digest: job.value?.digest || '',
    body: job.value?.body || '',
  }
  selectedTitleIndex.value = Math.max(0, (job.value?.titles || []).findIndex((item) => item === job.value?.selected_title))
  const subtitlePosition = (job.value?.subtitles || []).findIndex((item) => item === job.value?.selected_subtitle)
  selectedSubtitleIndex.value = subtitlePosition >= 0 ? subtitlePosition : null
  paragraphIndex.value = Math.max(0, Math.min(paragraphIndex.value, paragraphs.value.length - 1))
}

function applyParagraphs(items, selected) {
  editor.value.body = items.join('\n\n')
  paragraphIndex.value = Math.max(0, Math.min(selected, items.length - 1))
}

function moveParagraph(offset) {
  const items = [...paragraphs.value]
  const source = Number(paragraphIndex.value)
  const target = source + offset
  if (source < 0 || target < 0 || source >= items.length || target >= items.length) return
  ;[items[source], items[target]] = [items[target], items[source]]
  applyParagraphs(items, target)
}

async function deleteParagraph() {
  const items = [...paragraphs.value]
  const selected = Number(paragraphIndex.value)
  if (!items[selected]) return
  try {
    await ElMessageBox.confirm(`确定删除第 ${selected + 1} 段吗？保存前仍可关闭工作台放弃本次编辑。`, '删除正文段落', { type: 'warning' })
    items.splice(selected, 1)
    applyParagraphs(items, Math.max(0, selected - 1))
  } catch {
    // The paragraph remains unchanged when the operator cancels.
  }
}

async function regenerateParagraph() {
  const instruction = paragraphInstruction.value.trim()
  if (!instruction) return ElMessage.warning('请先填写这段正文的修改要求')
  if (!paragraphs.value[paragraphIndex.value]) return ElMessage.warning('请选择一个有效段落')
  action.value = 'paragraph'
  const task = activity.add({
    type: 'rewrite',
    title: `正在后台改写《${editor.value.title || '未命名文章'}》第 ${paragraphIndex.value + 1} 段`,
    description: '只替换所选段落，其他正文和已审核图片保持不变',
    context: { batchId: props.batchId, jobId: Number(props.jobId) },
  })
  try {
    await api.updateJob(props.batchId, Number(props.jobId), { ...editor.value })
    await api.regenerateParagraph(props.batchId, Number(props.jobId), Number(paragraphIndex.value), instruction)
    await Promise.all([refreshArticle(), loadAdvanced()])
    review.value = null
    application.value = null
    paragraphInstruction.value = ''
    activity.complete(task, '所选段落已更新，文章需要重新确认')
    ElMessage.success('所选段落已按要求改写；修改前版本可在历史版本中恢复')
    emit('updated')
  } catch (error) {
    activity.fail(task, error)
    ElMessage.error(`段落改写未完成：${error.message}`)
  } finally {
    action.value = ''
  }
}

async function loadAdvanced() {
  if (!props.batchId || !props.jobId) return
  advancedLoading.value = true
  try {
    const [versionRows, attemptRows, coverRows] = await Promise.all([
      api.versions(props.batchId, Number(props.jobId)).catch(() => []),
      api.jobAttempts(props.batchId, Number(props.jobId)).catch(() => []),
      api.covers(props.batchId, Number(props.jobId)).catch(() => []),
    ])
    versions.value = versionRows
    attempts.value = attemptRows
    covers.value = coverRows
  } finally {
    advancedLoading.value = false
  }
}

async function applyTitleSelection() {
  action.value = 'title'
  try {
    await api.selectJobTitle(
      props.batchId,
      Number(props.jobId),
      Number(selectedTitleIndex.value),
      selectedSubtitleIndex.value == null ? null : Number(selectedSubtitleIndex.value),
    )
    await refreshArticle()
    ElMessage.success('标题与副标题已更新')
    emit('updated')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    action.value = ''
  }
}

async function restoreVersion(item) {
  try {
    await ElMessageBox.confirm(`确定恢复“${item.reason || '历史版本'}”吗？当前版本会先自动保存。`, '恢复历史版本', { type: 'warning' })
    action.value = `version-${item.id}`
    await api.restoreVersion(props.batchId, Number(props.jobId), item.id)
    await Promise.all([refreshArticle(), loadAdvanced()])
    review.value = null
    application.value = null
    ElMessage.success('历史版本已恢复，请重新检查并确认文章')
    emit('updated')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  } finally {
    action.value = ''
  }
}

async function runAssetTask(key, title, callback, success) {
  action.value = key
  const task = activity.add({
    type: 'asset',
    title,
    description: '素材正在后台生成，可以关闭工作台继续处理其他任务',
    context: { batchId: props.batchId, jobId: Number(props.jobId) },
  })
  try {
    await callback()
    activity.complete(task, success)
    await Promise.all([refreshArticle(), loadAdvanced()])
    ElMessage.success(success)
    emit('updated')
  } catch (error) {
    activity.fail(task, error)
    ElMessage.error(error.message)
  } finally {
    action.value = ''
  }
}

async function regenerateAllImages() {
  await runAssetTask(
    'images-all',
    `正在重新生成《${editor.value.title || '未命名文章'}》的正文配图`,
    () => api.regenerateInlineImages(props.batchId, Number(props.jobId)),
    '正文配图已重新生成',
  )
}

async function regenerateOneImage(image) {
  try {
    const { value } = await ElMessageBox.prompt('说明希望如何修改这张图片', '定向重做正文配图', {
      inputPlaceholder: '例如：画面更简洁，改成蓝绿色商务插画，不要文字',
      inputValidator: (text) => Boolean(String(text || '').trim()) || '请填写修改要求',
      confirmButtonText: '后台生成',
    })
    const imageIndex = Number(image.index ?? image.image_index)
    await runAssetTask(
      `image-${imageIndex}`,
      `正在重做《${editor.value.title || '未命名文章'}》第 ${imageIndex} 张配图`,
      () => api.regenerateInlineImage(props.batchId, Number(props.jobId), imageIndex, value),
      '指定正文配图已更新',
    )
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

async function deleteOneImage(image) {
  try {
    const imageIndex = Number(image.index ?? image.image_index)
    await ElMessageBox.confirm(`确定删除第 ${imageIndex} 张正文配图吗？`, '删除正文配图', { type: 'warning' })
    action.value = `delete-image-${imageIndex}`
    await api.deleteInlineImage(props.batchId, Number(props.jobId), imageIndex)
    await refreshArticle()
    ElMessage.success('正文配图已删除并重新排版')
    emit('updated')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  } finally {
    action.value = ''
  }
}

async function generateCover() {
  try {
    const { value } = await ElMessageBox.prompt('可选：补充封面的视觉要求', '后台生成 AI 封面', {
      inputPlaceholder: '例如：深蓝色科技感、简洁、不要文字；也可以留空',
      confirmButtonText: '后台生成',
      inputValue: '',
    })
    await runAssetTask(
      'cover-generate',
      `正在生成《${editor.value.title || '未命名文章'}》的 AI 封面`,
      () => api.generateCover(props.batchId, Number(props.jobId), value || ''),
      'AI 封面已生成并选用',
    )
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

async function chooseCover(cover) {
  action.value = `cover-${cover.media_id}`
  try {
    await api.selectCover(props.batchId, Number(props.jobId), cover.media_id)
    await refreshArticle()
    ElMessage.success('封面素材已选用')
    emit('updated')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    action.value = ''
  }
}

function applyDefaultSettings(defaultValue) {
  profileId.value = defaultValue?.profile_id || profiles.value[0]?.id || ''
  const config = defaultValue?.config || profiles.value.find((item) => item.id === profileId.value)?.config || {}
  strictness.value = config.strictness || 'standard'
  roleIds.value = [...(config.role_ids || options.value.roles?.slice(0, 3).map((item) => item.id) || [])]
}

async function load() {
  if (!props.batchId || !props.jobId) return
  loading.value = true
  try {
    const [loadedBatch, profileRows, optionRows] = await Promise.all([
      api.batch(props.batchId, true),
      api.editorialProfiles(),
      api.editorialOptions(),
    ])
    batch.value = loadedBatch
    job.value = loadedBatch.jobs?.find((item) => Number(item.id) === Number(props.jobId)) || null
    if (!job.value) throw new Error('未找到需要审核的文章')
    profiles.value = profileRows
    options.value = optionRows
    const defaultValue = job.value.account_id ? await api.editorialDefault(job.value.account_id).catch(() => null) : null
    applyDefaultSettings(defaultValue)
    syncEditor()
    await Promise.all([api.markViewed(props.batchId, Number(props.jobId)).catch(() => null), loadLatestReview()])
    startPolling()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    if (!visible.value && !isBusy.value) return
    try {
      const latest = await loadLatestReview()
      if (latest && !['running', 'rewriting'].includes(latest.status) && !action.value) stopPolling()
    } catch {
      // A transient poll failure must not interrupt an in-flight AI request.
    }
  }, 3500)
}

function stopPolling() {
  if (pollTimer) window.clearInterval(pollTimer)
  pollTimer = null
}

async function refreshArticle() {
  const loaded = await api.batch(props.batchId, true)
  batch.value = loaded
  job.value = loaded.jobs?.find((item) => Number(item.id) === Number(props.jobId)) || job.value
  syncEditor()
}

async function saveArticle() {
  saving.value = true
  try {
    job.value = await api.updateJob(props.batchId, Number(props.jobId), { ...editor.value })
    await api.rerenderJob(props.batchId, Number(props.jobId))
    await refreshArticle()
    review.value = null
    application.value = null
    ElMessage.success('文章修改与排版已保存；已有 AI 评审需要重新执行')
    emit('updated')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function runReview() {
  if (!roleIds.value.length) return ElMessage.warning('请至少选择一个评审角色')
  action.value = 'review'
  const task = activity.add({
    type: 'review',
    title: `正在评审《${editor.value.title || '未命名文章'}》`,
    description: 'AI 正在分析标题、留存、完读、点赞与转发潜力',
    context: { batchId: props.batchId, jobId: Number(props.jobId) },
  })
  startPolling()
  try {
    review.value = await api.runReview(props.batchId, Number(props.jobId), {
      profile_id: profileId.value || null,
      config: { strictness: strictness.value, role_ids: roleIds.value },
    })
    activity.complete(task, '评审结论已生成，点击文章可查看详情')
    ElMessage.success('AI 评审已完成')
    await nextTick()
    resultAnchor.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
    emit('updated')
  } catch (error) {
    activity.fail(task, error)
    await loadLatestReview().catch(() => null)
    ElMessage.error(`AI 评审未完成：${error.message}`)
  } finally {
    action.value = ''
    stopPolling()
  }
}

async function generateRewrite() {
  if (!review.value?.id) return
  if (!selectedIssueIds.value.length) return ElMessage.warning('请先勾选至少一条可改写的改进意见')
  action.value = 'rewrite'
  const task = activity.add({
    type: 'rewrite',
    title: `正在后台改写《${editor.value.title || '未命名文章'}》`,
    description: `按 ${selectedIssueIds.value.length} 条已勾选意见生成候选稿，原文不会被覆盖`,
    context: { batchId: props.batchId, jobId: Number(props.jobId), reviewId: review.value.id },
  })
  startPolling()
  try {
    const updated = await api.rewriteCandidate(props.batchId, Number(props.jobId), review.value.id, {
      issue_ids: selectedIssueIds.value,
      rewrite_mode: 'selected_issues',
      paragraph_numbers: [],
      instruction: '',
    })
    review.value = updated
    application.value = updated.application || (await api.reviewApplications(updated.id))[0] || null
    activity.complete(task, '改写候选稿已生成，请对比后选择最终版本')
    ElMessage.success('候选稿已生成，尚未覆盖当前正文')
    emit('updated')
  } catch (error) {
    activity.fail(task, error)
    await loadLatestReview().catch(() => null)
    ElMessage.error(`生成候选稿未完成：${error.message}`)
  } finally {
    action.value = ''
    stopPolling()
  }
}

async function chooseVersion(useRewrite) {
  const applicationId = application.value?.id || review.value?.application?.id
  if (!applicationId) return ElMessage.error('没有找到可选择的候选稿记录')
  const label = useRewrite ? '采用 AI 改写稿' : '保留改写前原文'
  try {
    await ElMessageBox.confirm(`确定${label}作为最终文章版本吗？`, '选择最终版本', {
      confirmButtonText: label,
      cancelButtonText: '继续对比',
      type: useRewrite ? 'success' : 'warning',
    })
    action.value = useRewrite ? 'apply' : 'keep'
    if (useRewrite) await api.applyCandidate(props.batchId, Number(props.jobId), applicationId)
    else await api.keepSource(props.batchId, Number(props.jobId), applicationId)
    await Promise.all([refreshArticle(), loadLatestReview()])
    ElMessage.success(`已${label}`)
    emit('updated')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  } finally {
    action.value = ''
  }
}

async function resolveRisk(issue, resolution) {
  const note = resolution === 'resolved' ? '运营人员已人工核实' : resolution === 'waived' ? '运营人员选择保留原文并接受风险' : ''
  try {
    review.value = await api.resolveIssue(review.value.id, issue.id, {
      resolution,
      note,
      resolved_by: 'Web 运营人员',
    })
    ElMessage.success('风险处理结果已保存')
    emit('updated')
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function confirmArticle() {
  try {
    await api.confirmJob(props.batchId, Number(props.jobId))
    await refreshArticle()
    ElMessage.success('文章已确认，可写入公众号草稿箱')
    emit('updated')
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function requestChanges() {
  try {
    await api.needsChanges(props.batchId, Number(props.jobId))
    await refreshArticle()
    ElMessage.success('文章已标记为需要修改')
    emit('updated')
  } catch (error) {
    ElMessage.error(error.message)
  }
}

watch(() => [visible.value, props.batchId, props.jobId], ([isOpen]) => {
  if (isOpen) load()
  else if (!isBusy.value) stopPolling()
}, { immediate: true })
watch(tab, (value) => {
  if (value === 'assets') loadAdvanced()
})
onBeforeUnmount(stopPolling)
</script>

<template>
  <el-dialog
    v-model="visible"
    class="review-dialog"
    width="min(1380px, 96vw)"
    top="2.5vh"
    :destroy-on-close="false"
    :close-on-click-modal="false"
    :show-close="false"
    append-to-body
  >
    <template #header>
      <header class="review-header">
        <div class="review-header__main">
          <el-button circle text :icon="ArrowLeft" aria-label="关闭审核" @click="visible = false" />
          <span class="review-header__icon"><Document /></span>
          <div><h2>文章审核工作台</h2><p>{{ job?.account_name || '公众号' }} · 批次 #{{ batch?.display_id || batchId }} · 第 {{ articlePosition }}/{{ batch?.jobs?.length || 1 }} 篇</p></div>
        </div>
        <div class="review-header__actions">
          <el-tag v-if="job" :type="statusType(job.review_status)" round effect="light">{{ job.review_status === 'confirmed' ? '文章已确认' : '待人工确认' }}</el-tag>
          <el-button :icon="Close" circle plain aria-label="关闭" @click="visible = false" />
        </div>
      </header>
    </template>

    <div v-if="job" class="review-shell" :class="{ 'is-loading': loading }">
      <div class="review-toolbar">
        <el-segmented v-model="tab" :options="[{ label: '快速审核', value: 'quick' }, { label: '深度编辑与 AI 评审', value: 'deep' }, { label: '标题 · 图片 · 历史', value: 'assets' }]" />
        <p>AI 任务可以进入后台运行；关闭此窗口不会中断评审或改写。</p>
      </div>

      <section class="review-overview">
        <div><span>文章标题</span><strong>{{ editor.title || '未设置标题' }}</strong></div>
        <div><span>正文长度</span><strong>{{ plainText(editor.body).length.toLocaleString() }} 字</strong></div>
        <div><span>AI 评审</span><strong>{{ review ? statusLabel(review.status) : '尚未评审' }}</strong></div>
        <div><span>阻断风险</span><strong :class="{ 'text-danger': review?.blocking_count }">{{ review?.blocking_count || 0 }} 项</strong></div>
      </section>

      <section v-if="tab === 'quick'" class="review-quick-layout">
        <el-card class="element-surface quick-decision-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>审核决策</h3><p>快速确认文章状态与 AI 结论</p></div><el-tag v-if="review" :type="statusType(review.status)">{{ statusLabel(review.status) }}</el-tag></div></template>
          <div v-if="review?.result" class="quick-score"><el-progress type="dashboard" :percentage="Number(result.overall_score || 0)" :width="132"><template #default="{ percentage }"><strong>{{ percentage }}</strong><span>AI 潜力分</span></template></el-progress><div><h4>{{ result.conclusion || 'AI 评审已完成' }}</h4><p>{{ result.summary }}</p><el-button type="primary" text @click="tab = 'deep'">查看全部评审意见</el-button></div></div>
          <el-empty v-else description="还没有 AI 评审结论" :image-size="86"><el-button type="primary" :loading="action === 'review'" @click="tab = 'deep'">进入深度评审</el-button></el-empty>
          <div class="decision-actions"><el-button :icon="EditPen" @click="requestChanges">需要修改</el-button><el-button type="success" :icon="CircleCheck" :disabled="Boolean(review?.blocking_count) || review?.status === 'candidate_ready'" @click="confirmArticle">确认这篇文章</el-button></div>
        </el-card>
        <el-card class="element-surface article-glance" shadow="never"><template #header><div><h3>文章摘要</h3><p>确认标题、摘要与正文开头是否符合预期</p></div></template><h4>{{ editor.title }}</h4><p v-if="editor.subtitle" class="article-glance__subtitle">{{ editor.subtitle }}</p><p>{{ editor.digest || plainText(editor.body).slice(0, 220) }}</p><el-divider /><pre>{{ plainText(editor.body).slice(0, 900) }}</pre><el-button text type="primary" @click="tab = 'deep'">打开完整正文编辑器</el-button></el-card>
      </section>

      <div v-else-if="tab === 'deep'" class="review-deep-stack">
        <el-card class="element-surface editor-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>文章正文</h3><p>直接修改后保存会使旧评审失效，防止结论错配</p></div><el-button type="primary" :icon="Check" :loading="saving" @click="saveArticle">保存修改并重新排版</el-button></div></template>
          <div class="form-grid"><el-input v-model="editor.title" size="large" placeholder="文章标题" /><el-input v-model="editor.subtitle" size="large" placeholder="副标题（可选）" /></div>
          <el-input v-model="editor.digest" type="textarea" :rows="2" placeholder="文章摘要" />
          <el-input v-model="editor.body" class="body-editor" type="textarea" :rows="18" resize="vertical" placeholder="正文内容" />
        </el-card>

        <el-card class="element-surface paragraph-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>AI 定点改写（单段）</h3><p>只替换所选段落，修改前版本会自动保留在历史记录中</p></div><el-tag effect="plain">共 {{ paragraphs.length }} 段</el-tag></div></template>
          <div class="paragraph-tool-grid">
            <el-select v-model="paragraphIndex" placeholder="选择正文段落"><el-option v-for="(paragraph, index) in paragraphs" :key="`${index}-${paragraph.slice(0, 20)}`" :label="`第 ${index + 1} 段 · ${plainText(paragraph).slice(0, 46)}`" :value="index" /></el-select>
            <div class="inline-actions"><el-button :disabled="paragraphIndex <= 0" @click="moveParagraph(-1)">上移</el-button><el-button :disabled="paragraphIndex >= paragraphs.length - 1" @click="moveParagraph(1)">下移</el-button><el-button type="danger" plain :disabled="!paragraphs.length" @click="deleteParagraph">删除此段</el-button></div>
          </div>
          <el-input :model-value="paragraphs[paragraphIndex] || ''" type="textarea" :rows="5" readonly placeholder="当前没有可编辑段落" />
          <el-input v-model="paragraphInstruction" type="textarea" :rows="3" maxlength="2000" show-word-limit placeholder="例如：压缩到 120 字，突出经营风险；语气更克制，并保留原有数据" />
          <div class="paragraph-action"><span>模型会参考标题、前后文和原段落；请求可进入后台运行。</span><el-button type="primary" :icon="MagicStick" :loading="action === 'paragraph'" :disabled="!paragraphs.length || isBusy" @click="regenerateParagraph">后台改写所选段落</el-button></div>
        </el-card>

        <el-card class="element-surface ai-review-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>AI 评审团</h3><p>评审只给结论和建议，不会自动覆盖正文</p></div><el-button :icon="Refresh" text @click="loadLatestReview">刷新结果</el-button></div></template>
          <div class="review-settings-row"><el-select v-model="profileId" placeholder="评审方案"><el-option v-for="profile in profiles" :key="profile.id" :label="profile.name" :value="profile.id" /></el-select><el-segmented v-model="strictness" :options="[{ label: '宽松', value: 'relaxed' }, { label: '标准', value: 'standard' }, { label: '严格', value: 'strict' }]" /><el-button type="primary" :icon="MagicStick" :loading="action === 'review'" :disabled="isBusy && action !== 'review'" @click="runReview">{{ review ? '重新 AI 评审' : '开始 AI 评审' }}</el-button></div>
          <el-collapse class="advanced-collapse"><el-collapse-item title="评审角色与高级设置" name="roles"><el-checkbox-group v-model="roleIds" class="role-selector"><el-checkbox v-for="role in options.roles || []" :key="role.id" :value="role.id" border>{{ role.name || role.label }}</el-checkbox></el-checkbox-group></el-collapse-item></el-collapse>
          <el-alert v-if="isBusy" type="info" :closable="false" show-icon><template #title>{{ review?.status === 'rewriting' ? 'AI 正在后台生成候选稿' : 'AI 正在后台评审文章' }}</template><template #default>可以关闭当前工作台继续使用其他功能，右上角“后台任务”会持续显示百分比进度。</template></el-alert>
        </el-card>

        <section v-if="review" ref="resultAnchor" class="review-result-section">
          <div class="review-result-heading"><div><span class="eyebrow">AI REVIEW RESULT</span><h3>评审结果</h3><p>{{ review.profile_name || 'AI 评审团' }} · {{ review.model_name || '公众号绑定模型' }} · {{ formatDateTime(review.completed_at || review.updated_at) }}</p></div><div class="review-result-heading__score"><strong>{{ result.overall_score ?? '—' }}</strong><span>总分</span><el-tag :type="statusType(review.status)" round>{{ statusLabel(review.status) }}</el-tag></div></div>
          <el-alert v-if="review.status === 'failed'" type="error" :closable="false" show-icon :title="review.error || '本次评审未成功完成'" />
          <template v-else-if="result && Object.keys(result).length">
            <el-card class="element-surface conclusion-card" shadow="never"><h4>{{ result.conclusion || '评审结论' }}</h4><p>{{ result.summary }}</p><div v-if="result.strengths?.length" class="strength-list"><span v-for="item in result.strengths" :key="item"><CircleCheck />{{ item }}</span></div></el-card>
            <div class="dimension-grid"><article v-for="dimension in result.dimensions || []" :key="dimension.id"><div><span>{{ dimension.name }}</span><strong>{{ dimension.score ?? '—' }}</strong></div><el-progress :percentage="Number(dimension.score || 0)" :show-text="false" :stroke-width="7" /><p>{{ dimension.summary }}</p></article></div>

            <div v-if="editableIssues.length" class="issue-group"><div class="issue-group__heading"><div><h4>整体改进方向</h4><p>勾选需要改写的意见，再生成一份不覆盖原文的候选稿</p></div><el-tag effect="plain">已选 {{ selectedIssueIds.length }}/{{ editableIssues.length }}</el-tag></div><el-checkbox-group v-model="selectedIssueIds" class="issue-list"><label v-for="issue in editableIssues" :key="issue.id" class="issue-card"><el-checkbox :value="issue.id" /><div class="issue-card__body"><div><el-tag size="small" :type="severityType(issue.severity)">{{ severityLabel(issue.severity) }}</el-tag><el-tag size="small" effect="plain">{{ issue.role_name }}</el-tag><span>{{ issue.category }}</span></div><h5>{{ issue.problem }}</h5><p>{{ issue.suggestion }}</p></div></label></el-checkbox-group><div class="rewrite-action-bar"><div><strong>生成候选稿，不自动替换正文</strong><span>完成后会展示改写前后对比，并由你选择最终版本</span></div><el-button type="primary" :icon="MagicStick" :loading="action === 'rewrite'" :disabled="!selectedIssueIds.length || isBusy" @click="generateRewrite">后台改写已勾选意见</el-button></div></div>

            <div v-if="safetyIssues.length" class="issue-group issue-group--risk"><div class="issue-group__heading"><div><h4>事实与合规风险</h4><p>这类问题不能交给 AI 猜测，需要人工核实或明确接受风险</p></div><el-tag type="danger" effect="plain">{{ safetyIssues.filter((item) => item.resolution === 'open').length }} 项待处理</el-tag></div><article v-for="issue in safetyIssues" :key="issue.id" class="risk-card"><div class="risk-card__icon"><Warning /></div><div class="risk-card__body"><div><el-tag size="small" type="danger">{{ issue.role_name }}</el-tag><el-tag v-if="issue.blocks_draft && issue.resolution === 'open'" size="small" type="danger" effect="dark">阻止写入草稿</el-tag><el-tag v-if="issue.resolution !== 'open'" size="small" type="success">已处理</el-tag></div><h5>{{ issue.problem }}</h5><p>{{ issue.suggestion }}</p><small v-if="issue.excerpt">原文：{{ issue.excerpt }}</small><div class="risk-card__actions"><template v-if="issue.resolution === 'open'"><el-button size="small" type="success" plain @click="resolveRisk(issue, 'resolved')">我已人工核实</el-button><el-button size="small" type="warning" plain @click="resolveRisk(issue, 'waived')">保留原文并接受风险</el-button></template><el-button v-else size="small" text @click="resolveRisk(issue, 'open')">恢复待核实</el-button></div></div></article></div>
          </template>
        </section>

        <section v-if="hasCandidate" class="comparison-section">
          <div class="comparison-heading"><div><span class="eyebrow">VERSION COMPARISON</span><h3>改写前后文章对比</h3><p v-if="review.status === 'candidate_ready'">候选稿尚未覆盖正文，请对比后明确选择最终版本。</p><p v-else>{{ review.status === 'applied' ? '已采用右侧改写稿。' : '已保留左侧原文，右侧仅作为历史记录。' }}</p></div><el-tag type="warning" round>{{ review.status === 'candidate_ready' ? '等待选择' : statusLabel(review.status) }}</el-tag></div>
          <el-alert v-for="warning in candidateSnapshot.risk_warnings || []" :key="warning.title + warning.message" type="warning" :closable="false" show-icon :title="warning.title || '关键数字变化'" :description="warning.message" />
          <div v-if="candidateSnapshot.change_summary" class="change-summary"><MagicStick /><span><strong>AI 修改摘要</strong>{{ candidateSnapshot.change_summary }}</span></div>
          <div class="comparison-grid"><article class="version-card"><header><div><span>ORIGINAL</span><h4>改写前原文</h4></div><el-tag effect="plain">{{ plainText(sourceSnapshot.body).length }} 字</el-tag></header><h5>{{ sourceSnapshot.title }}</h5><p v-if="sourceSnapshot.digest" class="version-card__digest">{{ sourceSnapshot.digest }}</p><pre>{{ plainText(sourceSnapshot.body) }}</pre></article><article class="version-card version-card--candidate"><header><div><span>AI CANDIDATE</span><h4>AI 改写候选稿</h4></div><el-tag type="success" effect="plain">{{ plainText(candidateSnapshot.body).length }} 字</el-tag></header><h5>{{ candidateSnapshot.title }}</h5><p v-if="candidateSnapshot.digest" class="version-card__digest">{{ candidateSnapshot.digest }}</p><pre>{{ plainText(candidateSnapshot.body) }}</pre></article></div>
          <div v-if="review.status === 'candidate_ready'" class="version-choice"><div><strong>请选择最终使用的文章版本</strong><span>系统不会默认采用任何一边，只有点击确认后才会更新正文。</span></div><div><el-button size="large" :icon="ArrowLeft" :loading="action === 'keep'" @click="chooseVersion(false)">保留改写前原文</el-button><el-button type="success" size="large" :icon="Select" :loading="action === 'apply'" @click="chooseVersion(true)">采用 AI 改写稿</el-button></div></div>
        </section>
      </div>

      <div v-else class="review-deep-stack" :class="{ 'is-loading': advancedLoading }">
        <el-card class="element-surface" shadow="never">
          <template #header><div class="card-header-row"><div><h3>标题候选</h3><p>选择生成阶段产出的主标题与副标题，确认后会重新排版</p></div><el-button type="primary" :loading="action === 'title'" @click="applyTitleSelection">应用标题</el-button></div></template>
          <div class="title-candidate-grid">
            <el-form-item label="主标题"><el-radio-group v-model="selectedTitleIndex" class="candidate-radio-list"><el-radio v-for="(title, index) in job.titles || []" :key="`${index}-${title}`" :value="index" border>{{ title }}</el-radio></el-radio-group></el-form-item>
            <el-form-item label="副标题（可不选）"><el-radio-group v-model="selectedSubtitleIndex" class="candidate-radio-list"><el-radio :value="null" border>不使用副标题</el-radio><el-radio v-for="(subtitle, index) in job.subtitles || []" :key="`${index}-${subtitle}`" :value="index" border>{{ subtitle }}</el-radio></el-radio-group></el-form-item>
          </div>
        </el-card>

        <el-card class="element-surface" shadow="never">
          <template #header><div class="card-header-row"><div><h3>正文配图</h3><p>可以整体重新生成，也可以按图片定向修改或删除</p></div><el-button type="primary" plain :loading="action === 'images-all'" @click="regenerateAllImages">后台重做全部配图</el-button></div></template>
          <div v-if="inlineImages.length" class="asset-grid"><article v-for="image in inlineImages" :key="image.image_id || image.index" class="asset-card"><div class="asset-preview"><img v-if="image.url" :src="image.url" :alt="`正文配图 ${image.index}`" loading="lazy" /><el-empty v-else description="图片生成失败" :image-size="55" /></div><div><strong>正文配图 {{ image.index ?? image.image_index }}</strong><small>{{ image.status || (image.url ? '已生成' : '待处理') }}</small></div><div class="inline-actions"><el-button text type="primary" :loading="action === `image-${image.index ?? image.image_index}`" @click="regenerateOneImage(image)">定向重做</el-button><el-button text type="danger" :loading="action === `delete-image-${image.index ?? image.image_index}`" @click="deleteOneImage(image)">删除</el-button></div></article></div>
          <el-empty v-else description="当前文章没有正文配图"><el-button type="primary" plain @click="regenerateAllImages">生成正文配图</el-button></el-empty>
        </el-card>

        <el-card class="element-surface" shadow="never">
          <template #header><div class="card-header-row"><div><h3>文章封面</h3><p>生成 AI 封面，或从该公众号永久素材库选择已有图片</p></div><el-button type="primary" :loading="action === 'cover-generate'" @click="generateCover">后台生成 AI 封面</el-button></div></template>
          <el-alert v-if="job.thumb_media_id" type="success" :closable="false" :title="`当前已选择封面素材：${job.thumb_media_id}`" />
          <div v-if="covers.length" class="asset-grid cover-grid"><article v-for="cover in covers" :key="cover.media_id" class="asset-card" :class="{ 'is-selected': cover.media_id === job.thumb_media_id }"><div class="asset-preview"><img v-if="cover.url" :src="cover.url" :alt="cover.name" loading="lazy" /><el-empty v-else description="微信素材" :image-size="55" /></div><div><strong>{{ cover.name || '公众号图片素材' }}</strong><small>{{ cover.media_id }}</small></div><el-button type="primary" plain :disabled="cover.media_id === job.thumb_media_id" :loading="action === `cover-${cover.media_id}`" @click="chooseCover(cover)">{{ cover.media_id === job.thumb_media_id ? '当前封面' : '选作封面' }}</el-button></article></div>
          <el-empty v-else description="没有读取到公众号图片素材，仍可直接生成 AI 封面" />
        </el-card>

        <div class="advanced-history-grid">
          <el-card class="element-surface" shadow="never"><template #header><div><h3>文章历史版本</h3><p>修改、改写和图片重做前会自动留存，可随时恢复</p></div></template><el-timeline v-if="versions.length"><el-timeline-item v-for="item in versions" :key="item.id" :timestamp="formatDateTime(item.created_at)" placement="top"><div class="history-row"><div><strong>{{ item.reason || '文章历史版本' }}</strong><small>{{ item.title || '未命名文章' }} · {{ plainText(item.body).length }} 字</small></div><el-button text type="primary" :loading="action === `version-${item.id}`" @click="restoreVersion(item)">恢复</el-button></div></el-timeline-item></el-timeline><el-empty v-else description="还没有历史版本" :image-size="70" /></el-card>
          <el-card class="element-surface" shadow="never"><template #header><div><h3>处理记录</h3><p>查看生成、评审、重试和图片处理的执行结果</p></div></template><el-table :data="attempts" size="small" max-height="360"><el-table-column prop="stage" label="阶段" width="100" /><el-table-column label="结果" width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'succeeded' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'">{{ row.status }}</el-tag></template></el-table-column><el-table-column prop="attempt_no" label="次数" width="65" /><el-table-column prop="error" label="说明" min-width="180" show-overflow-tooltip /></el-table></el-card>
        </div>
      </div>
    </div>

    <template #footer>
      <footer class="review-footer"><span>所有 AI 结果都需要人工确认后才能进入公众号草稿箱</span><div><el-button @click="visible = false">关闭并继续其他工作</el-button><el-button type="success" :icon="CircleCheck" :disabled="Boolean(review?.blocking_count) || review?.status === 'candidate_ready'" @click="confirmArticle">确认这篇文章</el-button></div></footer>
    </template>
  </el-dialog>
</template>
