<script setup>
import { ArrowRight, DocumentAdd, Link, MagicStick, Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import { activity } from '@/stores/activity'
import { relativeTime, statusLabel, statusType } from '@/utils/format'

const router = useRouter()
const route = useRoute()
const loading = ref(true)
const submitting = ref(false)
const accounts = ref([])
const batches = ref([])
const onboarding = ref(null)
const form = reactive({
  source_mode: 'link',
  source_url: '',
  raw_content: '',
  topic: '',
  references: '',
  required_facts: '',
  rewrite_intensity: 'standard',
  account_ids: [],
})

const recentBatches = computed(() => batches.value.slice(0, 4))
const stats = computed(() => {
  const jobs = batches.value.flatMap((batch) => batch.jobs || [])
  return {
    total: batches.value.length,
    processing: batches.value.filter((batch) => ['processing', 'injecting'].includes(batch.status)).length,
    review: jobs.filter((job) => job.status === 'ready_for_review' && job.review_status !== 'confirmed').length,
    drafted: jobs.filter((job) => ['drafted', 'published'].includes(job.status)).length,
  }
})

async function load() {
  loading.value = true
  try {
    const [accountRows, batchRows, onboardingState] = await Promise.all([api.accounts(), api.batches(false), api.onboarding()])
    accounts.value = accountRows
    batches.value = batchRows
    onboarding.value = onboardingState
    if (!form.account_ids.length && accounts.value.length === 1) form.account_ids = [accounts.value[0].id]
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function payload() {
  return {
    source_mode: form.source_mode,
    source_url: form.source_mode === 'link' ? form.source_url.trim() : null,
    raw_content: form.source_mode === 'text' ? form.raw_content.trim() : null,
    topic: form.source_mode === 'topic' ? form.topic.trim() : null,
    reference_urls: form.source_mode === 'references'
      ? form.references.split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
      : [],
    required_facts: form.required_facts.trim() || null,
    rewrite_intensity: form.rewrite_intensity,
    account_ids: form.account_ids,
  }
}

async function submit() {
  const data = payload()
  if (!data.account_ids.length) return ElMessage.warning('请至少选择一个目标公众号')
  const sourceReady = data.source_url || data.raw_content || data.topic || data.reference_urls.length
  if (!sourceReady) return ElMessage.warning('请填写当前内容来源')
  submitting.value = true
  try {
    const checks = await api.preflightAccounts(data.account_ids)
    const blocked = checks.filter((item) => item.can_generate === false)
    if (blocked.length) {
      const details = blocked.map((item) => `${item.account_name || item.account_id}：${item.message || item.checks?.find((check) => !check.ok)?.message || '配置未就绪'}`).join('；')
      throw new Error(`目标公众号尚未准备好：${details}`)
    }
    const batch = await api.createBatch(data)
    const task = activity.add({
      type: 'generation',
      title: `正在生成 ${data.account_ids.length} 篇文章`,
      description: '已进入后台生成，可继续使用选题或设置功能',
      context: { batchId: batch.id },
    })
    task.progress = 8
    ElMessage.success('文章任务已提交到后台')
    await router.push({ path: '/tasks', query: { batchId: batch.id } })
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  const topic = String(route.query.topic || '').trim()
  const sourceUrl = String(route.query.sourceUrl || '').trim()
  if (topic) {
    form.source_mode = 'topic'
    form.topic = topic
  } else if (sourceUrl) {
    form.source_mode = 'link'
    form.source_url = sourceUrl
  }
  load()
})
</script>

<template>
  <div class="create-page page-stack" :class="{ 'is-loading': loading }">
    <section class="page-hero page-hero--create">
      <div>
        <span class="eyebrow">CREATE WITH CONTROL</span>
        <h2>今天想做什么内容？</h2>
        <p>选择素材来源与公众号，生成任务会在后台运行，你可以继续处理其他工作。</p>
      </div>
      <el-button :icon="Refresh" circle plain aria-label="刷新数据" @click="load" />
    </section>

    <el-alert v-if="onboarding && !onboarding.content_ready" type="warning" :closable="false" show-icon title="创作环境尚未完成配置"><template #default>请先在系统设置中添加并测试文章模型，再为至少一个公众号绑定模型。<el-button text type="warning" @click="router.push('/settings')">前往设置</el-button></template></el-alert>

    <section class="metric-grid">
      <article class="metric-card"><span>全部批次</span><strong>{{ stats.total }}</strong><small>当前账号的内容任务</small></article>
      <article class="metric-card metric-card--blue"><span>后台生成</span><strong>{{ stats.processing }}</strong><small>无需停留在当前页面</small></article>
      <article class="metric-card metric-card--amber"><span>待审核</span><strong>{{ stats.review }}</strong><small>需要人工确认的文章</small></article>
      <article class="metric-card metric-card--green"><span>已入草稿</span><strong>{{ stats.drafted }}</strong><small>历史累计成功</small></article>
    </section>

    <section class="content-grid content-grid--create">
      <el-card class="surface-card creation-card" shadow="never">
        <template #header>
          <div class="section-heading">
            <span class="section-heading__index">01</span>
            <div><h3>选择内容来源</h3><p>系统会根据不同来源自动调整采集和改写流程</p></div>
          </div>
        </template>

        <el-segmented v-model="form.source_mode" class="source-segmented" :options="[
          { label: '文章链接', value: 'link' },
          { label: '粘贴正文', value: 'text' },
          { label: '多篇参考', value: 'references' },
          { label: '话题原创', value: 'topic' },
        ]" />

        <div class="source-form">
          <el-input v-if="form.source_mode === 'link'" v-model="form.source_url" size="large" :prefix-icon="Link" placeholder="粘贴微信公众号或公开文章链接" clearable />
          <el-input v-if="form.source_mode === 'text'" v-model="form.raw_content" type="textarea" :rows="9" resize="vertical" placeholder="粘贴需要改写或优化的完整正文" />
          <el-input v-if="form.source_mode === 'references'" v-model="form.references" type="textarea" :rows="7" resize="vertical" placeholder="每行一个参考链接，系统会综合提炼而不是简单拼接" />
          <el-input v-if="form.source_mode === 'topic'" v-model="form.topic" size="large" :prefix-icon="MagicStick" placeholder="例如：AI 如何改变客服团队的工作方式" clearable />
        </div>

        <el-collapse class="advanced-collapse">
          <el-collapse-item title="高级要求 · 事实保留与改写强度" name="advanced">
            <el-form label-position="top">
              <el-form-item label="必须保留的事实或数据">
                <el-input v-model="form.required_facts" type="textarea" :rows="3" placeholder="例如：公司名称、关键数字、产品结论不能擅自改变" />
              </el-form-item>
              <el-form-item label="改写强度">
                <el-radio-group v-model="form.rewrite_intensity">
                  <el-radio-button value="conservative">轻度润色</el-radio-button>
                  <el-radio-button value="standard">标准改写</el-radio-button>
                  <el-radio-button value="strong">深度重构</el-radio-button>
                </el-radio-group>
              </el-form-item>
            </el-form>
          </el-collapse-item>
        </el-collapse>
      </el-card>

      <el-card class="surface-card account-card-panel" shadow="never">
        <template #header>
          <div class="section-heading">
            <span class="section-heading__index">02</span>
            <div><h3>选择目标公众号</h3><p>每个账号会使用自己的模型、提示词与排版方案</p></div>
          </div>
        </template>
        <el-checkbox-group v-model="form.account_ids" class="account-selector">
          <el-checkbox v-for="account in accounts" :key="account.id" :value="account.id" border>
            <span class="account-selector__name">{{ account.name }}</span>
            <small>{{ account.model_name || '使用平台默认模型' }}</small>
          </el-checkbox>
        </el-checkbox-group>
        <el-empty v-if="!accounts.length" description="尚未配置可用公众号" :image-size="84">
          <el-button type="primary" plain @click="$router.push('/settings')">前往设置</el-button>
        </el-empty>
        <div class="submit-panel">
          <div><strong>将生成 {{ form.account_ids.length }} 篇文章</strong><span>提交后可在任务中心查看实时状态</span></div>
          <el-button type="primary" size="large" :icon="DocumentAdd" :loading="submitting" @click="submit">
            开始生成文章
          </el-button>
        </div>
      </el-card>
    </section>

    <el-card class="surface-card recent-card" shadow="never">
      <template #header>
        <div class="card-header-row"><div><h3>最近任务</h3><p>继续处理刚刚生成或待审核的文章</p></div><el-button text :icon="ArrowRight" @click="router.push('/tasks')">查看全部</el-button></div>
      </template>
      <div v-if="recentBatches.length" class="recent-list">
        <button v-for="batch in recentBatches" :key="batch.id" type="button" class="recent-row" @click="router.push({ path: '/tasks', query: { batchId: batch.id } })">
          <span class="recent-row__icon"><DocumentAdd /></span>
          <span class="recent-row__main"><strong>{{ batch.topic || batch.jobs?.[0]?.selected_title || `批次 #${batch.display_id}` }}</strong><small>{{ batch.jobs?.length || 0 }} 个公众号 · {{ relativeTime(batch.updated_at || batch.created_at) }}</small></span>
          <el-tag :type="statusType(batch.status)" effect="light" round>{{ statusLabel(batch.status) }}</el-tag>
          <ArrowRight class="recent-row__arrow" />
        </button>
      </div>
      <el-empty v-else description="还没有文章任务，先创建第一篇内容" :image-size="92" />
    </el-card>
  </div>
</template>
