<script setup>
import { Check, Close, Delete, Loading, Warning } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'

import { activity } from '@/stores/activity'
import { formatDateTime } from '@/utils/format'

const router = useRouter()

function stateIcon(status) {
  if (status === 'completed') return Check
  if (status === 'failed') return Warning
  return Loading
}

async function openDetails(item) {
  activity.state.drawerOpen = false
  await router.push({
    path: '/tasks',
    query: {
      batchId: item.context?.batchId,
      ...(item.context?.jobId ? { jobId: item.context.jobId } : {}),
    },
  })
}
</script>

<template>
  <el-drawer
    v-model="activity.state.drawerOpen"
    class="activity-drawer"
    direction="rtl"
    size="min(420px, 92vw)"
    :show-close="false"
  >
    <template #header>
      <div class="drawer-heading">
        <div>
          <span class="eyebrow">BACKGROUND ACTIVITY</span>
          <h2>后台任务</h2>
        </div>
        <el-button circle text :icon="Close" aria-label="关闭" @click="activity.state.drawerOpen = false" />
      </div>
    </template>

    <div v-if="activity.state.items.length" class="activity-list">
      <article v-for="item in activity.state.items" :key="item.id" class="activity-item" :class="`is-${item.status}`">
        <div class="activity-item__top">
          <div class="activity-item__icon">
            <el-icon :class="{ 'is-loading': item.status === 'running' }">
              <component :is="stateIcon(item.status)" />
            </el-icon>
          </div>
          <div class="activity-item__content">
            <strong>{{ item.title }}</strong>
            <p>{{ item.description || (item.status === 'running' ? '正在后台处理，可继续使用其他功能' : '任务已完成') }}</p>
          </div>
          <el-button
            v-if="item.status !== 'running'"
            circle
            text
            :icon="Delete"
            aria-label="删除记录"
            @click="activity.remove(item.id)"
          />
        </div>
        <el-progress
          :percentage="Math.round(item.progress)"
          :status="item.status === 'failed' ? 'exception' : item.status === 'completed' ? 'success' : undefined"
          :stroke-width="8"
        />
        <div class="activity-item__meta">
          <span>{{ item.type === 'review' ? 'AI 评审' : item.type === 'rewrite' ? 'AI 改写' : '文章任务' }}</span>
          <time>{{ formatDateTime(item.updatedAt) }}</time>
        </div>
        <el-button v-if="item.context?.batchId" class="activity-detail-button" text type="primary" @click="openDetails(item)">
          查看任务详情
        </el-button>
      </article>
    </div>
    <el-empty v-else description="暂无后台任务" :image-size="104" />

    <template #footer>
      <div class="drawer-footer">
        <span>进行中的任务关闭抽屉后仍会继续</span>
        <el-button text :disabled="!activity.state.items.some((item) => item.status !== 'running')" @click="activity.clearFinished">
          清除已完成记录
        </el-button>
      </div>
    </template>
  </el-drawer>
</template>
