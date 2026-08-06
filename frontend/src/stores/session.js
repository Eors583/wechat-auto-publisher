import { computed, reactive } from 'vue'

import { api, getToken, setToken } from '@/api/client'

const state = reactive({
  ready: false,
  loading: false,
  user: null,
})

async function restore() {
  if (state.ready || state.loading) return state.user
  state.loading = true
  try {
    state.user = getToken() ? await api.me() : null
  } catch {
    state.user = null
    setToken('')
  } finally {
    state.loading = false
    state.ready = true
  }
  return state.user
}

async function login(username, password) {
  const result = await api.login(username, password)
  setToken(result.token)
  state.user = result.user || await api.me()
  state.ready = true
  return state.user
}

async function register(username, password) {
  const result = await api.register(username, password)
  setToken(result.token)
  state.user = result.user || await api.me()
  state.ready = true
  return state.user
}

async function logout() {
  try {
    await api.logout()
  } finally {
    setToken('')
    state.user = null
    state.ready = true
  }
}

window.addEventListener('publisher:unauthorized', () => {
  state.user = null
  state.ready = true
})

export const session = {
  state,
  isAuthenticated: computed(() => Boolean(state.user)),
  isAdmin: computed(() => state.user?.role === 'admin'),
  restore,
  login,
  register,
  logout,
}
