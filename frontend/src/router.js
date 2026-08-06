import { createRouter, createWebHashHistory } from 'vue-router'

import { session } from '@/stores/session'

const LoginView = () => import('@/views/LoginView.vue')
const CreateView = () => import('@/views/CreateView.vue')
const TopicsView = () => import('@/views/TopicsView.vue')
const TasksView = () => import('@/views/TasksView.vue')
const SettingsView = () => import('@/views/SettingsView.vue')

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'create', component: CreateView },
    { path: '/topics', name: 'topics', component: TopicsView },
    { path: '/tasks', name: 'tasks', component: TasksView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach(async (to) => {
  await session.restore()
  if (!to.meta.public && !session.isAuthenticated.value) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && session.isAuthenticated.value) return { name: 'create' }
  return true
})
