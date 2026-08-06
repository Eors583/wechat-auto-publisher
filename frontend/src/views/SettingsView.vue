<script setup>
import {
  Check,
  Connection,
  Delete,
  Document,
  Edit,
  Key,
  Operation,
  Picture,
  Plus,
  Promotion,
  Refresh,
  Setting,
  User,
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import { api } from '@/api/client'
import { session } from '@/stores/session'
import { formatDateTime } from '@/utils/format'

const active = ref('accounts')
const loading = ref(true)
const accounts = ref([])
const accountPlans = ref({})
const accountReviewDefaults = ref({})
const models = ref([])
const users = ref([])
const profiles = ref([])
const options = ref({})
const promptTemplates = ref([])
const creationPlans = ref([])
const feishuRuntime = ref({ status: 'unknown' })
const relayConnection = ref({})

const accountDialog = ref(false)
const modelDialog = ref(false)
const profileDialog = ref(false)
const promptDialog = ref(false)
const planDialog = ref(false)
const layoutDialog = ref(false)
const templateDialog = ref(false)
const modelPreviewDialog = ref(false)
const testingId = ref('')
const testingAccountId = ref('')
const testingFeishu = ref(false)
const loadingTemplates = ref(false)
const saving = ref(false)
const modelPreviewUrl = ref('')
const pairing = ref(null)

const accountForm = reactive({
  id: null,
  name: '',
  app_id: '',
  app_secret: '',
  model_id: '',
  review_priority: 0,
  plan_id: 'builtin:default',
  article_prompt_template_id: '',
  image_prompt_template_id: '',
  editorial_review_profile_id: '',
  enabled: true,
})
const modelForm = reactive({ id: null, name: '', provider_type: 'openai_compatible', api_base: '', model: '', api_key: '', enabled: true })
const profileForm = reactive({ id: null, name: '', description: '', enabled: true, strictness: 'standard', roles: [] })
const promptForm = reactive({ id: null, name: '', purpose: 'article', content: '', enabled: true })
const planForm = reactive({
  id: null,
  name: '',
  description: '',
  article_prompt_template_id: '',
  image_prompt_template_id: '',
  editorial_review_profile_id: '',
  draft_template_account_id: '',
  layoutJson: '',
  imageSettingsJson: '',
  enabled: true,
})
const feishu = reactive({
  enabled: false,
  app_id: '',
  app_secret: '',
  verification_token: '',
  encrypt_key: '',
  clear_event_security: false,
  allow_all: false,
  allowed_open_ids_text: '',
  allowed_chat_ids_text: '',
  default_account_ids: [],
  agent_model_id: '',
  has_app_secret: false,
  has_verification_token: false,
  has_encrypt_key: false,
})
const relay = reactive({
  enabled: false,
  gateway_url: '',
  username: '',
  password: '',
  clear_password: false,
  access_code: '',
  has_password: false,
  test_account_id: '',
})
const layoutForm = reactive({ account_id: '', account_name: '', json: '' })
const templateForm = reactive({ account_id: '', account_name: '', placeholder: '蓝血经营管理系统正文', selected_key: '', items: [] })

const isAdmin = computed(() => session.isAdmin.value)
const textModels = computed(() => models.value.filter((item) => !['gemini_image', 'openai_image', 'image'].includes(item.provider_type)))
const articleTemplates = computed(() => promptTemplates.value.filter((item) => item.purpose === 'article'))
const imageTemplates = computed(() => promptTemplates.value.filter((item) => item.purpose === 'image'))
const imageProviderTypes = new Set(['image_alibaba', 'image_minimax', 'image_volcengine', 'image_zhipu', 'openai_image'])
const isImageModel = (model) => imageProviderTypes.has(String(model?.provider_type || ''))
const adminSections = new Set(['models', 'prompts', 'plans', 'feishu', 'relay', 'users'])

function asLines(values) {
  return (values || []).join('\n')
}

function parseLines(value) {
  return [...new Set(String(value || '').split(/[\n,，]/).map((item) => item.trim()).filter(Boolean))]
}

function jsonObject(value, label) {
  const source = String(value || '').trim()
  if (!source) return null
  try {
    const parsed = JSON.parse(source)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error()
    return parsed
  } catch {
    throw new Error(`${label}必须是合法的 JSON 对象`)
  }
}

async function load() {
  loading.value = true
  try {
    const [profileRows, reviewOptions, accountRows, promptRows, planRows] = await Promise.all([
      api.editorialProfiles(),
      api.editorialOptions(),
      api.configurationAccounts(),
      api.promptTemplates(),
      api.creationPlans(),
    ])
    profiles.value = profileRows
    options.value = reviewOptions
    accounts.value = accountRows
    promptTemplates.value = promptRows
    creationPlans.value = planRows

    if (isAdmin.value) {
      const [modelRows, userRows, feishuData, relayData, pairingData] = await Promise.all([
        api.adminModels(),
        api.adminUsers(),
        api.feishuSettings(),
        api.wechatRelaySettings(),
        api.feishuPairing(),
      ])
      models.value = modelRows
      users.value = userRows
      hydrateFeishu(feishuData)
      hydrateRelay(relayData)
      pairing.value = pairingData
    } else {
      models.value = await api.models('text')
      if (adminSections.has(active.value)) active.value = 'accounts'
    }
    const planEntries = await Promise.all(accountRows.map(async (account) => {
      try {
        return [account.id, await api.accountCreationPlan(account.id)]
      } catch {
        return [account.id, null]
      }
    }))
    accountPlans.value = Object.fromEntries(planEntries)
    const reviewEntries = await Promise.all(accountRows.map(async (account) => {
      try {
        return [account.id, await api.editorialDefault(account.id)]
      } catch {
        return [account.id, null]
      }
    }))
    accountReviewDefaults.value = Object.fromEntries(reviewEntries)
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}

function hydrateFeishu(data = {}) {
  const settings = data.settings || {}
  Object.assign(feishu, {
    enabled: Boolean(settings.enabled),
    app_id: settings.app_id || '',
    app_secret: '',
    verification_token: '',
    encrypt_key: '',
    clear_event_security: false,
    allow_all: Boolean(settings.allow_all),
    allowed_open_ids_text: asLines(settings.allowed_open_ids),
    allowed_chat_ids_text: asLines(settings.allowed_chat_ids),
    default_account_ids: settings.default_account_ids || [],
    agent_model_id: settings.agent_model_id || '',
    has_app_secret: Boolean(settings.has_app_secret),
    has_verification_token: Boolean(settings.has_verification_token),
    has_encrypt_key: Boolean(settings.has_encrypt_key),
  })
  feishuRuntime.value = data.runtime || { status: 'unknown' }
}

function hydrateRelay(data = {}) {
  const settings = data.settings || {}
  relayConnection.value = data.connection || {}
  Object.assign(relay, {
    enabled: Boolean(settings.enabled),
    gateway_url: settings.gateway_url || data.connection?.gateway_url || '',
    username: settings.username || '',
    password: '',
    clear_password: false,
    access_code: '',
    has_password: Boolean(settings.has_password),
    test_account_id: relay.test_account_id || accounts.value[0]?.id || '',
  })
}

function editAccount(account = null) {
  const boundPlan = account ? accountPlans.value[account.id] : null
  const layout = account?.layout || {}
  const articlePrompt = layout.article_prompt || {}
  const imagePrompt = layout.inline_images || {}
  Object.assign(accountForm, account ? {
    id: account.id,
    name: account.name || '',
    app_id: account.app_id || '',
    app_secret: '',
    model_id: account.model_id || '',
    review_priority: Number(account.review_priority || 0),
    plan_id: boundPlan?.plan_id || 'builtin:default',
    article_prompt_template_id: articlePrompt.prompt_mode === 'template' ? (articlePrompt.prompt_template_id || '') : '',
    image_prompt_template_id: imagePrompt.prompt_mode === 'template' ? (imagePrompt.prompt_template_id || '') : '',
    editorial_review_profile_id: accountReviewDefaults.value[account.id]?.profile_id || '',
    enabled: account.enabled !== false,
  } : {
    id: null,
    name: '',
    app_id: '',
    app_secret: '',
    model_id: '',
    review_priority: 0,
    plan_id: 'builtin:default',
    article_prompt_template_id: '',
    image_prompt_template_id: '',
    editorial_review_profile_id: '',
    enabled: true,
  })
  accountDialog.value = true
}

async function saveAccount() {
  if (!accountForm.name.trim() || !accountForm.app_id.trim()) return ElMessage.warning('请填写公众号名称和 AppID')
  if (!accountForm.id && !accountForm.app_secret.trim()) return ElMessage.warning('首次添加公众号必须填写 AppSecret')
  saving.value = true
  try {
    const saved = await api.saveAccount({
      id: accountForm.id,
      name: accountForm.name.trim(),
      app_id: accountForm.app_id.trim(),
      app_secret: accountForm.app_secret.trim() || null,
      model_id: accountForm.model_id || '',
      review_priority: Number(accountForm.review_priority || 0),
      enabled: accountForm.enabled,
    })
    // The base record is already persisted before the related plan/prompt calls.
    // Retrying after a later validation error must update that record, not create
    // a duplicate account with the same AppID.
    accountForm.id = saved.id
    if (accountForm.plan_id) await api.applyCreationPlan(saved.id, accountForm.plan_id)
    await Promise.all([
      api.bindAccountPrompt(saved.id, 'article', accountForm.article_prompt_template_id),
      api.bindAccountPrompt(saved.id, 'image', accountForm.image_prompt_template_id),
      accountForm.editorial_review_profile_id
        ? api.setEditorialDefault(saved.id, accountForm.editorial_review_profile_id)
        : Promise.resolve(),
    ])
    accountDialog.value = false
    ElMessage.success('公众号配置和默认创作方案已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function testAccount(account) {
  testingAccountId.value = account.id
  try {
    const reports = await api.preflightAccounts([account.id], { forceWechatCheck: true })
    const report = reports[0] || {}
    const check = (report.checks || []).find((item) => item.key === 'wechat')
    if (check?.ok) ElMessage.success(`${account.name}：${check.message}`)
    else ElMessage.error(check?.message || '公众号连接检查未通过')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    testingAccountId.value = ''
  }
}

function editAccountLayout(account) {
  Object.assign(layoutForm, {
    account_id: account.id,
    account_name: account.name,
    json: JSON.stringify(account.layout || {}, null, 2),
  })
  layoutDialog.value = true
}

async function saveAccountLayout() {
  saving.value = true
  try {
    const layout = jsonObject(layoutForm.json, '公众号排版与图片设置') || {}
    await api.saveAccountLayout(layoutForm.account_id, layout)
    layoutDialog.value = false
    ElMessage.success('公众号排版、图片和封面设置已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function openTemplateManager(account) {
  Object.assign(templateForm, {
    account_id: account.id,
    account_name: account.name,
    placeholder: account.layout?.editor_template?.placeholder || '蓝血经营管理系统正文',
    selected_key: '',
    items: [],
  })
  templateDialog.value = true
  await loadTemplateDrafts()
}

async function loadTemplateDrafts() {
  loadingTemplates.value = true
  try {
    const data = await api.templateDrafts(templateForm.account_id, templateForm.placeholder)
    templateForm.items = data.items || []
    const current = data.current || {}
    templateForm.selected_key = current.media_id ? `${current.media_id}:${current.article_index || 0}` : ''
    if (current.placeholder) templateForm.placeholder = current.placeholder
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loadingTemplates.value = false
  }
}

async function applyTemplateDraft() {
  const selected = templateForm.items.find((item) => item.key === templateForm.selected_key)
  if (!selected) return ElMessage.warning('请先选择一个模板草稿')
  if (!selected.has_placeholder) return ElMessage.warning('所选草稿缺少正文占位符，不能应用')
  saving.value = true
  try {
    await api.applyTemplateDraft(templateForm.account_id, {
      media_id: selected.media_id,
      article_index: selected.article_index,
      placeholder: templateForm.placeholder,
    })
    templateDialog.value = false
    ElMessage.success(`已应用草稿模板：${selected.title}`)
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function deleteAccount(account) {
  try {
    await ElMessageBox.confirm(`删除公众号“${account.name}”后，该账号将不能再生成新文章。确定继续吗？`, '删除公众号', { type: 'warning' })
    await api.deleteAccount(account.id)
    ElMessage.success('公众号已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function editModel(model = null) {
  Object.assign(modelForm, model ? {
    id: model.id,
    name: model.name,
    provider_type: model.provider_type || 'openai_compatible',
    api_base: model.api_base || '',
    model: model.model || '',
    api_key: '',
    enabled: model.enabled !== false,
  } : { id: null, name: '', provider_type: 'openai_compatible', api_base: '', model: '', api_key: '', enabled: true })
  modelDialog.value = true
}

async function saveModel() {
  if (!modelForm.name.trim() || !modelForm.model.trim()) return ElMessage.warning('请填写模型名称和模型标识')
  saving.value = true
  try {
    await api.saveAdminModel({ ...modelForm, api_key: modelForm.api_key || null })
    modelDialog.value = false
    ElMessage.success('模型配置已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function testModel(model) {
  testingId.value = model.id
  try {
    if (isImageModel(model)) {
      const blob = await api.testAdminImageModel(model.id)
      if (modelPreviewUrl.value) URL.revokeObjectURL(modelPreviewUrl.value)
      modelPreviewUrl.value = URL.createObjectURL(blob)
      modelPreviewDialog.value = true
      ElMessage.success(`${model.name} 已成功生成测试图片`)
    } else {
      await api.testAdminModel(model.id)
      ElMessage.success(`${model.name} 连接测试通过`)
    }
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    testingId.value = ''
  }
}

async function testFeishu() {
  testingFeishu.value = true
  try {
    const result = await api.testFeishuSettings({
      app_id: feishu.app_id,
      app_secret: feishu.app_secret || null,
    })
    ElMessage.success(result.message || '飞书 App ID 与 App Secret 验证成功')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    testingFeishu.value = false
  }
}

async function createPairingCode() {
  try {
    pairing.value = await api.createFeishuPairing()
    ElMessage.success('已生成飞书绑定口令，请在有效期内发送给机器人')
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function refreshPairing() {
  try {
    pairing.value = await api.feishuPairing()
  } catch (error) {
    ElMessage.error(error.message)
  }
}

async function deleteModel(model) {
  try {
    await ElMessageBox.confirm(`确定删除模型“${model.name}”吗？`, '删除模型', { type: 'warning' })
    await api.deleteAdminModel(model.id)
    ElMessage.success('模型已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function editProfile(profile = null) {
  Object.assign(profileForm, profile ? {
    id: profile.id,
    name: profile.name,
    description: profile.description || '',
    enabled: profile.enabled !== false,
    strictness: profile.config?.strictness || 'standard',
    roles: profile.config?.role_ids || [],
  } : { id: null, name: '', description: '', enabled: true, strictness: 'standard', roles: [] })
  profileDialog.value = true
}

async function saveProfile() {
  if (!profileForm.name.trim()) return ElMessage.warning('请填写评审方案名称')
  const payload = {
    name: profileForm.name,
    description: profileForm.description,
    enabled: profileForm.enabled,
    config: { strictness: profileForm.strictness, role_ids: profileForm.roles },
  }
  saving.value = true
  try {
    if (profileForm.id) await api.updateEditorialProfile(profileForm.id, payload)
    else await api.createEditorialProfile(payload)
    profileDialog.value = false
    ElMessage.success('评审方案已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function deleteProfile(profile) {
  try {
    await ElMessageBox.confirm(`确定删除评审方案“${profile.name}”吗？`, '删除评审方案', { type: 'warning' })
    await api.deleteEditorialProfile(profile.id)
    ElMessage.success('评审方案已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function editPrompt(template = null) {
  Object.assign(promptForm, template ? {
    id: template.id,
    name: template.name || '',
    purpose: template.purpose || 'article',
    content: template.content || '',
    enabled: template.enabled !== false,
  } : { id: null, name: '', purpose: 'article', content: '', enabled: true })
  promptDialog.value = true
}

async function savePrompt() {
  if (!promptForm.name.trim() || !promptForm.content.trim()) return ElMessage.warning('请填写模板名称和提示词内容')
  saving.value = true
  try {
    await api.savePromptTemplate({ ...promptForm })
    promptDialog.value = false
    ElMessage.success('提示词模板已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function deletePrompt(template) {
  try {
    await ElMessageBox.confirm(`确定删除提示词“${template.name}”吗？`, '删除提示词', { type: 'warning' })
    await api.deletePromptTemplate(template.id)
    ElMessage.success('提示词模板已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

function editPlan(plan = null) {
  Object.assign(planForm, plan ? {
    id: plan.id,
    name: plan.name || '',
    description: plan.description || '',
    article_prompt_template_id: plan.article_prompt_template_id || '',
    image_prompt_template_id: plan.image_prompt_template_id || '',
    editorial_review_profile_id: plan.editorial_review_profile_id || '',
    draft_template_account_id: '',
    layoutJson: Object.keys(plan.layout || {}).length ? JSON.stringify(plan.layout, null, 2) : '',
    imageSettingsJson: Object.keys(plan.image_settings || {}).length ? JSON.stringify(plan.image_settings, null, 2) : '',
    enabled: plan.enabled !== false,
  } : {
    id: null,
    name: '',
    description: '',
    article_prompt_template_id: '',
    image_prompt_template_id: '',
    editorial_review_profile_id: '',
    draft_template_account_id: '',
    layoutJson: '',
    imageSettingsJson: '',
    enabled: true,
  })
  planDialog.value = true
}

async function savePlan() {
  if (!planForm.name.trim()) return ElMessage.warning('请填写创作方案名称')
  saving.value = true
  try {
    await api.saveCreationPlan({
      id: planForm.id,
      name: planForm.name.trim(),
      description: planForm.description.trim(),
      article_prompt_template_id: planForm.article_prompt_template_id || null,
      image_prompt_template_id: planForm.image_prompt_template_id || null,
      editorial_review_profile_id: planForm.editorial_review_profile_id || null,
      draft_template_account_id: planForm.draft_template_account_id || null,
      layout: jsonObject(planForm.layoutJson, '排版设置'),
      image_settings: jsonObject(planForm.imageSettingsJson, '图片设置'),
      enabled: planForm.enabled,
    })
    planDialog.value = false
    ElMessage.success('创作方案已保存')
    await load()
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function deletePlan(plan) {
  try {
    await ElMessageBox.confirm(`确定删除创作方案“${plan.name}”吗？`, '删除创作方案', { type: 'warning' })
    await api.deleteCreationPlan(plan.id)
    ElMessage.success('创作方案已删除')
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(error.message || String(error))
  }
}

async function saveFeishu() {
  saving.value = true
  try {
    const data = await api.saveFeishuSettings({
      enabled: feishu.enabled,
      app_id: feishu.app_id.trim(),
      app_secret: feishu.app_secret.trim() || null,
      verification_token: feishu.verification_token.trim() || null,
      encrypt_key: feishu.encrypt_key.trim() || null,
      clear_event_security: feishu.clear_event_security,
      allow_all: feishu.allow_all,
      allowed_open_ids: parseLines(feishu.allowed_open_ids_text),
      allowed_chat_ids: parseLines(feishu.allowed_chat_ids_text),
      default_account_ids: feishu.default_account_ids,
      agent_model_id: feishu.agent_model_id || '',
    })
    hydrateFeishu(data)
    ElMessage.success('飞书配置已加密保存')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function saveRelay() {
  saving.value = true
  try {
    const data = await api.saveWechatRelaySettings({
      enabled: relay.enabled,
      gateway_url: relay.gateway_url || relayConnection.value.gateway_url || '',
      username: relay.username.trim(),
      password: relay.password.trim() || null,
      clear_password: relay.clear_password,
      access_code: relay.access_code.trim() || null,
    })
    hydrateRelay(data)
    ElMessage.success('微信云中转配置已加密保存')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    saving.value = false
  }
}

async function testRelay() {
  if (!relay.test_account_id) return ElMessage.warning('请选择一个公众号进行检测')
  testingAccountId.value = relay.test_account_id
  try {
    const reports = await api.preflightAccounts([relay.test_account_id], { forceWechatCheck: true })
    const report = reports[0] || {}
    const wechat = (report.checks || []).find((item) => item.key === 'wechat')
    const draft = (report.checks || []).find((item) => item.key === 'draft')
    if (wechat?.ok && draft?.ok) ElMessage.success(`中转连接正常：${draft.message}`)
    else ElMessage.error(draft?.message || wechat?.message || '微信云中转检测未通过')
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    testingAccountId.value = ''
  }
}

async function changeUserState(user, enabled) {
  try {
    await api.updateAdminUser(user.id, enabled)
    user.enabled = enabled
    ElMessage.success(enabled ? '协作账号已启用' : '协作账号已停用')
  } catch (error) {
    user.enabled = !enabled
    ElMessage.error(error.message)
  }
}

onMounted(load)
</script>

<template>
  <div class="page-stack settings-page" :class="{ 'is-loading': loading }">
    <section class="page-hero">
      <div><span class="eyebrow">SYSTEM CONFIGURATION</span><h2>系统设置</h2><p>管理公众号、创作规则、AI 评审和外部连接。</p></div>
      <el-button :icon="Refresh" circle plain aria-label="刷新" @click="load" />
    </section>

    <div class="settings-layout">
      <el-card class="settings-nav surface-card" shadow="never">
        <el-menu :default-active="active" @select="(value) => (active = value)">
          <el-menu-item index="accounts"><el-icon><Connection /></el-icon><span>公众号</span></el-menu-item>
          <el-menu-item v-if="isAdmin" index="models"><el-icon><Key /></el-icon><span>模型管理</span></el-menu-item>
          <el-menu-item v-if="isAdmin" index="prompts"><el-icon><Document /></el-icon><span>提示词模板</span></el-menu-item>
          <el-menu-item v-if="isAdmin" index="plans"><el-icon><Operation /></el-icon><span>创作方案</span></el-menu-item>
          <el-menu-item index="reviews"><el-icon><Check /></el-icon><span>AI 评审方案</span></el-menu-item>
          <el-menu-item v-if="isAdmin" index="feishu"><el-icon><Promotion /></el-icon><span>飞书机器人</span></el-menu-item>
          <el-menu-item v-if="isAdmin" index="relay"><el-icon><Connection /></el-icon><span>微信云中转</span></el-menu-item>
          <el-menu-item v-if="isAdmin" index="users"><el-icon><User /></el-icon><span>协作账号</span></el-menu-item>
          <el-menu-item index="system"><el-icon><Setting /></el-icon><span>运行信息</span></el-menu-item>
        </el-menu>
      </el-card>

      <section class="settings-content">
        <el-card v-if="active === 'accounts'" class="surface-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>公众号配置</h3><p>每个公众号独立绑定模型、审核优先级和默认创作方案</p></div><el-button type="primary" :icon="Plus" @click="editAccount()">添加公众号</el-button></div></template>
          <el-table :data="accounts" style="width: 100%">
            <el-table-column prop="name" label="公众号" min-width="160" />
            <el-table-column prop="app_id" label="AppID" min-width="190" show-overflow-tooltip />
            <el-table-column prop="model_name" label="文字模型" min-width="160"><template #default="{ row }">{{ row.model_name || '暂未绑定模型' }}</template></el-table-column>
            <el-table-column label="创作方案" min-width="160"><template #default="{ row }">{{ accountPlans[row.id]?.plan?.name || '系统默认方案' }}</template></el-table-column>
            <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.enabled === false ? 'info' : 'success'" effect="light">{{ row.enabled === false ? '停用' : '可用' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" width="390" fixed="right"><template #default="{ row }"><el-button link type="primary" :icon="Connection" :loading="testingAccountId === row.id" @click="testAccount(row)">测试连接</el-button><el-button link type="primary" :icon="Edit" @click="editAccount(row)">基础信息</el-button><el-button link :icon="Operation" @click="editAccountLayout(row)">排版与图片</el-button><el-button link :icon="Document" @click="openTemplateManager(row)">草稿模板</el-button><el-button link type="danger" :icon="Delete" @click="deleteAccount(row)">删除</el-button></template></el-table-column>
          </el-table>
          <el-alert class="settings-note" type="info" :closable="false" show-icon title="AppSecret 等凭证会在服务器加密保存；编辑时留空表示保留已有密钥，页面不会回显明文。" />
        </el-card>

        <el-card v-else-if="active === 'models'" class="surface-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>AI 模型</h3><p>管理文字与图片模型连接、密钥和启用状态</p></div><el-button type="primary" :icon="Plus" @click="editModel()">添加模型</el-button></div></template>
          <el-table :data="models" style="width: 100%"><el-table-column prop="name" label="名称" min-width="150" /><el-table-column label="用途" width="100"><template #default="{ row }"><el-tag :type="isImageModel(row) ? 'warning' : 'primary'" effect="plain">{{ isImageModel(row) ? '图片' : '文字' }}</el-tag></template></el-table-column><el-table-column prop="provider_type" label="类型" min-width="150" /><el-table-column prop="model" label="模型标识" min-width="190" /><el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column><el-table-column label="操作" width="260" fixed="right"><template #default="{ row }"><el-button link type="primary" :icon="isImageModel(row) ? Picture : Connection" :loading="testingId === row.id" @click="testModel(row)">{{ isImageModel(row) ? '生成测试图' : '测试连接' }}</el-button><el-button link :icon="Edit" @click="editModel(row)">编辑</el-button><el-button link type="danger" :icon="Delete" @click="deleteModel(row)">删除</el-button></template></el-table-column></el-table>
        </el-card>

        <el-card v-else-if="active === 'prompts'" class="surface-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>提示词模板</h3><p>分别维护文章写作与图片生成规则，可在创作方案中复用</p></div><el-button type="primary" :icon="Plus" @click="editPrompt()">新建模板</el-button></div></template>
          <el-table :data="promptTemplates" style="width: 100%"><el-table-column prop="name" label="名称" min-width="180" /><el-table-column label="用途" width="110"><template #default="{ row }"><el-tag :type="row.purpose === 'image' ? 'warning' : 'primary'" effect="plain">{{ row.purpose === 'image' ? '图片生成' : '文章写作' }}</el-tag></template></el-table-column><el-table-column prop="content" label="规则摘要" min-width="300" show-overflow-tooltip /><el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag></template></el-table-column><el-table-column label="操作" width="150" fixed="right"><template #default="{ row }"><el-button link type="primary" :icon="Edit" @click="editPrompt(row)">编辑</el-button><el-button link type="danger" :icon="Delete" @click="deletePrompt(row)">删除</el-button></template></el-table-column></el-table>
        </el-card>

        <el-card v-else-if="active === 'plans'" class="surface-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>创作方案</h3><p>一次组合文章提示词、图片规则、排版和默认评审方案</p></div><el-button type="primary" :icon="Plus" @click="editPlan()">新建方案</el-button></div></template>
          <div class="profile-grid"><article v-for="plan in creationPlans" :key="plan.id" class="profile-card"><div><el-tag v-if="plan.builtin" size="small" effect="plain">系统内置</el-tag><el-tag v-else size="small" type="success" effect="plain">自定义</el-tag><el-tag v-if="plan.enabled === false" size="small" type="info">已停用</el-tag></div><h4>{{ plan.name }}</h4><p>{{ plan.description || '组合当前公众号的写作、排版与评审规则。' }}</p><small>文章提示词：{{ plan.article_prompt_template_name || '系统默认' }}</small><small>默认评审：{{ plan.editorial_review_profile_name || '系统默认' }}</small><div v-if="!plan.builtin" class="inline-actions"><el-button text type="primary" @click="editPlan(plan)">编辑</el-button><el-button text type="danger" @click="deletePlan(plan)">删除</el-button></div></article></div>
        </el-card>

        <el-card v-else-if="active === 'reviews'" class="surface-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>AI 评审方案</h3><p>控制评审角色、严格程度与改写权限</p></div><el-button type="primary" :icon="Plus" @click="editProfile()">新建方案</el-button></div></template>
          <div class="profile-grid"><article v-for="profile in profiles" :key="profile.id" class="profile-card"><div><el-tag v-if="profile.builtin" size="small" effect="plain">系统内置</el-tag><el-tag v-else size="small" type="success" effect="plain">自定义</el-tag><el-tag v-if="profile.enabled === false" size="small" type="info">已停用</el-tag></div><h4>{{ profile.name }}</h4><p>{{ profile.description || '适用于公众号文章的综合质量评审。' }}</p><small>严格程度：{{ profile.config?.strictness || 'standard' }}</small><div v-if="!profile.builtin" class="inline-actions"><el-button text type="primary" @click="editProfile(profile)">编辑</el-button><el-button text type="danger" @click="deleteProfile(profile)">删除</el-button></div></article></div>
        </el-card>

        <el-card v-else-if="active === 'feishu'" class="surface-card" shadow="never">
          <template #header><div class="card-header-row"><div><h3>飞书机器人</h3><p>配置企业自建应用、访问范围和机器人默认生成目标</p></div><el-tag :type="feishuRuntime.status === 'connected' ? 'success' : 'info'">运行状态：{{ feishuRuntime.status || 'unknown' }}</el-tag></div></template>
          <el-form class="settings-form" label-position="top">
            <el-switch v-model="feishu.enabled" active-text="启用飞书机器人" />
            <div class="form-grid"><el-form-item label="App ID"><el-input v-model="feishu.app_id" /></el-form-item><el-form-item :label="`App Secret${feishu.has_app_secret ? '（已保存，留空不修改）' : ''}`"><el-input v-model="feishu.app_secret" type="password" show-password /></el-form-item></div>
            <div class="form-grid"><el-form-item :label="`Verification Token${feishu.has_verification_token ? '（已保存）' : ''}`"><el-input v-model="feishu.verification_token" type="password" show-password /></el-form-item><el-form-item :label="`Encrypt Key${feishu.has_encrypt_key ? '（已保存）' : ''}`"><el-input v-model="feishu.encrypt_key" type="password" show-password /></el-form-item></div>
            <el-checkbox v-model="feishu.clear_event_security">清除已保存的 Verification Token 与 Encrypt Key</el-checkbox>
            <div class="form-grid"><el-form-item label="机器人智能体模型"><el-select v-model="feishu.agent_model_id" clearable style="width:100%"><el-option v-for="model in textModels" :key="model.id" :label="model.name" :value="model.id" /></el-select></el-form-item><el-form-item label="默认生成到哪些公众号"><el-select v-model="feishu.default_account_ids" multiple collapse-tags style="width:100%"><el-option v-for="account in accounts.filter((item) => item.enabled !== false)" :key="account.id" :label="account.name" :value="account.id" /></el-select></el-form-item></div>
            <el-switch v-model="feishu.allow_all" active-text="允许所有飞书用户和群聊使用" />
            <div v-if="!feishu.allow_all" class="form-grid"><el-form-item label="允许的 Open ID（每行一个）"><el-input v-model="feishu.allowed_open_ids_text" type="textarea" :rows="5" /></el-form-item><el-form-item label="允许的 Chat ID（每行一个）"><el-input v-model="feishu.allowed_chat_ids_text" type="textarea" :rows="5" /></el-form-item></div>
            <div class="inline-actions"><el-button :icon="Connection" :loading="testingFeishu" @click="testFeishu">验证 App ID 与密钥</el-button><el-button type="primary" :loading="saving" @click="saveFeishu">保存飞书配置</el-button></div>
            <el-divider content-position="left">安全绑定使用者</el-divider>
            <el-alert type="info" :closable="false" show-icon title="保存并启用机器人后生成一次性口令，在飞书里向机器人发送该口令即可绑定当前用户。" />
            <div class="inline-actions"><el-button type="primary" plain @click="createPairingCode">生成 30 分钟绑定口令</el-button><el-button @click="refreshPairing">刷新绑定状态</el-button></div>
            <el-result v-if="pairing?.message" icon="success" title="绑定口令已生成" :sub-title="`${pairing.message}（有效期至 ${formatDateTime(pairing.expires_at)}）`" />
            <el-descriptions v-else :column="1" border><el-descriptions-item label="绑定状态">{{ pairing?.status === 'used' ? '已绑定' : pairing?.status === 'waiting' ? '等待用户发送口令' : pairing?.status === 'expired' ? '口令已过期' : '尚未生成口令' }}</el-descriptions-item><el-descriptions-item v-if="pairing?.bound_open_id" label="已绑定 Open ID">{{ pairing.bound_open_id }}</el-descriptions-item></el-descriptions>
            <el-alert type="warning" :closable="false" show-icon title="若修改了已运行机器人的 App ID 或密钥，保存后需由服务器重启 API 容器，新的长连接配置才会生效。" />
          </el-form>
        </el-card>

        <el-card v-else-if="active === 'relay'" class="surface-card" shadow="never">
          <template #header><div><h3>微信公众号云中转</h3><p>通过固定出口 IP {{ relayConnection.fixed_egress_ip || '47.99.126.8' }} 调用微信官方接口</p></div></template>
          <el-form class="settings-form" label-position="top">
            <el-switch v-model="relay.enabled" active-text="启用微信云中转" />
            <el-alert type="info" :closable="false" show-icon title="可粘贴中转接入码快速配置；也可以在下方手动填写地址、用户名和密码。" />
            <el-form-item label="中转接入码"><el-input v-model="relay.access_code" type="password" show-password placeholder="留空表示使用手动配置" /></el-form-item>
            <el-form-item label="HTTPS 中转地址"><el-input v-model="relay.gateway_url" placeholder="https://example.com/wechat-relay" /></el-form-item>
            <div class="form-grid"><el-form-item label="中转用户名"><el-input v-model="relay.username" /></el-form-item><el-form-item :label="`中转密码${relay.has_password ? '（已保存，留空不修改）' : ''}`"><el-input v-model="relay.password" type="password" show-password /></el-form-item></div>
            <el-checkbox v-model="relay.clear_password">清除已保存的中转密码</el-checkbox>
            <el-form-item label="连接检测公众号"><el-select v-model="relay.test_account_id" placeholder="选择一个已保存凭证的公众号" style="width:100%"><el-option v-for="account in accounts" :key="account.id" :label="account.name" :value="account.id" /></el-select></el-form-item>
            <div class="inline-actions"><el-button type="primary" :loading="saving" @click="saveRelay">保存中转配置</el-button><el-button :icon="Connection" :loading="testingAccountId === relay.test_account_id" @click="testRelay">保存后重新检测</el-button></div>
            <el-alert type="info" :closable="false" show-icon title="检测只会获取 access_token 并只读查询素材与草稿箱，不会新建、修改或删除草稿。" />
          </el-form>
        </el-card>

        <el-card v-else-if="active === 'users'" class="surface-card" shadow="never">
          <template #header><div><h3>协作账号</h3><p>每个账号拥有独立的数据空间和任务记录</p></div></template>
          <el-table :data="users" style="width: 100%"><el-table-column prop="username" label="用户名" /><el-table-column prop="role" label="角色"><template #default="{ row }">{{ row.role === 'admin' ? '管理员' : '内容运营' }}</template></el-table-column><el-table-column label="状态"><template #default="{ row }"><el-switch :model-value="row.enabled" :disabled="row.id === session.state.user?.id" active-text="正常" inactive-text="停用" @change="(value) => changeUserState(row, value)" /></template></el-table-column><el-table-column label="创建时间" min-width="180"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column></el-table>
        </el-card>

        <el-card v-else class="surface-card" shadow="never">
          <template #header><div><h3>运行信息</h3><p>当前前端架构与数据安全边界</p></div></template>
          <el-descriptions :column="1" border><el-descriptions-item label="前端框架">Vue 3 + Element Plus</el-descriptions-item><el-descriptions-item label="接口服务">FastAPI</el-descriptions-item><el-descriptions-item label="当前用户">{{ session.state.user?.username }}</el-descriptions-item><el-descriptions-item label="安全模式">只写入公众号草稿箱，不自动群发</el-descriptions-item><el-descriptions-item label="后台任务">生成、评审和改写可关闭弹窗继续运行</el-descriptions-item></el-descriptions>
        </el-card>
      </section>
    </div>

    <el-dialog v-model="accountDialog" :title="accountForm.id ? '编辑公众号' : '添加公众号'" width="min(680px, 94vw)" append-to-body>
      <el-form label-position="top"><div class="form-grid"><el-form-item label="公众号名称" required><el-input v-model="accountForm.name" /></el-form-item><el-form-item label="公众号 AppID" required><el-input v-model="accountForm.app_id" /></el-form-item></div><el-form-item :label="`公众号 AppSecret${accountForm.id ? '（留空保留原密钥）' : ''}`" required><el-input v-model="accountForm.app_secret" type="password" show-password /></el-form-item><div class="form-grid"><el-form-item label="文章模型"><el-select v-model="accountForm.model_id" clearable placeholder="使用平台默认模型" style="width:100%"><el-option v-for="model in textModels" :key="model.id" :label="model.name" :value="model.id" /></el-select></el-form-item><el-form-item label="默认创作方案"><el-select v-model="accountForm.plan_id" style="width:100%"><el-option v-for="plan in creationPlans.filter((item) => item.enabled !== false)" :key="plan.id" :label="plan.name" :value="plan.id" /></el-select></el-form-item></div><el-divider content-position="left">单项覆盖（可选）</el-divider><div class="form-grid"><el-form-item label="文章提示词模板"><el-select v-model="accountForm.article_prompt_template_id" clearable placeholder="沿用默认规则" style="width:100%"><el-option v-for="item in articleTemplates" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="图片提示词模板"><el-select v-model="accountForm.image_prompt_template_id" clearable placeholder="沿用默认规则" style="width:100%"><el-option v-for="item in imageTemplates" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></div><el-form-item label="默认 AI 评审方案"><el-select v-model="accountForm.editorial_review_profile_id" clearable style="width:100%"><el-option v-for="item in profiles.filter((profile) => profile.enabled !== false)" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="审核优先级（0—100，数字越大越靠前）"><el-slider v-model="accountForm.review_priority" show-input :min="0" :max="100" /></el-form-item><el-switch v-model="accountForm.enabled" active-text="启用公众号" /></el-form>
      <template #footer><el-button @click="accountDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveAccount">保存公众号</el-button></template>
    </el-dialog>

    <el-dialog v-model="modelDialog" :title="modelForm.id ? '编辑模型' : '添加模型'" width="min(620px, 94vw)" append-to-body>
      <el-form label-position="top"><div class="form-grid"><el-form-item label="显示名称" required><el-input v-model="modelForm.name" /></el-form-item><el-form-item label="模型标识" required><el-input v-model="modelForm.model" /></el-form-item></div><el-form-item label="接口类型"><el-select v-model="modelForm.provider_type" style="width: 100%"><el-option-group label="文字模型"><el-option label="OpenAI 兼容接口" value="openai_compatible" /><el-option label="Google Gemini" value="gemini" /><el-option label="Manus" value="manus" /></el-option-group><el-option-group label="图片模型"><el-option label="阿里云百炼（通义万相）" value="image_alibaba" /><el-option label="MiniMax" value="image_minimax" /><el-option label="火山方舟（豆包 Seedream）" value="image_volcengine" /><el-option label="智谱 AI（GLM-Image / CogView）" value="image_zhipu" /><el-option label="自定义 OpenAI Images 接口" value="openai_image" /></el-option-group></el-select></el-form-item><el-form-item label="API Base"><el-input v-model="modelForm.api_base" placeholder="官方预设可留空，自定义接口请填写完整地址" /></el-form-item><el-form-item label="API Key"><el-input v-model="modelForm.api_key" type="password" show-password :placeholder="modelForm.id ? '留空表示不修改现有密钥' : '请输入密钥'" /></el-form-item><el-switch v-model="modelForm.enabled" active-text="启用模型" /></el-form>
      <template #footer><el-button @click="modelDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveModel">保存模型</el-button></template>
    </el-dialog>

    <el-dialog v-model="promptDialog" :title="promptForm.id ? '编辑提示词模板' : '新建提示词模板'" width="min(760px, 94vw)" append-to-body>
      <el-form label-position="top"><div class="form-grid"><el-form-item label="模板名称" required><el-input v-model="promptForm.name" /></el-form-item><el-form-item label="用途"><el-segmented v-model="promptForm.purpose" :options="[{ label: '文章写作', value: 'article' }, { label: '图片生成', value: 'image' }]" /></el-form-item></div><el-form-item label="提示词内容" required><el-input v-model="promptForm.content" type="textarea" :rows="14" resize="vertical" /></el-form-item><el-switch v-model="promptForm.enabled" active-text="启用模板" /></el-form>
      <template #footer><el-button @click="promptDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="savePrompt">保存模板</el-button></template>
    </el-dialog>

    <el-dialog v-model="planDialog" :title="planForm.id ? '编辑创作方案' : '新建创作方案'" width="min(820px, 96vw)" append-to-body>
      <el-form label-position="top"><div class="form-grid"><el-form-item label="方案名称" required><el-input v-model="planForm.name" /></el-form-item><el-form-item label="说明"><el-input v-model="planForm.description" /></el-form-item></div><div class="form-grid"><el-form-item label="文章提示词"><el-select v-model="planForm.article_prompt_template_id" clearable style="width:100%"><el-option v-for="item in articleTemplates" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="图片提示词"><el-select v-model="planForm.image_prompt_template_id" clearable style="width:100%"><el-option v-for="item in imageTemplates" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></div><div class="form-grid"><el-form-item label="默认评审方案"><el-select v-model="planForm.editorial_review_profile_id" clearable style="width:100%"><el-option v-for="item in profiles" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item><el-form-item label="复制草稿模板的公众号（可选）"><el-select v-model="planForm.draft_template_account_id" clearable style="width:100%"><el-option v-for="item in accounts" :key="item.id" :label="item.name" :value="item.id" /></el-select></el-form-item></div><el-collapse><el-collapse-item title="高级排版与图片参数（JSON）" name="advanced"><el-alert type="info" :closable="false" title="留空会保留现有值；这里用于迁移原系统的完整高级设置。" /><div class="form-grid json-editors"><el-form-item label="排版设置"><el-input v-model="planForm.layoutJson" type="textarea" :rows="12" spellcheck="false" /></el-form-item><el-form-item label="图片与封面设置"><el-input v-model="planForm.imageSettingsJson" type="textarea" :rows="12" spellcheck="false" /></el-form-item></div></el-collapse-item></el-collapse><el-switch v-model="planForm.enabled" active-text="启用方案" /></el-form>
      <template #footer><el-button @click="planDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="savePlan">保存方案</el-button></template>
    </el-dialog>

    <el-dialog v-model="profileDialog" :title="profileForm.id ? '编辑评审方案' : '新建评审方案'" width="min(680px, 94vw)" append-to-body>
      <el-form label-position="top"><el-form-item label="方案名称" required><el-input v-model="profileForm.name" /></el-form-item><el-form-item label="说明"><el-input v-model="profileForm.description" type="textarea" :rows="3" /></el-form-item><el-form-item label="严格程度"><el-segmented v-model="profileForm.strictness" :options="[{ label: '宽松', value: 'relaxed' }, { label: '标准', value: 'standard' }, { label: '严格', value: 'strict' }]" /></el-form-item><el-form-item label="评审角色"><el-checkbox-group v-model="profileForm.roles"><el-checkbox v-for="role in options.roles || []" :key="role.id" :value="role.id" border>{{ role.name || role.label }}</el-checkbox></el-checkbox-group></el-form-item><el-switch v-model="profileForm.enabled" active-text="启用方案" /></el-form>
      <template #footer><el-button @click="profileDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveProfile">保存方案</el-button></template>
    </el-dialog>

    <el-dialog v-model="layoutDialog" :title="`排版与图片 · ${layoutForm.account_name}`" width="min(900px, 96vw)" append-to-body>
      <el-alert type="info" :closable="false" show-icon title="完整保留原系统的正文样式、标题、引用块、页尾、正文图片和封面参数；修改前建议复制一份 JSON 作为局部备份。" />
      <el-form label-position="top"><el-form-item label="完整排版与图片配置（JSON）"><el-input v-model="layoutForm.json" type="textarea" :rows="22" spellcheck="false" /></el-form-item></el-form>
      <template #footer><el-button @click="layoutDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveAccountLayout">保存完整配置</el-button></template>
    </el-dialog>

    <el-dialog v-model="templateDialog" :title="`草稿模板 · ${templateForm.account_name}`" width="min(760px, 96vw)" append-to-body>
      <el-alert type="info" :closable="false" show-icon title="只读取当前公众号草稿箱中标题包含“模板”的草稿，不会修改或删除草稿箱内容。" />
      <el-form label-position="top"><el-form-item label="正文占位文字"><el-input v-model="templateForm.placeholder" placeholder="蓝血经营管理系统正文"><template #append><el-button :loading="loadingTemplates" @click="loadTemplateDrafts">重新读取</el-button></template></el-input></el-form-item></el-form>
      <div v-loading="loadingTemplates" class="template-options">
        <el-radio-group v-model="templateForm.selected_key" class="template-radio-group">
          <el-radio v-for="item in templateForm.items" :key="item.key" :value="item.key" border :disabled="!item.has_placeholder">
            {{ item.title }}<small v-if="!item.has_placeholder">（缺少正文占位符）</small>
          </el-radio>
        </el-radio-group>
        <el-empty v-if="!loadingTemplates && !templateForm.items.length" description="没有找到标题包含“模板”的草稿" />
      </div>
      <template #footer><el-button @click="templateDialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="applyTemplateDraft">应用所选模板</el-button></template>
    </el-dialog>

    <el-dialog v-model="modelPreviewDialog" title="图片模型测试结果" width="min(760px, 94vw)" append-to-body>
      <el-alert type="success" :closable="false" show-icon title="图片模型已完成一次真实生成，当前接口、模型名称和 API Key 可以正常使用。" />
      <img v-if="modelPreviewUrl" class="model-test-preview" :src="modelPreviewUrl" alt="图片模型测试生成结果" />
    </el-dialog>
  </div>
</template>
