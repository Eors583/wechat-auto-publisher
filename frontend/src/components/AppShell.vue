<script setup>
import {
  Bell,
  Collection,
  EditPen,
  Fold,
  Grid,
  Operation,
  Setting,
} from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ActivityCenter from '@/components/ActivityCenter.vue'
import { activity } from '@/stores/activity'
import { session } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const mobile = ref(false)
const sidebarOpen = ref(true)

const menuItems = [
  { path: '/', label: '创作工作台', icon: EditPen },
  { path: '/topics', label: '选题与素材', icon: Collection },
  { path: '/tasks', label: '任务与审核', icon: Operation },
  { path: '/settings', label: '系统设置', icon: Setting },
]

const currentPath = computed(() => route.path)
const title = computed(() => menuItems.find((item) => item.path === route.path)?.label || '内容工作台')

function syncViewport() {
  mobile.value = window.innerWidth < 900
  sidebarOpen.value = !mobile.value
}

async function logout() {
  await session.logout()
  await router.replace('/login')
}

onMounted(() => {
  syncViewport()
  activity.startSync()
  window.addEventListener('resize', syncViewport, { passive: true })
})
onBeforeUnmount(() => {
  activity.stopSync()
  window.removeEventListener('resize', syncViewport)
})
</script>

<template>
  <div class="app-layout">
    <transition name="sidebar-fade">
      <button
        v-if="mobile && sidebarOpen"
        class="sidebar-scrim"
        aria-label="关闭导航"
        @click="sidebarOpen = false"
      />
    </transition>

    <aside class="app-sidebar" :class="{ 'is-open': sidebarOpen, 'is-mobile': mobile }">
      <div class="brand-block">
        <div class="brand-block__icon"><Grid /></div>
        <div>
          <strong>蓝血内容工作台</strong>
          <span>CONTENT OPERATIONS</span>
        </div>
      </div>

      <div class="workspace-caption">内容生产</div>
      <el-menu
        class="app-menu"
        :default-active="currentPath"
        :router="true"
        @select="() => mobile && (sidebarOpen = false)"
      >
        <el-menu-item v-for="item in menuItems" :key="item.path" :index="item.path">
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-spacer" />
      <button class="account-card" type="button" @click="router.push('/settings')">
        <el-avatar :size="38">{{ session.state.user?.username?.slice(0, 1)?.toUpperCase() }}</el-avatar>
        <span>
          <strong>{{ session.state.user?.username }}</strong>
          <small>{{ session.state.user?.role === 'admin' ? '管理员' : '内容运营' }}</small>
        </span>
      </button>
      <el-button class="logout-button" text @click="logout">退出登录</el-button>
    </aside>

    <main class="app-main">
      <header class="app-header">
        <div class="app-header__left">
          <el-button v-if="mobile" circle text :icon="Fold" aria-label="打开导航" @click="sidebarOpen = true" />
          <div>
            <p>公众号内容生产系统</p>
            <h1>{{ title }}</h1>
          </div>
        </div>
        <div class="app-header__actions">
          <el-tooltip content="后台任务与进度" placement="bottom">
            <el-badge :value="activity.activeCount.value" :hidden="!activity.activeCount.value">
              <el-button circle plain :icon="Bell" aria-label="打开任务进度" @click="activity.show" />
            </el-badge>
          </el-tooltip>
          <el-tag effect="plain" type="success" round>仅写入草稿，不自动群发</el-tag>
        </div>
      </header>

      <div class="page-content">
        <slot />
      </div>
    </main>

    <ActivityCenter />
  </div>
</template>
