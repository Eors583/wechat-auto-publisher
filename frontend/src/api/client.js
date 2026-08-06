const TOKEN_KEY = 'wechat-publisher.auth-token'
const API_ROOT = String(import.meta.env.VITE_API_BASE || '/publisher-api/api/v1').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, { status = 0, failure = null, payload = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.failure = failure
    this.payload = payload
  }
}

export function getToken() {
  return window.localStorage.getItem(TOKEN_KEY) || ''
}

export function setToken(token) {
  if (token) window.localStorage.setItem(TOKEN_KEY, token)
  else window.localStorage.removeItem(TOKEN_KEY)
}

function errorMessage(payload, status) {
  const detail = payload?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item?.msg || '').filter(Boolean).join('；') || `请求参数不正确（${status}）`
  }
  return payload?.failure?.message || payload?.failure?.title || `请求失败（${status || '网络异常'}）`
}

export async function request(path, options = {}) {
  const token = getToken()
  const headers = new Headers(options.headers || {})
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (options.body != null && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  headers.set('Accept', 'application/json')

  let response
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...options, headers })
  } catch (error) {
    throw new ApiError('无法连接服务器，请检查网络后重试', { payload: error })
  }

  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => null)
    : await response.text().catch(() => '')

  if (!response.ok) {
    if (response.status === 401) {
      setToken('')
      window.dispatchEvent(new CustomEvent('publisher:unauthorized'))
    }
    throw new ApiError(errorMessage(payload, response.status), {
      status: response.status,
      failure: payload?.failure || null,
      payload,
    })
  }
  return payload
}

async function requestBlob(path, options = {}) {
  const token = getToken()
  const headers = new Headers(options.headers || {})
  if (token) headers.set('Authorization', `Bearer ${token}`)
  headers.set('Accept', 'image/*, application/json')
  let response
  try {
    response = await fetch(`${API_ROOT}${path}`, { ...options, headers })
  } catch (error) {
    throw new ApiError('无法连接服务器，请检查网络后重试', { payload: error })
  }
  if (!response.ok) {
    if (response.status === 401) {
      setToken('')
      window.dispatchEvent(new CustomEvent('publisher:unauthorized'))
    }
    const contentType = response.headers.get('content-type') || ''
    const payload = contentType.includes('application/json')
      ? await response.json().catch(() => null)
      : await response.text().catch(() => '')
    throw new ApiError(errorMessage(payload, response.status), {
      status: response.status,
      failure: payload?.failure || null,
      payload,
    })
  }
  return response.blob()
}

