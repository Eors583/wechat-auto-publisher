<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from '@/components/AppShell.vue'
import { session } from '@/stores/session'

const route = useRoute()
const isPublic = computed(() => Boolean(route.meta.public))
</script>

<template>
  <el-config-provider size="default" :z-index="4000">
    <router-view v-if="isPublic" />
    <AppShell v-else-if="session.state.ready && session.state.user">
      <router-view />
    </AppShell>
    <div v-else class="app-boot">
      <div class="app-boot__mark">蓝血内容工作台</div>
      <el-progress :percentage="68" :show-text="false" :stroke-width="4" />
    </div>
  </el-config-provider>
</template>
