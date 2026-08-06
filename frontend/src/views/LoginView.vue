<script setup>
import { Lock, User } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { session } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const formRef = ref()
const loading = ref(false)
const mode = ref('login')
const form = reactive({ username: '', password: '', confirm: '' })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function submit() {
  await formRef.value?.validate()
  if (mode.value === 'register') {
    if (form.password.length < 6) return ElMessage.warning('密码至少需要 6 位')
    if (form.password !== form.confirm) return ElMessage.warning('两次输入的密码不一致')
  }
  loading.value = true
  try {
    if (mode.value === 'login') await session.login(form.username, form.password)
    else await session.register(form.username, form.password)
    ElMessage.success(mode.value === 'login' ? '登录成功' : '账号创建成功')
    await router.replace(String(route.query.redirect || '/'))
  } catch (error) {
    ElMessage.error(error.message)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="login-page">
    <section class="login-story">
      <div class="login-story__content">
        <span class="login-kicker">CONTENT OPERATIONS</span>
        <h1>把内容生产，变成一套<br />清晰、可控的工作流。</h1>
        <p>从选题、生成、AI 评审到公众号草稿，所有进度都在一个工作台完成。</p>
        <div class="login-points">
          <div><strong>01</strong><span>任务后台运行<br /><small>不用等待页面结束</small></span></div>
          <div><strong>02</strong><span>改写前后对比<br /><small>最终版本由你选择</small></span></div>
          <div><strong>03</strong><span>草稿安全模式<br /><small>不会自动对外群发</small></span></div>
        </div>
      </div>
      <div class="login-story__orb login-story__orb--one" />
      <div class="login-story__orb login-story__orb--two" />
    </section>

    <section class="login-panel">
      <div class="login-card">
        <div class="login-card__brand">
          <span>蓝血</span>
          <div><strong>公众号内容工作台</strong><small>Vue 3 · Element Plus</small></div>
        </div>
        <div class="login-card__heading">
          <span>{{ mode === 'login' ? '欢迎回来' : '创建运营账号' }}</span>
          <h2>{{ mode === 'login' ? '登录工作台' : '开始协作' }}</h2>
          <p>{{ mode === 'login' ? '使用你的账号继续管理内容任务' : '注册后即可进入独立的数据空间' }}</p>
        </div>

        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" :prefix-icon="User" size="large" autocomplete="username" placeholder="请输入用户名" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input v-model="form.password" :prefix-icon="Lock" size="large" type="password" show-password :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" placeholder="请输入密码" @keyup.enter="submit" />
          </el-form-item>
          <el-form-item v-if="mode === 'register'" label="确认密码">
            <el-input v-model="form.confirm" :prefix-icon="Lock" size="large" type="password" show-password autocomplete="new-password" placeholder="再次输入密码" @keyup.enter="submit" />
          </el-form-item>
          <el-button class="login-submit" type="primary" size="large" :loading="loading" native-type="submit">
            {{ mode === 'login' ? '进入工作台' : '注册并进入' }}
          </el-button>
        </el-form>

        <div class="login-switch">
          {{ mode === 'login' ? '还没有账号？' : '已经有账号？' }}
          <el-button link type="primary" @click="mode = mode === 'login' ? 'register' : 'login'">
            {{ mode === 'login' ? '立即注册' : '返回登录' }}
          </el-button>
        </div>
      </div>
    </section>
  </main>
</template>
