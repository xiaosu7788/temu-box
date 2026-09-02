<script setup lang="ts">
import { computed, ref } from 'vue'
import { Collection, Lock, User as UserIcon, UserFilled } from '@element-plus/icons-vue'
import { errorMessage, login, register } from '../api'
import type { User } from '../types'

const emit = defineEmits<{ authenticated: [user: User] }>()
const registerMode = ref(false)
const username = ref('')
const password = ref('')
const displayName = ref('')
const loading = ref(false)
const formError = ref('')
const formNotice = ref('')
const submitText = computed(() => registerMode.value ? '提交注册申请' : '登录工作台')

function clearFeedback() {
  formError.value = ''
  formNotice.value = ''
}

function switchMode(nextRegisterMode: boolean) {
  if (loading.value || registerMode.value === nextRegisterMode) return
  registerMode.value = nextRegisterMode
  password.value = ''
  clearFeedback()
}

async function submit() {
  if (loading.value) return
  clearFeedback()
  const normalizedUsername = username.value.trim()
  if (!normalizedUsername) {
    formError.value = '请输入用户名'
    return
  }
  if (!password.value) {
    formError.value = '请输入密码'
    return
  }
  if (normalizedUsername.length < 3 || password.value.length < 8) {
    formError.value = registerMode.value ? '用户名至少 3 位，密码至少 8 位' : '密码至少 8 位'
    return
  }
  loading.value = true
  try {
    if (registerMode.value) {
      await register(normalizedUsername, password.value, displayName.value.trim())
      registerMode.value = false
      password.value = ''
      formNotice.value = '注册申请已提交，请等待管理员审核后登录'
    } else {
      emit('authenticated', await login(normalizedUsername, password.value))
    }
  } catch (error) {
    formError.value = errorMessage(error)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-shell">
    <section class="auth-panel" aria-labelledby="auth-title">
      <aside class="auth-context">
        <div class="auth-brand auth-brand--inverse">
          <span class="brand-mark"><el-icon><Collection /></el-icon></span>
          <div><strong>Temu-Box</strong><small>Operations Console</small></div>
        </div>
        <div class="auth-context-copy">
          <span>业务工作台</span>
          <h1>集中处理订单、库存与活动任务</h1>
          <p>使用已审核的账号进入系统。</p>
        </div>
        <div class="auth-data-preview" aria-hidden="true">
          <div><i></i><i></i><i></i></div>
          <div><i></i><i></i><i></i></div>
          <div><i></i><i></i><i></i></div>
        </div>
        <small class="auth-context-footer">Temu-Box · Internal Workspace</small>
      </aside>

      <div class="auth-form-panel">
        <div class="auth-mobile-brand">
          <span class="brand-mark"><el-icon><Collection /></el-icon></span>
          <div><strong>Temu-Box</strong><small>Operations Console</small></div>
        </div>

        <div class="auth-mode-switch" role="tablist" aria-label="账号操作">
          <button type="button" role="tab" :aria-selected="!registerMode" :class="{ active: !registerMode }" @click="switchMode(false)">账号登录</button>
          <button type="button" role="tab" :aria-selected="registerMode" :class="{ active: registerMode }" @click="switchMode(true)">注册账号</button>
        </div>

        <div class="auth-heading">
          <h1 id="auth-title">{{ registerMode ? '创建新账号' : '欢迎回来' }}</h1>
          <p>{{ registerMode ? '提交资料后，账号需由管理员审核。' : '请输入账号信息继续访问 Temu-Box。' }}</p>
        </div>

        <el-form class="auth-form" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input v-model="username" placeholder="请输入用户名" size="large" :prefix-icon="UserIcon" autocomplete="username" maxlength="80" @input="clearFeedback" />
          </el-form-item>
          <el-form-item v-if="registerMode" label="显示名称（可选）">
            <el-input v-model="displayName" placeholder="用于系统内显示" size="large" :prefix-icon="UserFilled" autocomplete="name" maxlength="120" @input="clearFeedback" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="password"
              type="password"
              show-password
              placeholder="请输入密码"
              size="large"
              :prefix-icon="Lock"
              :autocomplete="registerMode ? 'new-password' : 'current-password'"
              @input="clearFeedback"
            />
            <span v-if="registerMode" class="auth-field-hint">密码至少 8 位</span>
          </el-form-item>

          <el-alert v-if="formError" class="auth-feedback" type="error" :closable="false" show-icon :title="formError" />
          <el-alert v-if="formNotice" class="auth-feedback" type="success" :closable="false" show-icon :title="formNotice" />

          <el-button class="auth-submit" type="primary" size="large" native-type="submit" :loading="loading">{{ submitText }}</el-button>
        </el-form>

        <p class="auth-review-note"><el-icon><Lock /></el-icon><span>{{ registerMode ? '注册成功后需等待管理员审核' : '仅限已审核账号登录' }}</span></p>
      </div>
    </section>
  </main>
</template>