export const api = {
  login: (username, password) => request('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  register: (username, password) => request('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  }),
  me: () => request('/auth/me'),
  logout: () => request('/auth/logout', { method: 'POST' }),
  accounts: () => request('/accounts'),
  models: (purpose = 'text') => request(`/models?purpose=${encodeURIComponent(purpose)}`),
  onboarding: () => request('/onboarding/status'),
  preflightAccounts: (accountIds, { deepModelCheck = false, forceWechatCheck = false } = {}) => request(`/accounts/preflight?deep_model_check=${deepModelCheck}&force_wechat_check=${forceWechatCheck}`, {
    method: 'POST', body: JSON.stringify(accountIds),
  }),
  batches: (includeArchived = false) => request(`/batches?limit=100&include_archived=${includeArchived}`),
  batch: (batchId, includeContent = true) => request(`/batches/${encodeURIComponent(batchId)}?include_content=${includeContent}`),
  createBatch: (payload) => request('/batches', { method: 'POST', body: JSON.stringify(payload) }),
  retryBatch: (batchId) => request(`/batches/${encodeURIComponent(batchId)}/retry-failed`, { method: 'POST' }),
  copyBatch: (batchId) => request(`/batches/${encodeURIComponent(batchId)}/copy`, { method: 'POST' }),
  cancelBatch: (batchId) => request(`/batches/${encodeURIComponent(batchId)}/cancel`, { method: 'POST' }),
  archiveBatch: (batchId) => request(`/batches/${encodeURIComponent(batchId)}/archive`, { method: 'POST' }),
  injectBatch: (batchId) => request(`/batches/${encodeURIComponent(batchId)}/drafts`, { method: 'POST' }),
  reviewInbox: (params = {}) => {
    const query = new URLSearchParams(params)
    return request(`/review-inbox?${query}`)
  },
  markViewed: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/view`, { method: 'POST' }),
  confirmJob: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/confirm`, { method: 'POST' }),
  needsChanges: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/needs-changes`, { method: 'POST' }),
  updateJob: (batchId, jobId, payload) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/content`, {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  rerenderJob: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/rerender`, { method: 'POST' }),
  retryJob: (batchId, jobId, payload = { step: 'auto' }) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/retry`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  jobAttempts: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/attempts`),
  selectJobTitle: (batchId, jobId, titleIndex, subtitleIndex = null) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/selection`, {
    method: 'PUT', body: JSON.stringify({ title_index: titleIndex, subtitle_index: subtitleIndex }),
  }),
  regenerateParagraph: (batchId, jobId, paragraphIndex, instruction) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/paragraph`, {
    method: 'POST', body: JSON.stringify({ paragraph_index: paragraphIndex, instruction }),
  }),
  regenerateInlineImages: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/inline-images/regenerate`, { method: 'POST' }),
  regenerateInlineImage: (batchId, jobId, imageIndex, instruction) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/inline-images/${imageIndex}/regenerate`, {
    method: 'POST', body: JSON.stringify({ instruction }),
  }),
  deleteInlineImage: (batchId, jobId, imageIndex) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/inline-images/${imageIndex}`, { method: 'DELETE' }),
  versions: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/versions`),
  restoreVersion: (batchId, jobId, versionId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/versions/${versionId}/restore`, { method: 'POST' }),
  covers: (batchId, jobId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/covers`),
  selectCover: (batchId, jobId, thumbMediaId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/cover`, {
    method: 'PUT', body: JSON.stringify({ thumb_media_id: thumbMediaId }),
  }),
  generateCover: (batchId, jobId, instruction = '') => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/cover/generate`, {
    method: 'POST', body: JSON.stringify({ instruction }),
  }),
  topics: (params = {}) => request(`/topics?${new URLSearchParams(params)}`),
  searchTopics: (params = {}) => request(`/topics/search?${new URLSearchParams(params)}`),
  addTopic: (payload) => request('/topics/manual', { method: 'POST', body: JSON.stringify(payload) }),
  topicSources: () => request('/topic-sources'),
  saveTopicSource: (payload) => request('/topic-sources', { method: 'POST', body: JSON.stringify(payload) }),
  deleteTopicSource: (sourceId) => request(`/topic-sources/${encodeURIComponent(sourceId)}`, { method: 'DELETE' }),
  refreshTopicSources: () => request('/topic-sources/refresh', { method: 'POST' }),
  followedAccounts: () => request('/followed-accounts'),
  followedBackendSession: () => request('/followed-accounts/backend-session'),
  saveFollowedBackendSession: (payload) => request('/followed-accounts/backend-session', { method: 'PUT', body: JSON.stringify(payload) }),
  testFollowedBackendSession: (payload) => request('/followed-accounts/backend-session/test', { method: 'POST', body: JSON.stringify(payload) }),
  clearFollowedBackendSession: () => request('/followed-accounts/backend-session', { method: 'DELETE' }),
  saveFollowedAccount: (payload) => request('/followed-accounts', { method: 'POST', body: JSON.stringify(payload) }),
  deleteFollowedAccount: (accountId) => request(`/followed-accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
  refreshFollowedAccount: (accountId) => request(`/followed-accounts/${encodeURIComponent(accountId)}/refresh`, { method: 'POST' }),
  refreshFollowedAccounts: () => request('/followed-accounts/refresh', { method: 'POST' }),
  followedArticles: (params = {}) => request(`/followed-articles?${new URLSearchParams(params)}`),
  addFollowedArticle: (payload) => request('/followed-articles', { method: 'POST', body: JSON.stringify(payload) }),
  updateFollowedArticle: (articleId, payload) => request(`/followed-articles/${encodeURIComponent(articleId)}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  editorialOptions: () => request('/editorial-review/options'),
  editorialProfiles: () => request('/editorial-review/profiles'),
  createEditorialProfile: (payload) => request('/editorial-review/profiles', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  updateEditorialProfile: (profileId, payload) => request(`/editorial-review/profiles/${encodeURIComponent(profileId)}`, {
    method: 'PUT', body: JSON.stringify(payload),
  }),
  deleteEditorialProfile: (profileId) => request(`/editorial-review/profiles/${encodeURIComponent(profileId)}`, { method: 'DELETE' }),
  editorialDefault: (accountId) => request(`/accounts/${encodeURIComponent(accountId)}/editorial-review-default`),
  setEditorialDefault: (accountId, profileId, config = {}) => request(`/accounts/${encodeURIComponent(accountId)}/editorial-review-default`, {
    method: 'PUT', body: JSON.stringify({ profile_id: profileId, config }),
  }),
  reviews: ({ jobId, batchId, limit = 20 }) => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (jobId) params.set('job_id', String(jobId))
    if (batchId) params.set('batch_id', batchId)
    return request(`/editorial-reviews?${params}`)
  },
  review: (reviewId) => request(`/editorial-reviews/${encodeURIComponent(reviewId)}`),
  runReview: (batchId, jobId, payload = {}) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/editorial-reviews`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  rewriteCandidate: (batchId, jobId, reviewId, payload) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/editorial-reviews/${encodeURIComponent(reviewId)}/rewrite-candidates`, {
    method: 'POST', body: JSON.stringify(payload),
  }),
  reviewApplications: (reviewId) => request(`/editorial-reviews/${encodeURIComponent(reviewId)}/applications`),
  applyCandidate: (batchId, jobId, applicationId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/editorial-review-applications/${encodeURIComponent(applicationId)}/apply`, { method: 'POST' }),
  keepSource: (batchId, jobId, applicationId) => request(`/batches/${encodeURIComponent(batchId)}/jobs/${jobId}/editorial-review-applications/${encodeURIComponent(applicationId)}/keep-source`, { method: 'POST' }),
  resolveIssue: (reviewId, issueId, payload) => request(`/editorial-reviews/${encodeURIComponent(reviewId)}/issues/${encodeURIComponent(issueId)}`, {
    method: 'PATCH', body: JSON.stringify(payload),
  }),
  adminUsers: () => request('/admin/users'),
  updateAdminUser: (userId, enabled) => request(`/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH', body: JSON.stringify({ enabled }),
  }),
  adminModels: () => request('/admin/models'),
  saveAdminModel: (payload) => request('/admin/models', { method: 'POST', body: JSON.stringify(payload) }),
  testAdminModel: (modelId) => request(`/admin/models/${encodeURIComponent(modelId)}/test`, { method: 'POST' }),
  testAdminImageModel: (modelId) => requestBlob(`/admin/models/${encodeURIComponent(modelId)}/test-image`, { method: 'POST' }),
  deleteAdminModel: (modelId) => request(`/admin/models/${encodeURIComponent(modelId)}`, { method: 'DELETE' }),
  configurationAccounts: () => request('/configuration/accounts'),
  saveAccount: (payload) => request('/configuration/accounts', { method: 'POST', body: JSON.stringify(payload) }),
  deleteAccount: (accountId) => request(`/configuration/accounts/${encodeURIComponent(accountId)}`, { method: 'DELETE' }),
  saveAccountLayout: (accountId, layout) => request(`/configuration/accounts/${encodeURIComponent(accountId)}/layout`, { method: 'PUT', body: JSON.stringify({ layout }) }),
  bindAccountPrompt: (accountId, purpose, templateId) => request(`/configuration/accounts/${encodeURIComponent(accountId)}/prompts/${encodeURIComponent(purpose)}`, { method: 'PUT', body: JSON.stringify({ template_id: templateId || null }) }),
  templateDrafts: (accountId, placeholder) => request(`/configuration/accounts/${encodeURIComponent(accountId)}/template-drafts?placeholder=${encodeURIComponent(placeholder)}`),
  applyTemplateDraft: (accountId, payload) => request(`/configuration/accounts/${encodeURIComponent(accountId)}/template-draft`, { method: 'PUT', body: JSON.stringify(payload) }),
  promptTemplates: () => request('/configuration/prompt-templates'),
  savePromptTemplate: (payload) => request('/configuration/prompt-templates', { method: 'POST', body: JSON.stringify(payload) }),
  deletePromptTemplate: (templateId) => request(`/configuration/prompt-templates/${encodeURIComponent(templateId)}`, { method: 'DELETE' }),
  creationPlans: () => request('/configuration/creation-plans'),
  saveCreationPlan: (payload) => request('/configuration/creation-plans', { method: 'POST', body: JSON.stringify(payload) }),
  deleteCreationPlan: (planId) => request(`/configuration/creation-plans/${encodeURIComponent(planId)}`, { method: 'DELETE' }),
  accountCreationPlan: (accountId) => request(`/configuration/accounts/${encodeURIComponent(accountId)}/creation-plan`),
  applyCreationPlan: (accountId, planId) => request(`/configuration/accounts/${encodeURIComponent(accountId)}/creation-plan`, { method: 'PUT', body: JSON.stringify({ plan_id: planId }) }),
  feishuSettings: () => request('/configuration/feishu'),
  saveFeishuSettings: (payload) => request('/configuration/feishu', { method: 'PUT', body: JSON.stringify(payload) }),
  testFeishuSettings: (payload) => request('/configuration/feishu/test', { method: 'POST', body: JSON.stringify(payload) }),
  feishuPairing: () => request('/configuration/feishu/pairing'),
  createFeishuPairing: () => request('/configuration/feishu/pairing', { method: 'POST' }),
  wechatRelaySettings: () => request('/configuration/wechat-relay'),
  saveWechatRelaySettings: (payload) => request('/configuration/wechat-relay', { method: 'PUT', body: JSON.stringify(payload) }),
}

export { API_ROOT, TOKEN_KEY }
