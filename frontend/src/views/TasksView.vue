<script setup>
import { CircleCheck, CopyDocument, Document, FolderRemove as Archive, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import ReviewWorkbench from '@/components/ReviewWorkbench.vue'
import { activity } from '@/stores/activity'
import { formatDateTime, relativeTime, statusLabel, statusType } from '@/utils/format'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const batches = ref([])
const search = ref('')
const filter = ref('all')
const expanded = ref([])
const reviewOpen = ref(false)
const selectedBatchId = ref('')
const selectedJobId = ref(0)
const actionId = ref('')
let pollTimer = null

const filteredBatches = computed(() => batches.value.filter((batch) => {
  if (filter.value === 'processing' && !['processing', 'injecting'].includes(batch.status)) return false
  if (filter.value === 'review' && !batch.jobs?.some((job) => job.status === 'ready_for_review')) return false
  if (filter.value === 'failed' && !batch.jobs?.some((job) => job.status === 'failed')) return false
  if (filter.value === 'drafted' && !batch.jobs?.some((job) => ['drafted', 'published'].includes(job.status))) return false
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return true
  return [batch.display_id, batch.topic, ...batch.jobs.flatMap((job) => [job.account_name, job.selected_title])]
    .filter(Boolean).join(' ').toLowerCase().includes(keyword)
}))

function batchPercentage(batch) {
  const total = Number(batch.progress?.total || batch.jobs?.length || 0)
  const completed = Number(batch.progress?.completed || 0)
  return total ? Math.round(completed / total * 100) : 0
}

function syncActivities() {
  for (const batch of batches.value) {
    const item = activity.state.items.find((entry) => entry.context?.batchId === batch.id && entry.type === 'generation' && entry.status === 'running')
    if (!item) continue
    item.progress = Math.max(item.progress, batchPercentage(batch))
    item.description = `${batch.progress?.completed || 0}/${batch.progress?.total || batch.jobs?.length || 0} 篇已完成生成`
    if (!['processing', 'injecting'].includes(batch.status)) {
      if (['failed', 'partial_failed'].includes(batch.status)) activity.fail(item, '文章生成存在失败项，请进入任务中心处理')
      else activity.complete(item, '文章已生成，等待人工审核')
    }
  }
}

async function load({ silent = false } = {}) {
  if (!silent) loading.value = true
  try {
    batches.value = await api.batches(false)
    syncActivities()
    const requested = String(route.query.batchId || '')
    if (requested && !expanded.value.includes(requested)) expanded.value = [requested, ...expanded.value]
  } catch (error) {
    if (!silent) ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function openReview(batchId, jobId) {
  selectedBatchId.value = batchId
  selectedJobId.value = Number(jobId)
  reviewOpen.value = true
  router.replace({ path: '/tasks', query: { batchId, jobId } })
}

async function runAction(key, callback, success) {
  actionId.value = key
  try {
    await callback()
    ElMessage.success(success)
    await load({ silent: true })
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    actionId.value = ''
  }
}

async function writeDraft(batch) {
  try {
    await ElMessageBox.confirm('确认将该批次中已审核文章写入公众号草稿箱？系统不会自动群发。', '写入草稿箱', { type: 'success', confirmButtonText: '确认写入', cancelButtonText: '取消' })
    await runAction(`draft-${batch.id}`, () => api.injectBatch(batch.id), '草稿写入任务已提交')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

async function archiveBatch(batch) {
  try {
    await ElMessageBox.confirm(`归档批次 #${batch.display_id}？归档后不会删除历史数据。`, '归档批次', { type: 'warning' })
    await runAction(`archive-${batch.id}`, () => api.archiveBatch(batch.id), '批次已归档')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function processDeepLink() {
  const legacy = new URLSearchParams(window.location.search)
  const batchId = String(route.query.batchId || legacy.get('batch_id') || '')
  const jobId = Number(route.query.jobId || legacy.get('job_id') || 0)
  const wantsReview = route.query.jobId || legacy.get('view') === 'review'
  if (wantsReview && batchId && jobId) openReview(batchId, jobId)
}

onMounted(async () => {
  await load()
  processDeepLink()
  pollTimer = window.setInterval(() => load({ silent: true }), 5000)
})
onBeforeUnmount(() => pollTimer && window.clearInterval(pollTimer))
</script>

<template>
  <div class="page-stack tasks-page">
    <section class="page-hero">
      <div><span class="eyebrow">PRODUCTION PIPELINE</span><h2>任务与审核</h2><p>生成、评审、改写和草稿写入状态都集中在这里。</p></div>
      <el-button type="primary" plain :icon="Refresh" :loading="loading" @click="load()">刷新任务</el-button>
    </section>

    <el-card class="surface-card filter-card" shadow="never">
      <div class="task-filters"><el-segmented v-model="filter" :options="[{ label: '全部', value: 'all' }, { label: '生成中', value: 'processing' }, { label: '待审核', value: 'review' }, { label: '失败', value: 'failed' }, { label: '已入草稿', value: 'drafted' }]" /><el-input v-model="search" :prefix-icon="Search" placeholder="搜索标题、公众号或批次号" clearable /></div>
    </el-card>

    <el-collapse v-if="filteredBatches.length" v-model="expanded" class="batch-list">
      <el-collapse-item v-for="batch in filteredBatches" :key="batch.id" :name="batch.id" class="batch-card-el">
        <template #title>
          <div class="batch-title">
            <span class="batch-title__icon"><Document /></span>
            <div class="batch-title__main"><div><strong>批次 #{{ batch.display_id }}</strong><el-tag :type="statusType(batch.status)" effect="light" round>{{ statusLabel(batch.status) }}</el-tag></div><p>{{ batch.topic || batch.jobs?.[0]?.selected_title || batch.source_url || '文章生成任务' }}</p><small>创建于 {{ formatDateTime(batch.created_at) }} · 更新于 {{ relativeTime(batch.updated_at || batch.created_at) }}</small></div>
            <div class="batch-title__progress"><el-progress type="circle" :percentage="batchPercentage(batch)" :width="58" :stroke-width="6" /><span>{{ batch.progress?.completed || 0 }}/{{ batch.progress?.total || batch.jobs?.length || 0 }} 篇</span></div>
          </div>
        </template>

        <div class="batch-detail">
          <div class="batch-summary"><span><strong>{{ batch.progress?.ready_for_review || 0 }}</strong>待审核</span><span><strong>{{ batch.progress?.confirmed || 0 }}</strong>已确认</span><span><strong>{{ batch.progress?.drafted || 0 }}</strong>已入草稿</span><span><strong>{{ batch.progress?.failed || 0 }}</strong>失败</span><div class="batch-summary__actions"><el-button v-if="batch.progress?.failed" type="warning" plain :loading="actionId === `retry-${batch.id}`" @click.stop="runAction(`retry-${batch.id}`, () => api.retryBatch(batch.id), '失败任务已重新提交')">重试失败项</el-button><el-button :icon="CopyDocument" plain :loading="actionId === `copy-${batch.id}`" @click.stop="runAction(`copy-${batch.id}`, () => api.copyBatch(batch.id), '已复制为新批次')">再次生成</el-button><el-button v-if="batch.progress?.ready_for_draft" type="success" :icon="Upload" :loading="actionId === `draft-${batch.id}`" @click.stop="writeDraft(batch)">写入草稿箱</el-button><el-button :icon="Archive" text @click.stop="archiveBatch(batch)">归档</el-button></div></div>

          <el-table :data="batch.jobs" class="job-table" style="width: 100%">
            <el-table-column prop="account_name" label="公众号" min-width="150" />
            <el-table-column label="文章" min-width="320"><template #default="{ row }"><div class="job-title"><strong>{{ row.selected_title || row.titles?.[0] || '正在生成标题…' }}</strong><small>{{ row.model_name || '公众号绑定模型' }}</small></div></template></el-table-column>
            <el-table-column label="生成状态" width="130"><template #default="{ row }"><el-tag :type="statusType(row.status)" effect="light">{{ statusLabel(row.status) }}</el-tag></template></el-table-column>
            <el-table-column label="审核状态" width="130"><template #default="{ row }"><el-tag v-if="row.review_status === 'confirmed'" type="success" effect="light">已确认</el-tag><el-tag v-else-if="row.review_status === 'needs_changes'" type="warning" effect="light">需要修改</el-tag><el-tag v-else effect="plain">{{ row.status === 'ready_for_review' ? '待审核' : '—' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" width="230" fixed="right"><template #default="{ row }"><el-button v-if="row.status === 'ready_for_review'" type="primary" :icon="row.review_status === 'confirmed' ? CircleCheck : Document" @click="openReview(batch.id, row.id)">{{ row.review_status === 'confirmed' ? '查看文章' : '打开审核' }}</el-button><el-button v-else-if="row.status === 'failed'" type="warning" plain :loading="actionId === `job-${row.id}`" @click="runAction(`job-${row.id}`, () => api.retryJob(batch.id, row.id), '任务已重新提交')">重试</el-button><span v-else class="muted-inline">{{ statusLabel(row.status) }}</span></template></el-table-column>
          </el-table>
        </div>
      </el-collapse-item>
    </el-collapse>
    <el-empty v-else-if="!loading" description="当前条件下没有任务" :image-size="120"><el-button type="primary" @click="router.push('/')">创建文章任务</el-button></el-empty>

    <ReviewWorkbench v-model="reviewOpen" :batch-id="selectedBatchId" :job-id="selectedJobId" @updated="load({ silent: true })" />
  </div>
</template>
