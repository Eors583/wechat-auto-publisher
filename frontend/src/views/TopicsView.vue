<script setup>
import { Collection, Delete, Edit, Plus, Refresh, Search, Star, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import { formatDateTime } from '@/utils/format'

const router = useRouter()
const active = ref('topics')
const loading = ref(true)
const actionId = ref('')
const topics = ref([])
const sources = ref([])
const followedAccounts = ref([])
const followedArticles = ref([])
const addOpen = ref(false)
const followedDialog = ref(false)
const sourceDialog = ref(false)
const articleDialog = ref(false)
const backendDialog = ref(false)
const filters = reactive({ keyword: '', days: 7, source_ids: [] })
const articleFilters = reactive({ keyword: '', account_ids: [], days: 30, unread_only: false, favorite_only: false })
const manual = reactive({ title: '', url: '', summary: '', category: '' })
const followedForm = reactive({ id: null, name: '', wechat_id: '', category: '', tagsText: '', keywordsText: '', fetch_method: 'backend_search', sample_url: '', source_url: '', is_owned: false, enabled: true, refresh_hours: 12 })
const sourceForm = reactive({ id: null, name: '', source_type: 'rss', configJson: '{\n  "url": ""\n}', enabled: true })
const articleForm = reactive({ url: '', followed_account_id: '' })
const backendSession = reactive({ enabled: false, token: '', cookie: '', session_label: '', has_token: false, has_cookie: false })

const unreadCount = computed(() => followedArticles.value.filter((item) => !item.is_read).length)
const queriedAccount = computed(() => {
  if (articleFilters.account_ids.length !== 1) return null
  return followedAccounts.value.find((item) => item.id === articleFilters.account_ids[0]) || null
})

function topicParams() {
  const params = new URLSearchParams({ days: String(filters.days), limit: '200' })
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  for (const id of filters.source_ids) params.append('source_ids', id)
  return params
}

function articleParams() {
  const params = new URLSearchParams({ days: String(articleFilters.days), limit: '500' })
  if (articleFilters.keyword.trim()) params.set('keyword', articleFilters.keyword.trim())
  if (articleFilters.unread_only) params.set('unread_only', 'true')
  if (articleFilters.favorite_only) params.set('favorite_only', 'true')
  for (const id of articleFilters.account_ids) params.append('account_ids', id)
  return params
}

async function load() {
  loading.value = true
  try {
    const [topicRows, sourceRows, accountRows, articleRows, backendData] = await Promise.all([
      api.topics(topicParams()),
      api.topicSources(),
      api.followedAccounts(),
      api.followedArticles(articleParams()),
      api.followedBackendSession(),
    ])
    topics.value = topicRows
    sources.value = sourceRows
    followedAccounts.value = accountRows
    followedArticles.value = articleRows
    Object.assign(backendSession, backendData, { token: '', cookie: '' })
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

async function refreshSources() {
  actionId.value = 'sources'
  try {
    await api.refreshTopicSources()
    ElMessage.success('选题源刷新任务已完成')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    actionId.value = ''
  }
}

async function refreshFollowed(account = null) {
  actionId.value = account?.id || 'followed'
  try {
    const result = account ? await api.refreshFollowedAccount(account.id) : await api.refreshFollowedAccounts()
    ElMessage.success(`关注文章已刷新${result?.added != null ? `，新增 ${result.added} 篇` : ''}`)
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    actionId.value = ''
    // Even when the external refresh fails (for example, the backend login
    // session has expired), still show the locally collected articles for the
    // selected account instead of leaving the previous global list on screen.
    await load()
  }
}

async function queryFollowedArticles(account) {
  articleFilters.keyword = ''
  articleFilters.account_ids = [account.id]
  articleFilters.unread_only = false
  articleFilters.favorite_only = false
  active.value = 'followed'
  await refreshFollowed(account)
}

async function clearFollowedAccountQuery() {
  articleFilters.account_ids = []
  await load()
}

async function addManual() {
  if (!manual.title.trim()) return ElMessage.warning('请填写选题标题')
  try {
    await api.addTopic({ ...manual })
    addOpen.value = false
    Object.assign(manual, { title: '', url: '', summary: '', category: '' })
    ElMessage.success('选题已加入选题库')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

function editFollowed(account = null) {
  Object.assign(followedForm, account ? {
    id: account.id,
    name: account.name || '',
    wechat_id: account.wechat_id || '',
    category: account.category || '',
    tagsText: (account.tags || []).join('、'),
    keywordsText: (account.keywords || []).join('、'),
    fetch_method: account.fetch_method || 'backend_search',
    sample_url: account.sample_url || '',
    source_url: account.source_url || '',
    is_owned: Boolean(account.is_owned),
    enabled: account.enabled !== false,
    refresh_hours: Number(account.refresh_hours || 12),
  } : { id: null, name: '', wechat_id: '', category: '', tagsText: '', keywordsText: '', fetch_method: 'backend_search', sample_url: '', source_url: '', is_owned: false, enabled: true, refresh_hours: 12 })
  followedDialog.value = true
}

function splitTags(value) {
  return [...new Set(String(value || '').split(/[\n,，、]/).map((item) => item.trim()).filter(Boolean))]
}

async function saveFollowed() {
  if (!followedForm.name.trim()) return ElMessage.warning('请填写公众号名称')
  try {
    await api.saveFollowedAccount({
      id: followedForm.id,
      name: followedForm.name.trim(),
      wechat_id: followedForm.wechat_id.trim(),
      category: followedForm.category.trim(),
      tags: splitTags(followedForm.tagsText),
      keywords: splitTags(followedForm.keywordsText),
      fetch_method: followedForm.fetch_method,
      sample_url: followedForm.sample_url.trim(),
      source_url: followedForm.source_url.trim(),
      is_owned: followedForm.is_owned,
      enabled: followedForm.enabled,
      refresh_hours: Number(followedForm.refresh_hours),
    })
    followedDialog.value = false
    ElMessage.success('关注公众号已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function deleteFollowed(account) {
  try {
    await ElMessageBox.confirm(`确定删除关注公众号“${account.name}”吗？`, '删除关注公众号', { type: 'warning' })
    await api.deleteFollowedAccount(account.id)
    ElMessage.success('关注公众号已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function editSource(source = null) {
  Object.assign(sourceForm, source ? {
    id: source.id,
    name: source.name || '',
    source_type: source.source_type || 'rss',
    configJson: JSON.stringify(source.config || {}, null, 2),
    enabled: source.enabled !== false,
  } : { id: null, name: '', source_type: 'rss', configJson: '{\n  "url": ""\n}', enabled: true })
  sourceDialog.value = true
}

async function saveSource() {
  if (!sourceForm.name.trim()) return ElMessage.warning('请填写来源名称')
  try {
    const config = JSON.parse(sourceForm.configJson || '{}')
    await api.saveTopicSource({ id: sourceForm.id, name: sourceForm.name.trim(), source_type: sourceForm.source_type, config, enabled: sourceForm.enabled })
    sourceDialog.value = false
    ElMessage.success('选题来源已保存')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof SyntaxError ? '来源配置必须是合法的 JSON' : error.message)
  }
}

async function deleteSource(source) {
  try {
    await ElMessageBox.confirm(`确定删除选题来源“${source.name}”吗？`, '删除选题来源', { type: 'warning' })
    await api.deleteTopicSource(source.id)
    ElMessage.success('选题来源已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

async function addArticle() {
  if (!articleForm.url.trim()) return ElMessage.warning('请粘贴微信公众号原文链接')
  actionId.value = 'article'
  try {
    await api.addFollowedArticle({ url: articleForm.url.trim(), followed_account_id: articleForm.followed_account_id || null, source_channel: 'api' })
    articleDialog.value = false
    Object.assign(articleForm, { url: '', followed_account_id: '' })
    ElMessage.success('公众号文章已加入素材库')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    actionId.value = ''
  }
}

async function updateArticle(article, patch) {
  try {
    const updated = await api.updateFollowedArticle(article.id, patch)
    Object.assign(article, updated)
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function saveBackendSession(testOnly = false) {
  actionId.value = testOnly ? 'test-backend' : 'save-backend'
  try {
    const payload = {
      enabled: backendSession.enabled,
      token: backendSession.token.trim(),
      cookie: backendSession.cookie.trim(),
      session_label: backendSession.session_label.trim(),
    }
    if (testOnly) {
      await api.testFollowedBackendSession(payload)
      ElMessage.success('公众号后台登录态验证通过')
    } else {
      const result = await api.saveFollowedBackendSession(payload)
      Object.assign(backendSession, result, { token: '', cookie: '' })
      backendDialog.value = false
      ElMessage.success('公众号后台登录态已加密保存')
    }
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    actionId.value = ''
  }
}

async function clearBackendSession() {
  try {
    await ElMessageBox.confirm('确定清除已保存的公众号后台 Token 和 Cookie 吗？', '清除登录态', { type: 'warning' })
    const result = await api.clearFollowedBackendSession()
    Object.assign(backendSession, result, { token: '', cookie: '' })
    ElMessage.success('公众号后台登录态已清除')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function createFromTopic(topic) {
  router.push({ path: '/', query: { topic: topic.title } })
}

function createFromArticle(article) {
  updateArticle(article, { is_read: true })
  router.push({ path: '/', query: { sourceUrl: article.url } })
}

onMounted(load)
</script>

<template>
  <div class="page-stack topics-page">
    <section class="page-hero">
      <div><span class="eyebrow">TOPIC INTELLIGENCE</span><h2>选题与素材库</h2><p>把热点、关注公众号文章和人工灵感集中到一个可搜索的内容池。</p></div>
      <div class="hero-actions"><el-button v-if="active === 'topics'" :icon="Plus" @click="addOpen = true">添加选题</el-button><el-button v-if="active === 'followed'" :icon="Plus" @click="articleDialog = true">投递文章</el-button><el-button type="primary" :icon="Refresh" :loading="Boolean(actionId)" @click="active === 'followed' ? refreshFollowed() : refreshSources()">刷新内容</el-button></div>
    </section>

    <el-segmented v-model="active" class="library-tabs" :options="[{ label: '热点选题', value: 'topics' }, { label: `关注文章 · ${unreadCount} 未读`, value: 'followed' }, { label: '来源管理', value: 'sources' }]" />

    <template v-if="active === 'topics'">
      <el-card class="surface-card filter-card" shadow="never"><div class="topic-filters"><el-input v-model="filters.keyword" :prefix-icon="Search" placeholder="搜索标题、摘要或关键词" clearable @keyup.enter="load" /><el-select v-model="filters.days" style="width: 150px" @change="load"><el-option label="最近 3 天" :value="3" /><el-option label="最近 7 天" :value="7" /><el-option label="最近 30 天" :value="30" /></el-select><el-select v-model="filters.source_ids" multiple collapse-tags placeholder="全部来源" style="min-width: 220px" @change="load"><el-option v-for="source in sources" :key="source.id" :label="source.name" :value="source.id" /></el-select><el-button type="primary" :icon="Search" @click="load">筛选</el-button></div></el-card>
      <div v-if="topics.length" class="topic-grid" :class="{ 'is-loading': loading }"><el-card v-for="topic in topics" :key="topic.id" class="topic-card-el" shadow="hover"><div class="topic-card-el__meta"><el-tag size="small" effect="plain">{{ topic.category || topic.source_name || '内容选题' }}</el-tag><span>{{ formatDateTime(topic.published_at || topic.created_at) }}</span></div><h3>{{ topic.title }}</h3><p>{{ topic.summary || '暂无摘要，可直接进入创作工作台补充观点和事实。' }}</p><div class="topic-card-el__stats"><span><View /> {{ topic.view_count || 0 }}</span><span><Star /> {{ topic.heat || topic.score || 0 }}</span></div><div class="topic-card-el__actions"><el-button v-if="topic.url" link tag="a" :href="topic.url" target="_blank">查看来源</el-button><el-button type="primary" plain @click="createFromTopic(topic)">用这个选题创作</el-button></div></el-card></div>
      <el-empty v-else-if="!loading" description="当前筛选条件下没有选题" :image-size="120"><el-button :icon="Collection" @click="refreshSources">刷新选题源</el-button></el-empty>
    </template>

    <template v-else-if="active === 'followed'">
      <el-alert v-if="queriedAccount" class="account-query-alert" type="success" show-icon closable @close="clearFollowedAccountQuery">
        <template #title>正在查看“{{ queriedAccount.name }}”的文章列表</template>
        查询操作会先从已配置的公众号后台更新文章，再按该公众号筛选展示；也可以继续使用下方条件缩小范围。
      </el-alert>
      <el-card class="surface-card filter-card" shadow="never"><div class="topic-filters"><el-input v-model="articleFilters.keyword" :prefix-icon="Search" placeholder="搜索文章或公众号" clearable @keyup.enter="load" /><el-select v-model="articleFilters.account_ids" multiple collapse-tags placeholder="全部关注公众号" style="min-width:220px" @change="load"><el-option v-for="account in followedAccounts" :key="account.id" :label="account.name" :value="account.id" /></el-select><el-select v-model="articleFilters.days" style="width:140px" @change="load"><el-option label="最近 7 天" :value="7" /><el-option label="最近 30 天" :value="30" /><el-option label="最近一年" :value="365" /></el-select><el-checkbox v-model="articleFilters.unread_only" @change="load">仅未读</el-checkbox><el-checkbox v-model="articleFilters.favorite_only" @change="load">仅收藏</el-checkbox><el-button type="primary" @click="load">筛选</el-button></div></el-card>
      <div v-if="followedArticles.length" class="followed-article-list"><el-card v-for="article in followedArticles" :key="article.id" class="surface-card followed-article" shadow="hover"><div class="followed-article__main"><div class="topic-card-el__meta"><el-tag size="small" effect="plain">{{ article.account_name || '关注公众号' }}</el-tag><span>{{ formatDateTime(article.published_at || article.discovered_at) }}</span><el-tag v-if="!article.is_read" size="small" type="warning">未读</el-tag></div><h3>{{ article.title }}</h3><p>{{ article.summary || '该文章暂未提供摘要，可打开原文查看。' }}</p></div><div class="followed-article__actions"><el-button link tag="a" :href="article.url" target="_blank" @click="updateArticle(article, { is_read: true })">查看原文</el-button><el-button text :type="article.is_favorite ? 'warning' : 'info'" @click="updateArticle(article, { is_favorite: !article.is_favorite })">{{ article.is_favorite ? '已收藏' : '收藏' }}</el-button><el-button type="primary" plain @click="createFromArticle(article)">用此文创作</el-button><el-button text type="danger" @click="updateArticle(article, { is_ignored: true })">忽略</el-button></div></el-card></div>
      <el-empty v-else-if="!loading" :description="queriedAccount ? `暂未查询到“${queriedAccount.name}”的文章` : '还没有关注文章'"><el-button v-if="queriedAccount" type="primary" :loading="actionId === queriedAccount.id" @click="queryFollowedArticles(queriedAccount)">重新查询该公众号</el-button><el-button v-else type="primary" @click="active = 'sources'">管理关注公众号</el-button></el-empty>
    </template>

    <template v-else>
      <div class="source-management-grid">
        <el-card class="surface-card" shadow="never"><template #header><div class="card-header-row"><div><h3>关注公众号</h3><p>维护需要持续关注的公众号，并可直接查询每个公众号的文章列表</p></div><div class="hero-actions"><el-button :type="backendSession.enabled ? 'success' : 'default'" plain @click="backendDialog = true">后台登录态{{ backendSession.enabled ? '已启用' : '未配置' }}</el-button><el-button type="primary" :icon="Plus" @click="editFollowed()">添加关注</el-button></div></div></template><el-table :data="followedAccounts"><el-table-column prop="name" label="公众号" min-width="160" /><el-table-column prop="category" label="分类" min-width="120" /><el-table-column prop="fetch_method" label="获取方式" min-width="160" /><el-table-column label="状态" width="80"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column><el-table-column label="操作" width="240"><template #default="{ row }"><el-button link type="primary" :icon="Search" :loading="actionId === row.id" @click="queryFollowedArticles(row)">查询文章</el-button><el-button link :icon="Edit" @click="editFollowed(row)">编辑</el-button><el-button link type="danger" :icon="Delete" @click="deleteFollowed(row)">删除</el-button></template></el-table-column></el-table></el-card>
        <el-card class="surface-card" shadow="never"><template #header><div class="card-header-row"><div><h3>选题来源</h3><p>管理 RSS、热榜接口、新闻搜索和内部素材源</p></div><el-button type="primary" :icon="Plus" @click="editSource()">添加来源</el-button></div></template><el-table :data="sources"><el-table-column prop="name" label="来源" min-width="160" /><el-table-column prop="source_type" label="类型" min-width="140" /><el-table-column label="状态" width="80"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column><el-table-column label="操作" width="150"><template #default="{ row }"><el-button link :icon="Edit" @click="editSource(row)">编辑</el-button><el-button link type="danger" :icon="Delete" @click="deleteSource(row)">删除</el-button></template></el-table-column></el-table></el-card>
      </div>
    </template>

    <el-dialog v-model="addOpen" title="添加人工选题" width="min(560px, 92vw)" append-to-body><el-form label-position="top"><el-form-item label="选题标题" required><el-input v-model="manual.title" maxlength="120" show-word-limit /></el-form-item><el-form-item label="参考链接"><el-input v-model="manual.url" placeholder="可选" /></el-form-item><el-form-item label="分类"><el-input v-model="manual.category" placeholder="例如：AI、营销、管理" /></el-form-item><el-form-item label="核心观点"><el-input v-model="manual.summary" type="textarea" :rows="4" /></el-form-item></el-form><template #footer><el-button @click="addOpen = false">取消</el-button><el-button type="primary" @click="addManual">保存选题</el-button></template></el-dialog>

    <el-dialog v-model="followedDialog" :title="followedForm.id ? '编辑关注公众号' : '添加关注公众号'" width="min(680px, 94vw)" append-to-body><el-form label-position="top"><div class="form-grid"><el-form-item label="公众号名称" required><el-input v-model="followedForm.name" /></el-form-item><el-form-item label="微信号"><el-input v-model="followedForm.wechat_id" /></el-form-item></div><div class="form-grid"><el-form-item label="分类"><el-input v-model="followedForm.category" /></el-form-item><el-form-item label="获取方式"><el-select v-model="followedForm.fetch_method" style="width:100%"><el-option label="公众号后台搜索" value="backend_search" /><el-option label="仅人工投递" value="manual" /><el-option label="微信官方发布记录（自有）" value="official" /></el-select></el-form-item></div><div class="form-grid"><el-form-item label="标签（逗号分隔）"><el-input v-model="followedForm.tagsText" /></el-form-item><el-form-item label="关键词（逗号分隔）"><el-input v-model="followedForm.keywordsText" /></el-form-item></div><el-form-item label="示例文章链接"><el-input v-model="followedForm.sample_url" /></el-form-item><el-form-item label="刷新间隔"><el-input-number v-model="followedForm.refresh_hours" :min="1" :max="720" /><span class="field-suffix">小时</span></el-form-item><el-switch v-model="followedForm.enabled" active-text="启用关注" /></el-form><template #footer><el-button @click="followedDialog = false">取消</el-button><el-button type="primary" @click="saveFollowed">保存</el-button></template></el-dialog>

    <el-dialog v-model="sourceDialog" :title="sourceForm.id ? '编辑选题来源' : '添加选题来源'" width="min(680px, 94vw)" append-to-body><el-form label-position="top"><div class="form-grid"><el-form-item label="来源名称" required><el-input v-model="sourceForm.name" /></el-form-item><el-form-item label="来源类型"><el-select v-model="sourceForm.source_type" style="width:100%"><el-option label="行业网站 RSS" value="rss" /><el-option label="新闻搜索" value="news_search" /><el-option label="热点接口" value="hot_api" /><el-option label="手动选题库" value="manual" /><el-option label="关注公众号" value="followed_accounts" /></el-select></el-form-item></div><el-form-item label="来源配置（JSON）"><el-input v-model="sourceForm.configJson" type="textarea" :rows="12" spellcheck="false" /></el-form-item><el-switch v-model="sourceForm.enabled" active-text="启用来源" /></el-form><template #footer><el-button @click="sourceDialog = false">取消</el-button><el-button type="primary" @click="saveSource">保存来源</el-button></template></el-dialog>

    <el-dialog v-model="articleDialog" title="投递公众号文章" width="min(560px, 92vw)" append-to-body><el-form label-position="top"><el-form-item label="微信公众号原文链接" required><el-input v-model="articleForm.url" type="textarea" :rows="3" placeholder="https://mp.weixin.qq.com/s/..." /></el-form-item><el-form-item label="归属关注公众号（可选）"><el-select v-model="articleForm.followed_account_id" clearable style="width:100%"><el-option v-for="account in followedAccounts" :key="account.id" :label="account.name" :value="account.id" /></el-select></el-form-item></el-form><template #footer><el-button @click="articleDialog = false">取消</el-button><el-button type="primary" :loading="actionId === 'article'" @click="addArticle">读取并加入素材库</el-button></template></el-dialog>

    <el-dialog v-model="backendDialog" title="配置公众号后台搜索登录态" width="min(680px, 94vw)" append-to-body><el-alert type="warning" :closable="false" show-icon title="仅使用你有权管理的公众号后台登录态；Token 与 Cookie 会在服务器加密保存且不会回显。" /><el-form label-position="top" class="dialog-form-spaced"><el-switch v-model="backendSession.enabled" active-text="启用公众号后台搜索" /><el-form-item :label="`后台 Token${backendSession.has_token ? '（已保存，留空不修改）' : ''}`"><el-input v-model="backendSession.token" type="password" show-password /></el-form-item><el-form-item :label="`后台 Cookie${backendSession.has_cookie ? '（已保存，留空不修改）' : ''}`"><el-input v-model="backendSession.cookie" type="textarea" :rows="5" /></el-form-item><el-form-item label="会话说明"><el-input v-model="backendSession.session_label" placeholder="例如：运营账号 2026-08-06 登录" /></el-form-item></el-form><template #footer><el-button type="danger" text @click="clearBackendSession">清除登录态</el-button><el-button @click="backendDialog = false">取消</el-button><el-button :loading="actionId === 'test-backend'" @click="saveBackendSession(true)">验证连接</el-button><el-button type="primary" :loading="actionId === 'save-backend'" @click="saveBackendSession(false)">加密保存</el-button></template></el-dialog>
  </div>
</template>
